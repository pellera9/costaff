# How to write your own CoStaff agent

> Audience: anyone who wants to extend CoStaff with a new agent — adding
> a domain-specific specialist (e.g. a "legal-research agent",
> "marketing-copy agent"), wrapping an existing service, or
> experimenting with a new model provider.
>
> Time to working agent: **~30 minutes** if you fork the template; longer
> if you write everything from scratch.

This is a narrative tutorial. The normative contract is
[`AGENT_PROTOCOL_v1.0.md`](./AGENT_PROTOCOL_v1.0.md) — read that for
the precise semantics. This doc shows you the path.

---

## Prerequisites

- A working CoStaff install (`costaff start` brings up the dashboard).
- Python 3.10+ and Docker on your dev machine.
- A model provider key. Default examples assume **Gemini** via
  `GOOGLE_API_KEY`; LiteLLM-compatible providers also work.

---

## Path A: fork the template (recommended)

The fastest way is to start from
[`costaff-agent-template`](https://github.com/costaff-ai/costaff-agent-template).
It ships a working agent at the **Schema-valid + Tool-conformant**
compliance level (see protocol §11) and uses the recommended folder
layout (protocol §10).

### 1. Clone and rename

```bash
# Clone into your costaff-agent workspace
cd ~/.costaff/costaff-agent
git clone https://github.com/costaff-ai/costaff-agent-template.git my-agent
cd my-agent

# Detach from the template's git history
rm -rf .git && git init -b main
```

Your agent's *name* (the one that will appear in `costaff agent list`,
in env var prefixes, in container hostnames) is **`my-agent`** — keep
it lowercase, kebab-case, prefixed with `costaff-agent-` if you intend
to publish it.

### 2. Edit the manifest

Open `costaff.agent.json`. Change the four agent-specific fields:

```json
{
  "protocol_version": "1.0",
  "name": "costaff-agent-my-agent",
  "version": "0.1.0",
  "description": "What does your agent do, in one sentence the manager will use to decide whether to delegate to you.",
  "a2a_service": "agent-my-agent",
  "port": 8081,
  "health_path": "/.well-known/agent-card.json",
  ...
}
```

Why each field matters: protocol §3.1.

If you don't need access to the manager core MCP (rare), set
`"mcp_configurable": false` and remove `mcp_env_var` / the `_MCP_URLS`
entries from `env_auto`.

### 3. Edit the agent's behaviour

Two files own this:

| File | What you change |
|---|---|
| `agent/instruction/system.md` | The system prompt. **This is where 90% of agent personality lives.** Be specific about input format, output format, when to use each tool, when to refuse. |
| `agent/agent.py` | The `LlmAgent` definition. Add agent-specific tools to the `tools=[...]` list if you have any (e.g. domain-specific function tools, your own MCP server). |

Don't touch:

- `agent/agent_a2a.py` — wraps the agent as A2A. Generic.
- `agent/models/` — the gemini/litellm switch. Generic.
- `agent/mcp_toolsets/` — connects to the manager core MCP. Generic.
- `agent/Dockerfile` — generic Python+ADK image.

### 4. Decide whether you need an own MCP server

Your agent has access to the four core MCP tools (`send_message_now`,
`add_task_comment`, `move_to_shared`, `list_data_files`) through the
manager. If your agent needs *additional* tools — domain-specific
operations, third-party API wrappers — you have two choices:

- **In-process function tools.** Add them as Python functions to
  `agent/agent.py`'s `tools=[...]`. Easiest, lives in the same process.
- **Own MCP server.** Use `mcp/server.py` (already scaffolded in
  template) to publish tools as MCP. Worth it when tools are reused by
  other agents or maintained by a different team.

If you don't need the latter, **delete the entire `mcp/` directory** —
it's optional per protocol §10.

### 5. Build, register, test

```bash
# From the agent project root
costaff agent add my-agent --local . --strict
```

`--strict` runs the full JSON Schema validation against your manifest
(see protocol §11 "Schema-valid"). You want this on for development.

If the agent registers successfully:

```bash
costaff agent list                   # Should show "my-agent" with green ●
costaff status                       # Should list costaff-agent-my-agent
costaff logs costaff-agent-my-agent  # Tail logs
```

Open the dashboard, go to **Agents → my-agent**, and try delegating
from the chat ("ask my-agent to ...").

### 6. Iterate

```bash
# After editing instruction/system.md or agent code:
costaff agent rebuild my-agent
```

