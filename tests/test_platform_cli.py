"""Tests for `costaff platform` — registry, secret plumbing, ordering,
and the command-level flows (mocked compose / git / config).

The shared-DB design contract:
  - one Postgres instance (the `db` pseudo-platform), one role+database
    per service; the db repo's .env is the source of truth for passwords
  - OIDC client secrets must end up identical in the platform's .env and
    the Account Manager's .env
  - start order: db → account-manager → everything else
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest
import typer

from cli.commands import platform as plat
from cli.commands.platform import (
    OFFICIAL_PLATFORMS,
    _fill_env_secrets,
    _read_env_value,
    _set_env_value,
    _start_order,
    _sync_db_password,
    _sync_oidc_secret,
)


# ----- registry sanity -------------------------------------------------


def test_registry_urls_follow_naming_convention():
    for name, meta in OFFICIAL_PLATFORMS.items():
        assert meta["github"].startswith("https://github.com/costaff-ai/costaff-platform-")
        assert meta["github"].endswith(".git")


def test_registry_prefixes_unique():
    prefixes = [m["prefix"] for m in OFFICIAL_PLATFORMS.values() if m["prefix"]]
    assert len(prefixes) == len(set(prefixes))


def test_registry_ports_unique():
    ports = [m["port"] for m in OFFICIAL_PLATFORMS.values() if m["port"]]
    assert len(ports) == len(set(ports))


# ----- start order ------------------------------------------------------


def test_start_order_db_first_then_idp():
    platforms = {"erp": {}, "db": {}, "crm": {}, "account-manager": {}}
    assert _start_order(platforms) == ["db", "account-manager", "crm", "erp"]


def test_start_order_without_db_or_idp():
    assert _start_order({"erp": {}, "crm": {}}) == ["crm", "erp"]


# ----- env helpers ------------------------------------------------------


def test_set_and_read_env_value(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\nERP_DB_USER=erp\nERP_DB_PASSWORD=\n")
    _set_env_value(str(env), "ERP_DB_PASSWORD", "s3cret")
    _set_env_value(str(env), "NEW_KEY", "v")
    assert _read_env_value(str(env), "ERP_DB_PASSWORD") == "s3cret"
    assert _read_env_value(str(env), "NEW_KEY") == "v"
    assert "# comment" in env.read_text()  # comments survive


def test_fill_env_secrets_only_touches_empty_secret_keys(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "ERP_DB_PASSWORD=\n"
        "ERP_JWT_SECRET=\n"
        "ERP_API_KEY=already-set\n"
        "ERP_DB_USER=erp\n"
        "ERP_FRONTEND_PORT=\n"
    )
    filled = _fill_env_secrets(str(env))
    assert set(filled) == {"ERP_DB_PASSWORD", "ERP_JWT_SECRET"}
    assert _read_env_value(str(env), "ERP_API_KEY") == "already-set"
    assert _read_env_value(str(env), "ERP_DB_USER") == "erp"
    assert _read_env_value(str(env), "ERP_FRONTEND_PORT") == ""
    assert len(_read_env_value(str(env), "ERP_DB_PASSWORD")) > 20


# ----- secret plumbing ---------------------------------------------------


def test_sync_db_password_db_side_wins(tmp_path):
    db_env = tmp_path / "db.env"
    pf_env = tmp_path / "pf.env"
    db_env.write_text("ERP_DB_PASSWORD=from-db\n")
    pf_env.write_text("ERP_DB_PASSWORD=stale\n")
    value = _sync_db_password("ERP", str(pf_env), str(db_env))
    assert value == "from-db"
    assert _read_env_value(str(pf_env), "ERP_DB_PASSWORD") == "from-db"


def test_sync_db_password_generates_once_for_both(tmp_path):
    db_env = tmp_path / "db.env"
    pf_env = tmp_path / "pf.env"
    db_env.write_text("ERP_DB_PASSWORD=\n")
    pf_env.write_text("ERP_DB_PASSWORD=\n")
    value = _sync_db_password("ERP", str(pf_env), str(db_env))
    assert value
    assert _read_env_value(str(db_env), "ERP_DB_PASSWORD") == value
    assert _read_env_value(str(pf_env), "ERP_DB_PASSWORD") == value


def test_sync_oidc_secret_am_side_wins(tmp_path):
    am_env = tmp_path / "am.env"
    pf_env = tmp_path / "pf.env"
    am_env.write_text("AM_ERP_CLIENT_SECRET=idp-value\n")
    pf_env.write_text("ERP_OIDC_CLIENT_SECRET=\n")
    value = _sync_oidc_secret("ERP", "ERP", str(pf_env), str(am_env))
    assert value == "idp-value"
    assert _read_env_value(str(pf_env), "ERP_OIDC_CLIENT_SECRET") == "idp-value"


def test_sync_oidc_secret_platform_value_flows_to_am(tmp_path):
    am_env = tmp_path / "am.env"
    pf_env = tmp_path / "pf.env"
    am_env.write_text("AM_ERP_CLIENT_SECRET=\n")
    pf_env.write_text("ERP_OIDC_CLIENT_SECRET=pf-value\n")
    _sync_oidc_secret("ERP", "ERP", str(pf_env), str(am_env))
    assert _read_env_value(str(am_env), "AM_ERP_CLIENT_SECRET") == "pf-value"


def test_sync_oidc_secret_without_am_returns_none_but_fills_platform(tmp_path):
    pf_env = tmp_path / "pf.env"
    pf_env.write_text("ERP_OIDC_CLIENT_SECRET=\n")
    assert _sync_oidc_secret("ERP", "ERP", str(pf_env), None) is None
    assert _read_env_value(str(pf_env), "ERP_OIDC_CLIENT_SECRET")


# ----- command flows (mocked) --------------------------------------------


def test_add_unknown_platform_without_source_exits():
    with patch.object(plat.ConfigManager, "get_config", return_value={}):
        with pytest.raises(typer.Exit):
            plat.platform_add("nonexistent", local=None, github=None, tag=None, start=True)


def test_add_duplicate_exits():
    conf = {"platforms": {"erp": {"source_path": "/tmp/x"}}}
    with patch.object(plat.ConfigManager, "get_config", return_value=conf):
        with pytest.raises(typer.Exit):
            plat.platform_add("erp", local=None, github=None, tag=None, start=True)


def test_remove_db_blocked_while_dependents_exist():
    conf = {"platforms": {"db": {"source_path": "/tmp/db"}, "erp": {"source_path": "/tmp/erp"}}}
    with patch.object(plat.ConfigManager, "get_config", return_value=conf):
        with pytest.raises(typer.Exit):
            plat.platform_remove("db", purge=False)


def test_platform_start_runs_in_dependency_order(tmp_path):
    order: list[str] = []
    conf = {
        "platforms": {
            "erp": {"source_path": str(tmp_path / "erp"), "enabled": True},
            "db": {"source_path": str(tmp_path / "db"), "enabled": True},
            "account-manager": {"source_path": str(tmp_path / "am"), "enabled": True},
            "crm": {"source_path": str(tmp_path / "crm"), "enabled": False},
        }
    }

    def fake_compose(src, *args, check=True):
        order.append(src.rsplit("/", 1)[-1])
        return subprocess.CompletedProcess([], 0)

    with patch.object(plat.ConfigManager, "get_config", return_value=conf), \
         patch.object(plat, "_compose", side_effect=fake_compose), \
         patch.object(plat, "_ensure_networks"):
        plat.platform_start(build=False)

    assert order == ["db", "am", "erp"]  # disabled crm skipped


def test_rebuild_with_pinned_ref_uses_checkout(tmp_path):
    conf = {
        "platforms": {
            "erp": {
                "source_path": str(tmp_path),
                "container_names": ["costaff-erp-backend"],
                "ref": "v1.0.0",
            }
        }
    }
    fake_git = MagicMock()
    fake_git.is_repo.return_value = True
    compose_calls: list[tuple] = []

    (tmp_path / "docker-compose.yaml").write_text(
        "services:\n  backend:\n    container_name: costaff-erp-backend\n"
    )

    with patch.object(plat.ConfigManager, "get_config", return_value=conf), \
         patch.object(plat.ConfigManager, "save_config"), \
         patch.object(plat, "Git", return_value=fake_git), \
         patch.object(plat, "_compose", side_effect=lambda s, *a, **k: compose_calls.append(a)):
        plat.platform_rebuild("erp", no_cache=False, pull=True, tag=None)

    fake_git.fetch_tags.assert_called_once()
    fake_git.checkout.assert_called_once_with(str(tmp_path), "v1.0.0")
    fake_git.pull_ff_only.assert_not_called()
    assert ("build",) in compose_calls
    assert ("up", "-d", "--force-recreate") in compose_calls
