import os
import asyncio
import hashlib
import logging
import re
import threading
from fastapi import FastAPI, Request, HTTPException
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from dotenv import load_dotenv

from src.core import models
from src.core.database import SessionLocal
from src.core.adk_client import run_adk_prompt, delete_session

# --- Init ---
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
APP_NAME = os.getenv("ADK_APP_NAME", "costaff_agent")
SALT = os.getenv("ID_SALT", "default_secret_costaff_salt")

if not ACCESS_TOKEN or not CHANNEL_SECRET:
    logger.warning("LINE credentials missing. Line bot will not function correctly.")

line_bot_api = LineBotApi(ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)
app = FastAPI()

# --- Utilities ---

def get_user_id(line_id: str) -> str:
    """Generates a privacy-safe user ID."""
    return hashlib.sha256(f"{line_id}:{SALT}".encode()).hexdigest()[:16]

PENDING_MSG = "你好，我目前還無法跟你進行對話，因為必須經過管理員審核，我才能跟你進行後續對話，請等待管理員審核，並記得跟管理員要求開通權限。"

def _require_approval() -> bool:
    """
    Returns True only when an Enterprise license is present AND config enables approval.
    OSS installations (no license file) always return False — no approval gate.
    """
    import json as _json
    project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
    license_path = os.path.join(project_root, ".costaff", "costaff-license.yaml")
    if not os.path.exists(license_path):
        return False  # OSS: approval gate not available
    config_path = os.path.join(project_root, ".costaff", "config.json")
    try:
        with open(config_path, "r") as f:
            return _json.load(f).get("require_approval", True)
    except Exception:
        return True

def check_approved(session_id: str) -> bool:
    if not _require_approval():
        return True
    db = SessionLocal()
    try:
        mapping = db.query(models.IdentityMap).filter(models.IdentityMap.session_id == session_id).first()
        return bool(mapping and mapping.is_approved)
    finally:
        db.close()

def sync_identity(hashed_id: str, real_id: str, session_id: str):
    """Maps hashed ID to real Line ID."""
    db = SessionLocal()
    try:
        m = db.query(models.IdentityMap).filter(models.IdentityMap.session_id == session_id).first()
        if not m:
            db.add(models.IdentityMap(session_id=session_id, hashed_id=hashed_id, real_id=real_id))
        else:
            if m.real_id != real_id:
                m.real_id = real_id
            if m.hashed_id != hashed_id:
                m.hashed_id = hashed_id
        db.commit()
    finally:
        db.close()

def _run_async(coro):
    """
    Runs an async coroutine from a sync context (LINE webhook handler).
    Creates a new event loop in a dedicated thread to avoid conflicts with
    the FastAPI/uvicorn event loop running in the main thread.
    """
    result = None
    exception = None

    def target():
        nonlocal result, exception
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(coro)
        except Exception as e:
            exception = e
        finally:
            loop.close()

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()

    if exception:
        raise exception
    return result

# --- Endpoints ---

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature")
    body = await request.body()
    body_str = body.decode("utf-8")

    try:
        handler.handle(body_str, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    return "OK"

# --- Handlers ---

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    line_user_id = event.source.user_id
    uid = get_user_id(line_user_id)
    sid = f"line_{uid}"
    sync_identity(uid, line_user_id, sid)
    if not check_approved(sid):
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=PENDING_MSG))
        return

    text = event.message.text

    if text.startswith("/reset"):
        _run_async(handle_reset(event, uid, sid))
    else:
        _run_async(handle_adk_interaction(event, uid, sid, text))

async def handle_reset(event, uid, sid):
    if await delete_session(APP_NAME, uid, sid):
        preferred_lang = os.getenv("COSTAFF_PREFERRED_LANGUAGE", "Traditional Chinese (繁體中文)")
        res = await run_adk_prompt(APP_NAME, uid, sid,
                                   prompt=f"(Context ID: {uid}). Please check my identity and greet me in {preferred_lang}.")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🔄 對話已重設\n\n{res}"))
    else:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="Reset failed."))

async def handle_adk_interaction(event, uid, sid, text):
    prompt = f"(Context ID: {uid}) {text}"
    res = await run_adk_prompt(APP_NAME, uid, sid, prompt=prompt)

    # Detect file tags (match both /app/outputs and /app/output paths)
    file_paths = re.findall(r"[\[\(](?:FILE|檔案)[:：]\s*(/app/data/outputs/[\w.-]+)[\]\)]", res, re.IGNORECASE)
    clean_res = re.sub(r"[\[\(](?:FILE|檔案)[:：]\s*.*?[\]\)]", "", res, flags=re.IGNORECASE | re.DOTALL).strip()

    if clean_res:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=clean_res))

    # Note: LINE does not support sending local files directly (requires public URL).
    # Notify the user that a file was generated and is available via other channels.
    for path in file_paths:
        filename = os.path.basename(path)
        line_bot_api.push_message(event.source.user_id,
                                  TextSendMessage(text=f"✅ 檔案已生成: {filename}\n(請由其他管道或系統下載)"))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
