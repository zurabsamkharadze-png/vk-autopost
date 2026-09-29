#!/usr/bin/env python3
import os
import urllib.parse
from run_publisher import vk_call, download_image, multipart_upload

IMAGE_URL = "https://resize.tripster.ru/5-Zb81g6rfGSGHysszr6G_pG-uI%3D/fit-in/800x600/filters%3Ano_upscale%28%29/https%3A//cdn.tripster.ru/photos/353a1b90-06fb-406c-81a5-e4a627761e0d.jpg"

def main():
    token=os.environ.get("VK_ACCESS_TOKEN","").strip()
    if not token:
        raise SystemExit("VK_ACCESS_TOKEN missing")
    server=vk_call("photos.getMessagesUploadServer", token)
    url=server.get("upload_url","")
    print("upload_path=", urllib.parse.urlparse(url).path)
    filename, ctype, content=download_image(IMAGE_URL)
    field="file1" if "bulk_upload" in urllib.parse.urlparse(url).path else "photo"
    print("field=",field)
    uploaded=multipart_upload(url, field, filename, ctype, content)
    print("uploaded_keys=",sorted(uploaded.keys()))
    if isinstance(uploaded.get("files"),dict):
        print("files_keys=",sorted(uploaded["files"].keys()))
    photo=uploaded.get("photo")
    if not photo:
        raise SystemExit("NO_SAVABLE_PHOTO_PAYLOAD")
    saved=vk_call("photos.saveMessagesPhoto", token, photo=photo, server=uploaded.get("server"), hash=uploaded.get("hash"))
    if not saved:
        raise SystemExit("SAVE_RETURNED_EMPTY")
    item=saved[0]
    print("SAVED_OK owner_id=",item.get("owner_id"),"id=",item.get("id"),"has_access_key=",bool(item.get("access_key")))

if __name__=="__main__": main()
