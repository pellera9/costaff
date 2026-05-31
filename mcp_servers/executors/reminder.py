from core import models
from core.database import SessionLocal
from core.notifiers.telegram import send_telegram_notification
from core.notifiers.line_notifier import send_line_notification
from core.notifiers.discord import send_discord_notification
from core.notifiers.email_notifier import send_email_notification
from mcp_servers.setup import logger


async def execute_reminder(reminder_id: str):
    """Send a one-time scheduled reminder message to the user."""
    db = SessionLocal()
    try:
        reminder = db.query(models.Reminder).filter(models.Reminder.id == reminder_id).first()
        if not reminder or reminder.status != "pending":
            return

        # Authoritative channel: resolve from the user's IdentityMap (where the
        # user actually is), not the stored channel — which create_reminder may
        # have defaulted to "telegram" before webchat was a recognised branch.
        # Mirrors execute_regular_work. dispatch_notification handles every
        # channel (telegram / discord / line / email / webchat-enterprise) plus
        # the hashed_id → real_id resolution, so we no longer dispatch by hand.
        from mcp_servers.task_helpers import get_user_channel_info
        from core.notifiers.dispatcher import dispatch_notification

        chan = (reminder.channel or "").lower()
        recipient = reminder.recipient or reminder.user_id
        resolved_chan, resolved_recipient = get_user_channel_info(reminder.user_id, db)
        if resolved_chan:
            chan = resolved_chan
            recipient = resolved_recipient or recipient

        logger.info(f"Executing reminder {reminder_id} via {chan}")

        # Auto-execute: run the agent on the reminder's task text and deliver
        # the RESULT, instead of just posting the text and waiting for the user
        # to reply "OK". A "1 分鐘後讀信列 To-Do" reminder thus actually runs and
        # returns the finished To-Do — no human-in-the-loop. Mirrors the
        # automated-execution directive used by execute_regular_work.
        import os
        from core.adk_client import run_adk_prompt

        app_name = reminder.app_name or os.getenv("ADK_APP_NAME", "costaff_agent")
        run_session = f"rmd_{reminder_id}_{(reminder.user_id or '')[:8]}"
        prompt = (
            f"(System Context: Your ADK session user_id is '{reminder.user_id}'. "
            "Use this EXACT value whenever a tool requires a user_id parameter.)\n\n"
            "(AUTOMATED EXECUTION — nobody is watching this session to approve "
            "anything. This is a scheduled one-time task firing NOW. Carry out the "
            "request below immediately and return the finished result. Do NOT emit a "
            "plan / '執行計劃', do NOT ask the user to reply 'OK' or for any "
            "confirmation, and do NOT create another reminder or scheduled job. Just "
            "DO it now and report the result.)\n\n"
            f"{reminder.message}"
        )

        success = False
        try:
            result_text = await run_adk_prompt(app_name, reminder.user_id, run_session, prompt)
            await dispatch_notification(chan, recipient, result_text or reminder.message,
                                        session_id=reminder.session_id)
            success = True
        except Exception as e:
            logger.error(f"Reminder execute error {reminder_id}: {e}")
            # Fall back to just delivering the reminder text so the user is at
            # least notified even if the agent run failed.
            try:
                await dispatch_notification(chan, recipient, reminder.message,
                                            session_id=reminder.session_id)
                success = True
            except Exception:
                pass

        reminder.status = "sent" if success else "failed"
        db.commit()

    except Exception as e:
        logger.error(f"execute_reminder failed {reminder_id}: {e}")
    finally:
        db.close()
