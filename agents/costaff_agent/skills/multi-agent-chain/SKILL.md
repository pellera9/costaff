---
name: multi-agent-chain
description: >
  Activate for ANY request involving specialist delegation — single or multi-step.
  Contains the complete orchestration SOP: plan-and-confirm format, sequential
  execution rules, how to write a complete `request` argument, recovery from
  errors, retry limits, file delivery rules, and output presentation.
---

# Multi-Agent Orchestration SOP

## When to Use
- A task requires delegating to one or more registered specialist agent tools (`<agent_name>(request: str)`)
- Two or more specialists need to work in sequence (output of one → input of next)
- User asks to combine multiple specialist capabilities in a single workflow

---

## Principle 1 — Present a Plan First (multi-step only)

When a request requires **two or more** specialist calls (any file or data hand-off between specialists), present a written plan and wait for user confirmation **before calling any agent tool**.

**Skip the plan (proceed immediately) when**:
- A single specialist fulfils the whole request
- User already confirmed a plan earlier in this session
- User says "直接做", "不用計劃", "go ahead", or similar

**Plan format (Telegram HTML — use exactly):**
```
📋 <b>執行計劃</b>

<b>Step 1: [專家職稱] (agent: <code>&lt;agent_name&gt;</code>)</b>
• 任務：...
• 輸入：...
• 預期產出：<code>/app/data/shared/costaff-agent-&lt;hyphen-name&gt;/&lt;project&gt;/xxx.ext</code>

<b>Step 2: [專家職稱] (agent: <code>&lt;agent_name&gt;</code>)</b>
• 任務：...
• 輸入：Step 1 的產出
• 預期產出：<code>/app/data/shared/costaff-agent-&lt;hyphen-name&gt;/&lt;project&gt;/yyy.ext</code>

請回覆「OK」開始執行，或告訴我需要調整的地方（工具、順序、輸出格式等）。
```

After presenting the plan: **STOP**. Do not call any tool. Wait for the user's next message.
- Confirmation ("OK", "好", "同意", "開始", "go") → proceed to execute
- Change request → update plan, present again for re-confirmation
- Clarifying question → answer it, then re-confirm the (possibly revised) plan

---

## Principle 0 — On Plan OK, Use `dispatch_plan` (CRITICAL)

**This is THE rule that determines whether the user has a smooth experience or a multi-prompt slog.**

When the user confirms a multi-step plan ("OK", "好", "go"), use **one call** to `dispatch_plan(steps=[...])`. The tool itself creates every task and chains them via `depends_on` internally — you cannot accidentally dispatch only half the plan, and you cannot forget the downstream step.

### 🟢 CORRECT — one `dispatch_plan` call covers the entire plan

```
User: OK
Manager turn (one tool call):
  dispatch_plan(
    epic_id=...,
    user_id=...,
    session_id=..., channel=..., recipient=...,
    steps=[
      {"title": "Step 1: load wine CSV",
       "assigned_agent": "coding_agent",
       "spec": "Load sklearn wine dataset and write CSV at ..."},
      {"title": "Step 2: generate PDF report",
       "assigned_agent": "business_analysis_agent",
       "spec": "Read the CSV from Step 1 and produce PDF at ..."},
    ]
  )
  → returns: "Dispatched 2 task(s) as a chain: #A (Coding), #B (BA ↳ depends on #A)"
  reply: "已派工。Coding (#A) 處理中，BA (#B) 已排入待辦、Coding 完成後自動接續。"
```

Step 1 starts running immediately. Step 2 sits in `backlog` until Step 1 finishes, then auto-promotes and runs. You don't wait, you don't ask, you don't do anything between the dispatch and the final result.

### Single-step plans

