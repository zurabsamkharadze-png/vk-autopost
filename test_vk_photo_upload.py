#!/usr/bin/env python3
import os
from run_publisher import upload_message_photo

IMAGE_URL = "https://resize.tripster.ru/5-Zb81g6rfGSGHysszr6G_pG-uI%3D/fit-in/800x600/filters%3Ano_upscale%28%29/https%3A//cdn.tripster.ru/photos/353a1b90-06fb-406c-81a5-e4a627761e0d.jpg"

def main():
    token = os.getenv("VK_ACCESS_TOKEN", "").strip()
    if not token:
        raise SystemExit("VK_ACCESS_TOKEN is missing")
    attachment = upload_message_photo(token, IMAGE_URL)
    if not attachment.startswith("photo"):
        raise RuntimeError(f"Unexpected VK attachment: {attachment}")
    print(f"PHOTO_UPLOAD_OK {attachment}")

if __name__ == "__main__":
    main()
