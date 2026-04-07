import os
import asyncio
import logging
import base64
import io
import hashlib
import httpx
import json
import re
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message, BotCommand, FSInputFile
from dotenv import load_dotenv

from src.core import models
from src.core.database import SessionLocal
from src.core.adk_client import run_adk_prompt, delete_session
from src.core.notifiers.telegram import send_telegram_document

# --- Init ---
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Basic Bot Configuration
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
APP_NAME = os.getenv("ADK_APP_NAME", "mate_agent")
SALT = os.getenv("ID_SALT", "default_secret_mate_salt")

# PrivAI Configuration for handling direct file/photo uploads from Telegram
PRIVAI_URL = os.getenv("PRIVAI_API_BASE_URL", "https://api.bcm.apmic.ai").rstrip("/")
PRIVAI_KEY = os.getenv("PRIVAI_API_KEY")

if not TOKEN: raise ValueError("TELEGRAM_BOT_TOKEN missing from environment.")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Deduplication: track recently processed message IDs to prevent Telegram webhook retries
_processed_message_ids: set[int] = set()

# --- Utilities ---

def get_user_id(tg_id: int) -> str:
    """
    Generates a privacy-safe user ID by hashing the Telegram Chat ID with a salt.
    This ensures we don't leak real Telegram IDs to the AI model or database.
    """
    return hashlib.sha256(f"{tg_id}:{SALT}".encode()).hexdigest()[:16]

def _require_approval() -> bool:
    """
    Returns True only when an Enterprise license is present AND config enables approval.
    OSS installations (no license file) always return False — no approval gate.
    """
    project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
    license_path = os.path.join(project_root, ".mateclaw", "mateclaw-license.yaml")
    if not os.path.exists(license_path):
        return False  # OSS: approval gate not available
    config_path = os.path.join(project_root, ".mateclaw", "config.json")
    try:
        with open(config_path, "r") as f:
            return json.load(f).get("require_approval", True)
    except Exception:
        return True

def check_approved(session_id: str) -> bool:
    """Returns True if the identity (session) has been approved by admin, or if approval is disabled."""
    if not _require_approval():
        return True
    db = SessionLocal()
    try:
        mapping = db.query(models.IdentityMap).filter(models.IdentityMap.session_id == session_id).first()
        return bool(mapping and mapping.is_approved)
    finally:
        db.close()

def sync_identity(hashed_id: str, real_id: str, session_id: str):
    """
    Maintains a mapping between the secure hashed ID and the real Telegram Chat ID.
    Used for sending proactive notifications back to the correct user.
    """
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

async def safe_reply(msg: Message, text: str):
    """Replies to a message with HTML formatting, falling back to plain text on error."""
    try:
        await msg.answer(text, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"HTML reply failed, sending plain text: {e}")
        await msg.answer(text)

async def upload_to_mate(file_content: io.BytesIO, filename: str, user_id: str, sid: str = None, app_name: str = "mate_agent") -> str:
    """
    Directly uploads a file received from Telegram to the PrivAI cloud server.
    """
    if not PRIVAI_KEY:
        logger.error("PRIVAI_API_KEY not configured. Skipping upload.")
        return None
    
    metadata = json.dumps({"owner_id": user_id, "source": "telegram_upload"})
    headers = {"Authorization": f"Bearer {PRIVAI_KEY}"}
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            file_content.seek(0)
            files = {
                "file": (filename, file_content),
                "metadata": (None, metadata),
                "instant_parse": (None, json.dumps({"parsing_mode": "HQ"}))
            }
            res = await client.post(f"{PRIVAI_URL}/v1/files", 
                                   headers=headers, 
                                   files=files, 
                                   params={"purpose": "user_data"})
            if res.status_code == 200:
                file_id = res.json().get("id")
                if file_id and sid:
                    db = SessionLocal()
                    try:
                        db.add(models.FileTask(
                            file_id=file_id,
                            user_id=user_id,
                            session_id=sid,
                            app_name=app_name,
                            filename=filename
                        ))
                        db.commit()
                    except Exception as db_err:
                        logger.error(f"Failed to save FileTask: {db_err}")
                        db.rollback()
                    finally:
                        db.close()
                return file_id
            return None
        except Exception as e:
            logger.error(f"Error uploading to PrivAI: {e}")
            return None

