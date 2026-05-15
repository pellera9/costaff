"""Tests for the structured RESULT envelope parser.

The parser handles two formats:
1. Structured key-value (preferred — opt-in per sub-agent)
2. Free-text Markdown bullets (legacy — every existing sub-agent today)

Contract under test:
- A structured envelope parses cleanly with status/summary/files/error_code.
- A free-text envelope returns structured=False and text-only; callers
  fall back to regex.
- Empty / malformed input returns a safe empty envelope.
- The list under `files:` is robust to leading whitespace, trailing
  whitespace, and inline comma-separated form.
"""
from core.notifiers.result_envelope import (
    ParsedEnvelope,
    parse_result_envelope,
)


def test_empty_input_returns_empty_envelope():
    env = parse_result_envelope("")
    assert env.text == ""
    assert env.structured is False
    assert env.files == []
    assert env.status is None


def test_none_input_returns_empty_envelope():
    env = parse_result_envelope(None)  # type: ignore[arg-type]
    assert env.text == ""
    assert env.structured is False


def test_structured_happy_path():
    raw = """[RESULT_START]
status: ok
summary: Generated PDF report from wine dataset, 5 charts, 4 pages.
files:
  - /app/data/shared/costaff-agent-business-analysis/wine-report/wine_report.pdf
  - /app/data/shared/costaff-agent-business-analysis/wine-report/chart1.png
error_code: null
[RESULT_END]"""
    env = parse_result_envelope(raw)
    assert env.structured is True
    assert env.status == "ok"
    assert env.summary.startswith("Generated PDF report")
    assert env.files == [
        "/app/data/shared/costaff-agent-business-analysis/wine-report/wine_report.pdf",
        "/app/data/shared/costaff-agent-business-analysis/wine-report/chart1.png",
    ]
    assert env.error_code is None


def test_structured_failure_path():
    raw = """[RESULT_START]
status: failed
summary: Could not produce PDF — required font missing in container.
files:
error_code: TOOL_NOT_AVAILABLE
error_message: weasyprint cannot find Noto CJK font; install fonts-noto-cjk
[RESULT_END]"""
    env = parse_result_envelope(raw)
    assert env.structured is True
    assert env.status == "failed"
    assert env.files == []
    assert env.error_code == "TOOL_NOT_AVAILABLE"
    assert env.error_message.startswith("weasyprint")


def test_legacy_freetext_returns_unstructured():
    """Today's actual format used by every shipped sub-agent: Markdown
    bullets inside the RESULT block. Parser must NOT treat this as
    structured (otherwise the caller would think `files` is empty and
    miss the paths buried in the prose)."""
    raw = """[RESULT_START]
- **What was done**: Loaded wine dataset and ran EDA.
- **Files created**: /app/data/shared/costaff-agent-coding/wine-eda/results.json
- **Test results**: 12 tests passed.
[RESULT_END]"""
    env = parse_result_envelope(raw)
    assert env.structured is False, \
        "Legacy free-text format must NOT be flagged as structured"
    assert env.files == []
    # The inner block is still captured so callers can run regex on it
    assert "wine-eda/results.json" in env.text


def test_inline_comma_separated_files():
    raw = """[RESULT_START]
status: ok
files: /app/data/shared/a.csv, /app/data/shared/b.pdf
[RESULT_END]"""
    env = parse_result_envelope(raw)
    assert env.structured is True
    assert env.files == [
        "/app/data/shared/a.csv",
        "/app/data/shared/b.pdf",
    ]


def test_files_list_with_messy_whitespace():
    raw = """[RESULT_START]
status: ok
files:
   -   /app/data/shared/a.csv
   -    /app/data/shared/b.pdf
[RESULT_END]"""
    env = parse_result_envelope(raw)
    assert env.files == [
        "/app/data/shared/a.csv",
        "/app/data/shared/b.pdf",
    ]


def test_blank_line_terminates_files_block():
    """A blank line ends the files list — subsequent k:v pairs are not
    treated as filenames."""
    raw = """[RESULT_START]
status: ok
files:
  - /app/data/shared/a.csv

error_code: null
[RESULT_END]"""
    env = parse_result_envelope(raw)
    assert env.files == ["/app/data/shared/a.csv"]
    assert env.error_code is None


def test_only_status_present_still_structured():
    """A minimal valid envelope with just status:ok is structured."""
    raw = """[RESULT_START]
status: ok
summary: All done.
[RESULT_END]"""
    env = parse_result_envelope(raw)
    assert env.structured is True
    assert env.status == "ok"
    assert env.files == []


def test_missing_result_markers_treated_as_raw():
    """If [RESULT_START] markers are absent, parse the whole input as the
    inner block. Useful when callers pass already-extracted content."""
    raw = "status: ok\nfiles:\n  - /app/data/shared/x.csv"
    env = parse_result_envelope(raw)
    assert env.structured is True
    assert env.files == ["/app/data/shared/x.csv"]


def test_unrecognised_keys_are_ignored():
    """Extra fields the parser doesn't know about should not crash or
    pollute the canonical fields."""
    raw = """[RESULT_START]
status: ok
files:
  - /app/data/shared/x.csv
elapsed_seconds: 12.4
notes: this is a note
[RESULT_END]"""
    env = parse_result_envelope(raw)
    assert env.structured is True
    assert env.files == ["/app/data/shared/x.csv"]
