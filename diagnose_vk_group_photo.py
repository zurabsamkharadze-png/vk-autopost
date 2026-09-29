#!/usr/bin/env python3
import json, mimetypes, os, urllib.parse, urllib.request, uuid

VK_API_BASE = "https://api.vk.com/method"
VK_API_VERSION = os.getenv("VK_API_VERSION", "5.199")
GROUP_ID = os.getenv("VK_GROUP_ID", "241800139").strip().lstrip("-")
TOKEN = os.getenv("VK_ACCESS_TOKEN", "").strip()
IMAGE_URL = os.getenv("TEST_IMAGE_URL", "https://resize.tripster.ru/5-Zb81g6rfGSGHysszr6G_pG-uI%3D/fit-in/800x600/filters%3Ano_upscale%28%29/https%3A//cdn.tripster.ru/photos/353a1b90-06fb-406c-81a5-e4a627761e0d.jpg")


def vk_call(method, **params):
    payload = {**params, "access_token": TOKEN, "v": VK_API_VERSION}
    req = urllib.request.Request(
        f"{VK_API_BASE}/{method}",
        data=urllib.parse.urlencode(payload).encode(),
        headers={"User-Agent": "geotrips-vk-photo-diagnostic/1.1"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as r:
        data = json.loads(r.read().decode("utf-8"))
    if "error" in data:
        e = data["error"]
        raise RuntimeError(f"{method}: {e.get('error_code')} {e.get('error_msg')}")
    return data.get("response")


def download_image(url):
    req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0", "Referer":"https://experience.tripster.ru/"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
        ctype = r.headers.get_content_type() or "image/jpeg"
    ext = mimetypes.guess_extension(ctype) or ".jpg"
    return f"tripster{ext}", ctype, body


def multipart_upload(url, field_name, filename, content_type, content):
    boundary = f"----GeoTripsVK{uuid.uuid4().hex}"
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode(),
        f"Content-Type: {content_type}\r\n\r\n".encode(),
        content,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
        "User-Agent": "geotrips-vk-photo-diagnostic/1.1",
    }, method="POST")
    with urllib.request.urlopen(req, timeout=90) as r:
        text = r.read().decode("utf-8")
    return json.loads(text)


def recent_message_peers():
    response = vk_call("messages.getConversations", count=20, extended=0)
    peers = []
    for item in (response or {}).get("items", []):
        conv = item.get("conversation", {}) if isinstance(item, dict) else {}
        peer = conv.get("peer", {}) if isinstance(conv, dict) else {}
        pid = peer.get("id")
        ptype = peer.get("type")
        try:
            pid = int(pid)
        except Exception:
            continue
        if ptype == "user" and pid > 0 and pid not in peers:
            peers.append(pid)
    return peers


def manager_peers():
    managers = vk_call("groups.getMembers", group_id=GROUP_ID, filter="managers", fields="id")
    peers = []
    for item in managers.get("items", []) if isinstance(managers, dict) else []:
        uid = item.get("id") if isinstance(item, dict) else item
        try:
            uid = int(uid)
        except Exception:
            continue
        if uid > 0 and uid not in peers:
            peers.append(uid)
    return peers


def main():
    if not TOKEN:
        raise RuntimeError("VK_ACCESS_TOKEN missing")

    recent = recent_message_peers()
    print(f"RECENT_MESSAGE_PEERS={len(recent)}")
    candidates = recent + [x for x in manager_peers() if x not in recent]
    if not candidates:
        print("RESULT=NO_PEER")
        return 3

    server = None
    peer_id = None
    failures = []
    for candidate in candidates:
        try:
            server = vk_call("photos.getMessagesUploadServer", peer_id=candidate)
            peer_id = candidate
            break
        except RuntimeError as exc:
            failures.append(f"{candidate}:{exc}")
    if not server or not peer_id:
        print("PEER_FAILURES=" + " | ".join(failures[:5]))
        print("RESULT=NO_ALLOWED_MESSAGE_PEER")
        return 4

    print(f"PEER_OK={peer_id}")
    upload_url = str(server.get("upload_url", ""))
    if not upload_url:
        raise RuntimeError("No upload_url returned")
    path = urllib.parse.urlparse(upload_url).path
    field_name = "file1" if "bulk_upload" in path else "photo"
    print(f"UPLOAD_MODE={'bulk' if field_name == 'file1' else 'classic'}")

    filename, ctype, content = download_image(IMAGE_URL)
    uploaded = multipart_upload(upload_url, field_name, filename, ctype, content)
    print("UPLOAD_KEYS=" + ",".join(sorted(uploaded.keys())))
    print("HAS_PHOTO_PAYLOAD=" + str(bool(uploaded.get("photo"))).lower())
    if uploaded.get("files"):
        print("FILES_KEYS=" + ",".join(sorted(uploaded.get("files", {}).keys())))
    if not uploaded.get("photo"):
        print("RESULT=NO_CLASSIC_PHOTO_PAYLOAD")
        return 2

    saved = vk_call(
        "photos.saveMessagesPhoto",
        photo=uploaded.get("photo"),
        server=uploaded.get("server"),
        hash=uploaded.get("hash"),
    )
    if not isinstance(saved, list) or not saved:
        raise RuntimeError("saveMessagesPhoto returned no saved photo")
    photo = saved[0]
    attachment = f"photo{photo.get('owner_id')}_{photo.get('id')}"
    if photo.get("access_key"):
        attachment += f"_{photo.get('access_key')}"
    print("RESULT=SUCCESS")
    print("SAVED_ATTACHMENT=" + attachment)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print("DIAGNOSTIC_ERROR=" + str(e))
        raise
