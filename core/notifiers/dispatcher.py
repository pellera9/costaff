"""Cross-channel notification dispatcher.

Resolves an opaque recipient (which may be a hashed_id or a session_id)
through the IdentityMap, picks the right notifier (telegram / discord /
line) based on the requested channel, and forwards the message.

Used by the MCP scheduler executors and any other place that needs to
push a system-generated message to a user without going through the
agent loop.
"""
from core import models
from core.database import SessionLocal
from core.notifiers.discord import send_discord_notification
from core.notifiers.line_notifier import send_line_notification
from core.notifiers.telegram import (
    extract_file_paths,
    send_telegram_document,
    send_telegram_notification,
)


async def dispatch_notification(
    channel: str,
    recipient: str,
    message: str,
    session_id: str = None,
) -> None:
    """Resolve identity mapping and dispatch notification to the correct channel.

    - channel is a free-form string; routing matches case-insensitive substrings:
      tg/telegram → Telegram, dc/discord → Discord, line → LINE.
    - recipient may be a hashed_id, a session_id, or already a real platform
      id; the IdentityMap is consulted to translate the first two.
    """
    db = SessionLocal()
    try:
        target_id = recipient
        mapping = db.query(models.IdentityMap).filter(
            (models.IdentityMap.hashed_id == target_id)
            | (models.IdentityMap.session_id == target_id)
        ).first()
        if mapping:
            target_id = mapping.real_id

        chan = (channel or "").lower()
        if "tg" in chan or "telegram" in chan:
            send_telegram_notification(target_id, message)
            # Attach any /app/data/... files referenced in the message.
            # `send_message_now` already does this; without mirroring it here
            # the async callback path (BA / coding completion via project_task
            # executor) delivers prose only and the user never receives the
            # PDF / CSV the agent produced.
            for fp in extract_file_paths(message):
                send_telegram_document(target_id, fp)
        elif "dc" in chan or "discord" in chan:
            send_discord_notification(target_id, message, session_id=session_id)
        elif "line" in chan:
            await send_line_notification(target_id, message)
    finally:
        db.close()
