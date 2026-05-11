"""Tests for the startup orphan-task recovery (background.recover_orphaned_tasks).

Contract:
- Tasks with status='doing' AND updated_at < now() - ORPHAN_THRESHOLD_MINUTES
  must be flipped to 'failed' on call, and a TaskComment of type='issue'
  must be inserted documenting why.
- Tasks with status='doing' but recent updated_at must NOT be touched
  (an actual worker is presumably still running them).
- Tasks in other statuses (done / failed / queued / backlog) must NOT be
  touched regardless of age.
- The function must commit on success and not raise.
- Returns the number of recovered tasks for ops visibility.
"""
import uuid
from datetime import datetime, timedelta

import pytest

from core import models
from mcp_servers import background


def _make_task(db, *, status, age_minutes, title="t"):
    """Helper: insert a ProjectTask whose updated_at is `age_minutes` in the past."""
    # Epic FK is non-null on ProjectTask
    epic = models.Epic(
        id=str(uuid.uuid4()),
        user_id="u",
        title="E",
        status="active",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(epic)
    db.flush()
    t = models.ProjectTask(
        id=str(uuid.uuid4()),
        epic_id=epic.id,
        user_id="u",
        title=title,
        spec="...",
        type="immediate",
        assigned_agent="ba",
        status=status,
        created_at=datetime.utcnow() - timedelta(minutes=age_minutes),
        updated_at=datetime.utcnow() - timedelta(minutes=age_minutes),
    )
    db.add(t)
    db.commit()
    return t


def test_recovers_stuck_doing_tasks(db_session, monkeypatch):
    # The function-under-test calls SessionLocal() and .close()s it. We patch
    # it to a factory that hands out the same engine so we can re-read state
    # afterwards via a fresh query.
    monkeypatch.setattr(background, "SessionLocal", lambda: db_session)

    old_stuck_id = _make_task(db_session, status="doing", age_minutes=60, title="old-doing").id
    fresh_doing_id = _make_task(db_session, status="doing", age_minutes=2, title="fresh-doing").id
    queued_id = _make_task(db_session, status="queued", age_minutes=60, title="queued").id
    done_id = _make_task(db_session, status="done", age_minutes=60, title="done").id

    recovered = background.recover_orphaned_tasks()
    assert recovered == 1

    # Fresh query after the function closed its session — avoids stale ORM state
    db_session.expire_all()
    statuses = {
        t.id: t.status
        for t in db_session.query(models.ProjectTask).all()
    }
    assert statuses[old_stuck_id] == "failed"
    assert statuses[fresh_doing_id] == "doing"
    assert statuses[queued_id] == "queued"
    assert statuses[done_id] == "done"

    # A TaskComment explaining the orphan must have been inserted
    comments = db_session.query(models.TaskComment).filter(
        models.TaskComment.task_id == old_stuck_id
    ).all()
    assert len(comments) == 1
    assert comments[0].type == "issue"
    assert "orphan" in comments[0].content.lower()


def test_returns_zero_when_nothing_stuck(db_session, monkeypatch):
    monkeypatch.setattr(background, "SessionLocal", lambda: db_session)

    _make_task(db_session, status="doing", age_minutes=2)
    _make_task(db_session, status="done", age_minutes=60)

    assert background.recover_orphaned_tasks() == 0


def test_recovers_multiple_orphans(db_session, monkeypatch):
    monkeypatch.setattr(background, "SessionLocal", lambda: db_session)

    for _ in range(3):
        _make_task(db_session, status="doing", age_minutes=60)

    assert background.recover_orphaned_tasks() == 3
    remaining = db_session.query(models.ProjectTask).filter(
        models.ProjectTask.status == "doing"
    ).count()
    assert remaining == 0
