#!/usr/bin/env python3
import hashlib
import json
import mimetypes
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QUEUE_FILE = ROOT / "posts.json"
ENCRYPTED_USER_TOKEN_FILE = ROOT / "vk_user_token.enc"
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
        headers={"User-Agent": "geotrips-vk-autopost/2.0"},
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


def decrypt_server_user_token(client_secret: str) -> str:
    if not ENCRYPTED_USER_TOKEN_FILE.exists():
        return ""
    if not client_secret:
        fail("Encrypted VK user token exists, but VK_CLIENT_SECRET is missing")
    env = os.environ.copy()
    env["VK_TOKEN_PASSPHRASE"] = client_secret
    proc = subprocess.run(
        [
            "openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000",
            "-pass", "env:VK_TOKEN_PASSPHRASE", "-a", "-A"
        ],
        input=ENCRYPTED_USER_TOKEN_FILE.read_bytes(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        fail("Could not decrypt the stored VK user token")
    token = proc.stdout.decode("utf-8").strip()
    if not token:
        fail("Stored VK user token decrypted to an empty value")
    return token


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
            "User-Agent": "geotrips-vk-autopost/2.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as response:
        return json.loads(response.read().decode("utf-8"))



def upload_wall_image_document(group_token: str, group_id: int, image_url: str, title: str = "GeoTrips photo") -> str:
    """Upload an image as a VK image-document using only the community token.

    VK group tokens cannot use photos.getWallUploadServer, but VK API 5.199
    allows docs.getWallUploadServer/docs.save with group auth. Image documents
    include a photo preview and can be attached to wall.post.
    """
    filename, content_type, content = download_image(image_url)
    last_error = "unknown upload error"
    uploaded = None

    for attempt in range(1, 9):
        server = vk_call("docs.getWallUploadServer", group_token, group_id=group_id)
        upload_url = str((server or {}).get("upload_url") or "").strip()
        if not upload_url:
            last_error = "VK returned no document upload URL"
            continue
        try:
            result = multipart_upload(upload_url, "file", filename, content_type, content)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            last_error = f"HTTP {exc.code}: {detail[:300]}"
            print(f"VK document upload attempt {attempt} failed: {last_error}")
            continue
        except Exception as exc:
            last_error = str(exc)
            print(f"VK document upload attempt {attempt} failed: {last_error}")
            continue

        file_token = str((result or {}).get("file") or "").strip()
        if file_token:
            uploaded = result
            print(f"VK document upload succeeded on attempt {attempt}.")
            break

        last_error = f"{(result or {}).get('error', 'no file token')}: {(result or {}).get('error_descr', '')}".strip()
        print(f"VK document upload attempt {attempt} failed: {last_error}")

    if not uploaded:
        raise RuntimeError(f"Could not upload Tripster image to VK after retries: {last_error}")

    saved = vk_call(
        "docs.save",
        group_token,
        file=uploaded["file"],
        title=title[:100] or "GeoTrips photo",
    )
    doc = saved.get("doc") if isinstance(saved, dict) else None
    if not isinstance(doc, dict):
        raise RuntimeError("VK docs.save did not return an image document")

    preview = ((doc.get("preview") or {}).get("photo") or {})
    sizes = preview.get("sizes") or []
    if not sizes:
        raise RuntimeError("VK saved the document but returned no image preview; post was not published")

    attachment = f"doc{doc['owner_id']}_{doc['id']}"
    if doc.get("access_key"):
        attachment += f"_{doc['access_key']}"
    return attachment

def upload_wall_photo_via_gateway(gateway_url: str, gateway_key: str, image_url: str) -> str:
    payload = json.dumps({"action": "upload", "image_url": image_url}).encode("utf-8")
    req = urllib.request.Request(
        gateway_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {gateway_key}",
            "User-Agent": "geotrips-vk-autopost/3.0",
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
        raise RuntimeError(f"VK photo gateway failed: {body.get('error')} {body.get('message', '')}".strip())
    attachment = str(body.get("attachment") or "").strip()
    if not attachment.startswith("photo"):
        raise RuntimeError("VK photo gateway did not return a native photo attachment")
    return attachment


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
        raise RuntimeError("VK did not return saved wall photo data")
    photo = saved[0]
    attachment = f"photo{photo['owner_id']}_{photo['id']}"
    if photo.get("access_key"):
        attachment += f"_{photo['access_key']}"
    return attachment


def upload_message_photo(group_token: str, group_id: int, image_url: str) -> str:
    """Upload a VK photo through the community messages-photo endpoint and reuse it on the wall."""
    server = vk_call("photos.getMessagesUploadServer", group_token, group_id=group_id)
    upload_url = server["upload_url"]
    path = urllib.parse.urlparse(upload_url).path
    filename, content_type, content = download_image(image_url)
    field_name = "file1" if "bulk_upload" in path else "photo"
    uploaded = multipart_upload(upload_url, field_name, filename, content_type, content)
    photo_payload = uploaded.get("photo")
    if not photo_payload or str(photo_payload).strip() in ("", "[]", "{}", "None"):
        raise RuntimeError("VK message photo upload did not return a savable photo payload")
    saved = vk_call(
        "photos.saveMessagesPhoto",
        group_token,
        photo=photo_payload,
        server=uploaded.get("server"),
        hash=uploaded.get("hash"),
    )
    if not saved:
        raise RuntimeError("VK did not return saved message photo data")
    photo = saved[0]
    attachment = f"photo{photo['owner_id']}_{photo['id']}"
    if photo.get("access_key"):
        attachment += f"_{photo['access_key']}"
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


def main():
    group_token = os.getenv("VK_ACCESS_TOKEN", "").strip()
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
    for attachment in post.get("attachments", []) or []:
        value = str(attachment).strip()
        if value and value not in attachments:
            attachments.append(value)

    image_url = str(post.get("image_url", "")).strip()

    if image_url:
        invalid = [a for a in attachments if not a.startswith("photo")]
        if invalid:
            fail(f"Queued post {post.get('id')} contains a non-photo VK attachment; refusing to publish: {invalid}")
        if not attachments:
            print("Uploading Tripster image as a native VK photo with the community token...")
            attachment = upload_message_photo(group_token, group_id, image_url)
            if not attachment.startswith("photo"):
                fail("VK community photo upload returned a non-photo attachment; refusing to publish")
            attachments.append(attachment)
            post["photo_mode"] = "community_token_native_photo"

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
