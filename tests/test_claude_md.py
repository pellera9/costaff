"""CLAUDE.md drift test — backtick-wrapped paths must exist on disk.

Why: CLAUDE.md documents the repo for AI assistants. When code moves but the
docs don't, the AI gives bad advice. This test scans every CLAUDE.md under the
costaff repo and verifies that backtick-wrapped tokens that look like relative
paths actually resolve.

Heuristics (intentionally conservative — false negatives > false positives):
- Skip placeholders ("<X>"), globs ("*"), runtime paths ("~/", "/app/...").
- Skip absolute paths, command lines (contain space / "=" / "("), shell vars.
- Skip sections under headings marked as historical (e.g. "已消滅 / deprecated").
- Only accept tokens that have "/" AND either a known repo subdir prefix or a
  known file extension. Function/class names are not checked — too ambiguous.
- Resolution order: same dir as the .md file, repo root, workspace root
  (one level above repo, for cross-repo references like `skill/...`), then
  rglob suffix match (handles tree-structure paths quoted without their parent).

If this test fails, either the path moved (update CLAUDE.md) or the doc was
wrong from the start (update CLAUDE.md). Both are drift.
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = REPO_ROOT.parent

KNOWN_PREFIXES = (
    "services/", "core/", "agents/", "mcp_servers/", "server/", "cli/",
    "utils/", "tests/", "frontend/", "skills/", "instruction/",
    "models/", "sub_agents/", "mcp_toolsets/", "tools/", "executors/",
    "routers/", "commands/", "runtime/", "notifiers/", "skill/",
)
FILE_EXTENSIONS = (".py", ".md", ".json", ".yaml", ".yml", ".sh", ".html", ".sql", ".ini")
SKIP_SUBSTRINGS = ("<", "*", "~/", "/app/", "//", "${")
DEPRECATED_HEADING_KEYWORDS = ("已消滅", "已移除", "deprecated", "removed", "歷史記錄", "history")
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")


def _strip_trailing_punct(token: str) -> str:
    return token.rstrip(".,;:)】」")


def _looks_like_path(token: str) -> bool:
    if any(s in token for s in SKIP_SUBSTRINGS):
        return False
    if token.startswith("/"):
        return False
    if any(c in token for c in " =()|"):
        return False
    if "/" not in token:
        return False
    if any(token.startswith(p) for p in KNOWN_PREFIXES):
        return True
    if token.endswith(FILE_EXTENSIONS):
        return True
    return False


def _strip_deprecated_sections(text: str) -> str:
    """Drop lines under any heading whose title flags it as historical."""
    out: list[str] = []
    in_skip = False
    for line in text.split("\n"):
        if line.startswith(("# ", "## ", "### ", "#### ")):
            lower = line.lower()
            in_skip = any(k.lower() in lower for k in DEPRECATED_HEADING_KEYWORDS)
        if not in_skip:
            out.append(line)
    return "\n".join(out)


def _resolve(token: str, md_file: Path) -> bool:
    for base in (md_file.parent, REPO_ROOT, WORKSPACE_ROOT):
        if (base / token).exists():
            return True
    # Tree-structure paths: docs often quote a leaf path without its parent dir.
    return any(REPO_ROOT.rglob(token))


def _claude_md_files() -> list[Path]:
    return sorted(REPO_ROOT.rglob("CLAUDE.md"))


@pytest.mark.parametrize(
    "md_file",
    _claude_md_files(),
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_claude_md_paths_exist(md_file: Path):
    text = _strip_deprecated_sections(md_file.read_text(encoding="utf-8"))
    missing: list[str] = []
    for raw in INLINE_CODE_RE.findall(text):
        token = _strip_trailing_punct(raw.strip())
        if not _looks_like_path(token):
            continue
        if not _resolve(token, md_file):
            missing.append(token)

    assert not missing, (
        f"\n{md_file.relative_to(REPO_ROOT)} references paths that don't exist:\n  - "
        + "\n  - ".join(sorted(set(missing)))
    )
