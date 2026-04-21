"""
CoStaff CLI entry point.

setup.py entry point: costaff=costaff:app
"""
import sys
from pathlib import Path

# Resolve project root and ensure it is on sys.path before any package imports.
def _find_project_root() -> str:
    cwd = Path.cwd()
    if (cwd / "setup.py").exists() or (cwd / "costaff.py").exists():
        return str(cwd)
    return str(Path(__file__).resolve().parent)

_project_root = _find_project_root()
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from cli import app  # noqa: E402  (must come after sys.path is set)

if __name__ == "__main__":
    app()
