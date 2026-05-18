"""Shared helpers used across multiple MCP tool modules."""
import logging

from core import models

logger = logging.getLogger(__name__)


def require_within_license(db) -> "str | None":
    """Return a denial message if the system is on effective OSS limits
    (license expired / tampered / wrong-machine / absent) AND current
    usage exceeds OSS limits; else None.

    Decisions A+B+C: a degraded license degrades to OSS and keeps serving,
    but real work (creating/executing tasks) is blocked while usage is over
    OSS limits. Usage counts mirror the creation-time check_*_limit
    semantics (enabled external agents / UserContact rows / SkillConfig
    rows) so the runtime gate and the creation gate agree.
    """
    from core.license import LicenseManager
    try:
        from services.config import ConfigManager
        conf = ConfigManager.get_config()
        agents = sum(
            1 for v in conf.get("external_agents", {}).values()
            if v.get("enabled", True)
        )
    except Exception:
        agents = 0
    try:
        users = db.query(models.UserContact).count()
    except Exception:
        users = 0
    try:
        skills = db.query(models.SkillConfig).count()
    except Exception:
        skills = 0
    msg = LicenseManager.usage_gate(
        {"agents": agents, "users": users, "skills": skills}
    )
    if msg:
        logger.warning("License gate blocked work: %s", msg)
    return msg


def require_approved(user_id: str, db) -> "str | None":
    """Return a denial message if the user is unapproved, else None.

    Users with no identity_maps record (admin / system) are always
    granted access — the gate only applies to users who have signed
    in but have not been approved by an operator.
    """
    mapping = db.query(models.IdentityMap).filter(
        models.IdentityMap.hashed_id == user_id
    ).first()
    if mapping is not None and not mapping.is_approved:
        return "Access denied: your account has not been approved. Please contact an administrator."
    return None
