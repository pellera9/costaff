"""Tests for the executor's declared-output verification.

The executor must NOT mark a task `done` if the sub-agent's RESULT claims
output files that are not on disk. Otherwise a downstream agent in a chain
reads the spec, looks for the file, and silently fails — 30+ seconds after
the user has already been told the upstream step succeeded.

These tests cover the pure verifier helper (no DB / no ADK / no Telegram)
and the executor's wiring of it (executor must raise → mark task failed).
"""
import os
import uuid
import asyncio
from datetime import datetime
from pathlib import Path

import pytest

from core import models
from mcp_servers.executors import project_task as executor_mod
from mcp_servers.executors.project_task import (
    OutputVerificationError,
    _verify_declared_outputs,
)


# ---------------------------------------------------------------------------
# _verify_declared_outputs — pure helper, no executor, no DB
# ---------------------------------------------------------------------------

def test_returns_empty_when_no_paths_mentioned():
    """A result with no /app/data/* paths is valid — task may have produced no files."""
    assert _verify_declared_outputs("Everything went fine. No files produced.") == []


def test_returns_empty_when_text_is_empty():
    assert _verify_declared_outputs("") == []
    assert _verify_declared_outputs(None) == []  # type: ignore[arg-type]


def test_returns_empty_when_all_paths_exist(tmp_path, monkeypatch):
    """Paths under /app/data/... that exist on disk must not be flagged."""
    real = tmp_path / "real.csv"
    real.write_text("a,b\n1,2\n")

    # Patch the regex so the test paths under tmp_path are recognized
    monkeypatch.setattr(executor_mod, "_DECLARED_PATH_RE", _re_for(tmp_path))

    text = f"[RESULT_END] wrote {real}"
    assert _verify_declared_outputs(text) == []


def test_returns_missing_paths(tmp_path, monkeypatch):
    real = tmp_path / "real.csv"
    real.write_text("ok")
    fake = tmp_path / "hallucinated.pdf"  # never created

    monkeypatch.setattr(executor_mod, "_DECLARED_PATH_RE", _re_for(tmp_path))

    text = f"saved {real} and {fake}"
    missing = _verify_declared_outputs(text)
    assert missing == [str(fake)]


def test_deduplicates_repeated_mentions(tmp_path, monkeypatch):
    """The same missing path mentioned twice should only appear once."""
    fake = tmp_path / "ghost.csv"
    monkeypatch.setattr(executor_mod, "_DECLARED_PATH_RE", _re_for(tmp_path))

    text = f"see {fake} and again {fake} for details"
    assert _verify_declared_outputs(text) == [str(fake)]


def test_recognised_extensions_only():
    """Only files with the recognised extension list count — random suffixes are
    not treated as output declarations. (`.tar` is intentionally not in the
    declared list because no sub-agent claims to produce one.)"""
    # `.tar.gz` is not in the extension list — should not be picked up
    assert _verify_declared_outputs("see /app/data/foo.tar.gz") == []


# ---------------------------------------------------------------------------
# Executor wiring — verification failure routes through the failure branch
# ---------------------------------------------------------------------------

class _NonClosingSession:
    def __init__(self, inner): self._inner = inner
    def __getattr__(self, name): return getattr(self._inner, name)
    def close(self): pass


