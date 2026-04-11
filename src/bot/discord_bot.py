import os
import asyncio
import logging
import base64
import io
import hashlib
import httpx
import json
import re
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

from src.core import models
from src.core.database import SessionLocal
from src.core.adk_client import run_adk_prompt, delete_session

# --- Init ---
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Basic Bot Configuration
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
APP_NAME = os.getenv("ADK_APP_NAME", "costaff_agent")
SALT = os.getenv("ID_SALT", "default_secret_costaff_salt")

# PrivAI Configuration
PRIVAI_URL = os.getenv("PRIVAI_API_BASE_URL", "https://api.bcm.apmic.ai").rstrip("/")
PRIVAI_KEY = os.getenv("PRIVAI_API_KEY")

if not TOKEN:
    logger.warning("DISCORD_BOT_TOKEN missing. Discord bot will not start.")

class CoStaffBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def setup_hook(self):
        await self.tree.sync()
        logger.info("Synced Slash Commands tree.")

bot = CoStaffBot()

# --- Utilities ---

def get_user_id(discord_id: int) -> str:
    """Generates a privacy-safe user ID."""
    return hashlib.sha256(f"{discord_id}:{SALT}".encode()).hexdigest()[:16]

PENDING_MSG = "你好，我目前還無法跟你進行對話，因為必須經過管理員審核，我才能跟你進行後續對話，請等待管理員審核，並記得跟管理員要求開通權限。"

def _require_approval() -> bool:
    """
    Returns True only when an Enterprise license is present AND config enables approval.
    OSS installations (no license file) always return False — no approval gate.
    """
    project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
    license_path = os.path.join(project_root, ".costaff", "costaff-license.yaml")
    if not os.path.exists(license_path):
        return False  # OSS: approval gate not available
    config_path = os.path.join(project_root, ".costaff", "config.json")
    try:
        with open(config_path, "r") as f:
            return json.load(f).get("require_approval", True)
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
    """
    Maintains a robust mapping between Session IDs and real platform IDs.
    This ensures proactive notifications return to the exact same channel.
    """
    db = SessionLocal()
    try:
        m = db.query(models.IdentityMap).filter(models.IdentityMap.session_id == session_id).first()
        if not m:
            db.add(models.IdentityMap(session_id=session_id, hashed_id=hashed_id, real_id=real_id))
        elif m.real_id != real_id or m.hashed_id != hashed_id:
            m.real_id = real_id
            m.hashed_id = hashed_id
        db.commit()
    finally:
        db.close()

