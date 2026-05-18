"""Manager-side deterministic PROGRESS_CONTEXT injection into AgentTool."""
import importlib.util
from pathlib import Path

import pytest

# Load by file path: the package __init__.py eagerly builds the full
# LlmAgent (needs model env vars). progress_inject only needs stdlib.
_MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "agents" / "costaff_agent" / "progress_inject.py"
)
_spec = importlib.util.spec_from_file_location("_progress_inject_uut", _MODULE_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
before_tool_callback = _mod.before_tool_callback

_BLOCK = (
    "[PROGRESS_CONTEXT]\n"
    "user_id=4afb1ffe5b6ee7ef\n"
    "channel=telegram\n"
    "session_id=task_28b8933d-2e92-427f-b1c2-2688673f2bfd"
)


class _Part:
    def __init__(self, text):
        self.text = text


class _Content:
    def __init__(self, text):
        self.parts = [_Part(text)]


class _Ctx:
    def __init__(self, text):
        self.user_content = _Content(text)


class _Tool:
    name = "business_analysis_agent"


@pytest.mark.asyncio
async def test_appends_block_when_request_lacks_it():
    args = {"request": "Write a 5-page PDF report on bubble tea trends."}
    src = "[Task: ...]\nWrite a report\n\n" + _BLOCK
    out = await before_tool_callback(_Tool(), args, _Ctx(src))
    assert out is None
    assert "[PROGRESS_CONTEXT]" in args["request"]
    assert "session_id=task_28b8933d" in args["request"]
    assert args["request"].startswith("Write a 5-page PDF")


@pytest.mark.asyncio
async def test_noop_when_request_already_has_block():
    args = {"request": "Do it.\n\n" + _BLOCK}
    before = args["request"]
    await before_tool_callback(_Tool(), args, _Ctx("spec\n\n" + _BLOCK))
    assert args["request"] == before  # unchanged, not duplicated


@pytest.mark.asyncio
async def test_noop_when_source_has_no_block():
    args = {"request": "Just a normal chat-driven task."}
    await before_tool_callback(_Tool(), args, _Ctx("ordinary user message"))
    assert args["request"] == "Just a normal chat-driven task."


@pytest.mark.asyncio
async def test_noop_when_no_request_arg():
    args = {"other": 1}
    assert await before_tool_callback(_Tool(), args, _Ctx("x\n\n" + _BLOCK)) is None
    assert args == {"other": 1}


@pytest.mark.asyncio
async def test_failsafe_on_bad_context():
    # user_content missing/None must not raise
    class _Bad:
        user_content = None

    args = {"request": "task"}
    assert await before_tool_callback(_Tool(), args, _Bad()) is None
    assert args["request"] == "task"
