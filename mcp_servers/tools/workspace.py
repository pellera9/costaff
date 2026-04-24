import os
import shutil
from pathlib import Path

from mcp_servers.core import mcp

_DATA_ROOT = Path(os.getenv("WORKSPACE_DIR", "/app/data"))


@mcp.tool()
def move_to_shared(src_path: str, overwrite: bool = False) -> str:
    """
    Copy a file or directory from an agent's private workspace to the shared workspace.
    src_path: absolute path inside /app/data (e.g. /app/data/costaff-agent-coding/report.pdf)
    The item is mirrored under /app/data/shared/ preserving its relative path after /app/data/.
    Use overwrite=True to replace an existing destination.
    """
    src = Path(src_path).resolve()
    data_root = _DATA_ROOT.resolve()

    if not str(src).startswith(str(data_root) + "/"):
        return f"[ERROR] src_path must be under {data_root}"
    if not src.exists():
        return f"[ERROR] Source not found: {src}"

    rel = src.relative_to(data_root)
    dst = data_root / "shared" / rel
    dst.parent.mkdir(parents=True, exist_ok=True)

    if dst.exists() and not overwrite:
        return f"[WARN] Destination already exists: {dst}. Use overwrite=True to replace."

    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)

    return f"[OK] Copied {src} → {dst}"
