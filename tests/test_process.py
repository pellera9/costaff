"""Unit tests for services.runtime.process."""
import socket
import subprocess
import sys
import time

import pytest

from services.runtime import process as proc_mod
from services.runtime.process import _pids_on_port, kill_port


def _free_port() -> int:
    """Reserve and immediately release a port, returning the number."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_kill_port_returns_zero_when_nothing_listens():
    assert kill_port(_free_port()) == 0


def test_kill_port_terminates_listener():
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # Wait until it's actually listening (poll _pids_on_port directly).
        deadline = time.time() + 5
        while time.time() < deadline and not list(_pids_on_port(port)):
            time.sleep(0.1)
        assert list(_pids_on_port(port)), "server never started listening"

        killed = kill_port(port)
        assert killed >= 1

        # Confirm it actually went down.
        deadline = time.time() + 5
        while time.time() < deadline and list(_pids_on_port(port)):
            time.sleep(0.1)
        assert not list(_pids_on_port(port))
    finally:
        if server.poll() is None:
            server.terminate()
            server.wait(timeout=2)


def test_kill_port_returns_zero_when_psutil_missing(monkeypatch):
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def fake_import(name, *args, **kwargs):
        if name == "psutil":
            raise ImportError("psutil missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    assert kill_port(_free_port()) == 0
