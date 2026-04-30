import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from google.adk.agents import LlmAgent

from .mcp_toolsets import load_all_mcp_toolsets
from .models import selected_model
from .instruction import build_instruction
from .skills import load_all_skills
from .sub_agents import load_all_remote_agent_tools

tools = list(load_all_mcp_toolsets())
tools.append(load_all_skills())

# Each remote agent is exposed as `<agent_name>(request: str)`. The manager
# LLM must write a self-contained task description in `request`; the
# sub-agent receives only that string and acts on it. See sub_agents/__init__.py.
remote_agent_tools = load_all_remote_agent_tools()
tools.extend(remote_agent_tools)

instruction = build_instruction(has_agent_tools=bool(remote_agent_tools))

root_agent = LlmAgent(
    model=selected_model,
    name="costaff_agent",
    description="Orchestrates specialists for tasks.",
    instruction=instruction,
    tools=tools,
    sub_agents=[],
)
