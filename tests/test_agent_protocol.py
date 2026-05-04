"""Tests for services.agent_protocol — manifest validation."""
import pytest

from services.agent_protocol import (
    LATEST_PROTOCOL_MINOR,
    ProtocolError,
    SUPPORTED_PROTOCOL_MAJORS,
    parse_protocol_version,
    validate_manifest,
)


_MINIMAL_VALID = {
    "protocol_version": "1.0",
    "name": "costaff-agent-example",
    "version": "0.1.0",
    "description": "Example agent.",
    "a2a_service": "agent-example",
    "port": 8081,
    "health_path": "/.well-known/agent-card.json",
}


# ---------------------------------------------------------------------------
# parse_protocol_version
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("1.0", (1, 0)),
    ("1.5", (1, 5)),
    ("2.0", (2, 0)),
    ("10.20", (10, 20)),
])
def test_parse_protocol_version_ok(value, expected):
    assert parse_protocol_version(value) == expected


@pytest.mark.parametrize("value", [
    "1",          # missing minor
    "1.0.0",      # has patch
    "1.x",        # non-int minor
    "v1.0",       # leading "v"
    "",           # empty
])
def test_parse_protocol_version_rejects_bad_format(value):
    with pytest.raises(ProtocolError):
        parse_protocol_version(value)


def test_parse_protocol_version_rejects_non_string():
    with pytest.raises(ProtocolError):
        parse_protocol_version(1.0)


# ---------------------------------------------------------------------------
# validate_manifest — lenient mode (default)
# ---------------------------------------------------------------------------

def test_lenient_accepts_valid_manifest():
    assert validate_manifest(_MINIMAL_VALID) == []


def test_lenient_warns_on_missing_protocol_version():
    bad = {k: v for k, v in _MINIMAL_VALID.items() if k != "protocol_version"}
    warnings = validate_manifest(bad)
    assert any("no protocol_version" in w for w in warnings)


def test_lenient_warns_on_newer_minor_within_supported_major():
    future = dict(_MINIMAL_VALID, protocol_version="1.99")
    warnings = validate_manifest(future)
    assert any("only implements up to" in w for w in warnings)


def test_lenient_rejects_unsupported_major():
    future = dict(_MINIMAL_VALID, protocol_version="99.0")
    with pytest.raises(ProtocolError):
        validate_manifest(future)


def test_lenient_rejects_unparseable_protocol_version():
    bad = dict(_MINIMAL_VALID, protocol_version="not-a-version")
    with pytest.raises(ProtocolError):
        validate_manifest(bad)


# ---------------------------------------------------------------------------
# validate_manifest — strict mode
# ---------------------------------------------------------------------------

def test_strict_accepts_valid_manifest():
    assert validate_manifest(_MINIMAL_VALID, strict=True) == []


def test_strict_rejects_missing_protocol_version():
    bad = {k: v for k, v in _MINIMAL_VALID.items() if k != "protocol_version"}
    with pytest.raises(ProtocolError, match="protocol_version is required"):
        validate_manifest(bad, strict=True)


def test_strict_rejects_missing_required_field():
    bad = {k: v for k, v in _MINIMAL_VALID.items() if k != "a2a_service"}
    with pytest.raises(ProtocolError, match="schema"):
        validate_manifest(bad, strict=True)


def test_strict_rejects_wrong_field_type():
    bad = dict(_MINIMAL_VALID, port="not-a-number")
    with pytest.raises(ProtocolError, match="schema"):
        validate_manifest(bad, strict=True)


def test_strict_rejects_bad_name_pattern():
    bad = dict(_MINIMAL_VALID, name="my-agent")  # missing costaff-agent- prefix
    with pytest.raises(ProtocolError, match="schema"):
        validate_manifest(bad, strict=True)


def test_strict_rejects_wrong_health_path():
    bad = dict(_MINIMAL_VALID, health_path="/health")
    with pytest.raises(ProtocolError, match="schema"):
        validate_manifest(bad, strict=True)


def test_strict_rejects_extra_unknown_top_level_field():
    bad = dict(_MINIMAL_VALID, undeclared_field="value")
    with pytest.raises(ProtocolError, match="schema"):
        validate_manifest(bad, strict=True)


def test_strict_accepts_extra_x_prefixed_field():
    """The x_ namespace is reserved for forward-compat extensions."""
    extended = dict(_MINIMAL_VALID, x_my_extension={"any": "value"})
    assert validate_manifest(extended, strict=True) == []


def test_strict_requires_mcp_env_var_when_mcp_configurable():
    bad = dict(_MINIMAL_VALID, mcp_configurable=True)  # no mcp_env_var
    with pytest.raises(ProtocolError, match="schema"):
        validate_manifest(bad, strict=True)


def test_strict_accepts_mcp_configurable_with_env_var():
    ok = dict(
        _MINIMAL_VALID,
        mcp_configurable=True,
        mcp_env_var="EXAMPLE_AGENT_MCP_URLS",
    )
    assert validate_manifest(ok, strict=True) == []


# ---------------------------------------------------------------------------
# Module-level constants — sanity
# ---------------------------------------------------------------------------

def test_supported_majors_non_empty():
    assert SUPPORTED_PROTOCOL_MAJORS, "must support at least one major"


def test_latest_minor_covers_every_supported_major():
    for major in SUPPORTED_PROTOCOL_MAJORS:
        assert major in LATEST_PROTOCOL_MINOR