If the plan is a single specialist (or the user's request only needs one agent), use `dispatch_task` directly. `dispatch_plan` is for multi-step plans.

### ❌ FORBIDDEN — dispatch step 1, then ask between steps

```
User: OK
Manager turn:
  dispatch_task(Step 1 only)         ← only 1 call, plan had 2 steps
  reply: "已派 Coding 處理 (#A)。完成後我會通知您。"
…(Coding finishes, SYSTEM_CALLBACK fires)…
Manager turn (callback):
  reply: "Step 1 完成。要不要派 BA 處理 Step 2？"   ← ❌ user already said OK
```

This pattern repeatedly bit users on 2026-05-15 (wine PDF retest at 13:47, iris retests at 15:32 and 15:37 — all three needed the user to OK the same plan twice). `dispatch_plan` exists to make this failure mode impossible.

### The only valid reasons to ask between steps

- Step N's actual output makes Step N+1 obsolete or invalid (e.g. Coding reports the dataset is empty → BA's report makes no sense). Tell the user honestly, do not silently abandon.
- The upstream step **failed** and the user needs to decide retry vs abandon.
- The user has interjected a new instruction that changes the plan mid-stream.

If none of the above applies and the plan is still valid, do NOT ask. The chain auto-advances; just report progress when the last step finishes.

### Tool primitives (legacy notes)

`dispatch_plan` is built on top of `dispatch_task`, which in turn replaced the two-step `create_project_task` + `update_task_queue` legacy pattern. Use `dispatch_task` directly for single-step dispatches. Use `dispatch_plan` for ≥2-step plans. `create_project_task` is LEGACY — only use if you genuinely need two-phase create (rare).

**Hallucinated-dispatch check:** every reply that says "已派 … 處理（任務編號：xxx）" or "已建立新任務 #xxx" MUST be preceded by an actual `dispatch_plan` or `dispatch_task` call producing that task_id in the same turn. A reply that cites a task ID without a matching tool call is a hallucination.

---

## Principle 1A — Iteration: Use the Existing Path (apply BEFORE drafting the Plan)

Classify the request before planning. **An iteration is anything that touches a deliverable that already exists in this session.**

**Iteration signals** (any of these → treat as iteration):
- The request refers to a previously delivered file or feature
- The request uses modify / fix / extend verbs. Examples (Chinese): 改, 修, 加, 優化, 升級, 修復, 沒了, 不見了, 不能用. Examples (English): fix, update, extend, broken, bug, missing, not working
- The request reports a defect in something already shipped this session
- The request adds a feature to a deliverable already produced

**When the request is iteration — strict rules**:

