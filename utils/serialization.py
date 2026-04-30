"""Datetime / row serialization helpers used by the API and CLI."""
from datetime import datetime, timezone
from typing import Optional


def _dt_to_z(v) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        return v.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(v)


def _serialize_row(d: dict) -> dict:
    return {k: _dt_to_z(v) if isinstance(v, datetime) else v for k, v in d.items()}
