"""Tests for `costaff update --all` plugin fan-out (feature #1)."""
import typer

from cli.commands import update as upd


def _patch(monkeypatch, conf, agent_fn, channel_fn):
    monkeypatch.setattr(
        "services.config.ConfigManager.get_config", staticmethod(lambda: conf)
    )
    monkeypatch.setattr("cli.commands.agent_container.agent_rebuild", agent_fn)
    monkeypatch.setattr("cli.commands.channel.channel_rebuild", channel_fn)


def test_update_all_rebuilds_source_plugins_and_skips_remote(monkeypatch):
    conf = {
        "external_agents": {
            "coding": {"type": "github", "fragment_path": "/f", "container_names": ["c"]},
            "remote": {"type": "url", "a2a_url": "http://x"},  # not pinnable
        },
        "dynamic_channels": {
            "telegram": {"fragment_path": "/f2", "container_names": ["t"]},
        },
    }
    calls = []
    _patch(
        monkeypatch,
        conf,
        lambda **kw: calls.append(("agent", kw)),
        lambda **kw: calls.append(("channel", kw)),
    )

    upd._update_all_plugins("v0.1.0-alpha-2")

    assert [c[0] for c in calls] == ["agent", "channel"]  # remote url skipped
    assert calls[0][1]["name"] == "coding"
    assert calls[0][1]["tag"] == "v0.1.0-alpha-2"
    assert calls[1][1]["name"] == "telegram"


def test_update_all_continues_after_one_failure(monkeypatch):
    conf = {
        "external_agents": {
            "coding": {"type": "github", "fragment_path": "/f", "container_names": ["c"]},
        },
        "dynamic_channels": {
            "telegram": {"fragment_path": "/f2", "container_names": ["t"]},
        },
    }
    channel_calls = []

    def failing_agent(**kw):
        raise typer.Exit(1)

    _patch(
        monkeypatch,
        conf,
        failing_agent,
        lambda **kw: channel_calls.append(kw),
    )

    # Must not raise — one plugin failing should not abort the batch.
    upd._update_all_plugins("v9")
    assert len(channel_calls) == 1
    assert channel_calls[0]["name"] == "telegram"


def test_update_all_no_plugins_is_noop(monkeypatch):
    _patch(
        monkeypatch,
        {"external_agents": {}, "dynamic_channels": {}},
        lambda **kw: (_ for _ in ()).throw(AssertionError("should not be called")),
        lambda **kw: (_ for _ in ()).throw(AssertionError("should not be called")),
    )
    upd._update_all_plugins(None)  # no plugins → clean no-op
