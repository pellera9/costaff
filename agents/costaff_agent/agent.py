import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from google.adk.agents import LlmAgent

from .mcp_toolsets import load_all_mcp_toolsets
from .models import selected_model
from .instruction import build_instruction
from .skills import load_all_skills
from .sub_agents import load_remote_agents_split

tools = list(load_all_mcp_toolsets())
tools.append(load_all_skills())

# Default: every remote agent is an AgentTool `<name>(request: str)` and
# `sub_agents=[]` (the proven, stable contract). EXPERIMENTAL: agents
# listed in COSTAFF_TRANSFER_AGENTS are instead wired into `sub_agents`
# (transfer mechanism) and excluded from the AgentTool list — to test
# whether transfer forwards multimodal/image parts that AgentTool does
# not. Unset the env var to fully revert (no redeploy needed).
remote_agent_tools, transfer_sub_agents = load_remote_agents_split()
tools.extend(remote_agent_tools)

instruction = build_instruction(
    has_agent_tools=bool(remote_agent_tools or transfer_sub_agents)
)

root_agent = LlmAgent(
    model=selected_model,
    name="costaff_agent",
    description="Orchestrates specialists for tasks.",
    instruction=instruction,
    tools=tools,
    sub_agents=transfer_sub_agents,
)
