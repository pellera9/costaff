"""Unit tests for services.runtime.git."""
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from services.runtime.git import Git, GitError


def test_is_repo_true(tmp_path):
    (tmp_path / ".git").mkdir()
    assert Git().is_repo(tmp_path) is True


def test_is_repo_false_for_plain_dir(tmp_path):
    assert Git().is_repo(tmp_path) is False


def test_is_repo_false_when_git_is_a_file(tmp_path):
    (tmp_path / ".git").write_text("submodule pointer")
    assert Git().is_repo(tmp_path) is False


def test_clone_runs_expected_command(tmp_path):
    with patch("services.runtime.git.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        Git().clone("https://example.com/foo.git", tmp_path / "dest")
        args = mock_run.call_args[0][0]
        assert args[:2] == ["git", "clone"]
        assert "--depth" in args and args[args.index("--depth") + 1] == "1"
        assert args[-2:] == ["https://example.com/foo.git", str(tmp_path / "dest")]


def test_clone_omits_depth_when_zero(tmp_path):
    with patch("services.runtime.git.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        Git().clone("https://example.com/foo.git", tmp_path / "dest", depth=0)
        args = mock_run.call_args[0][0]
        assert "--depth" not in args


def test_clone_raises_giterror_on_non_zero(tmp_path):
    err = subprocess.CalledProcessError(128, ["git", "clone"], stderr="repo not found")
    with patch("services.runtime.git.subprocess.run", side_effect=err):
        with pytest.raises(GitError) as exc:
            Git().clone("https://example.com/missing.git", tmp_path / "dest")
        assert "repo not found" in str(exc.value)


def test_clone_raises_giterror_when_git_missing(tmp_path):
    with patch("services.runtime.git.subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(GitError) as exc:
            Git().clone("https://example.com/foo.git", tmp_path / "dest")
        assert "git binary not found" in str(exc.value)


def test_pull_ff_only_runs_in_repo_cwd(tmp_path):
    with patch("services.runtime.git.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        Git().pull_ff_only(tmp_path)
        assert mock_run.call_args[0][0] == ["git", "pull", "--ff-only"]
        assert mock_run.call_args.kwargs["cwd"] == str(tmp_path)


def test_pull_ff_only_raises_giterror_on_diverged_branch(tmp_path):
    err = subprocess.CalledProcessError(
        1, ["git", "pull"], stderr="fatal: Not possible to fast-forward"
    )
    with patch("services.runtime.git.subprocess.run", side_effect=err):
        with pytest.raises(GitError) as exc:
            Git().pull_ff_only(tmp_path)
        assert "fast-forward" in str(exc.value)
