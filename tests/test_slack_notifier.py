"""Tests for core.notifiers.slack_notifier.

Slack delivery is a 2-step (DM open + postMessage) or 4-step (DM open +
getUploadURLExternal + bytes POST + completeUploadExternal) HTTP flow.
We fake httpx.Client and assert on the captured request sequence —
no network, no real token.
"""
import json

import pytest

from core.notifiers import slack_notifier


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeClient:
    """Stands in for httpx.Client; routes by URL substring."""

    def __init__(self, *a, **kw):
        self.calls = FakeClient.calls  # shared across the with-block

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if "conversations.open" in url:
            return FakeResponse({"ok": True, "channel": {"id": "D123"}})
        if "chat.postMessage" in url:
            return FakeResponse({"ok": True})
        if "getUploadURLExternal" in url:
            return FakeResponse(
                {"ok": True, "upload_url": "https://up.slack/abc", "file_id": "F1"}
            )
        if "up.slack" in url:
            return FakeResponse({}, status_code=200)
        if "completeUploadExternal" in url:
            return FakeResponse({"ok": True})
        return FakeResponse({"ok": False, "error": "unknown_url"})


@pytest.fixture
def fake_slack(monkeypatch):
    FakeClient.calls = []
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(slack_notifier.httpx, "Client", FakeClient)
    return FakeClient.calls


def test_notification_opens_dm_and_posts(fake_slack):
    assert slack_notifier.send_slack_notification("U42", "## Done\n**ok**")
    urls = [u for u, _ in fake_slack]
    assert "conversations.open" in urls[0]
    assert "chat.postMessage" in urls[1]
    body = fake_slack[1][1]["json"]
    assert body["channel"] == "D123"
    # mrkdwn conversion applied: headings/bold become *single asterisks*
    assert "*Done*" in body["text"] and "*ok*" in body["text"]


def test_channel_id_recipient_skips_dm_open(fake_slack):
    assert slack_notifier.send_slack_notification("C999", "hi")
    urls = [u for u, _ in fake_slack]
    assert all("conversations.open" not in u for u in urls)
    assert fake_slack[0][1]["json"]["channel"] == "C999"


def test_missing_token_returns_false(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    assert not slack_notifier.send_slack_notification("U42", "hi")


def test_file_upload_three_step_flow(fake_slack, tmp_path):
    f = tmp_path / "report.pdf"
    f.write_bytes(b"%PDF fake")
    assert slack_notifier.send_slack_file("U42", str(f))
    urls = [u for u, _ in fake_slack]
    assert "conversations.open" in urls[0]
    assert "getUploadURLExternal" in urls[1]
    assert "up.slack" in urls[2]
    assert "completeUploadExternal" in urls[3]
    complete = fake_slack[3][1]["json"]
    assert complete["channel_id"] == "D123"
    assert complete["files"][0]["title"] == "report.pdf"


def test_file_upload_missing_file_returns_false(fake_slack):
    assert not slack_notifier.send_slack_file("U42", "/no/such/file.pdf")
    assert fake_slack == []  # bails before any HTTP call
