import httpx
import logging
import os
from dotenv import load_dotenv
from core import models
from core.database import SessionLocal
from core.notifiers.formatters import md_to_discord

load_dotenv()
logger = logging.getLogger(__name__)

def _resolve_destination(recipient_id: str, session_id: str = None) -> str:
    """Resolve a recipient (hashed_id / session_id) to a Discord channel or
    user id via the IdentityMap. Prioritizes session_id for precise routing
    in multi-server environments."""
    final_destination = recipient_id  # Default fallback
    db = SessionLocal()
    try:
        # 1. Primary: Use Session ID to find the EXACT channel this reminder was created in
        if session_id:
            mapping = db.query(models.IdentityMap).filter(models.IdentityMap.session_id == session_id).first()
            if mapping:
                final_destination = mapping.real_id
        # 2. Fallback: Use hashed_id to find the last known channel for this user
        elif len(recipient_id) == 16 and not recipient_id.isdigit():
            mapping = db.query(models.IdentityMap).filter(models.IdentityMap.hashed_id == recipient_id).first()
            if mapping:
                final_destination = mapping.real_id
    finally:
        db.close()
    return final_destination


def _open_dm_channel(client: httpx.Client, headers: dict, user_id: str) -> str | None:
    """Open a DM with `user_id` and return its channel id, or None."""
    res = client.post(
        "https://discord.com/api/v10/users/@me/channels",
        headers=headers,
        json={"recipient_id": user_id},
    )
    if res.status_code == 200:
        return res.json().get("id")
    return None


def send_discord_notification(recipient_id: str, message: str, session_id: str = None):
    """
    Sends a notification to a Discord Channel or User.
    Prioritizes session_id for precise routing in multi-server environments.
    """
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        logger.error("DISCORD_BOT_TOKEN not found")
        return False

    final_destination = _resolve_destination(recipient_id, session_id)

    # Strip the [RESULT_START] / [RESULT_END] envelope; Discord renders
    # Markdown (headings, bold, code, lists) natively, so nothing else
    # needs converting. See core/notifiers/formatters.py for the rationale.
    message = md_to_discord(message)

    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json"
    }

    with httpx.Client(timeout=10.0) as client:
        try:
            # Try sending to the resolved channel/destination
            url_msg = f"https://discord.com/api/v10/channels/{final_destination}/messages"
            res_msg = client.post(url_msg, headers=headers, json={"content": message})

            if res_msg.status_code == 200:
                return True

            # DM Fallback if channel send fails
            dm_channel_id = _open_dm_channel(client, headers, final_destination)
            if dm_channel_id:
                url_msg_dm = f"https://discord.com/api/v10/channels/{dm_channel_id}/messages"
                res_final = client.post(url_msg_dm, headers=headers, json={"content": message})
                return res_final.status_code == 200

            return False
        except Exception:
            logger.exception("Discord notification failed")
            return False


def send_discord_file(recipient_id: str, file_path: str, session_id: str = None):
    """Attach the file at `file_path` to the resolved Discord destination.

    Mirrors send_discord_notification's routing (channel first, DM
    fallback) so async callbacks deliver the agent's actual outputs —
    not just the prose describing them."""
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token or not os.path.exists(file_path):
        return False

    final_destination = _resolve_destination(recipient_id, session_id)
    headers = {"Authorization": f"Bot {token}"}

    def _post_file(client: httpx.Client, channel_id: str) -> bool:
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        with open(file_path, "rb") as f:
            files = {"files[0]": (os.path.basename(file_path), f)}
            res = client.post(url, headers=headers, files=files)
        return res.status_code == 200

    with httpx.Client(timeout=30.0) as client:
        try:
            if _post_file(client, final_destination):
                return True
            dm_channel_id = _open_dm_channel(client, headers, final_destination)
            if dm_channel_id:
                return _post_file(client, dm_channel_id)
            return False
        except Exception:
            logger.exception("Discord file upload failed")
            return False