`rebuild` is required (not `restart`) when:
- You changed Python code (the image needs to pick it up).
- You changed `<NAME>_AGENT_MCP_URLS` (the env var only re-reads on
  rebuild).
- You added/removed an env var declared in the manifest.

`restart` is enough for clearing a stuck container with no code change.

---

## Path B: minimal from scratch (advanced)

You don't have to use the template. Any project that:

1. Has a `costaff.agent.json` that passes the manifest schema.
2. Exposes an A2A endpoint per protocol §4.
3. (Optionally) connects to the manager core MCP per §6.
4. Builds into a Docker image and joins the `costaff_default` network.

…is conformant.

The bare-minimum file set is:

```
my-agent/
├── costaff.agent.json
├── docker-compose.yaml
├── Dockerfile
├── requirements.txt
└── agent.py            # LlmAgent + to_a2a in one file
```

This makes sense if:

- You're writing the agent in **Go / Rust / Node** (no Python ADK
  ergonomics — implement A2A directly per the [Google A2A spec][]).
- You're integrating an existing service that already has its own
  layout and you don't want template's directory structure.
- You're optimising for image size or cold-start.

[Google A2A spec]: https://github.com/google/A2A

For Python agents, **Path A is almost always the right choice** — the
template's "extra" files (model abstraction, instruction module, etc.)
solve real ergonomic problems you'd hit within an hour.

---

## Common pitfalls

### Forgot `disallow_transfer_to_parent` / `disallow_transfer_to_peers`

If your agent has no sub-agents (the typical leaf case) and you don't
set both flags to `True`, Gemini will hallucinate
`transfer_to_agent("...")` calls and fail at runtime with
`Tool 'transfer_to_agent' not found`. Symptom: agent's first reply
errors out.

Fix: in `agent/agent.py`, ensure your `LlmAgent(...)` has both flags.

### Manifest validates but agent never appears in dashboard

Most often the A2A endpoint isn't reachable. Check:

```bash
docker logs costaff-agent-my-agent 2>&1 | tail
```

You should see "Uvicorn running on http://0.0.0.0:8081". If the
agent crashed, fix the underlying error (often a missing env var or
import). Then `costaff agent restart my-agent`.

### Tool calls return `permission denied` or `not approved`

The four core tools refuse calls from unapproved users (protocol §6.2).
First-time users start in **pending** state — log into the dashboard,
go to **Users**, approve them.

### Tool calls don't seem to reach the manager core MCP

Check the agent's MCP env var:

```bash
docker exec costaff-agent-my-agent env | grep _MCP_URLS
```

Should be a JSON map containing `"costaff": {...}`. If it's empty or
missing, your manifest's `mcp_env_var` doesn't match what the CLI
generates (CLI uppercases the agent name with hyphens → underscores).

### MCP whitelist not applied

Symptom: agent sees 40+ manager tools instead of just the four. Cause:
the agent's `mcp_toolsets/__init__.py` doesn't pass `tool_filter` to
`McpToolset(...)`. The template handles this; if you wrote your own,
mirror it. The whitelist itself lives in `config.json` →
`agent_mcp_filters` (see manager `costaff/CLAUDE.md` §2.3).

---

## Going further

- **Sub-agents.** If your agent needs to delegate to other agents,
  add them under `agent/sub_agents/`. The template's `load_all_sub_agents()`
  auto-discovers folders.
- **Skills.** Reusable prompt templates live under `agent/skills/`.
  Each skill is a folder with a `SKILL.md` describing when to use it.
- **Custom model.** Edit `agent/models/litellm_model.py` to pin a
  specific provider. Set `<NAME>_AGENT_MODEL` and
  `COSTAFF_AGENT_MODEL_PROVIDER=litellm` in your `.env`.
- **Publishing.** Push the repo to GitHub under
  `costaff-ai/costaff-agent-<name>`. CoStaff users can then
  `costaff agent add <name> --github https://github.com/...`.

---

## Reference

- **Normative**: [`AGENT_PROTOCOL_v1.0.md`](./AGENT_PROTOCOL_v1.0.md)
- **JSON Schemas**: [`schemas/`](./schemas/)
- **Template (recommended starting point)**: [costaff-agent-template](https://github.com/costaff-ai/costaff-agent-template)
- **Working examples**: [costaff-agent-coding](https://github.com/costaff-ai/costaff-agent-coding), [costaff-agent-business-analysis](https://github.com/costaff-ai/costaff-agent-business-analysis)
- **A2A protocol**: [Google A2A](https://github.com/google/A2A)
- **MCP**: [modelcontextprotocol.io](https://modelcontextprotocol.io/)
