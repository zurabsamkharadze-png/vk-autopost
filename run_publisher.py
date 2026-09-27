#!/usr/bin/env python3
import hashlib
import json
import mimetypes
import os
import sys
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QUEUE_FILE = ROOT / "posts.json"
VK_API_BASE = "https://api.vk.com/method"
VK_API_VERSION = os.getenv("VK_API_VERSION", "5.199")


def fail(message: str, code: int = 1):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def vk_call(method: str, token: str, **params):
    payload = {**params, "access_token": token, "v": VK_API_VERSION}
    data = urllib.parse.urlencode(payload, doseq=True).encode("utf-8")
    req = urllib.request.Request(
        f"{VK_API_BASE}/{method}",
        data=data,
        headers={"User-Agent": "geotrips-vk-autopost/1.6"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as response:
        body = json.loads(response.read().decode("utf-8"))
    if "error" in body:
        err = body["error"]
        raise RuntimeError(
            f"VK API {method} failed: {err.get('error_code')} {err.get('error_msg')}"
        )
    return body.get("response")


def download_image(url: str):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://experience.tripster.ru/",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        content = response.read()
        content_type = response.headers.get_content_type() or "application/octet-stream"
    if not content:
        raise RuntimeError("Downloaded image is empty")
    if not content_type.startswith("image/"):
        raise RuntimeError(f"Image URL returned unexpected content type: {content_type}")
    ext = mimetypes.guess_extension(content_type) or ".jpg"
    return f"tripster{ext}", content_type, content


def multipart_upload(url: str, field_name: str, filename: str, content_type: str, content: bytes):
    boundary = f"----GeoTripsVK{uuid.uuid4().hex}"
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        (
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode(),
        content,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
            "User-Agent": "geotrips-vk-autopost/1.6",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as response:
        return json.loads(response.read().decode("utf-8"))


def upload_wall_photo(user_token: str, group_id: int, image_url: str) -> str:
    server = vk_call("photos.getWallUploadServer", user_token, group_id=group_id)
    filename, content_type, content = download_image(image_url)
    uploaded = multipart_upload(server["upload_url"], "photo", filename, content_type, content)
    saved = vk_call(
        "photos.saveWallPhoto",
        user_token,
        group_id=group_id,
        photo=uploaded["photo"],
        server=uploaded["server"],
        hash=uploaded["hash"],
    )
    if not saved:
        raise RuntimeError("VK did not return saved photo data")
    photo = saved[0]
    attachment = f"photo{photo['owner_id']}_{photo['id']}"
    if photo.get("access_key"):
        attachment += f"_{photo['access_key']}"
    return attachment


def upload_message_photo(group_token: str, image_url: str):
    """Upload an image through the group-authorized messages photo endpoint.

    VK 5.199 permits community tokens for photos.getMessagesUploadServer and
    photos.saveMessagesPhoto. The saved photo is then supplied to wall.post as
    link_photo_id so VK can build a normal link card without wall photo upload.
    """
    server = vk_call("photos.getMessagesUploadServer", group_token)
    filename, content_type, content = download_image(image_url)
    uploaded = multipart_upload(server["upload_url"], "photo", filename, content_type, content)
    saved = vk_call(
        "photos.saveMessagesPhoto",
        group_token,
        photo=uploaded["photo"],
        server=uploaded["server"],
        hash=uploaded["hash"],
    )
    if not saved:
        raise RuntimeError("VK did not return saved message photo data")
    photo = saved[0]
    attachment = f"photo{photo['owner_id']}_{photo['id']}"
    if photo.get("access_key"):
        attachment += f"_{photo['access_key']}"
    link_photo_id = f"{photo['owner_id']}_{photo['id']}"
    return photo, attachment, link_photo_id


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
    QUEUE_FILE.write_text(
        json.dumps(posts, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


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
    group_token = os.getenv("VK_ACCESS_TOKEN", "").strip()
    user_token = os.getenv("VK_USER_TOKEN", "").strip()
    raw_group_id = os.getenv("VK_GROUP_ID", "").strip()

    if not group_token:
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
    preview_url = str(post.get("preview_url", "")).strip()
    link_title = str(post.get("link_title", "")).strip()
    if not link_title:
        link_title = text.splitlines()[0].strip()[:120]

    link_photo_id = ""
    message_photo_attachment = ""

    if image_url and user_token:
        print("Uploading native Tripster photo to VK with user token...")
        attachments.append(upload_wall_photo(user_token, group_id, image_url))
        post["photo_mode"] = "native_photo"
    elif image_url and preview_url:
        print("Uploading Tripster image through community-token messages photo API...")
        photo, message_photo_attachment, link_photo_id = upload_message_photo(group_token, image_url)
        print(
            "Saved community-token photo for link preview: "
            f"owner_id={photo.get('owner_id')} id={photo.get('id')}"
        )
        attachments.append(preview_url)
        post["photo_mode"] = "message_photo_link_preview"
    elif preview_url:
        attachments.append(preview_url)
        post["photo_mode"] = "og_link_preview"
    elif image_url:
        fail(
            "This post has a Tripster image, but neither VK_USER_TOKEN nor preview_url is available. "
            "Refusing to publish without a visual."
        )

    for attachment in post.get("attachments", []) or []:
        value = str(attachment).strip()
        if value and value not in attachments:
            attachments.append(value)

    post_id = str(post.get("id") or uuid.uuid4().hex)
    guid = hashlib.sha256(post_id.encode("utf-8")).hexdigest()[:32]

    params = {
        "owner_id": -group_id,
        "from_group": 1,
        "message": text,
        "attachments": ",".join(attachments) if attachments else "",
        "guid": guid,
    }
    if link_photo_id:
        params["link_title"] = link_title
        params["link_photo_id"] = link_photo_id

    try:
        result = vk_call("wall.post", group_token, **params)
    except RuntimeError as first_error:
        if not message_photo_attachment or not preview_url:
            raise
        print(f"Link-photo preview failed: {first_error}")
        print("Retrying with the saved message photo attached directly beside the preview URL...")
        params.pop("link_title", None)
        params.pop("link_photo_id", None)
        params["attachments"] = f"{message_photo_attachment},{preview_url}"
        params["guid"] = hashlib.sha256((post_id + "-photo-link").encode("utf-8")).hexdigest()[:32]
        result = vk_call("wall.post", group_token, **params)
        post["photo_mode"] = "message_photo_attachment_with_link"

    vk_post_id = result.get("post_id") if isinstance(result, dict) else result
    post["status"] = "published"
    post["published_at"] = datetime.now(timezone.utc).isoformat()
    post["vk_post_id"] = vk_post_id
    save_queue(posts)
    print(f"Published VK post: {vk_post_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