async def upload_to_costaff(file_content: io.BytesIO, filename: str, user_id: str, sid: str = None, app_name: str = "costaff_agent") -> str:
    """Synchronizes attachments to PrivAI cloud."""
    if not PRIVAI_KEY: return None
    metadata = json.dumps({"owner_id": user_id, "source": "discord_upload"})
    headers = {"Authorization": f"Bearer {PRIVAI_KEY}"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            file_content.seek(0)
            files = {"file": (filename, file_content), "metadata": (None, metadata), 
                     "instant_parse": (None, json.dumps({"parsing_mode": "HQ"}))}
            res = await client.post(f"{PRIVAI_URL}/v1/files", headers=headers, files=files, params={"purpose": "user_data"})
            if res.status_code == 200:
                file_id = res.json().get("id")
                if file_id and sid:
                    db = SessionLocal()
                    try:
                        db.add(models.FileTask(file_id=file_id, user_id=user_id, session_id=sid, app_name=app_name, filename=filename))
                        db.commit()
                    except Exception as db_err:
                        logger.error(f"Failed to save FileTask: {db_err}")
                        db.rollback()
                    finally:
                        db.close()
                return file_id
            return None
        except Exception as e:
            logger.error(f"Upload error: {e}")
            return None

async def process_and_deliver_files(destination, raw_response: str):
    """
    Common logic to detect [FILE: ...] tags, SSR URLs, and deliver actual files to Discord.
    """
    # 1. Detect Files (Robust against translation and AI newlines)
    raw_doc_tags = re.findall(r"[\[\(](?:FILE|檔案)[:：]\s*(.*?\.pdf|.*?\.docx|.*?\.md|.*?\.txt)[\]\)]", raw_response, re.IGNORECASE | re.DOTALL)
    
    doc_filenames = []
    for tag in raw_doc_tags:
        clean_tag = re.sub(r"\s+", "", tag) # Remove AI-inserted whitespace/newlines
        filename = os.path.basename(clean_tag)
        if filename: doc_filenames.append(filename)

    # 2. Clean response text thoroughly
    clean_res = raw_response
    clean_res = re.sub(r"[\[\(](?:FILE|檔案)[:：]\s*.*?[\]\)]", "", clean_res, flags=re.IGNORECASE | re.DOTALL)
    clean_res = re.sub(r"(?:/app)?/outputs?/([\w.-]+\.(?:pdf|docx|md|txt))", "", clean_res)
    clean_res = clean_res.strip()

    # 3. Send text part
    if isinstance(destination, discord.Interaction):
        if clean_res:
            await destination.followup.send(clean_res)
        elif doc_filenames:
            await destination.followup.send("檔案已產生，請查收：")
        else:
            await destination.followup.send(raw_response)

        target_channel = destination.channel
    else:
        if clean_res:
            await destination.reply(clean_res)
        elif doc_filenames:
            await destination.reply("檔案已產生，請查收：")
        else:
            await destination.reply(raw_response)

        target_channel = destination.channel

    # 4. Deliver Documents
    for filename in list(set(doc_filenames)):
        actual_path = os.path.join("/app/data/outputs", filename)
        if os.path.exists(actual_path):
            await target_channel.send(content=f"附件: {filename}", file=discord.File(actual_path, filename=filename))
        else:
            logger.warning(f"MISSING DOC: {actual_path}")

# --- Slash Commands ---

@bot.tree.command(name="start", description="Initialize session and verify identity")
async def slash_start(interaction: discord.Interaction):
    uid = get_user_id(interaction.user.id); sid = f"dc_{interaction.channel_id}_{uid}"
    sync_identity(uid, str(interaction.channel_id), sid)
    if not check_approved(sid):
        await interaction.response.send_message(PENDING_MSG, ephemeral=True)
        return
    await interaction.response.defer()
    res = await run_adk_prompt(APP_NAME, uid, sid, prompt=f"(Context ID: {uid}) (Session ID: {sid}). Please check my identity and greet me.")
    await process_and_deliver_files(interaction, res)

@bot.tree.command(name="reset", description="Reset current conversation context")
async def slash_reset(interaction: discord.Interaction):
    uid = get_user_id(interaction.user.id); sid = f"dc_{interaction.channel_id}_{uid}"
    sync_identity(uid, str(interaction.channel_id), sid)
    if not check_approved(sid):
        await interaction.response.send_message(PENDING_MSG, ephemeral=True)
        return
    await interaction.response.defer()
    if await delete_session(APP_NAME, uid, sid):
        res = await run_adk_prompt(APP_NAME, uid, sid, prompt=f"(Context ID: {uid}) (Session ID: {sid}). Please check my identity and greet me.")
        await process_and_deliver_files(interaction, f"🔄 **對話已重設**\n\n{res}")
    else:
        await interaction.followup.send("Reset failed.")

@bot.tree.command(name="profile", description="Show your saved profile")
async def slash_profile(interaction: discord.Interaction):
    uid = get_user_id(interaction.user.id); sid = f"dc_{interaction.channel_id}_{uid}"
    sync_identity(uid, str(interaction.channel_id), sid)
    if not check_approved(sid):
        await interaction.response.send_message(PENDING_MSG, ephemeral=True)
        return
    await interaction.response.defer()
    res = await run_adk_prompt(APP_NAME, uid, sid, prompt=f"(Context ID: {uid}) (Session ID: {sid}) Show my profile.")
    await process_and_deliver_files(interaction, res)

@bot.tree.command(name="list", description="View active reminders and tasks")
async def slash_list(interaction: discord.Interaction):
    uid = get_user_id(interaction.user.id); sid = f"dc_{interaction.channel_id}_{uid}"
    sync_identity(uid, str(interaction.channel_id), sid)
    if not check_approved(sid):
        await interaction.response.send_message(PENDING_MSG, ephemeral=True)
        return
    await interaction.response.defer()
    res = await run_adk_prompt(APP_NAME, uid, sid, prompt=f"(Context ID: {uid}) (Session ID: {sid}) List my reminders.")
    await process_and_deliver_files(interaction, res)

@bot.tree.command(name="files", description="List the latest generated documents")
@app_commands.describe(n="Number of latest files to list (max 20, default 5)")
async def slash_files(interaction: discord.Interaction, n: int = 5):
    uid = get_user_id(interaction.user.id); sid = f"dc_{interaction.channel_id}_{uid}"
    sync_identity(uid, str(interaction.channel_id), sid)
    await interaction.response.defer(ephemeral=True)
    n = max(1, min(n, 20))

    outputs_dir = "/app/data/outputs"

    def get_latest_files(path, limit):
        if not os.path.exists(path): return []
        files = []
        for f in os.listdir(path):
            full_path = os.path.join(path, f)
            if os.path.isfile(full_path) and not f.startswith('.'):
                files.append((f, os.path.getmtime(full_path)))
        files.sort(key=lambda x: x[1], reverse=True)
        return [f[0] for f in files[:limit]]

    doc_files = get_latest_files(outputs_dir, n)

    msg = f"**📁 系統生成最新 {n} 筆文件清單：**\n\n"
    if doc_files:
        msg += "\n".join([f"- `{f}`" for f in doc_files])
    else:
        msg += "_尚無生成文件_"

    if len(msg) > 1900:
        msg = msg[:1900] + "\n\n...(清單過長已截斷)"

    await interaction.followup.send(msg)

@bot.tree.command(name="help", description="Show list of available commands")
async def slash_help(interaction: discord.Interaction):
    txt = ("**CoStaff 指令：**\n"
           "`/start` - 開始/身份檢查\n"
           "`/reset` - 重設對話\n"
           "`/profile` - 查看個人資料\n"
           "`/list` - 查看提醒任務\n"
           "`/files` - 查看生成檔案")
    await interaction.response.send_message(txt)

# --- Message Handler ---

async def handle_msg(message):
    if message.author == bot.user: return
    uid = get_user_id(message.author.id); sid = f"dc_{message.channel.id}_{uid}"
    sync_identity(uid, str(message.channel.id), sid)
    if not check_approved(sid):
        await message.channel.send(PENDING_MSG)
        return
    text = message.content or ""; parts = [{"text": text}] if text else []
    
    if message.attachments:
        for attachment in message.attachments:
            if any(attachment.filename.lower().endswith(ext) for ext in ['png', 'jpg', 'jpeg', 'webp']):
                img_data = await attachment.read(); data_b64 = base64.b64encode(img_data).decode()
                parts.append({"inlineData": {"mimeType": attachment.content_type or "image/jpeg", "data": data_b64}})
            buf = io.BytesIO(await attachment.read())
            file_id = await upload_to_costaff(buf, attachment.filename, uid, sid=sid, app_name=APP_NAME)
            if file_id and not any(attachment.filename.lower().endswith(ext) for ext in ['png', 'jpg', 'jpeg', 'webp']):
                parts.append({"text": f"[System: User uploaded a document: {attachment.filename} (ID: {file_id})]"})

    if not parts: return
    async with message.channel.typing():
        context_text = f"(Context ID: {uid}) (Session ID: {sid})"
        if text: parts[0]["text"] = f"{context_text} {parts[0]['text']}"
        else: parts.insert(0, {"text": context_text})
        final_res = await run_adk_prompt(APP_NAME, uid, sid, parts=parts)
        logger.debug(f"Agent response for session {sid}: {final_res[:100]}...")
        await process_and_deliver_files(message, final_res)

@bot.event
async def on_ready():
    logger.info(f"Discord Bot Logged in as {bot.user.name} ({bot.user.id})")

@bot.event
async def on_message(message):
    if message.author == bot.user: return
    
    # Check if bot is mentioned OR if it's a private message (DM)
    is_mentioned = bot.user in message.mentions
    is_dm = isinstance(message.channel, discord.DMChannel)
    
    if is_mentioned or is_dm:
        # If mentioned in a channel, clean up the mention string (e.g. <@123...>) from text
        if is_mentioned and not is_dm:
            # Remove the mention part so it doesn't confuse the AI
            message.content = re.sub(r'<@!?\d+>', '', message.content).strip()
            
        await handle_msg(message)

if __name__ == "__main__":
    if TOKEN: bot.run(TOKEN)