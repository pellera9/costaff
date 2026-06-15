"""Full-install backup & restore.

A CoStaff backup bundles everything an operator needs to recreate an install
on another machine into a single ``.tar.gz``:

    manifest.json   — costaff version, timestamp, what's inside
    env             — the core ``.env`` (secrets, DB URI, API keys)
    config.json     — system config (external_agents / dynamic_channels / ...)
    auth.json       — dashboard admin credentials (if present)
    db.sql          — pg_dump of the Postgres database (--clean --if-exists)
    workspace/      — the shared bind-mounted data dir (agent outputs)

The database is dumped with ``pg_dump`` *inside* the running postgres
container, so no host-side Postgres client is required and the dump is a
consistent snapshot — there is no need to stop services to take a backup.

The host CLI only reaches the DB through the postgres container, so both
backup and restore require that container to be running (``costaff start``).
"""
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime
from typing import Callable, Optional

from dotenv import dotenv_values

from utils.paths import PATHS, _workspace_root

POSTGRES_CONTAINER = "costaff-postgres"

# Files copied verbatim into / out of the archive, keyed by archive member name.
_FILE_MEMBERS = {
    "env": "env",
    "config": "config.json",
    "auth": "auth.json",
}


class BackupError(Exception):
    """Raised when a backup or restore cannot be completed."""


def _db_creds() -> tuple[str, str]:
    """Read POSTGRES_USER / POSTGRES_DB from the core .env (with defaults)."""
    env = dotenv_values(PATHS["env"]) if os.path.exists(PATHS["env"]) else {}
    user = env.get("POSTGRES_USER") or "costaff"
    db = env.get("POSTGRES_DB") or "costaff_db"
    return user, db


def workspace_dir() -> str:
    """Resolve the shared workspace dir the same way the compose mount does.

    Honours ``COSTAFF_WORKSPACE_DIR`` from the core .env (the bind-mount path
    that ``install.sh`` writes) and falls back to ``~/.costaff/workspace``.
    """
    env = dotenv_values(PATHS["env"]) if os.path.exists(PATHS["env"]) else {}
    return env.get("COSTAFF_WORKSPACE_DIR") or _workspace_root


def _pg_dump(sql_path: str, runner: Callable = subprocess.run) -> None:
    """Dump the Postgres DB to ``sql_path`` via the running container."""
    user, db = _db_creds()
    cmd = [
        "docker", "exec", POSTGRES_CONTAINER,
        "pg_dump", "-U", user, "-d", db,
        "--clean", "--if-exists", "--no-owner", "--no-privileges",
    ]
    with open(sql_path, "wb") as fh:
        result = runner(cmd, stdout=fh, stderr=subprocess.PIPE)
    if getattr(result, "returncode", 0) != 0:
        err = getattr(result, "stderr", b"") or b""
        if isinstance(err, bytes):
            err = err.decode(errors="replace")
        raise BackupError(
            f"pg_dump failed (is '{POSTGRES_CONTAINER}' running? try 'costaff start'):\n{err.strip()}"
        )


def _pg_restore(sql_path: str, runner: Callable = subprocess.run) -> None:
    """Load a ``db.sql`` dump back into the running Postgres container."""
    user, db = _db_creds()
    cmd = ["docker", "exec", "-i", POSTGRES_CONTAINER, "psql", "-U", user, "-d", db]
    with open(sql_path, "rb") as fh:
        result = runner(cmd, stdin=fh, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if getattr(result, "returncode", 0) != 0:
        err = getattr(result, "stderr", b"") or b""
        if isinstance(err, bytes):
            err = err.decode(errors="replace")
        raise BackupError(
            f"psql restore failed (is '{POSTGRES_CONTAINER}' running?):\n{err.strip()}"
        )


def create_backup(
    output: Optional[str] = None,
    *,
    include_db: bool = True,
    include_workspace: bool = True,
    db_dump: Optional[Callable[[str], None]] = None,
) -> str:
    """Bundle the whole install into a single ``.tar.gz`` and return its path.

    ``db_dump`` (a callable taking the target ``db.sql`` path) is injectable so
    tests can avoid touching Docker; it defaults to a container ``pg_dump``.
    """
    output = output or f"costaff_backup_{datetime.now():%Y%m%d_%H%M%S}.tar.gz"
    db_dump = db_dump or _pg_dump
    contents: list[str] = []

    with tempfile.TemporaryDirectory() as staging:
        # 1. Verbatim config / secret files.
        for key, member in _FILE_MEMBERS.items():
            src = PATHS[key]
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(staging, member))
                contents.append(member)

        # 2. Database dump.
        if include_db:
            db_dump(os.path.join(staging, "db.sql"))
            contents.append("db.sql")

        # 3. Shared workspace tree.
        ws = workspace_dir()
        if include_workspace and os.path.isdir(ws):
            shutil.copytree(ws, os.path.join(staging, "workspace"))
            contents.append("workspace/")

        # 4. Manifest.
        from utils.paths import VERSION
        manifest = {
            "costaff_version": VERSION,
            "created_at": datetime.now().isoformat(),
            "contents": contents,
            "include_db": include_db,
            "include_workspace": include_workspace,
        }
        import json
        with open(os.path.join(staging, "manifest.json"), "w") as fh:
            json.dump(manifest, fh, indent=2)

        # 5. Archive.
        with tarfile.open(output, "w:gz") as tar:
            for entry in sorted(os.listdir(staging)):
                tar.add(os.path.join(staging, entry), arcname=entry)

    return output


def read_manifest(archive: str) -> dict:
    """Return the manifest.json embedded in a backup archive."""
    import json
    with tarfile.open(archive, "r:gz") as tar:
        try:
            member = tar.extractfile("manifest.json")
        except KeyError:
            member = None
        if member is None:
            raise BackupError("Not a CoStaff backup (no manifest.json inside).")
        return json.load(member)


def restore_backup(
    archive: str,
    *,
    include_db: bool = True,
    include_workspace: bool = True,
    db_restore: Optional[Callable[[str], None]] = None,
) -> dict:
    """Restore files + database from a backup archive. Returns the manifest.

    Overwrites the core .env / config.json / auth.json and replaces the
    workspace tree. The caller is expected to confirm first — this is
    destructive. ``db_restore`` is injectable for tests.
    """
    if not os.path.exists(archive):
        raise BackupError(f"Archive not found: {archive}")
    db_restore = db_restore or _pg_restore
    manifest = read_manifest(archive)

    with tempfile.TemporaryDirectory() as staging:
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(staging)  # noqa: S202 — trusted, operator-supplied backup

        # 1. Verbatim files back to their canonical locations.
        for key, member in _FILE_MEMBERS.items():
            src = os.path.join(staging, member)
            if os.path.exists(src):
                dest = PATHS[key]
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copy2(src, dest)

        # 2. Workspace tree (replace wholesale).
        ws_src = os.path.join(staging, "workspace")
        if include_workspace and os.path.isdir(ws_src):
            ws_dest = workspace_dir()
            if os.path.isdir(ws_dest):
                shutil.rmtree(ws_dest)
            shutil.copytree(ws_src, ws_dest)

        # 3. Database.
        db_src = os.path.join(staging, "db.sql")
        if include_db and os.path.exists(db_src):
            db_restore(db_src)

    return manifest
