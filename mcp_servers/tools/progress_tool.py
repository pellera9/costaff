"""report_step — LLM-driven live progress panel tool.

Same plumbing class as send_message_now: a sub-agent's LLM calls this
(per its instruction) via the costaff-core /api/tool shim; here it drives
the single self-updating Telegram message (core/notifiers/progress_panel).

Why LLM-driven and not automatic tool callbacks: ADK's A2A boundary gives
an A2A-invoked sub-agent a fresh opaque session with no link back to the
task, so an automatic callback cannot tell core which chat to update
(verified). The only context that crosses the A2A boundary is the prompt
the LLM reads — so the panel must be driven by the LLM calling this tool
with the PROGRESS_CONTEXT values.

Panel keying: `session_id` here == the PROGRESS_CONTEXT session_id ==
`task_<task_id>`, which is exactly the key the executor's panel_finalize
uses on task done/failed — so steps + header stay on one message.
"""
import logging

logger = logging.getLogger("costaff-agent-engine")

_START = {"start", "begin", "doing", "working", "in_progress", "running"}
_OK = {"done", "ok", "complete", "completed", "success", "succeeded"}
_FAIL = {"failed", "fail", "error", "errored"}


async def report_step(
    session_id: str,
    step: str,
    status: str,
    agent: str = "business_analysis_agent",
    channel: str = "telegram",
    user_id: str = "",
) -> str:
    """Report one work step to the live progress panel.

    Call at each major step: status="doing" when it starts, then
    status="done" (or "failed") when it finishes. Use the session_id,
    channel and user_id from the task's [PROGRESS_CONTEXT] block. The
    panel is a single Telegram message edited in place.

    Args:
      session_id: PROGRESS_CONTEXT session_id (the panel key).
      step: short human label, e.g. "generate report".
      status: "doing" | "done" | "failed".
      agent: the reporting agent name.
      channel: PROGRESS_CONTEXT channel (telegram).
      user_id: PROGRESS_CONTEXT user_id (for chat resolution).
    """
    try:
        from core.notifiers.progress_panel import panel_step
        s = (status or "").strip().lower()
        if s in _FAIL:
            phase, ok = "end", False
        elif s in _OK:
            phase, ok = "end", True
        else:  # default → treat as start/doing
            phase, ok = "start", True
        await panel_step(
            key=session_id, recipient=user_id, channel=channel,
            session_id=session_id, agent=agent, tool=step,
            phase=phase, ok=ok,
        )
        return f"step '{step}' → {s or 'doing'}"
    except Exception:
        logger.exception("[report_step] swallowed")
        return "ok"  # fail-safe: panel must never break the caller
