"""Tests for mcp_servers.task_helpers — channel resolution + task spec build.

These helpers feed the MCP scheduler executors. The most important
contracts are:
  - The session-id prefix → channel mapping (tg_/dc_/line_/web_)
  - Delegation instructions are injected ONLY when assigned_agent != costaff_agent
  - PROGRESS_CONTEXT is included only when channel + recipient resolve
"""
import uuid
from datetime import datetime

import pytest

from core import models
from mcp_servers.task_helpers import build_task_spec, get_user_channel_info


# ---------------------------------------------------------------------------
# get_user_channel_info — session_id prefix → channel
# ---------------------------------------------------------------------------

def _add_identity(db, hashed_id: str, session_prefix: str):
    db.add(models.IdentityMap(
        session_id=f"{session_prefix}{hashed_id[:6]}",
        hashed_id=hashed_id,
        real_id=f"real-{hashed_id}",
        is_approved=True,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    ))
    db.commit()


def test_returns_none_when_no_identity_map(db_session):
    assert get_user_channel_info("missing-user", db_session) == (None, None)


@pytest.mark.parametrize("prefix,expected_channel", [
    ("tg_", "telegram"),
    ("dc_", "discord"),
    ("line_", "line"),
    ("web_", "webchat"),
])
def test_resolves_channel_from_session_prefix(db_session, prefix, expected_channel):
    _add_identity(db_session, "user1", prefix)
    channel, recipient = get_user_channel_info("user1", db_session)
    assert channel == expected_channel
    assert recipient == "user1"  # always returns hashed_id, not real_id


def test_unknown_prefix_returns_none(db_session):
    """An unrecognized session_id prefix means we don't know how to deliver
    notifications — better to return None than guess."""
    _add_identity(db_session, "user1", "weird_")
    assert get_user_channel_info("user1", db_session) == (None, None)


def test_returns_most_recent_mapping_when_user_has_multiple(db_session):
    """A user can re-link from a different platform; the most recent
    session_id wins."""
    older = datetime(2026, 1, 1)
    newer = datetime(2026, 6, 1)
    db_session.add(models.IdentityMap(
        session_id="tg_old", hashed_id="user1", real_id="r1",
        is_approved=True, created_at=older, updated_at=older,
    ))
    db_session.add(models.IdentityMap(
        session_id="dc_new", hashed_id="user1", real_id="r2",
        is_approved=True, created_at=newer, updated_at=newer,
    ))
    db_session.commit()
    channel, _ = get_user_channel_info("user1", db_session)
    assert channel == "discord"  # newer mapping wins


# ---------------------------------------------------------------------------
# build_task_spec — minimal task (no story, no delegation)
# ---------------------------------------------------------------------------

def _make_task(db, **overrides):
    epic_id = overrides.pop("epic_id", str(uuid.uuid4()))
    task = models.ProjectTask(
        id=overrides.pop("id", str(uuid.uuid4())),
        epic_id=epic_id,
        story_id=overrides.pop("story_id", None),
        user_id=overrides.pop("user_id", "user1"),
        session_id=overrides.pop("session_id", None),
        title=overrides.pop("title", "Test Task"),
        spec=overrides.pop("spec", "Do the thing"),
        type="immediate",
        assigned_agent=overrides.pop("assigned_agent", "costaff_agent"),
        status="backlog",
        priority="medium",
        created_at=datetime.utcnow(),
    )
    return task


def test_build_spec_includes_task_title_and_spec(db_session):
    task = _make_task(db_session, title="My Task", spec="Run analysis")
    out = build_task_spec(task, db_session)
    assert "[Task: My Task]" in out
    assert "Run analysis" in out


def test_build_spec_omits_epic_section_when_no_epic_row(db_session):
    """task.epic_id may exist but the row may have been deleted; helper
    must not blow up — just skip the project header."""
    task = _make_task(db_session, epic_id="orphan-epic")
    out = build_task_spec(task, db_session)
    assert "[Project:" not in out


def test_build_spec_includes_epic_title(db_session):
    epic = models.Epic(
        id="e1", user_id="user1",
        title="Q4 Goals", description="Annual planning",
        status="active", created_at=datetime.utcnow(),
    )
    db_session.add(epic)
    db_session.commit()
    task = _make_task(db_session, epic_id="e1")
    out = build_task_spec(task, db_session)
    assert "[Project: Q4 Goals]" in out
    assert "Project goal: Annual planning" in out


def test_build_spec_includes_story_title(db_session):
    epic = models.Epic(id="e1", user_id="user1", title="E", status="active", created_at=datetime.utcnow())
    story = models.Story(
        id="s1", epic_id="e1", user_id="user1",
        title="Phase 1", description="Discovery",
        status="open", priority="high", created_at=datetime.utcnow(),
    )
    db_session.add_all([epic, story])
    db_session.commit()
    task = _make_task(db_session, epic_id="e1", story_id="s1")
    out = build_task_spec(task, db_session)
    assert "[Story: Phase 1]" in out
    assert "Story context: Discovery" in out


# ---------------------------------------------------------------------------
# build_task_spec — delegation instructions
# ---------------------------------------------------------------------------

def test_no_delegation_when_assigned_to_costaff_agent(db_session):
    task = _make_task(db_session, assigned_agent="costaff_agent")
    out = build_task_spec(task, db_session)
    assert "DELEGATION INSTRUCTIONS" not in out


def test_delegation_block_appears_for_external_agent(db_session):
    task = _make_task(db_session, assigned_agent="coding_agent")
    out = build_task_spec(task, db_session)
    assert "DELEGATION INSTRUCTIONS" in out
    assert "coding_agent" in out
    # Must instruct waiting for the sub-agent's actual output, not a delegation ack
    assert "WAIT for" in out
    assert "Do NOT return 'I have delegated this task'" in out


def test_delegation_block_omits_for_no_assigned_agent(db_session):
    task = _make_task(db_session, assigned_agent=None)
    out = build_task_spec(task, db_session)
    assert "DELEGATION INSTRUCTIONS" not in out


# ---------------------------------------------------------------------------
# build_task_spec — PROGRESS_CONTEXT
# ---------------------------------------------------------------------------

def test_progress_context_included_when_task_has_explicit_channel(db_session):
    task = _make_task(db_session, user_id="u1", session_id=None)
    task.channel = "telegram"
    task.recipient = "u1"
    out = build_task_spec(task, db_session)
    assert "[PROGRESS_CONTEXT]" in out
    assert "user_id=u1" in out
    assert "channel=telegram" in out
    assert f"session_id=task_{task.id}" in out


def test_progress_context_resolved_from_identity_map_when_task_lacks_channel(db_session):
    """When task has no explicit channel, helper must look up the user's
    primary channel via get_user_channel_info."""
    _add_identity(db_session, "u1", "tg_")
    task = _make_task(db_session, user_id="u1")
    task.channel = None
    task.recipient = None
    out = build_task_spec(task, db_session)
    assert "[PROGRESS_CONTEXT]" in out
    assert "channel=telegram" in out


def test_progress_context_omitted_when_no_channel_resolvable(db_session):
    """No identity map + no explicit channel → don't emit a PROGRESS_CONTEXT
    block. The agent will simply not send progress messages."""
    task = _make_task(db_session, user_id="orphan-user")
    task.channel = None
    task.recipient = None
    out = build_task_spec(task, db_session)
    assert "[PROGRESS_CONTEXT]" not in out
