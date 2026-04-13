# CoStaff

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Docker Support](https://img.shields.io/badge/docker-supported-blue.svg)](https://www.docker.com/)
[![Google ADK](https://img.shields.io/badge/Google%20ADK-latest-orange.svg)](https://github.com/google/adk-python)
[![MCP](https://img.shields.io/badge/MCP-enabled-green.svg)](https://modelcontextprotocol.io/)
[![A2A Protocol](https://img.shields.io/badge/A2A-protocol-violet.svg)](https://github.com/google/A2A)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

[繁體中文](./README_zhtw.md) | **English**

**CoStaff** is a self-hosted, privacy-first AI agent platform built on **Google ADK (Agent Development Kit)** and **Model Context Protocol (MCP)**. It connects your preferred chat platforms — **Telegram, Discord, and Line** — while exposing a full-featured web dashboard for operators to manage agents, tools, users, and sessions.

External agents (such as [`costaff-coding-agent`](https://github.com/CoStaffAI/costaff-coding-agent) and [`costaff-viz-report-agent`](https://github.com/CoStaffAI/costaff-viz-report-agent)) integrate via the **A2A protocol**, extending the platform's capabilities without modifying the core.

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Web Dashboard](#web-dashboard)
- [External Agents](#external-agents)
- [Tech Stack](#tech-stack)
- [Getting Started](#getting-started)
- [CLI Reference](#cli-reference)
- [Bot Commands](#bot-commands)
- [License](#license)

---

## Features

- **Multi-Agent Orchestration** — The primary CoStaff Agent delegates tasks to external agents via A2A protocol; add any `costaff.agent.json`-compatible agent with a single CLI command
- **Per-Agent Tool Assignment** — Assign which MCPs, APIs, and Skills each agent can access; changes apply with a targeted restart, no full redeploy needed
- **Dynamic MCP Layer** — Core MCPs are always present; add external MCPs (streamable HTTP or SSE) from the dashboard at runtime
- **API & Skill Registry** — Register external REST APIs and reusable prompt templates in the database, assigned per-agent and per-user
- **Multi-Channel Support** — Telegram, Discord, and Line bots out of the box
- **Multi-Model Support** — Google Gemini natively, or any LiteLLM-compatible provider
- **Secure Identity Hashing** — Platform IDs are mapped to 16-character SHA-256 hashes; real IDs never stored
- **Identity Approval Workflow** — New users held pending until operator approves from the dashboard
- **User Profile Memory** — Agent remembers name, role, company, contact info, and preferences across sessions
- **Proactive Reminders** — Schedule cron-based notifications delivered to any connected chat platform
- **Kanban Task Automation** — Recurring agent tasks (web search, DB queries, report generation) that push results back to users
- **Web Dashboard** — Dark/light mode operator console for full lifecycle management

---

## Architecture

CoStaff uses a **plugin-style architecture**. The core platform (agent + MCP + dashboard) runs as a standalone Docker stack. Channels and external agents each live in their own Docker projects and connect to the core via the shared `costaff_default` network.

```mermaid
graph TD
    subgraph Channels ["Channel Plugins (separate Docker projects)"]
        CH_TG[costaff-channel-telegram]
        CH_DC[costaff-channel-discord]
        CH_LN[costaff-channel-line]
        CH_WEB[costaff-channel-webchat]
    end

    subgraph Core ["CoStaff Core (.costaff/)"]
        CoStaffAgent[CoStaff Agent\nGoogle ADK]
        MCP_Core[Core MCP\nAPScheduler + Notifier]
        DB[(PostgreSQL)]
        API[FastAPI Backend]
        Dashboard[Web Dashboard]
    end

    subgraph Agents ["External Agent Plugins (separate Docker projects)"]
        AG_CODE[costaff-agent-coding\nA2A]
        AG_BA[costaff-agent-business-analysis\nA2A]
    end

    CH_TG & CH_DC & CH_LN & CH_WEB -->|HTTP ADK API| CoStaffAgent
    CoStaffAgent <--> MCP_Core
    CoStaffAgent -->|A2A| AG_CODE & AG_BA
    MCP_Core <--> DB
    MCP_Core -->|push| CH_TG & CH_DC & CH_LN & CH_WEB
    Dashboard <--> API
    API <--> DB
    API -->|restart| CoStaffAgent
```

All plugins connect through the **`costaff_default` Docker network** — no port mapping required between services.

---

## Web Dashboard

The dashboard (`costaff dashboard`) is a browser-based operator console with dark/light mode:

| Module | Description |
|--------|-------------|
| **Dashboard** | Live system stats (CPU, memory, disk) and service health overview |
| **Chat** | Talk to the CoStaff Agent directly from the browser with full conversation history |
| **Agents** | View internal/external agent status; configure per-agent MCP assignments with Apply & Restart |
| **MCPs** | Manage MCP extensions — add/remove external MCPs at runtime |
| **APIs** | Register external REST API configs and assign them per-agent or per-user |
| **Skills** | Register reusable prompt templates and assign them per-agent or per-user |
| **Reminders** | View and manage scheduled cron reminders |
| **Tasks** | Monitor Kanban-style automation task results |
| **Users** | Identity Map with User Profile detail panel |
| **Sessions** | Browse chat sessions; Event Logs show full function call / response traces |
| **Channels** | Configure Telegram / Discord / Line bot tokens |
| **Config** | Theme, model provider, approval gate settings |
| **Logs** | Stream container logs from any service in real time |

---

## External Agents

CoStaff supports deploying and managing external agents that communicate via the **A2A protocol**.

Any project containing a `costaff.agent.json` manifest can be registered and deployed:

```bash
# Deploy a local agent project
costaff agent deploy --local /path/to/my-agent

# Add a remote URL agent
costaff agent add my-agent --url http://my-agent.example.com

# List all agents
costaff agent list
```

**First-party agents:**

| Agent | Repository | Role |
|-------|------------|------|
| Coding Agent | [costaff-coding-agent](https://github.com/CoStaffAI/costaff-coding-agent) | Sandboxed Python code execution |
| Viz Report Agent | [costaff-viz-report-agent](https://github.com/CoStaffAI/costaff-viz-report-agent) | Chart generation & HTML/PDF reports |

---

## Plugin Architecture

CoStaff is designed as a **pluggable platform**. Both channels and agents are independent Docker projects that attach to the core at runtime — no modification to the core codebase is required.

### How Plugins Connect

Every plugin (channel or agent) joins the shared Docker network:

```yaml
# In the plugin's docker-compose.yaml
networks:
  default:
    external: true
    name: costaff_default
```

This gives the plugin direct access to `costaff-agent-costaff` (ADK API on port 8080) and `costaff-mcp-costaff` (MCP on port 8000) by container hostname — no port forwarding or tunnels needed.

### Channel Plugins

Channels are standalone bots/servers that:
1. Receive messages from users (Telegram, Discord, LINE, HTTP)
2. Forward them to the CoStaff Agent via `POST /run` on the ADK API
3. Receive push notifications from the MCP notifier

Built-in channels live under `.costaff/dynamic-channels/` and are started automatically with `costaff start`.

### Agent Plugins

External agents expose an **A2A-compatible** endpoint and register a `costaff.agent.json` manifest. The CoStaff Agent discovers and delegates tasks to them automatically.

```bash
# Register a local agent project
costaff agent deploy --local /path/to/my-agent

# Register a remote agent
costaff agent add my-agent --url http://my-agent.internal
```

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Agent Framework | [Google ADK](https://github.com/google/adk-python) |
| Agent-to-Agent | A2A Protocol (`RemoteA2aAgent`) |
| Tool Protocol | [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) |
| AI Models | Google Gemini, LiteLLM-compatible providers |
| Telegram Bot | [Aiogram 3.x](https://docs.aiogram.dev/) |
| Discord Bot | [discord.py](https://discordpy.readthedocs.io/) |
| Line Bot | [line-bot-sdk](https://github.com/line/line-bot-sdk-python) |
| Web Backend | [FastAPI](https://fastapi.tiangolo.com/) + [uvicorn](https://www.uvicorn.org/) |
| Database | [SQLAlchemy](https://www.sqlalchemy.org/) — PostgreSQL or SQLite |
| Scheduler | [APScheduler](https://apscheduler.readthedocs.io/) |
| Deployment | Docker + Docker Compose |
| CLI | [Typer](https://typer.tiangolo.com/) + [Rich](https://rich.readthedocs.io/) |

---

## Getting Started

### Prerequisites

- Python 3.10+
- Docker and Docker Compose
- A **Gemini API Key** from [Google AI Studio](https://aistudio.google.com/) (or any LiteLLM-compatible provider)

### Quick Start (no bot tokens required)

The fastest way to get started is with the built-in **Webchat** channel — no Telegram, Discord, or Line tokens needed.

```bash
# 1. Install the CLI
pip install -e .

# 2. Run the setup wizard (only Gemini API key required)
costaff onboard

# 3. Start the platform
costaff start

# 4. Open the dashboard
costaff dashboard
```

Then open **http://localhost:8501**, go to **Chat**, and start talking to the agent immediately.

To add bot channels later, go to **Dashboard → Channels** and enter your token — no restart of the core platform required.

### Full Setup

The setup wizard (`costaff onboard`) configures:
- AI model provider (Gemini or LiteLLM)
- Database type (SQLite or PostgreSQL)
- Bot tokens (Telegram, Discord, Line — all optional)
- Admin credentials for the web dashboard
- Identity hashing salt

All configuration is saved to `.costaff/` in the current directory.

---

## CLI Reference

| Command | Description |
|---------|-------------|
| `costaff onboard` | Interactive setup wizard |
| `costaff start` | Build and start all services |
| `costaff start --no-build` | Start without rebuilding images |
| `costaff stop` | Stop all services |
| `costaff restart` | Restart all services |
| `costaff ps` | Show status of running services |
| `costaff dashboard` | Open the web dashboard |
| `costaff chat` | CLI-based chat with the agent |
| `costaff agent deploy --local <path>` | Deploy a local agent project |
| `costaff agent add <name> --url <url>` | Register a remote URL agent |
| `costaff agent list` | List all registered agents |
| `costaff agent remove <name>` | Remove a registered agent |
| `costaff config show` | Display current configuration |
| `costaff database backup` | Backup the database |
| `costaff database restore` | Restore from a backup |
| `costaff version` | Show CLI version |

---

## Bot Commands

Available on Telegram, Discord, and Line:

| Command | Description |
|---------|-------------|
| `/start` | Initialise session and check identity |
| `/profile` | View your stored profile |
| `/list` | List active reminders and tasks |
| `/reset` | Clear the current conversation context |
| `/help` | Show available commands |

**Natural language examples:**

- "Remind me to drink water at 3 PM every day."
- "Search for the latest AI news every morning at 9 AM and send me a summary."
- "Analyse this CSV and generate an HTML report with charts."
- "Save my name as Simon and my role as Software Engineer."

---

## License

This project is dual-licensed under **AGPL v3 + Commercial License**. See `LICENSE` for details.

For commercial licensing inquiries, contact: simonliuyuwei@gmail.com
