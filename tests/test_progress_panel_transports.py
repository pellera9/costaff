"""Tests for the progress panel's multi-channel transports.

The panel was Telegram-only; Discord and Slack both support message
editing, so they now share the panel lifecycle via a `transport` field
on the panel state. These tests lock in:
  - channel string → transport mapping (incl. None for LINE)
  - panel_step creates state tagged with the right transport
  - _flush routes to the matching send/edit pair
"""
import asyncio

import pytest

import core.notifiers.progress_panel as pp


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    pp._PANELS.clear()
    pp._LOCKS.clear()
    # Panels resolve chat ids / task titles from the DB — irrelevant here.
    monkeypatch.setattr(pp, "_resolve_chat", lambda r, s: "CHAT1")
    monkeypatch.setattr(pp, "_resolve_task_title", lambda k: "demo task")
    yield
    pp._PANELS.clear()
    pp._LOCKS.clear()


def test_panel_transport_mapping():
    assert pp._panel_transport("telegram") == "telegram"
    assert pp._panel_transport("tg_main") == "telegram"
    assert pp._panel_transport("discord") == "discord"
    assert pp._panel_transport("dc_x") == "discord"
    assert pp._panel_transport("slack") == "slack"
    assert pp._panel_transport("slack_team") == "slack"
    assert pp._panel_transport("line") is None
    assert pp._panel_transport(None) is None


@pytest.mark.parametrize("channel,expected", [
    ("discord", "discord"),
    ("slack", "slack"),
    ("telegram", "telegram"),
])
def test_panel_step_tags_state_with_transport(monkeypatch, channel, expected):
    async def _noop_flush(key):
        return None

    monkeypatch.setattr(pp, "_flush", _noop_flush)

    async def run():
        await pp.panel_step(
            "task_1", "user", channel, "sid", "coding_agent",
            tool="run_tests", phase="start", ok=None,
        )
        # Stop the breathing-dots ticker inside the loop so teardown is clean.
        st = pp._PANELS["task_1"]
        if st.get("ticker"):
            st["ticker"].cancel()

    asyncio.run(run())
    assert pp._PANELS["task_1"]["transport"] == expected


def test_line_channel_is_ignored(monkeypatch):
    async def _noop_flush(key):
        return None

    monkeypatch.setattr(pp, "_flush", _noop_flush)
    asyncio.run(pp.panel_step(
        "task_1", "user", "line", "sid", "coding_agent",
        tool="x", phase="start", ok=None,
    ))
    assert pp._PANELS == {}


@pytest.mark.parametrize("transport,env,send_attr,edit_attr", [
    ("discord", "DISCORD_BOT_TOKEN", "_dc_send", "_dc_edit"),
    ("slack", "SLACK_BOT_TOKEN", "_slack_send", "_slack_edit"),
])
def test_flush_routes_to_transport(monkeypatch, transport, env, send_attr, edit_attr):
    calls = []
    monkeypatch.setenv(env, "token")
    monkeypatch.setattr(pp, send_attr, lambda t, s, x: calls.append("send") or "MSG1")
    monkeypatch.setattr(pp, edit_attr, lambda t, s, x: calls.append("edit"))

    state = {
        "chat_id": "CHAT1", "message_id": None, "steps": [["tool", "Doing"]],
        "agent_disp": "Agent", "task_title": "t", "header": "Working",
        "last_text": None, "phase": 0, "ticker": None, "transport": transport,
    }
    pp._PANELS["k"] = state

    asyncio.run(pp._flush("k"))  # first flush → send
    state["steps"][0][1] = "Done"
    asyncio.run(pp._flush("k"))  # second flush → edit

    assert calls == ["send", "edit"]
    assert state["message_id"] == "MSG1"


def test_flush_without_token_is_noop(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    sent = []
    monkeypatch.setattr(pp, "_slack_send", lambda t, s, x: sent.append(1))
    pp._PANELS["k"] = {
        "chat_id": "U1", "message_id": None, "steps": [],
        "agent_disp": "A", "task_title": "", "header": "Working",
        "last_text": None, "phase": 0, "ticker": None, "transport": "slack",
    }
    asyncio.run(pp._flush("k"))
    assert sent == []
