---
name: multi-agent-chain-recovery
description: >
  Activate when a multi-agent orchestration goes wrong. Contains: recovery from
  `ValueError: Tool '<name>' not found`, the full forbidden-tool reference for
  each specialist, retry limits and abandon criteria, recovery if you
  accidentally invoked two specialists in parallel, and the catalogue of
  historically-reproduced mistakes. Pair with `multi-agent-chain` (the always-
  loaded core skill) — that one has the happy-path rules; this one has the
  escape hatches.
---

# Multi-Agent Recovery & Anti-Patterns

This is the companion skill to `multi-agent-chain`. Load it on demand — when a tool failed, a chain stalled, or a recovery decision is needed.

---

## R1 — Parallel Invocation: Recovery

If you realise you already invoked two specialists in parallel (e.g. fired `business_analysis(request=...)` and `coding(request=...)` in the same turn, or dispatched a chain without setting `depends_on` and they're now racing):

1. **Do NOT retry** the downstream specialist "just in case it works this time". The upstream output is still missing — retrying just produces another silent failure.
2. **Wait** for the upstream specialist to actually return.
3. Read the upstream's return value, extract the artefact path or concrete result.
4. Call the downstream specialist again, this time with the extracted value threaded into `request`.
5. If the first downstream failure was already reported to the user, briefly apologise. If not, do not pre-emptively mention the failure.

### Historical record

Reproduced 2026-05-14 on the wine PDF chain: Manager fired `business_analysis(request=...)` and `coding(request=...)` back-to-back in the same turn. BA ran for ~3s and reported "file not found" because Coding's CSV wasn't written yet. Coding finished ~30s later, but BA had already failed.

`dispatch_task` (Principle 0/0A in core skill) + auto-link via `depends_on` makes this structurally impossible going forward — the executor keeps the downstream task in `backlog` until the upstream finishes. The historical rule only applies to direct AgentTool invocation, which should be rare now.

---

## R2 — "Tool Not Found" Error: Recovery

When you receive `ValueError: Tool '<name>' not found`:

1. **DO NOT retry** the same forbidden tool call. Retrying only hallucinates another non-existent name and burns minutes of clock time.
2. **DO NOT fabricate** any result, file path, or completion message to the user. The user has no way to know the tool failed; you do — be honest.
3. Identify which specialist OWNS the tool you tried to call (see the reference table below).
4. Call that specialist via its registered agent tool wrapper (`<agent_name>(request='...')` or, more commonly, dispatch a fresh `dispatch_task(assigned_agent="<agent_name>", ...)`).
5. Wait for the specialist's actual completion signal (file path, computed value, structured summary, or explicit failure declaration).
6. Only after receiving the real return value → report to the user.

If the specialist also fails after one retry → stop. Report partial results honestly with what succeeded, what failed, and what artefacts were produced.

---

## R3 — Forbidden Tools by Specialist

These tools live INSIDE each specialist's own MCP server. They are **not in your toolset**. You may see them listed inside a specialist's agent card — that is informational about what the specialist itself can do, not an invitation for you to call them directly.

| Specialist role | Internal tools you must NOT call directly |
|---|---|
| Coding / Python execution | `run_python_code`, `write_file`, `patch_file`, `lint_file`, `run_shell`, `pip_install`, `run_pytest` |
| Reporting / visualisation (BA) | `export_pdf`, `export_pptx`, `create_html_report`, `create_report_from_markdown`, `generate_chart` |
| Open-data / curation (Twinkle Hub) | `opendata-search_datasets`, `opendata-get_dataset`, `materialize_dataset`, `save_curated_csv`, `save_curated_json` |
| Database access | `run_query`, `get_schema`, `list_tables`, `execute_sql` |
| Medical reference | `icd10_search`, `loinc_search`, `rxnorm_search`, `fhir_validate` |

**Your own legitimate tools are**: the registered specialist agent tool wrappers (one per agent), `dispatch_task`, `send_message_now`, `get_user_profile`, `update_user_profile`, `get_current_time`, `check_identity`, reminder tools, regular-work tools, epic/story/task tools, diary tools, API/skill index tools, `move_to_shared`, `list_data_files`.

If a function name you are about to call is not in the list above and is not a registered specialist's AgentTool wrapper or `dispatch_task` itself — **stop**. You are about to call a specialist's internal tool.

---

## R4 — Retry Limits

- Each specialist may be retried **at most once** on failure.
- If the same specialist fails **twice consecutively** → stop immediately. Do not try a third time.
- Do NOT attempt creative workarounds (different paths, alternative directories, slightly different `request` wording) — if the specialist couldn't do it twice, the third attempt is unlikely to help and burns clock time.
- Report failure honestly: what succeeded, what failed, what partial artefacts were produced.
- Each distinct specialist tool call counts as one attempt — including dispatches that the specialist immediately rejected.

---

## R5 — Catalogue of Reproduced Mistakes

When debugging an in-progress chain, scan this list first — most failures match one of these patterns.

| Mistake | Consequence | First-aid |
|---|---|---|
| Writing a vague `request` like "OK" or "do it" | Specialist replies conversationally without acting | Re-send with full imperative (see core Principle 2) |
| Mentioning other specialists or chaining inside `request` | Specialist may try to delegate and fail or get confused | Strip the cross-talk; specialists only see their own job |
| Calling a specialist's internal MCP tool directly | `ValueError: Tool '<name>' not found` → run crashes | Use the registered AgentTool wrapper (see R2 / R3) |
| Treating a `send_message_now` progress line as the completion signal | Next specialist receives incomplete or fabricated input | Wait for the AgentTool return value or the SYSTEM_CALLBACK |
| Reconstructing or guessing an output path | Downstream specialist fails reading the wrong path | Pass the upstream's exact returned path verbatim |
| Fabricating a result after a tool error | Critical hallucination — user receives false information | Stop, surface the failure honestly, do NOT invent files or numbers |
| Using hyphens in agent tool name / underscores in file path | Path or specialist not found | Tool names use underscores (`coding_agent`), paths use hyphens (`costaff-agent-coding`) |
| `create_project_task` without `update_task_queue` | Task stranded in `backlog`, never runs (pre-`dispatch_task` era) | Use `dispatch_task` instead (atomic create+queue) |
| Plan presented but only Step 1 dispatched | "Should I continue?" loop between every step | See core Principle 0A — dispatch entire chain on user OK |
| Manager-in-executor-session dispatching more tasks | Recursive task explosion (reproduced 2026-05-15: 4 Coding tasks for 1 request) | In an executor session, call the AgentTool directly. Never call `dispatch_task` from inside a `task_*` session |
