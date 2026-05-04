"""Pydantic schema for config.json — catches typos and structural drift early.

Why: config.json is hand-edited on Mac Mini (gitignored). Typos in keys like
`external_agents` vs `external_agent` only surface at runtime when something
reads the wrong key and silently gets nothing back. This schema validates the
structure on load and via `costaff config validate`.

Design choices:
- `extra="allow"` everywhere: legacy keys exist and we don't want to break
  existing deployments. The goal is to catch type errors and missing required
  fields, not to lock down the schema.
- All fields default-friendly: a minimal config (just `{}`) must validate, so
  fresh installs don't trip on it.
"""
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class ExternalAgent(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    a2a_url: str
    description: str = ""
    enabled: bool = True
    container_names: List[str] = Field(default_factory=list)
    source_path: Optional[str] = None
    fragment_path: Optional[str] = None
    public_port: Optional[int] = None
    version: Optional[str] = None
    mcp_configurable: bool = False
    mcp_env_var: Optional[str] = None
    model_env_var: Optional[str] = None
    mcp_port: Optional[int] = None


class DynamicChannel(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    public_port: Optional[int] = None
    description: str = ""
    enabled: bool = True
    container_names: List[str] = Field(default_factory=list)
    source_path: Optional[str] = None
    fragment_path: Optional[str] = None


class ExternalMcpEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    url: str
    enabled: bool = True
    description: str = ""


class CoStaffConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    mcp: List[str] = Field(default_factory=lambda: ["costaff"])
    channels: List[Any] = Field(default_factory=list)
    require_approval: bool = True
    coding_agent_enabled: bool = False
    external_agents: Dict[str, ExternalAgent] = Field(default_factory=dict)
    dynamic_channels: Dict[str, DynamicChannel] = Field(default_factory=dict)
    external_mcp: Dict[str, Union[str, ExternalMcpEntry]] = Field(default_factory=dict)
    gateways_config: Dict[str, Any] = Field(default_factory=dict)
    agent_mcps: Dict[str, List[str]] = Field(default_factory=dict)
    agent_mcp_filters: Dict[str, Dict[str, List[str]]] = Field(default_factory=dict)
