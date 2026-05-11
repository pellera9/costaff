import os
import asyncio
import uuid
from datetime import datetime

from core import models
from core.database import SessionLocal
from core.adk_client import run_adk_prompt
from core.license import LicenseManager
from mcp_servers.setup import logger
from core.notifiers.dispatcher import dispatch_notification
from mcp_servers.task_helpers import get_user_channel_info, build_task_spec


async def execute_project_task(task_id: str):
    """Execute a ProjectTask by calling costaff_agent with full project context."""
    db = SessionLocal()
    try:
        task = db.query(models.ProjectTask).filter(models.ProjectTask.id == task_id).first()
        if not task or task.status in ("doing",):
            return

        # Check dependency
        if task.depends_on:
            dep = db.query(models.ProjectTask).filter(models.ProjectTask.id == task.depends_on).first()
            if dep and dep.status not in ("done",):
                logger.info(f"ProjectTask {task_id} waiting on dependency {task.depends_on}")
                return

        logger.info(f"Executing ProjectTask {task_id}: {task.title}")

        # Resolve channel/recipient — auto-detect from IdentityMap if not set on task
        channel = task.channel
        recipient = task.recipient
        if not channel:
            channel, recipient = get_user_channel_info(task.user_id, db)

        task.status = "doing"
        task.updated_at = datetime.utcnow()
        db.commit()

        # Write start comment
        start_comment = models.TaskComment(
            id=str(uuid.uuid4()),
            task_id=task_id,
            user_id=task.user_id,
            author=task.assigned_agent or "costaff_agent",
            content=(
                f"## 🚀 Started\n"
                f"- **Task**: {task.title}\n"
                f"- **Assigned Agent**: {task.assigned_agent or 'costaff_agent'}\n"
                f"- **Description**: {(task.spec or '').strip()[:300] or '—'}"
            ),
            type="note",
            created_at=datetime.utcnow()
        )
        db.add(start_comment)
        db.commit()

        # Build context-enriched spec and use task-scoped session to prevent context bleed
        spec = build_task_spec(task, db)
        task_session_id = f"task_{task_id}"
        app_name = os.getenv("ADK_APP_NAME", "costaff_agent")

        try:
            result_text = await run_adk_prompt(app_name, task.user_id, task_session_id, spec)

            task.status = "done"
            task.last_run = datetime.utcnow()
            task.updated_at = datetime.utcnow()

            comment = models.TaskComment(
                id=str(uuid.uuid4()),
                task_id=task_id,
                user_id=task.user_id,
                author=task.assigned_agent or "costaff_agent",
                content=result_text,
                type="result",
                created_at=datetime.utcnow()
            )
            db.add(comment)
            db.commit()

            if channel and recipient:
                # If the task carries an origin session_id (the user's main
                # conversation), inject a SYSTEM_CALLBACK turn into that session
                # so the Manager re-engages and presents the result naturally
                # in the user's conversation thread. Falls back to raw dispatch
                # if the synthetic callback fails (e.g. session expired, ADK
                # unreachable).
                origin_session_id = task.session_id
                callback_delivered = False
                if origin_session_id and origin_session_id != task_session_id:
                    try:
                        synthetic = (
                            f"[SYSTEM_CALLBACK|task_id={task_id}"
                            f"|agent={task.assigned_agent or 'costaff_agent'}"
                            f"|status=done]\n"
                            f"Original task title: {task.title}\n"
                            f"Result from sub-agent:\n{result_text[:4000]}\n"
                            f"\nInstructions: this is an asynchronous task that the user "
                            f"asked about earlier. Summarise the result in the user's "
                            f"language using your usual style, then ask the next "
                            f"logical step. Do NOT call create_project_task again "
                            f"unless the user explicitly asks for follow-up work."
                        )
                        manager_reply = await run_adk_prompt(
                            app_name, task.user_id, origin_session_id, synthetic
                        )
                        if manager_reply and not manager_reply.startswith("⚠️"):
                            await dispatch_notification(
                                channel, recipient, manager_reply, origin_session_id
                            )
                            callback_delivered = True
                            logger.info(
                                f"[execute_project_task] synthetic callback "
                                f"delivered for task {task_id} → session "
                                f"{origin_session_id}"
                            )
                    except Exception:
                        logger.exception(
                            f"[execute_project_task] synthetic callback failed "
                            f"for task {task_id}, falling back to raw dispatch"
                        )

                if not callback_delivered:
                    await dispatch_notification(
                        channel, recipient, result_text, task_session_id
                    )

            # Advance queue and wake up dependents
            if task.assigned_agent:
                asyncio.create_task(_advance_agent_queue(task.assigned_agent, task.user_id, finished_task_id=task_id))

        except Exception as e:
            logger.error(f"ProjectTask execution failed {task_id}: {e}")
            task.status = "failed"
            task.updated_at = datetime.utcnow()
            import traceback
            comment = models.TaskComment(
                id=str(uuid.uuid4()),
                task_id=task_id,
                user_id=task.user_id,
                author=task.assigned_agent or "costaff_agent",
                content=(
                    f"## ❌ Error Occurred\n"
                    f"- **Error Type**: {type(e).__name__}\n"
                    f"- **Error Message**: {str(e)}\n"
                    f"- **Location**: Agent execution stage (task_id={task_id})\n"
                    f"- **Traceback**:\n```\n{traceback.format_exc()[-1000:]}\n```"
                ),
                type="issue",
                created_at=datetime.utcnow()
            )
            db.add(comment)
            db.commit()

            # Notify the user about the failure — via synthetic callback if we
            # have an origin session, otherwise raw dispatch.
            if channel and recipient:
                origin_session_id = task.session_id
                failure_delivered = False
                if origin_session_id and origin_session_id != task_session_id:
                    try:
                        synthetic = (
                            f"[SYSTEM_CALLBACK|task_id={task_id}"
                            f"|agent={task.assigned_agent or 'costaff_agent'}"
                            f"|status=failed]\n"
                            f"Original task title: {task.title}\n"
                            f"Error type: {type(e).__name__}\n"
                            f"Error message: {str(e)[:500]}\n"
                            f"\nInstructions: this async task failed. Tell the user "
                            f"in their language what failed and suggest a recovery "
                            f"action (retry, change approach, or skip)."
                        )
                        manager_reply = await run_adk_prompt(
                            app_name, task.user_id, origin_session_id, synthetic
                        )
                        if manager_reply and not manager_reply.startswith("⚠️"):
                            await dispatch_notification(
                                channel, recipient, manager_reply, origin_session_id
                            )
                            failure_delivered = True
                    except Exception:
                        logger.exception(
                            f"[execute_project_task] failure callback errored "
                            f"for task {task_id}"
                        )
                if not failure_delivered:
                    fallback = (
                        f"❌ Task '{task.title}' (id={task_id}) failed: "
                        f"{type(e).__name__}: {str(e)[:300]}"
                    )
                    await dispatch_notification(
                        channel, recipient, fallback, task_session_id
                    )

            # Still advance the queue even on failure so remaining tasks are not blocked
            if task.assigned_agent:
                asyncio.create_task(_advance_agent_queue(task.assigned_agent, task.user_id, finished_task_id=task_id))


    finally:
        db.close()