# --- Handlers ---

PENDING_MSG = "你好，我目前還無法跟你進行對話，因為必須經過管理員審核，我才能跟你進行後續對話，請等待管理員審核，並記得跟管理員要求開通權限。"

@dp.message(Command("start"))
async def cmd_start(msg: Message):
    """Initializes the conversation."""
    uid = get_user_id(msg.chat.id)
    sid = f"tg_{uid}"
    sync_identity(uid, str(msg.chat.id), sid)
    if not check_approved(sid):
        await msg.answer(PENDING_MSG)
        return
    await bot.send_chat_action(msg.chat.id, "typing")
    res = await run_adk_prompt(APP_NAME, uid, sid,
                               prompt=f"(Context ID: {uid}). Please check my identity and greet me.")
    await safe_reply(msg, res)

@dp.message(Command("reset"))
async def cmd_reset(msg: Message):
    """Clears the current conversation session."""
    uid = get_user_id(msg.chat.id)
    sid = f"tg_{uid}"
    if not check_approved(sid):
        await msg.answer(PENDING_MSG)
        return
    await bot.send_chat_action(msg.chat.id, "typing")
    if await delete_session(APP_NAME, uid, sid):
        res = await run_adk_prompt(APP_NAME, uid, sid,
                                   prompt=f"(Context ID: {uid}). Please check my identity and greet me in Traditional Chinese.")
        await safe_reply(msg, f"🔄 <b>對話已重設</b>\n\n{res}")
    else:
        await msg.answer("Reset failed.")

@dp.message(Command("help"))
async def cmd_help(msg: Message):
    """Displays a list of available slash commands."""
    txt = ("<b>Mate Agent 指令：</b>\n"
           "/start - 開始/身份檢查\n"
           "/reset - 重設對話\n"
           "/profile - 查看個人資料\n"
           "/list - 查看提醒任務")
    await safe_reply(msg, txt)

@dp.message(Command("profile"))
async def cmd_profile(msg: Message):
    uid = get_user_id(msg.chat.id)
    sid = f"tg_{uid}"
    if not check_approved(sid):
        await msg.answer(PENDING_MSG)
        return
    await bot.send_chat_action(msg.chat.id, "typing")
    res = await run_adk_prompt(APP_NAME, uid, sid, prompt="Show my profile.")
    await safe_reply(msg, res)

@dp.message(Command("list"))
async def cmd_list(msg: Message):
    uid = get_user_id(msg.chat.id)
    sid = f"tg_{uid}"
    if not check_approved(sid):
        await msg.answer(PENDING_MSG)
        return
    await bot.send_chat_action(msg.chat.id, "typing")
    res = await run_adk_prompt(APP_NAME, uid, sid, prompt="List my reminders.")
    await safe_reply(msg, res)

async def _send_file(chat_id: str, path: str):
    """Send a file to Telegram — photos as image, everything else as document."""
    if not os.path.exists(path):
        logger.warning(f"File not found for delivery: {path}")
        return
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    try:
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
            await bot.send_photo(chat_id, FSInputFile(path), caption=name)
        else:
            await bot.send_document(chat_id, FSInputFile(path), caption=name)
        logger.info(f"Delivered file: {path}")
    except Exception as e:
        logger.error(f"Failed to deliver {path}: {e}")


WORKSPACE_DIR = "/app/data/coding_workspace"
REPORTS_DIR = "/app/data/reports"
FILE_SEARCH_DIRS = [WORKSPACE_DIR, REPORTS_DIR, "/app/data/outputs"]

def _resolve_path(raw: str) -> str | None:
    """Resolve a file path (absolute or relative) to an existing absolute path."""
    raw = raw.strip().strip("`")
    if os.path.isabs(raw):
        return raw if os.path.exists(raw) else None
    # Relative path: search common base directories
    for base in FILE_SEARCH_DIRS:
        candidate = os.path.join(base, raw)
        if os.path.exists(candidate):
            return candidate
    return None


