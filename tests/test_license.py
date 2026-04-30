"""Tests for core.license — Ed25519-signed license verification.

The production `_PUBLIC_KEY_B64` is hardcoded; for signature-success tests
we monkeypatch it to a test-only public key whose private key the test
holds, then sign known canonical payloads.
"""
import base64
from datetime import date, timedelta

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import license as license_mod
from core.license import (
    LicenseInfo,
    LicenseManager,
    OSS_LIMITS,
    _canonical,
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_canonical_is_deterministic():
    data = {
        "license_id": "L-1",
        "plan": "team",
        "issued_to": "Acme Co.",
        "contact_phone": "555-1234",
        "issued_at": "2026-01-01",
        "expires_at": "2027-01-01",
        "machine_id": "abc",
        "limits": {"max_agents": 5, "max_users": 10, "max_skills": 50},
    }
    assert _canonical(data) == _canonical(dict(data))


def test_canonical_excludes_signature_field():
    """Different signature values must produce identical canonical bytes —
    otherwise verification is impossible (the signature signs itself)."""
    base = {"license_id": "L-1", "plan": "team", "issued_to": "x"}
    a = dict(base, signature="aaaa")
    b = dict(base, signature="bbbb")
    assert _canonical(a) == _canonical(b)


def test_canonical_includes_default_limits_when_absent():
    """All limit fields are always present (defaulted to 0) so the signed
    payload shape is stable across plans."""
    out = _canonical({"plan": "oss"}).decode()
    assert "max_agents=0" in out
    assert "max_users=0" in out
    assert "max_skills=0" in out


# ---------------------------------------------------------------------------
# LicenseInfo properties
# ---------------------------------------------------------------------------

def test_license_info_not_expired_when_no_expiry():
    info = LicenseInfo(plan="team", issued_to="x", expires_at=None, limits={})
    assert info.is_expired is False


def test_license_info_expired_when_past():
    yesterday = date.today() - timedelta(days=1)
    info = LicenseInfo(plan="team", issued_to="x", expires_at=yesterday, limits={})
    assert info.is_expired is True


def test_license_info_not_expired_when_future():
    tomorrow = date.today() + timedelta(days=1)
    info = LicenseInfo(plan="team", issued_to="x", expires_at=tomorrow, limits={})
    assert info.is_expired is False


def test_license_info_falls_back_to_oss_limits():
    """When `limits` dict is missing a key, the property must fall back to
    OSS_LIMITS rather than raising or returning None."""
    info = LicenseInfo(plan="custom", issued_to="x", expires_at=None, limits={})
    assert info.max_agents == OSS_LIMITS["max_agents"]
    assert info.max_users == OSS_LIMITS["max_users"]
    assert info.max_skills == OSS_LIMITS["max_skills"]


def test_license_info_uses_provided_limits():
    info = LicenseInfo(
        plan="team", issued_to="x", expires_at=None,
        limits={"max_agents": 99, "max_users": 100, "max_skills": 1000},
    )
    assert info.max_agents == 99
    assert info.max_users == 100
    assert info.max_skills == 1000


# ---------------------------------------------------------------------------
# LicenseManager.load — failure paths (don't need a valid signature)
# ---------------------------------------------------------------------------

def test_load_returns_none_when_file_missing(tmp_path, monkeypatch):
    LicenseManager._license = None  # reset class state
    missing = tmp_path / "no_such_license.yaml"
    monkeypatch.setenv("COSTAFF_LICENSE_PATH", str(missing))
    assert LicenseManager.load() is None


def test_load_raises_when_signature_missing(tmp_path, monkeypatch):
    LicenseManager._license = None
    path = tmp_path / "license.yaml"
    path.write_text(yaml.safe_dump({"license": {"plan": "team"}}))
    monkeypatch.setenv("COSTAFF_LICENSE_PATH", str(path))
    with pytest.raises(ValueError, match="missing a signature"):
        LicenseManager.load()


def test_load_raises_on_invalid_signature(tmp_path, monkeypatch):
    """A wrong signature must raise — not silently fall back to OSS."""
    LicenseManager._license = None
    path = tmp_path / "license.yaml"
    bogus_sig = base64.b64encode(b"\x00" * 64).decode()
    path.write_text(yaml.safe_dump({"license": {
        "plan": "team", "issued_to": "evil", "signature": bogus_sig,
    }}))
    monkeypatch.setenv("COSTAFF_LICENSE_PATH", str(path))
    with pytest.raises(ValueError, match="signature is invalid|verification failed"):
        LicenseManager.load()


# ---------------------------------------------------------------------------
# LicenseManager.load — success path with a test keypair
# ---------------------------------------------------------------------------

def _write_signed_license(path, data, private_key):
    """Sign `data` with `private_key` and write the YAML license file."""
    canonical = _canonical(data)
    sig = private_key.sign(canonical)
    full = dict(data, signature=base64.b64encode(sig).decode())
    path.write_text(yaml.safe_dump({"license": full}))


@pytest.fixture
def test_keypair(monkeypatch):
    """Generate a fresh Ed25519 keypair and patch the module's public key
    so the test's signatures verify."""
    priv = Ed25519PrivateKey.generate()
    pub_bytes = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    pub_b64 = base64.b64encode(pub_bytes).decode()
    monkeypatch.setattr(license_mod, "_PUBLIC_KEY_B64", pub_b64)
    return priv


def test_load_succeeds_with_valid_signature(tmp_path, monkeypatch, test_keypair):
    LicenseManager._license = None
    path = tmp_path / "license.yaml"
    future = (date.today() + timedelta(days=30)).isoformat()
    _write_signed_license(path, {
        "license_id": "L-001",
        "plan": "team",
        "issued_to": "TestCo",
        "contact_phone": "",
        "issued_at": "2026-01-01",
        "expires_at": future,
        "machine_id": "",
        "limits": {"max_agents": 5, "max_users": 10, "max_skills": 50},
    }, test_keypair)
    monkeypatch.setenv("COSTAFF_LICENSE_PATH", str(path))
    info = LicenseManager.load()
    assert info is not None
    assert info.plan == "team"
    assert info.issued_to == "TestCo"
    assert info.max_agents == 5


def test_load_raises_when_license_expired(tmp_path, monkeypatch, test_keypair):
    LicenseManager._license = None
    path = tmp_path / "license.yaml"
    past = (date.today() - timedelta(days=1)).isoformat()
    _write_signed_license(path, {
        "license_id": "L-old",
        "plan": "team",
        "issued_to": "TestCo",
        "expires_at": past,
        "machine_id": "",
        "limits": {"max_agents": 5, "max_users": 10, "max_skills": 50},
    }, test_keypair)
    monkeypatch.setenv("COSTAFF_LICENSE_PATH", str(path))
    with pytest.raises(ValueError, match="License expired"):
        LicenseManager.load()


def test_load_raises_on_machine_id_mismatch(tmp_path, monkeypatch, test_keypair):
    LicenseManager._license = None
    path = tmp_path / "license.yaml"
    _write_signed_license(path, {
        "license_id": "L-mac",
        "plan": "team",
        "issued_to": "TestCo",
        "expires_at": (date.today() + timedelta(days=30)).isoformat(),
        "machine_id": "some-other-machine",
        "limits": {"max_agents": 5, "max_users": 10, "max_skills": 50},
    }, test_keypair)
    monkeypatch.setenv("COSTAFF_LICENSE_PATH", str(path))
    monkeypatch.setattr(license_mod, "get_machine_id", lambda: "not-the-licensed-one")
    with pytest.raises(ValueError, match="bound to a different machine"):
        LicenseManager.load()


# ---------------------------------------------------------------------------
# LicenseManager.get / check_*_limit
# ---------------------------------------------------------------------------

def test_get_returns_oss_defaults_when_no_license_loaded():
    LicenseManager._license = None
    info = LicenseManager.get()
    assert info.plan == "oss"
    assert info.max_agents == OSS_LIMITS["max_agents"]
    assert info.max_users == OSS_LIMITS["max_users"]
    assert info.max_skills == OSS_LIMITS["max_skills"]


def test_check_agent_limit_allows_under_limit():
    LicenseManager._license = None  # OSS limit is 1
    LicenseManager.check_agent_limit(0)  # should not raise


def test_check_agent_limit_raises_at_limit():
    LicenseManager._license = None  # OSS limit is 1
    with pytest.raises(ValueError, match="Agent limit reached"):
        LicenseManager.check_agent_limit(1)


def test_check_user_limit_uses_db_count(db_session):
    """check_user_limit queries UserContact count from the supplied session."""
    from core.models import UserContact
    LicenseManager._license = None  # OSS limit is 1
    # Empty DB → under limit, should pass
    LicenseManager.check_user_limit(db_session)
    # Insert one user → at limit, should raise
    db_session.add(UserContact(user_id="u1", chinese_name="王小明"))
    db_session.commit()
    with pytest.raises(ValueError, match="User limit reached"):
        LicenseManager.check_user_limit(db_session)


def test_check_skill_limit_uses_db_count(db_session):
    from core.models import SkillConfig
    LicenseManager._license = None  # OSS limit is 10
    # 9 skills — still under
    for i in range(9):
        db_session.add(SkillConfig(id=f"sk{i}", name=f"skill-{i}", user_id="u1"))
    db_session.commit()
    LicenseManager.check_skill_limit(db_session)  # under limit
    # Add the 10th — now at limit
    db_session.add(SkillConfig(id="sk9", name="skill-9", user_id="u1"))
    db_session.commit()
    with pytest.raises(ValueError, match="Skill limit reached"):
        LicenseManager.check_skill_limit(db_session)
