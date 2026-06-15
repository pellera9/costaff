"""Tests for full-install backup / restore (feature #4).

The DB dump/restore are injected so these run without Docker or Postgres;
the file/archive bundling is exercised for real against tmp dirs.
"""
import json
import os

import pytest

from services import backup as backup_mod


@pytest.fixture
def fake_install(tmp_path, monkeypatch):
    runtime = tmp_path / "costaff"
    runtime.mkdir()
    (runtime / ".env").write_text("POSTGRES_USER=u\nPOSTGRES_DB=d\n")
    (runtime / "config.json").write_text('{"x": 1}')
    (runtime / "auth.json").write_text('{"admin": true}')

    ws = tmp_path / "workspace"
    (ws / "shared").mkdir(parents=True)
    (ws / "shared" / "report.txt").write_text("hello")

    paths = {
        "env": str(runtime / ".env"),
        "config": str(runtime / "config.json"),
        "auth": str(runtime / "auth.json"),
        "frontend": "",
    }
    monkeypatch.setattr(backup_mod, "PATHS", paths)
    monkeypatch.setattr(backup_mod, "_workspace_root", str(ws))
    return {"paths": paths, "ws": str(ws), "tmp": tmp_path}


def test_backup_restore_roundtrip(fake_install, tmp_path):
    out = str(tmp_path / "bk.tar.gz")

    def fake_dump(path):
        with open(path, "w") as f:
            f.write("-- SQL DUMP --")

    archive = backup_mod.create_backup(out, db_dump=fake_dump)
    assert os.path.exists(archive)

    manifest = backup_mod.read_manifest(archive)
    assert set(manifest["contents"]) >= {"config.json", "db.sql", "workspace/"}
    assert manifest["include_db"] is True

    # Mutate the live install, then restore it.
    with open(fake_install["paths"]["config"], "w") as f:
        f.write('{"x": 999}')
    (tmp_path / "stray").write_text("x")  # noise outside workspace

    captured = {}

    def fake_restore(path):
        with open(path) as f:
            captured["sql"] = f.read()

    backup_mod.restore_backup(archive, db_restore=fake_restore)

    assert json.load(open(fake_install["paths"]["config"]))["x"] == 1
    assert captured["sql"] == "-- SQL DUMP --"
    assert os.path.exists(os.path.join(fake_install["ws"], "shared", "report.txt"))


def test_backup_without_db_or_workspace(fake_install, tmp_path):
    out = str(tmp_path / "min.tar.gz")
    archive = backup_mod.create_backup(out, include_db=False, include_workspace=False)
    manifest = backup_mod.read_manifest(archive)
    assert "db.sql" not in manifest["contents"]
    assert "workspace/" not in manifest["contents"]
    assert "config.json" in manifest["contents"]


def test_pg_dump_builds_container_command(fake_install, tmp_path):
    seen = {}

    class _Result:
        returncode = 0
        stderr = b""

    def fake_runner(cmd, **kwargs):
        seen["cmd"] = cmd
        return _Result()

    backup_mod._pg_dump(str(tmp_path / "db.sql"), runner=fake_runner)
    cmd = seen["cmd"]
    assert cmd[:3] == ["docker", "exec", "costaff-postgres"]
    assert "pg_dump" in cmd
    assert cmd[cmd.index("-U") + 1] == "u"   # POSTGRES_USER from fake .env
    assert cmd[cmd.index("-d") + 1] == "d"   # POSTGRES_DB from fake .env


def test_pg_dump_failure_raises(fake_install, tmp_path):
    class _Result:
        returncode = 1
        stderr = b"could not connect"

    def fake_runner(cmd, **kwargs):
        return _Result()

    with pytest.raises(backup_mod.BackupError):
        backup_mod._pg_dump(str(tmp_path / "db.sql"), runner=fake_runner)


def test_read_manifest_rejects_non_backup(tmp_path):
    import tarfile

    junk = tmp_path / "junk.tar.gz"
    (tmp_path / "f.txt").write_text("nope")
    with tarfile.open(junk, "w:gz") as tar:
        tar.add(tmp_path / "f.txt", arcname="f.txt")

    with pytest.raises(backup_mod.BackupError):
        backup_mod.read_manifest(str(junk))
