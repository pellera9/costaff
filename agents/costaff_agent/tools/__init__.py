"""純 Python function tools — 永遠對 LLM 可用（不像 SkillToolset 是依需求動態載入）。

使用方式（新增 tool 時）：
    1. 在本資料夾新增 <tool_name>.py，定義 function（含繁體中文 docstring，
       因為 docstring 決定 Agent 何時呼叫此工具）
    2. 在這個檔案 import 並加入 __all__
    3. agent.py 從 tools import：
         from tools import get_current_time   # 例
       並放入 Agent(tools=[..., get_current_time]) 或
       SkillToolset(additional_tools=[get_current_time]) 視情境而定

對齊 idea/google-adk-template/agent/tools/__init__.py 的設計慣例。
目前無 function tool — manager agent 透過 MCP toolsets + ADK Skills 提供
全部能力，本資料夾為未來預留。
"""

__all__: list = []
