---
name: multi-agent-chain
description: >
  Use when a task requires chaining two or more sub-agents in sequence — where one
  agent's output becomes the next agent's input. Covers the correct sequential
  delegation pattern, how to distinguish progress signals from completion signals,
  how to pass results between agents, and what to do when a step fails.
---

# Multi-Agent Chain Orchestration

## When to Use
- A task requires two or more specialists to execute **in order**
- The output from one agent (file path, computed value, structured data) becomes the input to the next
- User asks to combine multiple specialist capabilities in a single workflow

## Core Principles

### Principle 1 — Present the Plan Before Acting

Before calling any agent, present the full execution plan to the user and wait for confirmation.
Do NOT call any tool in the same turn as the plan presentation.

### Principle 2 — Execute One Agent at a Time

Call only one agent per step. Wait for the A2A call to **fully resolve** before proceeding.

Each agent emits two types of events — you MUST distinguish them:

| Event type | Characteristics | Action |
|---|---|---|
| **Progress signal** (mid-task) | Status messages like "安裝中…", "執行中…", sent via `send_message_now` | Keep waiting — the agent is still running |
| **Completion signal** (final response) | Contains a file path, computed result, or explicit failure message | Proceed to the next step |

A completion signal always includes at least one of:
- An absolute file path (e.g. `/app/data/shared/<agent-slot>/<filename>`)
- A concrete computed value or structured summary
- An explicit failure message explaining what went wrong

### Principle 3 — Pass Exact Results, Never Reconstruct

Extract the **exact** output from the completion signal — file path, value, or identifier — and pass it verbatim to the next agent.

Never reconstruct, guess, or retype an output. If the path or value came from the agent, copy it character-for-character.

### Principle 4 — No Plain Text Between Agent Calls

Do NOT emit a plain text reply between two `transfer_to_agent` calls. Doing so terminates the ADK run, and the next agent will never execute.

If you need to communicate mid-chain progress to the user, use `send_message_now(body='...')` instead.

### Principle 5 — Deliver a Single Final Reply

After all agents have returned completion signals, send one consolidated reply containing:
- A brief summary of what was accomplished
- All output file paths in `` [FILE: /absolute/path] `` format
- Any key findings or metrics from the results

### Principle 6 — Check Roster Before Each Agent

Before each `transfer_to_agent` call, verify the target agent appears in your **Section 12.2 team roster**.
If an agent is not in the roster, do not attempt to call it — inform the user honestly and stop the chain at that step.

## Error Handling

If any agent in the chain fails:
1. Retry that specific agent **once**.
2. If it fails again, stop and report: what succeeded, what failed, and deliver any partial artifacts that were produced.
3. Never fabricate a file path or claim a task is complete when it is not.

## Common Mistakes

| Mistake | Consequence |
|---|---|
| Calling an agent's internal MCP tool directly instead of `transfer_to_agent` | `ValueError: Tool '<name>' not found` → run crashes |
| Emitting a plain text reply between two `transfer_to_agent` calls | ADK run terminates — subsequent agents never execute |
| Treating a progress signal as a completion signal | Next agent receives missing or incomplete input |
| Reconstructing or guessing an output path instead of copying it | Wrong path delivered — downstream agent fails or produces garbage |
| Fabricating a result after a tool error | Critical hallucination — user receives false information |
| Skipping roster check before calling an agent | Agent not deployed, call fails with no meaningful error |
