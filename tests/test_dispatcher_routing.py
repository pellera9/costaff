"""Tests for core.notifiers.dispatcher channel routing.

Locks in the contracts added with Slack support:
  - "slack" routes to the Slack notifier (was: silently dropped)
  - Discord and Slack branches attach extracted files (was: Telegram and
    WebChat only — Discord users got prose without the produced PDF/CSV)
"""
import pytest

from core.notifiers import dispatcher


@pytest.fixture
def capture(monkeypatch, db_session):
    """Patch every notifier the dispatcher fans out to and record calls."""
    calls = []

    def _rec(name):
        def _f(*a, **kw):
            calls.append((name, a, kw))
            return True
        return _f

    async def _arec(*a, **kw):
        calls.append(("line", a, kw))
        return True

    monkeypatch.setattr(dispatcher, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(dispatcher, "send_telegram_notification", _rec("tg"))
    monkeypatch.setattr(dispatcher, "send_telegram_document", _rec("tg_file"))
    monkeypatch.setattr(dispatcher, "send_discord_notification", _rec("discord"))
    monkeypatch.setattr(dispatcher, "send_discord_file", _rec("discord_file"))
    monkeypatch.setattr(dispatcher, "send_slack_notification", _rec("slack"))
    monkeypatch.setattr(dispatcher, "send_slack_file", _rec("slack_file"))
    monkeypatch.setattr(dispatcher, "send_line_notification", _arec)
    monkeypatch.setattr(dispatcher, "send_webchat_notification", _rec("webchat"))
    monkeypatch.setattr(dispatcher, "send_webchat_file", _rec("webchat_file"))
    monkeypatch.setattr(
        dispatcher, "extract_file_paths", lambda text: ["/app/data/shared/x.pdf"]
    )
    return calls


@pytest.mark.asyncio
async def test_slack_channel_routes_to_slack_notifier(capture):
    await dispatcher.dispatch_notification("slack", "U42", "done")
    names = [n for n, _, _ in capture]
    assert "slack" in names
    assert "slack_file" in names  # extracted files ride along


@pytest.mark.asyncio
async def test_discord_branch_attaches_files(capture):
    await dispatcher.dispatch_notification("discord", "12345", "done")
    names = [n for n, _, _ in capture]
    assert "discord" in names
    assert "discord_file" in names


@pytest.mark.asyncio
async def test_telegram_branch_unchanged(capture):
    await dispatcher.dispatch_notification("telegram", "12345", "done")
    names = [n for n, _, _ in capture]
    assert names.count("tg") == 1 and "tg_file" in names
    assert "slack" not in names and "discord" not in names
