"""MCP toolset 載入器：從環境變數讀取設定並建立 McpToolset 清單。

使用方式：
    from mcp_toolsets import load_all_mcp_toolsets
    toolsets = load_all_mcp_toolsets()  # 取得 McpToolset 清單

讀取環境變數順序：COSTAFF_AGENT_MCP_URLS → MCP_SERVER_URLS。
支援格式：
    1. JSON 物件：{"name": {"url": "...", "headers": {...}, "transport": "sse|streamable", "enabled": true}}
    2. JSON 字串：{"name": "url", ...}
    3. Comma-separated URL 字串："url1,url2,url3"

注意：本資料夾命名為 mcp_toolsets/ 而非 mcp/，避免與 site-packages 中的
google-adk MCP SDK 套件 (mcp) 命名衝突。
"""
import json
import logging
import os
from typing import List

from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import (
    SseServerParams,
    StreamableHTTPServerParams,
)

logger = logging.getLogger(__name__)


def _get_connection_params(entry):
    """根據 entry 內的 url / headers / transport 設定回傳對應的 ADK ServerParams。"""
    if isinstance(entry, str):
        url, headers, transport = entry, None, None
    else:
        url = entry.get("url", "")
        headers = entry.get("headers") or None
        transport = entry.get("transport")

    if not url:
        raise ValueError("MCP entry has no URL")
    if transport is None:
        transport = "sse" if url.rstrip("/").endswith("/sse") else "streamable"

    if transport == "sse":
        return SseServerParams(url=url, headers=headers)
    return StreamableHTTPServerParams(url=url, headers=headers)


def load_all_mcp_toolsets() -> List[McpToolset]:
    """讀取環境變數中的 MCP 設定並建立 McpToolset 清單。

    Returns:
        List[McpToolset]: 已成功註冊的 MCP toolset 清單，可直接放入 Agent(tools=[...])。

    Raises:
        EnvironmentError: 當 COSTAFF_AGENT_MCP_URLS 與 MCP_SERVER_URLS 都未設定時。
    """
    raw = os.getenv("COSTAFF_AGENT_MCP_URLS") or os.getenv("MCP_SERVER_URLS", "")
    if not raw:
        raise EnvironmentError(
            "COSTAFF_AGENT_MCP_URLS (or MCP_SERVER_URLS) is not set."
        )

    try:
        config = json.loads(raw)
    except json.JSONDecodeError:
        config = {
            f"mcp_{i}": url.strip()
            for i, url in enumerate(raw.split(","))
            if url.strip()
        }

    toolsets: List[McpToolset] = []
    for name, entry in config.items():
        if isinstance(entry, dict) and not entry.get("enabled", True):
            continue
        try:
            toolsets.append(
                McpToolset(connection_params=_get_connection_params(entry))
            )
            logger.info(f"Registered MCP: {name}")
        except Exception as e:
            logger.error(f"FAILED to load MCP '{name}': {e}")
    return toolsets