async def _advance_agent_queue(agent_id: str, user_id: str, finished_task_id: str = None):
    """
    1. Pick up the next already-queued task for this specific agent.
    2. [NEW] Wake up any dependent tasks (from ANY agent) that were waiting for this finished task.
    """
    db = SessionLocal()
    try:
        # 1. Trigger dependent tasks across the whole project
        if finished_task_id:
            dependents = (
                db.query(models.ProjectTask)
                .filter(models.ProjectTask.depends_on == finished_task_id)
                .all()
            )
            for dep_task in dependents:
                if dep_task.status == "backlog":
                    logger.info(f"Dependency met! Queuing dependent task: {dep_task.id} (Agent: {dep_task.assigned_agent})")
                    dep_task.status = "queued"
                    dep_task.updated_at = datetime.utcnow()
                    db.commit() # Commit each change immediately to avoid race
                    asyncio.create_task(execute_project_task(dep_task.id))

        # 2. Advance the original agent's own queue (existing logic)
        next_task = (
            db.query(models.ProjectTask)
            .filter(
                models.ProjectTask.assigned_agent == agent_id,
                models.ProjectTask.user_id == user_id,
                models.ProjectTask.status == "queued"
            )
            .order_by(models.ProjectTask.queue_order.asc().nullslast(), models.ProjectTask.created_at.asc())
            .first()
        )
        if next_task:
            logger.info(f"Advancing queue: next task for {agent_id} is {next_task.id}")
            asyncio.create_task(execute_project_task(next_task.id))
    finally:
        db.close()
