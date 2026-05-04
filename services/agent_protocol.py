"""Validate `costaff.agent.json` manifests against the CoStaff Agent Protocol.

Two validation modes:

- **Lenient** (default): require `protocol_version` to exist and be a
  MAJOR.MINOR string within a supported MAJOR. Used by `costaff agent add`
  so that legacy manifests (pre-protocol-formalisation) only emit a
  warning rather than block deployment.
- **Strict**: lenient checks PLUS full JSON Schema validation against
  `costaff/docs/schemas/costaff.agent.json.schema.json`. Used by
  `costaff agent add --strict` and recommended for new agents.
"""
import json
from pathlib import Path
from typing import Any

# Major versions this CoStaff release supports. Add new entries when shipping
# a new MAJOR; legacy entries remain so old agents keep working.
SUPPORTED_PROTOCOL_MAJORS: tuple[int, ...] = (1,)

# Highest minor version this CoStaff release implements within each major.
# Agents declaring a higher minor still load (forward-compat within MAJOR)
# but trigger a warning so the operator knows some manifest features may
# not be honoured.
LATEST_PROTOCOL_MINOR: dict[int, int] = {1: 0}

_SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "schemas"
    / "costaff.agent.json.schema.json"
)


class ProtocolError(ValueError):
    """Raised when a manifest violates the CoStaff Agent Protocol."""


def parse_protocol_version(value: Any) -> tuple[int, int]:
    """Parse a `MAJOR.MINOR` string into a (major, minor) tuple."""
    if not isinstance(value, str):
        raise ProtocolError(
            f"protocol_version must be a string, got {type(value).__name__}"
        )
    parts = value.split(".")
    if len(parts) != 2:
        raise ProtocolError(
            f'protocol_version must be MAJOR.MINOR, got "{value}"'
        )
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        raise ProtocolError(
            f'protocol_version components must be integers, got "{value}"'
        )


def supported(major: int, minor: int) -> bool:
    if major not in SUPPORTED_PROTOCOL_MAJORS:
        return False
    return True


def newer_than_implemented(major: int, minor: int) -> bool:
    """True if a supported MAJOR but a higher MINOR than this release knows."""
    if major not in LATEST_PROTOCOL_MINOR:
        return False
    return minor > LATEST_PROTOCOL_MINOR[major]


def validate_manifest(manifest: dict, strict: bool = False) -> list[str]:
    """Validate a manifest. Returns a list of warning messages (lenient
    issues that did not block). Raises ProtocolError on hard failures.

    Hard failures:
    - missing `protocol_version` (in strict mode)
    - unparseable `protocol_version`
    - unsupported MAJOR
    - JSON Schema violation (strict mode only)
    """
    warnings: list[str] = []

    pv = manifest.get("protocol_version")
    if pv is None:
        if strict:
            raise ProtocolError(
                "protocol_version is required (set protocol_version: \"1.0\")"
            )
        warnings.append(
            "manifest has no protocol_version — assuming 1.0; add "
            'protocol_version: "1.0" to silence this warning'
        )
    else:
        major, minor = parse_protocol_version(pv)
        if not supported(major, minor):
            raise ProtocolError(
                f"protocol_version {pv} (major {major}) is not supported "
                f"by this CoStaff release (supports majors "
                f"{', '.join(str(m) for m in SUPPORTED_PROTOCOL_MAJORS)})"
            )
        if newer_than_implemented(major, minor):
            warnings.append(
                f"manifest declares protocol_version {pv}; this CoStaff "
                f"release only implements up to "
                f"{major}.{LATEST_PROTOCOL_MINOR[major]} — features "
                f"introduced in newer minors may not be honoured"
            )

    if strict:
        _strict_schema_check(manifest)

    return warnings


def _strict_schema_check(manifest: dict) -> None:
    """Run JSON Schema validation. Imported lazily so lenient validation
    does not require `jsonschema` to be installed."""
    try:
        import jsonschema
    except ImportError as e:
        raise ProtocolError(
            "strict validation requires the `jsonschema` package; "
            "install with `pip install jsonschema`"
        ) from e

    if not _SCHEMA_PATH.exists():
        raise ProtocolError(
            f"manifest schema not found at {_SCHEMA_PATH}; this CoStaff "
            "install may be incomplete"
        )

    schema = json.loads(_SCHEMA_PATH.read_text())
    try:
        jsonschema.validate(manifest, schema)
    except jsonschema.ValidationError as e:
        # Build a friendly path like "env_required[0]" instead of deque(...)
        path = ".".join(str(p) for p in e.absolute_path) or "<root>"
        raise ProtocolError(
            f"manifest fails schema at {path}: {e.message}"
        ) from e
