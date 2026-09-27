#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

APP_ID = "54792536"
REDIRECT_URI = "https://zurabsamkharadze-png.github.io/vk-autopost/"
VK_API_VERSION = os.getenv("VK_API_VERSION", "5.199")
GROUP_ID = os.getenv("VK_GROUP_ID", "241800139")
OUT_FILE = Path(__file__).resolve().parent / "vk_user_token.enc"


def fail(message: str):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "geotrips-vk-auth/1.0"})
    with urllib.request.urlopen(req, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def vk_call(method: str, token: str, **params):
    payload = {**params, "access_token": token, "v": VK_API_VERSION}
    body = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.vk.com/method/{method}",
        data=body,
        headers={"User-Agent": "geotrips-vk-auth/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as response:
        data = json.loads(response.read().decode("utf-8"))
    if "error" in data:
        err = data["error"]
        fail(f"VK API {method} failed: {err.get('error_code')} {err.get('error_msg')}")
    return data.get("response")


def main():
    client_secret = os.getenv("VK_CLIENT_SECRET", "").strip()
    auth_code = os.getenv("VK_AUTH_CODE", "").strip()
    if not client_secret:
        fail("VK_CLIENT_SECRET is missing")
    if not auth_code:
        fail("VK_AUTH_CODE is missing")

    query = urllib.parse.urlencode({
        "client_id": APP_ID,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT_URI,
        "code": auth_code,
    })
    result = get_json("https://oauth.vk.com/access_token?" + query)
    if result.get("error"):
        fail(f"OAuth exchange failed: {result.get('error')} {result.get('error_description', '')}".strip())

    token = str(result.get("access_token", "")).strip()
    if not token:
        fail("OAuth exchange returned no access_token")

    vk_call("photos.getWallUploadServer", token, group_id=GROUP_ID)

    env = os.environ.copy()
    env["VK_TOKEN_PASSPHRASE"] = client_secret
    proc = subprocess.run(
        [
            "openssl", "enc", "-aes-256-cbc", "-salt", "-pbkdf2", "-iter", "200000",
            "-pass", "env:VK_TOKEN_PASSPHRASE", "-a", "-A"
        ],
        input=token.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        fail("Could not encrypt the VK user token")

    OUT_FILE.write_bytes(proc.stdout + b"\n")
    print("Server-side VK user token obtained, validated, and encrypted successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
