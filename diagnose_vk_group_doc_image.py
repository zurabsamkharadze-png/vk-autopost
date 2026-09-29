#!/usr/bin/env python3
import json
import mimetypes
import os
import urllib.parse
import urllib.request
import uuid

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
        headers={"User-Agent": "geotrips-vk-doc-diagnostic/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as r:
        data = json.loads(r.read().decode("utf-8"))
    if "error" in data:
        e = data["error"]
        raise RuntimeError(f"{method}: {e.get('error_code')} {e.get('error_msg')}")
    return data.get("response")


def download_image(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://experience.tripster.ru/"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
        ctype = r.headers.get_content_type() or "image/jpeg"
    if not body:
        raise RuntimeError("Downloaded image is empty")
    ext = mimetypes.guess_extension(ctype) or ".jpg"
    return f"tripster{ext}", ctype, body


def multipart_upload(url, filename, content_type, content):
    boundary = f"----GeoTripsVK{uuid.uuid4().hex}"
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
        f"Content-Type: {content_type}\r\n\r\n".encode(),
        content,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
            "User-Agent": "geotrips-vk-doc-diagnostic/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    if not TOKEN:
        raise RuntimeError("VK_ACCESS_TOKEN missing")
    server = vk_call("docs.getWallUploadServer", group_id=GROUP_ID)
    print("GET_WALL_UPLOAD_SERVER=SUCCESS")
    filename, ctype, content = download_image(IMAGE_URL)
    uploaded = multipart_upload(server["upload_url"], filename, ctype, content)
    print("UPLOAD_KEYS=" + ",".join(sorted(uploaded.keys())))
    file_token = uploaded.get("file")
    if not file_token:
        raise RuntimeError("VK document upload returned no file token")
    saved = vk_call("docs.save", file=file_token, title="GeoTrips test image")
    print("DOCS_SAVE_TYPE=" + str((saved or {}).get("type")))
    doc = (saved or {}).get("doc") if isinstance(saved, dict) else None
    if not isinstance(doc, dict):
        raise RuntimeError("docs.save returned no doc object")
    attachment = f"doc{doc.get('owner_id')}_{doc.get('id')}"
    if doc.get("access_key"):
        attachment += f"_{doc.get('access_key')}"
    print("RESULT=SUCCESS")
    print("SAVED_ATTACHMENT=" + attachment)
    print("DOC_EXT=" + str(doc.get("ext")))
    print("DOC_TYPE=" + str(doc.get("type")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
