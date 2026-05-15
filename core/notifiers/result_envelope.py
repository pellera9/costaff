"""Parser for sub-agent RESULT envelopes.

Sub-agents wrap their final reply in `[RESULT_START] ... [RESULT_END]`. The
content can be either:

1. **Structured** (preferred — opt-in per agent):
   ```
   [RESULT_START]
   status: ok
   summary: Generated PDF report from wine dataset.
   files:
     - /app/data/shared/costaff-agent-business-analysis/wine-report/report.pdf
     - /app/data/shared/costaff-agent-business-analysis/wine-report/chart1.png
   error_code: null
   [RESULT_END]
   ```

2. **Free-text** (legacy — Markdown bullets, regex-extracted paths):
   ```
   [RESULT_START]
   - **What was done**: ...
   - **Files**: /app/data/shared/...
   ...
   [RESULT_END]
   ```

`parse_result_envelope()` recognises both. When the structured form is
detected (presence of `status:` AND/OR `files:` key), `structured=True`
is set and the dispatcher / verifier can read `files` directly without
regex guessing. Otherwise `structured=False` and callers fall back to
their existing regex extraction.

Recognised `error_code` enum (informational — callers can switch on these):
  - TOOL_NOT_AVAILABLE   — required tool missing from this agent's toolset
  - INPUT_MISSING        — required input file/argument not present
  - OUTPUT_MISSING       — agent claimed an output but it's not on disk
  - WRONG_AGENT          — task was routed to the wrong specialist
  - TIMEOUT              — agent hit an internal timeout
  - PERMISSION_DENIED    — sandbox / OS denied the operation
  - UNKNOWN              — unclassified failure
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


_RESULT_BLOCK_RE = re.compile(
    r"\[RESULT_START\](.*?)\[RESULT_END\]",
    re.DOTALL,
)
_KV_LINE_RE = re.compile(r"^\s*([a-z_]+)\s*:\s*(.*)$", re.IGNORECASE)
_LIST_ITEM_RE = re.compile(r"^\s*-\s+(.+?)\s*$")


@dataclass
class ParsedEnvelope:
    """Result of parsing a sub-agent's reply.

    `structured` is the load-bearing flag: True means the dispatcher /
    verifier should trust `files`, `status`, `error_code` etc. directly;
    False means only `text` is populated and the caller should fall back
    to their legacy regex.
    """
    text: str                          # the raw inner block (or full raw if no markers)
    structured: bool = False
    status: Optional[str] = None       # "ok" / "failed" / None
    summary: Optional[str] = None
    files: list[str] = field(default_factory=list)
    error_code: Optional[str] = None
    error_message: Optional[str] = None


def _extract_inner_block(raw: str) -> str:
    """Return the content between [RESULT_START] and [RESULT_END], or the
    whole input if those markers are absent."""
    if not raw:
        return ""
    m = _RESULT_BLOCK_RE.search(raw)
    return m.group(1).strip() if m else raw.strip()


def parse_result_envelope(raw: str) -> ParsedEnvelope:
    """Parse a sub-agent reply into a ParsedEnvelope.

    Always succeeds — when the input is empty or unstructured, returns an
    envelope with `structured=False` and `text` populated. The caller
    decides whether to trust structured fields or fall back to regex.
    """
    if not raw:
        return ParsedEnvelope(text="")

    inner = _extract_inner_block(raw)
    if not inner:
        return ParsedEnvelope(text="")

    env = ParsedEnvelope(text=inner)

    # Two-pass parser. First pass: detect structured key-value lines and
    # whether we're inside a `files:` list. Anything that doesn't match a
    # key-value or list-item pattern is treated as prose (kept in `text`
    # via the inner block).
    in_files_list = False
    saw_status_key = False
    saw_files_key = False
    saw_error_key = False

    for line in inner.splitlines():
        stripped = line.strip()
        if not stripped:
            in_files_list = False
            continue

        # List item under `files:` block. Match BEFORE k:v so "- foo: bar"
        # is treated as a value, not a new key.
        if in_files_list:
            li = _LIST_ITEM_RE.match(line)
            if li:
                env.files.append(li.group(1).strip())
                continue
            # Non-list line → end of files block, fall through to k:v parse
            in_files_list = False

        kv = _KV_LINE_RE.match(line)
        if not kv:
            continue
        key = kv.group(1).lower()
        val = kv.group(2).strip()

        if key == "status":
            saw_status_key = True
            env.status = val.lower() or None
        elif key == "summary":
            env.summary = val or None
        elif key == "files":
            saw_files_key = True
            if val:
                # Inline form: `files: /a/b.csv, /c/d.pdf` (rare but handle it)
                for p in [x.strip() for x in val.split(",")]:
                    if p:
                        env.files.append(p)
            else:
                in_files_list = True
        elif key == "error_code":
            saw_error_key = True
            v = val.lower().strip()
            env.error_code = None if v in ("", "null", "none") else val
        elif key in ("error_message", "error"):
            env.error_message = val or None

    # The envelope is considered "structured" if it provided at least one
    # of the canonical keys. A free-text Markdown reply will have none.
    env.structured = saw_status_key or saw_files_key or saw_error_key

    return env


__all__ = ["ParsedEnvelope", "parse_result_envelope"]
