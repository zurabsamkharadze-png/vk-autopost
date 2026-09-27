#!/usr/bin/env python3
import hashlib
import html
import json
import mimetypes
import os
import re
import sys
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QUEUE_FILE = ROOT / "posts.json"
VK_API_BASE = "https://api.vk.com/method"
VK_API_VERSION = os.getenv("VK_API_VERSION", "5.199")
TRIPSTER_HOSTS = {"experience.tripster.ru", "tripster.ru", "www.tripster.ru"}
TRIPSTER_IMAGE_HOSTS = {"resize.tripster.ru", "cdn.tripster.ru", "static2.tripster.ru"}


def fail(message: str, code: int = 1):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def vk_call(method: str, token: str, **params):
    payload = {**params, "access_token": token, "v": VK_API_VERSION}
    data = urllib.parse.urlencode(payload, doseq=True).encode("utf-8")
    req = urllib.request.Request(
        f"{VK_API_BASE}/{method}",
        data=data,
        headers={"User-Agent": "geotrips-vk-autopost/1.1"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as response:
        body = json.loads(response.read().decode("utf-8"))
    if "error" in body:
        err = body["error"]
        raise RuntimeError(f"VK API {method} failed: {err.get('error_code')} {err.get('error_msg')}")
    return body.get("response")


def download_image(url: str):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Referer": "https://experience.tripster.ru/",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        content = response.read()
        content_type = response.headers.get_content_type() or "application/octet-stream"
    if not content:
        raise RuntimeError("Downloaded image is empty")
    if not content_type.startswith("image/"):
        raise RuntimeError(f"Tripster image URL returned unexpected content type: {content_type}")
    ext = mimetypes.guess_extension(content_type) or ".jpg"
    return f"image{ext}", content_type, content


def multipart_upload(url: str, field_name: str, filename: str, content_type: str, content: bytes):
    boundary = f"----GeoTripsVK{uuid.uuid4().hex}"
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        (f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n' f"Content-Type: {content_type}\r\n\r\n").encode(),
        content,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
            "User-Agent": "geotrips-vk-autopost/1.1",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as response:
        return json.loads(response.read().decode("utf-8"))


def upload_wall_photo(token: str, group_id: int, image_url: str) -> str:
    server = vk_call("photos.getWallUploadServer", token, group_id=group_id)
    filename, content_type, content = download_image(image_url)
    uploaded = multipart_upload(server["upload_url"], "photo", filename, content_type, content)
    saved = vk_call(
        "photos.saveWallPhoto",
        token,
        group_id=group_id,
        photo=uploaded["photo"],
        server=uploaded["server"],
        hash=uploaded["hash"],
    )
    if not saved:
        raise RuntimeError("VK did not return saved photo data")
    photo = saved[0]
    return f"photo{photo['owner_id']}_{photo['id']}"


class TripsterMetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "meta":
            return
        values = {str(k).lower(): str(v) for k, v in attrs if k and v}
        marker = values.get("property", "").lower() or values.get("name", "").lower() or values.get("itemprop", "").lower()
        if marker in {"og:image", "og:image:url", "twitter:image", "twitter:image:src", "image"}:
            content = values.get("content", "").strip()
            if content:
                self.images.append(content)


def _normalize_tripster_image_url(value: str):
    value = html.unescape(str(value).strip())
    value = value.replace("\\/", "/")
    value = re.sub(r"\\u002[fF]", "/", value)
    value = re.sub(r"\\u003[aA]", ":", value)
    value = value.strip('"\' ')
    if value.startswith("//"):
        value = "https:" + value
    parsed = urllib.parse.urlparse(value)
    host = parsed.netloc.lower().split(":", 1)[0]
    if parsed.scheme not in {"http", "https"} or host not in TRIPSTER_IMAGE_HOSTS:
        return None
    return value


def tripster_photo_candidates(tripster_url: str):
    parsed = urllib.parse.urlparse(tripster_url)
    host = parsed.netloc.lower().split(":", 1)[0]
    if parsed.scheme != "https" or host not in TRIPSTER_HOSTS:
        raise RuntimeError("tripster_url must point to an official Tripster page")

    req = urllib.request.Request(
        tripster_url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        final_url = response.geturl()
        body = response.read().decode("utf-8", errors="replace")

    parser = TripsterMetaParser()
    parser.feed(body)

    raw_candidates = list(parser.images)
    raw_candidates.extend(re.findall(r"https?:\\?/\\?/(?:resize|cdn|static2)\\?\.tripster\\?\.ru[^\"'<>\\s]+", body))

    candidates = []
    seen = set()
    for raw in raw_candidates:
        normalized = _normalize_tripster_image_url(raw)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(normalized)
        if len(candidates) >= 5:
            break

    if not candidates:
        raise RuntimeError(f"No excursion photos found on Tripster page: {final_url}")
    return final_url, candidates


def choose_tripster_photo(post):
    tripster_url = str(post.get("tripster_url", "")).strip()
    if not tripster_url:
        return ""

    final_url, candidates = tripster_photo_candidates(tripster_url)
    explicit_index = post.get("tripster_photo_index")
    if explicit_index is not None:
        try:
            index = int(explicit_index) % len(candidates)
        except (TypeError, ValueError):
            index = 0
    else:
        seed = str(post.get("id") or tripster_url).encode("utf-8")
        index = int(hashlib.sha256(seed).hexdigest()[:8], 16) % len(candidates)

    chosen = candidates[index]
    post["tripster_resolved_url"] = final_url
    post["tripster_image_url"] = chosen
    print(f"Tripster photo selected ({index + 1}/{len(candidates)}): {chosen}")
    return chosen


def parse_iso_datetime(value):
    if not value:
        return None
    normalized = str(value).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_queue():
    if not QUEUE_FILE.exists():
        fail(f"Queue file not found: {QUEUE_FILE}")
    data = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        fail("posts.json must contain a JSON array")
    return data


def save_queue(posts):
    QUEUE_FILE.write_text(json.dumps(posts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def select_next_post(posts):
    now = datetime.now(timezone.utc)
    for post in posts:
        if post.get("status") != "queued":
            continue
        publish_after = parse_iso_datetime(post.get("publish_after"))
        if publish_after and publish_after > now:
            continue
        return post
    return None


def normalized_group_id(raw: str) -> int:
    value = raw.strip().lstrip("-")
    if not value.isdigit():
        fail("VK_GROUP_ID must be numeric")
    return int(value)


def main():
    token = os.getenv("VK_ACCESS_TOKEN", "").strip()
    raw_group_id = os.getenv("VK_GROUP_ID", "").strip()
    if not token:
        fail("VK_ACCESS_TOKEN secret is missing")
    if not raw_group_id:
        fail("VK_GROUP_ID is missing")

    group_id = normalized_group_id(raw_group_id)
    posts = load_queue()
    post = select_next_post(posts)
    if not post:
        print("No queued VK posts are ready for publication.")
        return 0

    text = str(post.get("text", "")).strip()
    if not text:
        fail(f"Queued post {post.get('id')} has empty text")

    attachments = []
    image_url = str(post.get("image_url", "")).strip()
    if not image_url and post.get("tripster_url"):
        print("Finding an excursion photo on Tripster...")
        image_url = choose_tripster_photo(post)
    if image_url:
        print("Uploading photo to VK...")
        attachments.append(upload_wall_photo(token, group_id, image_url))

    for attachment in post.get("attachments", []) or []:
        if str(attachment).strip():
            attachments.append(str(attachment).strip())

    post_id = str(post.get("id") or uuid.uuid4().hex)
    guid = hashlib.sha256(post_id.encode("utf-8")).hexdigest()[:32]
    result = vk_call(
        "wall.post",
        token,
        owner_id=-group_id,
        from_group=1,
        message=text,
        attachments=",".join(attachments) if attachments else "",
        guid=guid,
    )
    vk_post_id = result.get("post_id") if isinstance(result, dict) else result
    post["status"] = "published"
    post["published_at"] = datetime.now(timezone.utc).isoformat()
    post["vk_post_id"] = vk_post_id
    save_queue(posts)
    print(f"Published VK post: {vk_post_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
