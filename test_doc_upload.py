#!/usr/bin/env python3
import json
import os
from pathlib import Path

from run_publisher import normalized_group_id, upload_wall_image_document

posts = json.loads(Path("posts.json").read_text(encoding="utf-8"))
post = next((p for p in reversed(posts) if p.get("image_url")), None)
if not post:
    raise SystemExit("No post with image_url found")

token = os.environ["VK_ACCESS_TOKEN"].strip()
group_id = normalized_group_id(os.environ["VK_GROUP_ID"])
attachment = upload_wall_image_document(
    token, group_id, post["image_url"], title="GeoTrips upload test"
)
print("DOC_UPLOAD_OK", attachment)
