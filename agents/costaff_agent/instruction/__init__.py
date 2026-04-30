"""自動讀取本資料夾下的 system.md 作為 agent 系統提示詞。

使用方式：
    from instruction import instruction_content

若 system.md 不存在則回傳預設訊息。
"""
from pathlib import Path

_SYSTEM_PATH = Path(__file__).parent / "system.md"

if _SYSTEM_PATH.exists():
    instruction_content = _SYSTEM_PATH.read_text(encoding="utf-8")
else:
    instruction_content = "你是一個專業的 AI 助手。"
