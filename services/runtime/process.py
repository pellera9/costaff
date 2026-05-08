"""Host-process operations for the costaff CLI.

Wraps psutil so callers don't have to know about it (and so that
fallbacks for environments without psutil can live in one place rather
than be re-invented in CLI commands).
"""
from __future__ import annotations

import signal
from typing import Iterable


def kill_port(port: int) -> int:
    """Kill any local processes listening on `port`. Returns count killed.

    Silent on errors (port empty, permission denied, psutil missing) —
    callers treat this as best-effort cleanup.
    """
    try:
        import psutil
    except ImportError:
        return 0

    pids = _pids_on_port(port)
    killed = 0
    for pid in pids:
        try:
            psutil.Process(pid).send_signal(signal.SIGTERM)
            killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return killed


def _pids_on_port(port: int) -> Iterable[int]:
    """Iterate processes the current user owns and return any whose
    listening sockets bind to `port`.

    Avoids `psutil.net_connections()` because it requires root on macOS;
    per-process `connections()` works without privileges for the current
    user's processes — which covers the dashboard / CLI's needs.
    """
    import psutil

    seen: set[int] = set()
    for p in psutil.process_iter(["pid"]):
        try:
            for c in p.net_connections(kind="inet"):
                if c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN:
                    seen.add(p.info["pid"])
                    break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return seen