async def _deliver_response(msg: Message, final_res: str):
    """Parse agent response, send text reply, and deliver any file attachments."""
    logger.debug(f"Agent response: {final_res[:100]}...")

    FILE_EXTS = r"pdf|docx|md|txt|html|htm|png|jpg|jpeg|gif|csv|json|xlsx|xls|zip"

    # 1a. [FILE: path] or (FILE: path) tags — absolute or relative
    tag_paths = re.findall(
        rf"[\[\(](?:FILE|檔案)[:：]\s*([^\]\)\s]+\.(?:{FILE_EXTS}))[\]\)]",
        final_res, re.IGNORECASE
    )

    # 1b. Absolute /app/data/... paths
    abs_paths = re.findall(
        rf"`?(/app/data/[\w./-]+\.(?:{FILE_EXTS}))`?",
        final_res, re.IGNORECASE
    )

    # 1c. Relative paths in backticks e.g. `79814feed6d42f30/svm_report.html`
    rel_paths = re.findall(
        rf"`([\w/-]+\.(?:{FILE_EXTS}))`",
        final_res, re.IGNORECASE
    )

    raw_paths = list(dict.fromkeys(tag_paths + abs_paths + rel_paths))
    all_paths = [r for p in raw_paths if (r := _resolve_path(p))]

    # 2. Clean response text
    clean_res = re.sub(
        rf"[\[\(](?:FILE|檔案)[:：]\s*[^\]\)\s]+\.(?:{FILE_EXTS})[\]\)]",
        "", final_res, flags=re.IGNORECASE
    )
    clean_res = re.sub(rf"`?/app/data/[\w./-]+\.(?:{FILE_EXTS})`?", "", clean_res, flags=re.IGNORECASE)
    clean_res = re.sub(rf"`[\w/-]+\.(?:{FILE_EXTS})`", "", clean_res, flags=re.IGNORECASE)
    clean_res = clean_res.strip()

    if clean_res:
        await safe_reply(msg, clean_res)
    elif not all_paths:
        await safe_reply(msg, final_res)

    # 3. Deliver files
    for path in all_paths:
        await _send_file(str(msg.chat.id), path)


async def _run_agent_task(msg: Message, uid: str, sid: str, parts: list):
    """Background task: runs ADK agent and delivers response. Errors are caught and reported to user."""
    try:
        final_res = await run_adk_prompt(APP_NAME, uid, sid, parts=parts)
        await _deliver_response(msg, final_res)
    except Exception as e:
        logger.error(f"Agent task failed for session {sid}: {e}")
        await safe_reply(msg, "很抱歉，處理您的請求時發生錯誤，請稍後再試。")


