"""Slack push notifications for system-generated messages.

Mirrors the Telegram notifier's role: the MCP scheduler executors and
`dispatch_notification` call this to reach a Slack user without going
through the agent loop. The recipient is a Slack user id (resolved from
the IdentityMap by the caller); messages are delivered to the user's DM
channel via `conversations.open` + `chat.postMessage`.

File delivery uses Slack's external-upload flow (the only supported one
since files.upload was retired in 2025):
  1. files.getUploadURLExternal → upload_url + file_id
  2. POST the raw bytes to upload_url
  3. files.completeUploadExternal with the DM channel id
"""
import logging
import os

import httpx
from dotenv import load_dotenv

from core.notifiers.formatters import md_to_slack

load_dotenv()
logger = logging.getLogger(__name__)

_API = "https://slack.com/api"


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _open_dm(client: httpx.Client, token: str, user_id: str) -> str | None:
    """Open (or fetch) the DM channel for `user_id`. Returns channel id.

    If `user_id` already looks like a channel id (C…/D…/G…), use it as-is —
    callers may store a channel rather than a user in IdentityMap.real_id.
    """
    if user_id and user_id[0] in ("C", "D", "G"):
        return user_id
    res = client.post(
        f"{_API}/conversations.open",
        headers=_auth_headers(token),
        json={"users": user_id},
    )
    data = res.json()
    if not data.get("ok"):
        logger.error(f"conversations.open failed for {user_id}: {data.get('error')}")
        return None
    return data["channel"]["id"]


def send_slack_notification(user_id: str, message: str) -> bool:
    """Send `message` to the Slack user's DM channel. Returns success."""
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        logger.error("SLACK_BOT_TOKEN not found")
        return False

    message = md_to_slack(message)

    with httpx.Client(timeout=10.0) as client:
        try:
            channel = _open_dm(client, token, user_id)
            if not channel:
                return False
            res = client.post(
                f"{_API}/chat.postMessage",
                headers=_auth_headers(token),
                json={"channel": channel, "text": message},
            )
            data = res.json()
            if not data.get("ok"):
                logger.error(f"chat.postMessage failed: {data.get('error')}")
                return False
            return True
        except Exception:
            logger.exception("Slack notification failed")
            return False


def send_slack_file(user_id: str, file_path: str, title: str = None) -> bool:
    """Upload the file at `file_path` to the user's DM channel."""
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token or not os.path.exists(file_path):
        return False

    filename = os.path.basename(file_path)
    with httpx.Client(timeout=60.0) as client:
        try:
            channel = _open_dm(client, token, user_id)
            if not channel:
                return False

            size = os.path.getsize(file_path)
            res = client.post(
                f"{_API}/files.getUploadURLExternal",
                headers=_auth_headers(token),
                data={"filename": filename, "length": str(size)},
            )
            data = res.json()
            if not data.get("ok"):
                logger.error(f"getUploadURLExternal failed: {data.get('error')}")
                return False
            upload_url, file_id = data["upload_url"], data["file_id"]

            with open(file_path, "rb") as f:
                up = client.post(upload_url, content=f.read())
            if up.status_code != 200:
                logger.error(f"upload POST failed ({up.status_code}) for {filename}")
                return False

            res = client.post(
                f"{_API}/files.completeUploadExternal",
                headers={**_auth_headers(token), "Content-Type": "application/json"},
                json={
                    "files": [{"id": file_id, "title": title or filename}],
                    "channel_id": channel,
                },
            )
            data = res.json()
            if not data.get("ok"):
                logger.error(f"completeUploadExternal failed: {data.get('error')}")
                return False
            return True
        except Exception:
            logger.exception("Slack file upload failed")
            return False
