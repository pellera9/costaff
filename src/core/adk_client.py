import asyncio
import httpx
import os
import logging
from typing import Optional, List
from dotenv import load_dotenv

# Initialize logging and load environment variables
load_dotenv()
logger = logging.getLogger(__name__)

# ADK API Configuration
ADK_URL = os.getenv("ADK_API_BASE_URL", "http://localhost:8000")
TIMEOUT = 1800.0  # 30 minutes — long-running agent tasks (code execution, multi-agent chains)

async def ensure_session(app: str, uid: str, sid: str) -> bool:
    """
    Guarantees a session exists for the user in ADK.
    Succeeds if session is created (201) or already exists (409).

    Args:
        app (str): The application name.
        uid (str): Hashed user ID.
        sid (str): Session identifier.

    Returns:
        bool: True if session is ready for use.
    """
    url = f"{ADK_URL}/apps/{app}/users/{uid}/sessions"
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            res = await client.post(url, json={"sessionId": sid, "state": {}})
            return res.status_code in [200, 201, 409]
        except httpx.RequestError as e:
            logger.error(f"Network error in ensure_session for session {sid}: {e}")
            return False

async def delete_session(app: str, uid: str, sid: str) -> bool:
    """
    Explicitly terminates an active ADK session.

    Args:
        app (str): The application name.
        uid (str): Hashed user ID.
        sid (str): Session identifier.

    Returns:
        bool: True if the session was successfully deleted.
    """
    url = f"{ADK_URL}/apps/{app}/users/{uid}/sessions/{sid}"
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            res = await client.delete(url)
            return res.status_code == 200
        except httpx.RequestError as e:
            logger.error(f"Network error in delete_session for session {sid}: {e}")
            return False

async def run_adk_prompt(
    app: str, 
    uid: str, 
    sid: str, 
    prompt: Optional[str] = None, 
    parts: Optional[List[dict]] = None
) -> str:
    """
    Submits a message to the ADK Agent and parses the stream of events for text output.
    Implements a 'nudge' mechanism: if the Agent does not provide a verbal response after
    processing tool calls, it automatically sends a follow-up prompt to complete the turn.

    Args:
        app (str): Target application.
        uid (str): Hashed user ID.
        sid (str): Session ID.
        prompt (str, optional): User's text input.
        parts (list, optional): Multi-modal message parts.

    Returns:
        str: The final text response from the Agent.
    """
    await ensure_session(app, uid, sid)

    msg_parts = parts if parts else ([{"text": prompt}] if prompt else [])
    payload = {
        "appName": app,
        "userId": uid,
        "sessionId": sid,
        "newMessage": {"role": "user", "parts": msg_parts}
    }

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        # Maximum 3 attempts to get a verbal response
        last_error: Optional[str] = None
        for attempt in range(3):
            try:
                res = await client.post(f"{ADK_URL}/run", json=payload)

                if res.status_code == 200:
                    events = res.json()
                    # Iterate backwards to find the definitive final response
                    for event in reversed(events):
                        if event.get("author") != "user" and "content" in event:
                            content_parts = event["content"].get("parts", [])
                            txts = [p.get("text", "") for p in content_parts if "text" in p]
                            if txts:
                                return "".join(txts).strip()

                    # Nudge: The agent used tools but stayed silent
                    logger.warning(f"Attempt {attempt + 1}: No text found for session {sid}. Nudging...")
                    payload["newMessage"] = {"role": "user", "parts": [{"text": "Please continue and provide a verbal response."}]}
                    continue

                logger.error(f"ADK Run Error ({res.status_code}): {res.text}")
                return f"⚠️ 發生錯誤（{res.status_code}）：{res.text[:200]}"

            except httpx.RequestError as e:
                error_detail = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                logger.warning(f"Attempt {attempt + 1}: ADK connection error for session {sid}: {error_detail}")
                last_error = error_detail
                if attempt < 2:
                    await asyncio.sleep(2)
                continue

        logger.error(f"ADK Connection Failed for session {sid} after 3 attempts: {last_error}")
        return f"⚠️ 無法連線至 Agent：{last_error}"
