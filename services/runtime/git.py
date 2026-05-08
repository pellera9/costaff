"""Git operations for the costaff CLI.

CLI commands should not call `subprocess.run(["git", ...])` directly —
they go through this thin wrapper so that error messages stay consistent
("git binary not found" vs "clone failed: <stderr>") and a future
non-subprocess implementation (e.g. dulwich for sandboxed environments)
can swap in without touching callers.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Union


PathLike = Union[str, Path]


class GitError(RuntimeError):
    """Any git operation that did not complete successfully."""


class Git:
    def is_repo(self, path: PathLike) -> bool:
        return (Path(path) / ".git").is_dir()

    def clone(self, url: str, dest: PathLike, *, depth: int = 1) -> None:
        cmd = ["git", "clone"]
        if depth:
            cmd += ["--depth", str(depth)]
        cmd += [url, str(dest)]
        self._run(cmd)

    def pull_ff_only(self, repo: PathLike) -> None:
        self._run(["git", "pull", "--ff-only"], cwd=str(repo))

    @staticmethod
    def _run(cmd: list[str], *, cwd: str | None = None) -> None:
        try:
            subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)
        except FileNotFoundError as e:
            raise GitError(
                "git binary not found in PATH. Install git and retry."
            ) from e
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "").strip() or (e.stdout or "").strip() or "(no output)"
            raise GitError(f"{' '.join(cmd)} failed: {stderr}") from e
