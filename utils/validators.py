"""Input validators for cron expressions and external A2A URLs."""
import re
from urllib.parse import urlparse


_CRON_PATTERN = re.compile(
    r'^(\*|[0-9*/,\-]+)\s+'    # minute
    r'(\*|[0-9*/,\-]+)\s+'     # hour
    r'(\*|[0-9?*/,\-L]+)\s+'   # day-of-month
    r'(\*|[0-9*/,\-]+)\s+'     # month
    r'(\*|[0-9?*/,\-L]+)$'     # day-of-week
)


def _validate_cron(cron: str) -> None:
    """Raises ValueError if the cron expression is not a valid 5-field format."""
    if not _CRON_PATTERN.match(cron.strip()):
        raise ValueError(
            f"Invalid cron expression: '{cron}'. "
            "Expected 5 fields: minute hour day-of-month month day-of-week"
        )


_BLOCKED_HOSTNAMES = {"localhost", "127.0.0.1", "0.0.0.0", "169.254.169.254", "::1", ""}


def _validate_a2a_url(url: str) -> None:
    """Raises ValueError if the URL is not a safe external http/https endpoint."""
    try:
        parsed = urlparse(url)
    except Exception:
        raise ValueError("Invalid URL format")
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must use http or https scheme")
    hostname = (parsed.hostname or "").lower()
    if hostname in _BLOCKED_HOSTNAMES:
        raise ValueError(f"URL hostname '{hostname}' is not allowed")
