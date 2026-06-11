"""Tests for the channel-specific format adapters in core.notifiers.formatters.

Three converters, one rule each:

- md_to_telegram_html: full Markdown -> HTML subset (Telegram parse_mode=HTML)
- md_to_discord:       passthrough that only strips the result envelope
                       (Discord renders Markdown natively, since 2023)
- md_to_plain:         strips ALL Markdown for transports that render none
                       (LINE text messages)

Locks the behaviour added 2026-05-22 after specialist completion
comments produced by build_task_spec ('## task complete' /
'### acceptance' / '- bullet') were observed arriving in Telegram
unconverted because parse_mode=HTML does NOT parse Markdown.
"""
from core.notifiers.formatters import (
    md_to_discord,
    md_to_plain,
    md_to_telegram_html,
    strip_result_envelope,
)


# -------------------------------------------------------- envelope marker


def test_strip_envelope_markers():
    assert strip_result_envelope("[RESULT_START]\nhello\n[RESULT_END]") == "hello"
    assert strip_result_envelope("[RESULT_START] body [RESULT_END]") == "body"
    assert strip_result_envelope("no markers here") == "no markers here"


def test_strip_handles_empty_and_none():
    assert strip_result_envelope("") == ""
    assert strip_result_envelope(None) is None


# -------------------------------------------------------- Telegram (HTML)


def test_telegram_strips_result_envelope():
    assert md_to_telegram_html("[RESULT_START]\nhello\n[RESULT_END]") == "hello"


def test_telegram_headings_become_bold():
    assert md_to_telegram_html("## Done") == "<b>Done</b>"
    assert md_to_telegram_html("### Acceptance") == "<b>Acceptance</b>"
    assert md_to_telegram_html("# Top heading") == "<b>Top heading</b>"


def test_telegram_inline_bold():
    assert md_to_telegram_html("**Important** info") == "<b>Important</b> info"


def test_telegram_inline_code_and_paths():
    out = md_to_telegram_html("File at `/app/data/foo.csv`")
    assert out == "File at <code>/app/data/foo.csv</code>"


def test_telegram_fenced_code_block():
    src = "Note:\n```python\ndef f():\n    pass\n```\nEnd."
    out = md_to_telegram_html(src)
    assert "<pre>def f():\n    pass</pre>" in out
    assert "End." in out
    assert "```" not in out


def test_telegram_bullets_become_dot():
    assert md_to_telegram_html("- Item") == "• Item"
    assert md_to_telegram_html("  - nested") == "  • nested"


def test_telegram_full_completion_block():
    src = (
        "## Task Complete\n"
        "\n"
        "### Use Cases\n"
        "- **Data**: 100 rows.\n"
        "\n"
        "### Acceptance\n"
        "- **File at** `/app/data/foo.csv`: ok.\n"
        "\n"
        "### Output\n"
        "- Data file: `/app/data/foo.csv`\n"
    )
    out = md_to_telegram_html(src)
    assert "<b>Task Complete</b>" in out
    assert "<b>Use Cases</b>" in out
    assert "<b>Acceptance</b>" in out
    assert "<b>Output</b>" in out
    assert "<b>Data</b>" in out
    assert "<b>File at</b>" in out
    assert "<code>/app/data/foo.csv</code>" in out
    assert "• Data file" in out
    assert "##" not in out
    assert "**" not in out


def test_telegram_idempotent_on_html():
    src = "<b>Heading</b>\n<code>/app/data/x</code>\n• item"
    assert md_to_telegram_html(src) == src


def test_telegram_preserves_paths_with_underscores():
    src = "Path: `/app/data/shared/costaff_agent/trig_data.csv`"
    out = md_to_telegram_html(src)
    assert "costaff_agent" in out
    assert "<i>" not in out


def test_telegram_empty_and_none():
    assert md_to_telegram_html("") == ""
    assert md_to_telegram_html(None) is None


# -------------------------------------------------------- Discord (native MD)


def test_discord_passes_through_markdown():
    # Discord renders headings, bold, code natively - only the envelope
    # markers should disappear.
    src = "## Heading\n**bold** `code` - bullet"
    assert md_to_discord(src) == "## Heading\n**bold** `code` - bullet"


def test_discord_strips_only_envelope():
    src = "[RESULT_START]\n## Heading\n**bold**\n[RESULT_END]"
    assert md_to_discord(src) == "## Heading\n**bold**"


def test_discord_empty_and_none():
    assert md_to_discord("") == ""
    assert md_to_discord(None) is None


# -------------------------------------------------------- LINE / plain text


def test_plain_strips_headings():
    assert md_to_plain("## Done") == "Done"
    assert md_to_plain("### Acceptance") == "Acceptance"


def test_plain_strips_bold():
    assert md_to_plain("**Important** info") == "Important info"


def test_plain_strips_inline_code():
    assert md_to_plain("File at `/app/data/foo.csv`") == "File at /app/data/foo.csv"


def test_plain_strips_fenced_code():
    src = "```python\ndef f(): pass\n```"
    assert md_to_plain(src) == "def f(): pass"


def test_plain_bullets_become_dot():
    assert md_to_plain("- File") == "• File"


def test_plain_links_become_text_paren_url():
    src = "see [GitHub](https://github.com/costaff-ai)"
    assert md_to_plain(src) == "see GitHub (https://github.com/costaff-ai)"


def test_plain_full_completion_block():
    src = (
        "[RESULT_START]\n"
        "## Task Complete\n"
        "### Output\n"
        "- Data file: `/app/data/foo.csv`\n"
        "[RESULT_END]"
    )
    out = md_to_plain(src)
    assert "Task Complete" in out
    assert "Output" in out
    assert "• Data file: /app/data/foo.csv" in out
    for sigil in ("[RESULT_START]", "[RESULT_END]", "##", "**", "`"):
        assert sigil not in out


def test_plain_preserves_underscored_paths():
    src = "Path: `/app/data/shared/costaff_agent/trig_data.csv`"
    assert md_to_plain(src) == "Path: /app/data/shared/costaff_agent/trig_data.csv"


def test_plain_empty_and_none():
    assert md_to_plain("") == ""
    assert md_to_plain(None) is None


# -------------------------------------------------------- Slack (mrkdwn)


def test_slack_bold_and_heading_use_single_asterisk():
    from core.notifiers.formatters import md_to_slack
    out = md_to_slack("## Title\n**bold** text")
    assert "*Title*" in out
    assert "*bold* text" in out
    assert "**" not in out


def test_slack_links_become_angle_form():
    from core.notifiers.formatters import md_to_slack
    assert md_to_slack("see [docs](https://x.y/z)") == "see <https://x.y/z|docs>"


def test_slack_code_content_untouched():
    from core.notifiers.formatters import md_to_slack
    out = md_to_slack("`**not bold**` and ```\n## not a heading\n```")
    assert "`**not bold**`" in out
    assert "## not a heading" in out


def test_slack_strips_result_envelope():
    from core.notifiers.formatters import md_to_slack
    assert md_to_slack("[RESULT_START]done[RESULT_END]") == "done"
