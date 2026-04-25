---
name: multi-agent-chain
description: >
  Use when a task requires chaining two or more sub-agents in sequence — for example,
  the coding expert produces data or runs analysis, then the business analysis expert
  generates a PDF report from that data. Covers the correct sequential delegation pattern,
  how to distinguish progress signals from completion signals, how to pass file paths
  between agents, and what to do when a step fails.
---

# Multi-Agent Chain Orchestration

## When to Use
- Task needs 2+ sub-agents in sequence (e.g. coding → business_analysis)
- One agent's output is the next agent's input
- User asks for "run analysis AND generate a report"

## Step-by-Step Pattern

### Step 1 — Present Plan and Wait for Confirmation
Before calling any agent, present the plan to the user and wait for "OK".
Do NOT call any tool in the same turn as the plan.

### Step 2 — Execute Step 1 Agent

```
transfer_to_agent(agent_name='coding', message='...')
```

**Wait for the A2A call to fully resolve.**

The coding agent emits two types of events — you MUST tell them apart:

| Type | Example | Action |
|---|---|---|
| **Progress signal** (mid-task `send_message_now`) | "正在安裝套件…", "執行腳本中…" | Keep waiting — task is NOT done |
| **Completion signal** (A2A final response) | Contains file path or concrete result | Proceed to Step 3 |

A completion signal always includes at least one of:
- An absolute file path: `/app/data/shared/costaff-agent-coding/results.json`
- A computed value or structured summary
- An explicit failure message

### Step 3 — Extract Output Path

From the completion signal, extract the **exact** output file path. Example:
```
coding_output = "/app/data/shared/costaff-agent-coding/wine_svm_results.json"
```

**Never reconstruct or guess this path.** Use only what the agent returned.

### Step 4 — Execute Step 2 Agent

```
transfer_to_agent(
    agent_name='business_analysis',
    message=f'根據 {coding_output} 生成 PDF 報告，存至 /app/data/shared/costaff-agent-business-analysis/report.pdf'
)
```

Wait for this call to fully resolve as well.

### Step 5 — Deliver to User

Only after ALL agents have returned completion signals, send a single final reply with:
- Brief summary of what was done
- All output file paths in `[FILE: /absolute/path]` format
- Any key findings or metrics

**Do NOT send any plain-text response between steps** — it terminates the ADK run and subsequent `transfer_to_agent` calls will never execute. Use `send_message_now(body='...')` for mid-chain progress updates.

## Error Handling

If an agent fails:
1. Retry that specific agent **once**.
2. If it fails again, stop and report honestly: what succeeded, what failed, and deliver any partial artifacts.
3. **Never fabricate a file path or claim a task is complete when it is not.**

## Common Mistakes

| Mistake | Consequence |
|---|---|
| Calling `export_pdf` directly instead of `transfer_to_agent(agent_name='business_analysis')` | `ValueError: Tool 'export_pdf' not found` → run crashes |
| Emitting a text reply between two `transfer_to_agent` calls | ADK run terminates — second agent never executes |
| Proceeding to Step 4 after only a progress signal | Next agent receives incomplete/missing input data |
| Reconstructing the file path instead of copying it | Wrong path delivered to next agent or user |
| Fabricating result after a tool error | Critical hallucination — user receives false information |
