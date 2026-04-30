"""Tests for services.auth — session token rotation + bearer verification.

`AuthManager` keeps token state on the class object, so tests must reset
it between cases. The `verify_token` dependency calls FastAPI's HTTPException
directly — we assert on that.
"""
import time

import pytest
from fastapi import HTTPException

from services.auth import AuthManager


@pytest.fixture(autouse=True)
def _reset_auth_state():
    """Every test starts with a clean class-level token cache."""
    AuthManager._session_token = ""
    AuthManager._token_expires = 0.0
    yield
    AuthManager._session_token = ""
    AuthManager._token_expires = 0.0


# ---------------------------------------------------------------------------
# hash_password
# ---------------------------------------------------------------------------

def test_hash_password_returns_hex_digest_and_salt():
    digest, salt = AuthManager.hash_password("hunter2")
    # SHA-256 hex is 64 chars
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)
    # Default salt is 16 hex chars (8 bytes)
    assert len(salt) == 16


def test_hash_password_is_stable_for_same_salt():
    a, _ = AuthManager.hash_password("hunter2", salt="abcd")
    b, _ = AuthManager.hash_password("hunter2", salt="abcd")
    assert a == b


def test_hash_password_differs_when_salt_changes():
    a, _ = AuthManager.hash_password("hunter2", salt="aaaa")
    b, _ = AuthManager.hash_password("hunter2", salt="bbbb")
    assert a != b


def test_hash_password_differs_when_password_changes():
    a, _ = AuthManager.hash_password("hunter2", salt="abcd")
    b, _ = AuthManager.hash_password("hunter3", salt="abcd")
    assert a != b


# ---------------------------------------------------------------------------
# rotate_token
# ---------------------------------------------------------------------------

def test_rotate_token_creates_new_token_and_sets_expiry():
    token = AuthManager.rotate_token()
    assert token == AuthManager._session_token
    assert len(token) == 32  # 16 bytes hex
    assert AuthManager._token_expires > time.time()


def test_rotate_token_replaces_previous_token():
    first = AuthManager.rotate_token()
    second = AuthManager.rotate_token()
    assert first != second
    assert AuthManager._session_token == second


# ---------------------------------------------------------------------------
# verify_token — must reject every invalid case
# ---------------------------------------------------------------------------

def test_verify_token_raises_when_no_session():
    with pytest.raises(HTTPException) as exc:
        AuthManager.verify_token(authorization="Bearer anything")
    assert exc.value.status_code == 401
    assert "Not logged in" in exc.value.detail


def test_verify_token_raises_when_expired():
    AuthManager._session_token = "abcd"
    AuthManager._token_expires = time.time() - 1  # expired 1s ago
    with pytest.raises(HTTPException) as exc:
        AuthManager.verify_token(authorization="Bearer abcd")
    assert exc.value.status_code == 401
    assert "Session expired" in exc.value.detail


def test_verify_token_raises_when_token_mismatched():
    AuthManager._session_token = "correct-token"
    AuthManager._token_expires = time.time() + 3600
    with pytest.raises(HTTPException) as exc:
        AuthManager.verify_token(authorization="Bearer wrong-token")
    assert exc.value.status_code == 401
    assert "Unauthorized" in exc.value.detail


def test_verify_token_raises_when_authorization_header_missing():
    AuthManager._session_token = "correct"
    AuthManager._token_expires = time.time() + 3600
    with pytest.raises(HTTPException) as exc:
        AuthManager.verify_token(authorization=None)
    assert exc.value.status_code == 401


def test_verify_token_raises_when_scheme_wrong():
    """`Basic <token>` or bare `<token>` must not pass — only `Bearer <token>`."""
    AuthManager._session_token = "abcd"
    AuthManager._token_expires = time.time() + 3600
    for bad in ("abcd", "Basic abcd", "bearer abcd"):  # last one is case-sensitive
        with pytest.raises(HTTPException) as exc:
            AuthManager.verify_token(authorization=bad)
        assert exc.value.status_code == 401


def test_verify_token_returns_true_when_valid():
    AuthManager._session_token = "valid-token"
    AuthManager._token_expires = time.time() + 3600
    assert AuthManager.verify_token(authorization="Bearer valid-token") is True


def test_rotate_then_verify_round_trip():
    token = AuthManager.rotate_token()
    assert AuthManager.verify_token(authorization=f"Bearer {token}") is True


# ---------------------------------------------------------------------------
# save_auth / get_auth — file-backed credential store
# ---------------------------------------------------------------------------

def test_save_and_get_auth_round_trip(tmp_path, monkeypatch):
    """save_auth writes to PATHS['auth']; get_auth reads it back."""
    auth_path = tmp_path / "auth.json"
    monkeypatch.setitem(__import__("services.auth", fromlist=["PATHS"]).PATHS, "auth", str(auth_path))

    AuthManager.save_auth("admin", "password123")
    stored = AuthManager.get_auth()

    assert stored is not None
    assert stored["username"] == "admin"
    assert "hashed" in stored and "salt" in stored
    # Hash must verify against the original password
    expected_hash, _ = AuthManager.hash_password("password123", salt=stored["salt"])
    assert stored["hashed"] == expected_hash


def test_get_auth_returns_none_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setitem(__import__("services.auth", fromlist=["PATHS"]).PATHS, "auth", str(tmp_path / "missing.json"))
    assert AuthManager.get_auth() is None


def test_get_auth_returns_none_on_corrupt_json(tmp_path, monkeypatch):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text("{not json at all")
    monkeypatch.setitem(__import__("services.auth", fromlist=["PATHS"]).PATHS, "auth", str(auth_path))
    # Should not raise — just return None so the dashboard prompts setup
    assert AuthManager.get_auth() is None