def _make_task(db_session, *, session_id=None):
    epic = models.Epic(
        id=str(uuid.uuid4()), user_id="user_x", title="E",
        status="active", created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add(epic)
    task = models.ProjectTask(
        id=str(uuid.uuid4()),
        epic_id=epic.id,
        user_id="user_x",
        session_id=session_id,
        title="Pretend to write a CSV",
        spec="Generate a CSV at /app/data/shared/foo/x.csv.",
        type="immediate",
        assigned_agent="coding_agent",
        status="queued",
        channel="telegram",
        recipient="user_x",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add(task)
    db_session.commit()
    return task


@pytest.mark.asyncio
async def test_executor_marks_failed_when_outputs_missing(db_session, monkeypatch, tmp_path):
    """The headline contract: sub-agent says `wrote X` but X is missing →
    task ends up `failed`, not `done`."""
    task = _make_task(db_session)
    monkeypatch.setattr(executor_mod, "SessionLocal", lambda: _NonClosingSession(db_session))

    # Sub-agent claims a path under /app/data/... that does not exist
    fake_path = "/app/data/shared/coding-agent/never-written.csv"

    async def fake_run(app, uid, sid, prompt):
        return f"[RESULT_START] done, wrote {fake_path} [RESULT_END]"

    async def fake_dispatch(channel, recipient, body, sid):
        return None

    monkeypatch.setattr(executor_mod, "run_adk_prompt", fake_run)
    monkeypatch.setattr(executor_mod, "dispatch_notification", fake_dispatch)

    await executor_mod.execute_project_task(task.id)
    # Drain any background tasks the executor spawned
    for t in list(asyncio.all_tasks()):
        if t is not asyncio.current_task():
            t.cancel()

    db_session.refresh(task)
    assert task.status == "failed", "Missing declared outputs must fail the task"

    # The failure comment should mention the missing path
    issues = (
        db_session.query(models.TaskComment)
        .filter_by(task_id=task.id, type="issue")
        .all()
    )
    assert len(issues) == 1
    assert fake_path in issues[0].content
    assert "OutputVerificationError" in issues[0].content


@pytest.mark.asyncio
async def test_executor_marks_done_when_outputs_exist(db_session, monkeypatch, tmp_path):
    """Symmetric case: when the sub-agent's claimed paths exist on disk, the
    task completes normally."""
    task = _make_task(db_session)
    monkeypatch.setattr(executor_mod, "SessionLocal", lambda: _NonClosingSession(db_session))

    # Create a real file the sub-agent will claim it produced
    real = tmp_path / "real.csv"
    real.write_text("a,b\n1,2\n")
    real_str = str(real)

    # Widen the regex so paths under tmp_path are treated as declared outputs
    monkeypatch.setattr(executor_mod, "_DECLARED_PATH_RE", _re_for(tmp_path))

    async def fake_run(app, uid, sid, prompt):
        return f"[RESULT_START] done, wrote {real_str} [RESULT_END]"

    async def fake_dispatch(channel, recipient, body, sid):
        return None

    monkeypatch.setattr(executor_mod, "run_adk_prompt", fake_run)
    monkeypatch.setattr(executor_mod, "dispatch_notification", fake_dispatch)

    await executor_mod.execute_project_task(task.id)
    for t in list(asyncio.all_tasks()):
        if t is not asyncio.current_task():
            t.cancel()

    db_session.refresh(task)
    assert task.status == "done"


@pytest.mark.asyncio
async def test_executor_marks_done_when_no_paths_claimed(db_session, monkeypatch):
    """A sub-agent that does not declare any output files (e.g. a pure query
    or analysis with no file artefact) must still complete normally."""
    task = _make_task(db_session)
    monkeypatch.setattr(executor_mod, "SessionLocal", lambda: _NonClosingSession(db_session))

    async def fake_run(app, uid, sid, prompt):
        return "[RESULT_START] Found 49 rows. PM2.5 avg = 17.2 [RESULT_END]"

    async def fake_dispatch(channel, recipient, body, sid):
        return None

    monkeypatch.setattr(executor_mod, "run_adk_prompt", fake_run)
    monkeypatch.setattr(executor_mod, "dispatch_notification", fake_dispatch)

    await executor_mod.execute_project_task(task.id)
    for t in list(asyncio.all_tasks()):
        if t is not asyncio.current_task():
            t.cancel()

    db_session.refresh(task)
    assert task.status == "done"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _re_for(tmp_path: Path):
    """Build a regex that matches absolute paths under tmp_path (so tests can
    use a real temp dir instead of needing to create /app/data/ on the host)."""
    import re as _re
    exts = r"pdf|docx|md|txt|html|htm|png|jpg|jpeg|gif|csv|json|xlsx|xls|zip"
    prefix = _re.escape(str(tmp_path))
    return _re.compile(prefix + r"/[\w./-]+\.(?:" + exts + r")", _re.IGNORECASE)