0. **🔴 MUST dispatch a real task — NOT just a verbal promise.**

   When the user asks to modify / extend / fix an already-completed deliverable (e.g. "改成 10 頁", "加結論段落", "換更深的模型", "報告再詳細一點"), you **MUST** call `dispatch_task` **before** replying to the user:

   1. Call `dispatch_task` — write the `spec` to clearly state the modification (referencing the prior task's output path and the requested change). One atomic call creates and queues the task.
   2. **Only after** the call returns successfully → reply to the user, citing the new task ID.

   ❌ **FORBIDDEN — verbal-only acknowledgment:**
   ```
   User:    "可以擴展到 10 頁嗎？"
   Manager: "沒問題，我會請 BA 擴展到 10 頁…我會將此要求更新到任務規格中
            （任務編號：f12d8d10）。完成後我會立即通知您。"
            [no dispatch_task call — user waits forever for work that never starts]
   ```
   Reproduced 2026-05-15 on costaff-prod-test: manager promised an update to a *done* task, never dispatched, user spent minutes asking "有在做事嗎？". The DB had zero new rows.

   ✅ **CORRECT — dispatch first, then reply:**
   ```
   User:    "可以擴展到 10 頁嗎？"
   Manager: [call dispatch_task(title="[Iteration] 擴展論文至 10 頁",
                                spec="In-place modify of .../thesis.pdf,
                                      extend to ≥10 pages…",
                                assigned_agent="business_analysis_agent")]
            → task id e.g. abc12345
            [reply to user]: "已建立新任務 #abc12345 給 BA 擴展報告至 10 頁，
                              完成後我會通知您。"
   ```

   The Task DB is the source of truth — a promise that is not backed by a row in `project_tasks` is a hallucination, no matter how friendly the wording.

1. **Reuse the existing path.** The plan's expected-output path MUST be the **same path** as the previous deliverable.
   **FORBIDDEN suffixes** on the filename: `_v2`, `_v3`, `_new`, `_fixed`, `_updated`, `-mobile`, `-deluxe`, `-final`, or any version marker. The file is updated in place — git is for tracking history, not filenames.

2. **Plan wording**: describe the action as "modify the existing file at `<path>`" (in the user's preferred language), not "create a new file".

3. **The `request` you pass to a coding-style specialist MUST contain this exact instruction block** (in addition to describing the change):
   ```
   This is an in-place modification of an existing file at <absolute path>.
   Use patch_file() or insert_after_line() for surgical edits.
   Do NOT call write_file() to overwrite the whole file.
   Do NOT create a new versioned filename (no _v2, _new, _fixed suffixes).
   ```

4. **Keep all related files under the same project subdirectory.** Do not spawn a new project dir per iteration.

**When the request is a brand-new build** (no prior deliverable in this session, no existing file referenced): proceed normally — choose a fresh project directory and filename.

---

## Principle 1B — Path Format (CRITICAL)

Every path you write in a plan or in a `request` MUST include a kebab-case **project subdirectory** under the agent's shared slot. Format:

```
/app/data/shared/costaff-agent-<name>/<project>/.../<filename>
```

- `<project>` = kebab-case name inferred from the user's task (e.g. `wine-eda`, `quicksort-practice`, `wine-svm-report`).
- The exact inner structure (`outputs/`, `src/`, or files at the project root) is the specialist's responsibility — describe **what** the file is, let the specialist decide the inner layout.
- Reuse the same `<project>` across every step of the plan so the user sees one consistent project name.

**FORBIDDEN**: never prescribe a path directly under `/app/data/shared/costaff-agent-<name>/` (no subdirectory). The agent will normalize it elsewhere; your plan and final `[FILE: ...]` summary will then point at a non-existent path, confusing the user. Examples of bad vs good:

| ❌ Bad (flat root) | ✅ Good (with project subdir) |
|---|---|
| `/app/data/shared/costaff-agent-coding/wine_stats.csv` | `/app/data/shared/costaff-agent-coding/wine-eda/wine_stats.csv` |
| `/app/data/shared/costaff-agent-business-analysis/wine_report.pdf` | `/app/data/shared/costaff-agent-business-analysis/wine-eda-report/wine_report.pdf` |

Do NOT add inner directories like `outputs/` or `src/` to the path you prescribe — that is the specialist's own decision. Your job is only to ensure the kebab-case `<project>/` segment is present.

---

## Principle 2 — Write a Complete `request` (CRITICAL)

The specialist agent tool receives **only the `request` string you write** — it does not see the user's prior messages, your plan text, or the conversation history. If `request` is vague, ambiguous, or just an acknowledgement like "OK" or "go", the specialist has no context and will reply conversationally without doing any work.

**Every `request` must be self-contained and imperative.** Include:

| Element | Required? | Example |
|---|---|---|
| Concrete action verb | always | "Load the wine dataset and run EDA…" |
| Input source / file paths | if any | "Read `/app/data/shared/costaff-agent-coding/wine-svm/wine_results.json`" (use the exact path the upstream specialist returned — including any `outputs/` it added) |
| Expected output format & path | always | "Save as `/app/data/shared/costaff-agent-business-analysis/wine-svm-report/wine_report.pdf`" |
| Constraints / language / quality bar | as needed | "Report in Traditional Chinese; include charts and analysis narrative" |
| `[PROGRESS_CONTEXT]` block (when applicable) | if user-facing progress matters | with `user_id`, `channel`, `session_id` |

**Forbidden in `request`:**
- ❌ Mentions of other specialists or chaining ("then pass to X", "after this, transfer to Y")
- ❌ Orchestration verbs aimed at the specialist ("delegate", "transfer", "hand off")
- ❌ Empty / single-word strings ("OK", "go", "do it") — the specialist will not act
- ❌ References to "the user's earlier message" or "what was discussed" — the specialist cannot see those

The specialist's only job: **do the work, save the file, report back**. Chaining to the next specialist is your responsibility after the current call returns.

---

## Principle 3 — Sequential, Not Parallel (brief)

`dispatch_task` (Principle 0 / 0A) handles ordering automatically via `depends_on` + auto-link, so dispatching multiple steps in one turn is the **correct** behaviour. Just provide `depends_on` (or rely on auto-link), and the executor enforces sequentiality.

The historical "parallel forbidden" rule applies to **direct AgentTool invocation** (e.g. `coding(request=...)` called synchronously). That mode is rarely used now — but if you ever fall back to it: don't call two specialist agent tools in the same turn. For full recovery procedures and the historical bug record, **activate `multi-agent-chain-recovery`**.

---

## Principle 4 — Pass Exact Results, Never Reconstruct

Extract the **exact** output from the specialist's return value (file path, value, identifier) and pass it verbatim into the next specialist's `request`. Never retype, reconstruct, or guess.

**Path naming (CRITICAL — common bug source)**:
- Filesystem paths use **hyphens**: `costaff-agent-business-analysis`
- Agent tool names use **underscores**: `business_analysis(request=...)`
- Never derive one from the other; refer to each registered tool's exact name

---

## Principle 5 — Brief Progress Updates Are Allowed Between Calls

Between two specialist calls, you may emit a brief `send_message_now` progress line so the user knows work is continuing. Keep it to one Chinese sentence (e.g. "▶️ 商業分析專家正在生成報告，請稍候。"). Do NOT include internal reasoning, file paths, or technical details in these progress lines.

After **all** specialists have returned completion signals, compose and emit your final consolidated text response to the user.

---

## Principle 6 — Deliver a Single Final Reply

After all specialists have returned, send one consolidated response containing:
- A 1–2 sentence summary of which specialists collaborated (no jargon)
- **All output file paths** in `[FILE: /absolute/path]` format — include intermediate files (CSV/JSON), not just the final PDF
- Any key insights or findings from the results

**In partial success**: if the chain fails mid-way, still deliver every artifact produced by successful steps. Never omit intermediate files.

**Verify before reporting**: Before saying "報告已生成" or "已傳遞給X", confirm you have an actual completion signal (file path or concrete result) from that specialist. If you have only progress signals or the tool call has not yet returned, do not claim completion. When in doubt, call `list_data_files` to verify the file exists on disk.

---

## Principle 7 — No Specialist's Internal Tools (brief)

A specialist's internal MCP tools (e.g. `run_python_code` for Coding, `export_pdf` for BA) are **NOT in your toolset**. Always delegate via `dispatch_task` or, if running synchronously, via the registered AgentTool wrapper.

If you ever see `ValueError: Tool '<name>' not found`: **activate `multi-agent-chain-recovery`** — it has the full forbidden-tool reference table and the recovery checklist. Do not retry the forbidden call.

---

## Output Presentation

1. Extract only content wrapped in `[RESULT_START]` / `[RESULT_END]` tags from specialist return values. If no tags found, use the last meaningful paragraph only.
2. Filter out: `_Thinking:_` prefixes, raw JSON, tool call logs, code blocks, English reasoning.
3. **Convert Markdown to Telegram HTML** — specialists write in Markdown; you MUST convert before sending:
   - `**text**` → `<b>text</b>`
   - `*text*` or `_text_` → `<i>text</i>`
   - `` `text` `` → `<code>text</code>`
   - `# Heading` / `## Heading` → `<b>Heading</b>` (no heading tags in Telegram)
   - `- item` → `• item` (keep the bullet, remove the dash)
4. Deliver ALL files using `[FILE: /app/data/shared/costaff-agent-<name>/<project>/file.ext]` format — include intermediate files (CSV, PNG) AND final output (PDF). Use the **exact paths returned by the specialists**, not the paths you wrote in the plan (specialists may normalize paths).
5. Use `{PREFERRED_LANGUAGE}` and Telegram HTML throughout.
6. Never output raw JSON, `_Thinking:_`, English reasoning, or Markdown syntax to the user.

---

## When Something Goes Wrong

Recovery procedures, retry limits, the full forbidden-tool table, and the historical mistakes catalogue live in a separate skill: **`multi-agent-chain-recovery`**. Activate it when:

- You receive `ValueError: Tool '<name>' not found`
- A specialist fails and you are deciding whether to retry / change approach / abandon
- You suspect you accidentally invoked two specialists in parallel
- You need to recall which tools belong to which specialist's internal toolset