@dp.message()
async def handle_msg(msg: Message):
    """
    The main message handler for text, photos, and documents.
    Deduplicates webhook retries and runs the agent as a background task
    so Telegram does not time out on long-running requests.
    """
    # --- A: Deduplication — drop Telegram webhook retries for the same message ---
    if msg.message_id in _processed_message_ids:
        logger.info(f"Duplicate message {msg.message_id} dropped.")
        return
    _processed_message_ids.add(msg.message_id)
    if len(_processed_message_ids) > 2000:
        _processed_message_ids.clear()

    text = msg.text or msg.caption or ""
    parts = [{"text": text}] if text else []
    uid = get_user_id(msg.chat.id)
    sid = f"tg_{uid}"
    sync_identity(uid, str(msg.chat.id), sid)
    if not check_approved(sid):
        await msg.answer(PENDING_MSG)
        return

    UPLOADS_DIR = "/app/data/coding_workspace/shared/uploads"
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    uploaded_file_paths: list[str] = []

    if msg.photo:
        photo = msg.photo[-1]
        info = await bot.get_file(photo.file_id)
        buf = io.BytesIO()
        await bot.download_file(info.file_path, buf)
        data = base64.b64encode(buf.getvalue()).decode()
        parts.append({"inlineData": {"mimeType": "image/jpeg", "data": data}})
        # Also save to shared workspace so coding-agent can access it
        fname = f"photo_{photo.file_id}.jpg"
        fpath = os.path.join(UPLOADS_DIR, fname)
        buf.seek(0)
        with open(fpath, "wb") as f:
            f.write(buf.read())
        uploaded_file_paths.append(fpath)
        await upload_to_mate(buf, fname, uid, sid=sid, app_name=APP_NAME)

    if msg.document:
        doc = msg.document
        info = await bot.get_file(doc.file_id)
        buf = io.BytesIO()
        await bot.download_file(info.file_path, buf)
        fname = doc.file_name or f"document_{doc.file_id}"
        fpath = os.path.join(UPLOADS_DIR, fname)
        buf.seek(0)
        with open(fpath, "wb") as f:
            f.write(buf.read())
        uploaded_file_paths.append(fpath)
        await upload_to_mate(buf, fname, uid, sid=sid, app_name=APP_NAME)

    # Inject uploaded file paths into the agent message
    if uploaded_file_paths:
        rel_paths = [os.path.relpath(p, "/app/data/coding_workspace") for p in uploaded_file_paths]
        paths_note = (
            "（使用者上傳了以下檔案：" + ", ".join(uploaded_file_paths) + "。"
            "coding_agent 可用 read_file 工具以相對路徑存取：" + ", ".join(rel_paths) + "）"
        )
        if parts and "text" in parts[0]:
            parts[0]["text"] = parts[0]["text"] + " " + paths_note
        else:
            parts.append({"text": paths_note})

    if not parts:
        return

    await bot.send_chat_action(msg.chat.id, "typing")

    # Inject Context ID so the Agent always knows the user_id for tools
    context_text = f"(Context ID: {uid})"
    if "text" in parts[0]:
        parts[0]["text"] = f"{context_text} {parts[0]['text']}"
    else:
        parts.insert(0, {"text": context_text})

    # --- B: Fire-and-forget — return immediately, deliver result when done ---
    asyncio.create_task(_run_agent_task(msg, uid, sid, parts))

async def reset_all_sessions():
    """Proactively clears sessions and greets all known users."""
    db = SessionLocal()
    try:
        users = db.query(models.IdentityMap).all()
        # Deduplicate by real_id: keep only the most recently created entry per user
        # to avoid sending multiple greetings if a user has stale duplicate records
        latest_by_real_id: dict = {}
        for user in users:
            existing = latest_by_real_id.get(user.real_id)
            if not existing or (user.created_at and (not existing.created_at or user.created_at > existing.created_at)):
                latest_by_real_id[user.real_id] = user

        for user in latest_by_real_id.values():
            # Recompute uid from real_id using current salt, same as /start handler
            # Avoids stale hashed_id in identity_maps causing profile lookup misses
            uid = get_user_id(int(user.real_id))
            sid = f"tg_{uid}"
            sync_identity(uid, user.real_id, sid)
            await delete_session(APP_NAME, uid, sid)
            res = await run_adk_prompt(APP_NAME, uid, sid, prompt=f"(Context ID: {uid}). Please check my identity and greet me in Traditional Chinese.")
            try:
                await bot.send_message(chat_id=user.real_id, text=f"🔄 <b>系統已重啟並自動重置</b>\n\n{res}", parse_mode="HTML")
            except Exception as e: logger.error(f"Greeting error: {e}")
    finally:
        db.close()

async def main():
    cmds = [BotCommand(command=c[0], description=c[1]) for c in [
        ("start", "開始"), ("reset", "重設"), ("help", "幫助"), ("profile", "資料"), ("list", "排程")
    ]]
    await bot.set_my_commands(cmds)
    await reset_all_sessions()
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        logger.info("Telegram bot shut down gracefully.")

if __name__ == "__main__":
    import signal

    def _handle_signal(sig, frame):
        logger.info(f"Received signal {sig}, shutting down...")
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    asyncio.run(main())
