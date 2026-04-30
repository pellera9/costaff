import os
import json
import re
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from google.adk.agents import LlmAgent
from google.adk.tools import skill_toolset

from .mcp_toolsets import load_all_mcp_toolsets
from .models import selected_model
from .instruction import instruction_content
from .skills import load_all_skills

# Load MCP toolsets
tools = list(load_all_mcp_toolsets())

# Load ADK Skills
_skills = load_all_skills()
tools.append(skill_toolset.SkillToolset(skills=_skills))
logger.info(f"Loaded {len(_skills)} skill(s): {[s.frontmatter.name for s in _skills]}")

# --- Sub-Agents (consumed via A2A) ---
# ADK auto-injects each sub-agent's name + description into the
# transfer_to_agent tool spec. Manual roster rendering is therefore
# unnecessary — instruction text only needs orchestration SOPs and
# routing rules, not a duplicated agent listing.
sub_agents = []
raw_agents = os.getenv("EXTERNAL_AGENTS_CONFIG", "").strip()

if raw_agents:
    try:
        from google.adk.agents.remote_a2a_agent import RemoteA2aAgent, AGENT_CARD_WELL_KNOWN_PATH

        agents_config = json.loads(raw_agents)
        for agent_name, agent_cfg in agents_config.items():
            a2a_url = agent_cfg.get("a2a_url", "").strip()
            description = agent_cfg.get("description", f"Specialist: {agent_name}").strip()

            if not a2a_url:
                continue

            try:
                a2a_name = agent_name.replace("-", "_")
                logger.info(f"Registering sub-agent '{a2a_name}' via A2A at {a2a_url}")

                remote_agent = RemoteA2aAgent(
                    name=a2a_name,
                    description=description,
                    agent_card=f"{a2a_url.rstrip('/')}{AGENT_CARD_WELL_KNOWN_PATH}",
                    use_legacy=False,
                )
                sub_agents.append(remote_agent)
                logger.info(f"Successfully registered sub-agent '{a2a_name}'")
            except Exception as e:
                logger.error(f"Failed to load sub-agent '{agent_name}': {e}")
    except Exception as e:
        logger.error(f"A2A system failure: {e}")

# Construct dynamic instruction. Strip the sub-agent SOP block when no
# sub-agents are registered (so the LLM doesn't see orchestration rules
# it can't follow). When sub-agents exist, just remove the markers and
# keep the SOP content.
preferred_lang = os.getenv("COSTAFF_PREFERRED_LANGUAGE", "English")

if sub_agents:
    instruction_body = re.sub(r"<!--\s*(BEGIN|END)_SUB_AGENTS\s*-->", "", instruction_content)
else:
    instruction_body = re.sub(r"<!--\s*BEGIN_SUB_AGENTS\s*-->.*?<!--\s*END_SUB_AGENTS\s*-->", "", instruction_content, flags=re.DOTALL)
    instruction_body = "\n# NO SUB-AGENTS\nYou work alone.\n\n" + instruction_body

instruction = instruction_body.replace("{PREFERRED_LANGUAGE}", preferred_lang)

root_agent = LlmAgent(
    model=selected_model,
    name="costaff_agent",
    description="Orchestrates specialists for tasks.",
    instruction=instruction,
    tools=tools,
    sub_agents=sub_agents,
)
