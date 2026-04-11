import os
import asyncio
import uuid
from datetime import datetime

from src.core import models
from src.core.database import SessionLocal
from src.core.adk_client import run_adk_prompt
from src.core.license import LicenseManager, ExecutionWarning
from mcp_servers.core import logger
from mcp_servers.utils import _send_notification


async def execute_regular_work(regular_work_id: str):
    """Execute a RegularWork item by calling the designated agent."""
    db = SessionLocal()
    try:
        work = db.query(models.RegularWork).filter(models.RegularWork.id == regular_work_id).first()
        if not work or work.status != "active":
            return

        logger.info(f"Executing RegularWork {regular_work_id}: {work.title}")

        # Check monthly execution limit
        try:
            LicenseManager.check_execution_limit(work.user_id, db)
        except ExecutionWarning as w:
            asyncio.create_task(_send_notification(work.channel, work.recipient, f"⚠️ {str(w)}", work.session_id))
        except ValueError as e:
            asyncio.create_task(_send_notification(work.channel, work.recipient, f"🚫 {str(e)}", work.session_id))
            logger.warning(f"RegularWork {regular_work_id} blocked: limit reached.")
            return

        app_name = os.getenv("ADK_APP_NAME", "costaff_agent")
        spec = work.spec
        if work.channel and work.recipient:
            spec += f"\n\n(System Note: This is a scheduled regular work. Deliver your output to the user via {work.channel}. Do NOT call send_message_now for this recipient.)"

        result_text = ""
        status = "success"
        try:
            result_text = await run_adk_prompt(app_name, work.user_id, work.session_id, spec)
            work.last_run = datetime.utcnow()
            work.updated_at = datetime.utcnow()

            new_log = models.RegularWorkLog(
                id=str(uuid.uuid4()),
                regular_work_id=regular_work_id,
                user_id=work.user_id,
                status="success",
                output=result_text,
                created_at=datetime.utcnow()
            )
            db.add(new_log)
            db.commit()

            if work.channel and work.recipient:
                await _send_notification(work.channel, work.recipient, result_text, work.session_id)

        except Exception as e:
            logger.error(f"RegularWork execution failed {regular_work_id}: {e}")
            status = "failed"
            new_log = models.RegularWorkLog(
                id=str(uuid.uuid4()),
                regular_work_id=regular_work_id,
                user_id=work.user_id,
                status="failed",
                output=str(e),
                created_at=datetime.utcnow()
            )
            db.add(new_log)
            db.commit()

    finally:
        db.close()
