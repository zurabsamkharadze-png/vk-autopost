#!/usr/bin/env python3
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QUEUE_FILE = ROOT / "posts.json"
VK_API_BASE = "https://api.vk.com/method"
VK_API_VERSION = os.getenv("VK_API_VERSION", "5.199")
AD_DISCLOSURE = "Реклама. TRIPSGO PORTAL L.L.C, ИНН 9909760608"


def fail(message: str, code: int = 1):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def vk_call(method: str, token: str, **params):
    payload = {**params, "access_token": token, "v": VK_API_VERSION}
    data = urllib.parse.urlencode(payload, doseq=True).encode("utf-8")
    req = urllib.request.Request(
        f"{VK_API_BASE}/{method}",
        data=data,
        headers={"User-Agent": "geotrips-vk-autopost/4.0"},
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


def upload_wall_photo_via_gateway(gateway_url: str, gateway_key: str, image_url: str) -> str:
    if not gateway_url:
        raise RuntimeError("VK_GATEWAY_URL is missing")
    if not gateway_key:
        raise RuntimeError("VK_GATEWAY_KEY secret is missing")

    payload = json.dumps({"action": "upload", "image_url": image_url}).encode("utf-8")
    req = urllib.request.Request(
        gateway_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {gateway_key}",
            "User-Agent": "geotrips-vk-autopost/4.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"VK photo gateway HTTP {exc.code}: {detail[:500]}") from exc

    if not body.get("ok"):
        raise RuntimeError(
            f"VK photo gateway failed: {body.get('error')} {body.get('message', '')}".strip()
        )

    attachment = str(body.get("attachment") or "").strip()
    if not attachment.startswith("photo"):
        raise RuntimeError("VK photo gateway did not return a native photo attachment")
    return attachment


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


def is_tripster_post(post: dict, text: str, image_url: str) -> bool:
    haystack = " ".join(
        str(value or "")
        for value in (
            text,
            image_url,
            post.get("source"),
            post.get("partner"),
            post.get("url"),
            post.get("title"),
        )
    ).lower()
    return "tripster" in haystack


def ensure_ad_disclosure(text: str) -> str:
    text = text.rstrip()
    if text.endswith(AD_DISCLOSURE):
        return text
    return f"{text}\n\n{AD_DISCLOSURE}"


def main():
    group_token = os.getenv("VK_ACCESS_TOKEN", "").strip()
    raw_group_id = os.getenv("VK_GROUP_ID", "").strip()
    gateway_url = os.getenv("VK_GATEWAY_URL", "").strip()
    gateway_key = os.getenv("VK_GATEWAY_KEY", "").strip()

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
    for attachment in post.get("attachments", []) or []:
        value = str(attachment).strip()
        if value and value not in attachments:
            attachments.append(value)

    invalid = [a for a in attachments if not a.startswith("photo")]
    if invalid:
        fail(
            f"Queued post {post.get('id')} contains a non-photo VK attachment; "
            f"refusing to publish: {invalid}"
        )

    image_url = str(post.get("image_url", "")).strip()
    tripster_post = is_tripster_post(post, text, image_url)

    if image_url and not attachments:
        print("Uploading Tripster image through the server gateway as a native VK photo...")
        try:
            attachment = upload_wall_photo_via_gateway(gateway_url, gateway_key, image_url)
        except Exception as exc:
            fail(f"Native VK photo upload failed; post will NOT be published: {exc}")
        attachments.append(attachment)
        post["photo_mode"] = "community_oauth_native_photo"

    if tripster_post and not attachments:
        fail(
            f"Tripster post {post.get('id')} has no native VK photo. "
            "Text-only publication is forbidden."
        )

    if tripster_post:
        text = ensure_ad_disclosure(text)
        post["text"] = text

    post_id = str(post.get("id") or uuid.uuid4().hex)
    guid = hashlib.sha256(post_id.encode("utf-8")).hexdigest()[:32]

    result = vk_call(
        "wall.post",
        group_token,
        owner_id=-group_id,
        from_group=1,
        message=text,
        attachments=",".join(attachments) if attachments else "",
        guid=guid,
    )

    vk_post_id = result.get("post_id") if isinstance(result, dict) else result
    if attachments:
        post["attachments"] = attachments
    post["status"] = "published"
    post["published_at"] = datetime.now(timezone.utc).isoformat()
    post["vk_post_id"] = vk_post_id
    save_queue(posts)
    print(f"Published VK post: {vk_post_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
