# Session-Log Schema Reference (Claude Code · Codex · Hermes)

Evidence-backed reference for the on-disk session-log formats of three agentic coding
harnesses, produced to inform a "session portability" tool. All findings below are
derived from parsing real session files on this machine. Secrets, tokens, encrypted
blobs, and personal data are redacted as `<REDACTED>`.

## Files inspected

| Harness | Files parsed (all >5KB except where noted) |
|---|---|
| Claude Code | `agent-skills/74f84179-…1cace5.jsonl` (201 rec), `agent-skills/ca60e68f-…5bec31b897.jsonl` (13 rec), `agent-relay/8f8833cc-…52dfd81.jsonl` (352 rec), `agent-relay/f0c15c7b-…f01a36166.jsonl`, `lazyflow/de29e20b-…5bdf6a4a2020.jsonl` (+ a `term-chameleon` file for thinking/todo search) |
| Codex | `2026/07/18/rollout-…6394.jsonl` (12 rec), `2026/06/27/rollout-…f5dd.jsonl` (9 rec), `2026/04/09/rollout-…0565.jsonl` (39 rec) — **these are the only three Codex sessions present** |
| Hermes | `20260416_004124_10f12c82.jsonl` (21 rec, tool-heavy), `20260417_212844_53f28960.jsonl` (3 rec), `20260419_010530_0fcf7b4c.jsonl` (3 rec) |

All three harnesses store one JSON object per line (JSONL). All parsed cleanly with
zero JSON errors.

---

## 1. Claude Code

Path: `~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl`. Subagent (sidechain)
transcripts live under `<session-uuid>/subagents/agent-*.jsonl`.

### 1.1 Top-level record types and frequency

The discriminator is the top-level **`type`** field. Observed across files:

| `type` | Meaning | Seen counts (per file) |
|---|---|---|
| `user` | A user turn OR a tool_result turn (see 1.3) | 1, 11, 29, 130 |
| `assistant` | One assistant API message (text / thinking / tool_use blocks) | 2, 20, 43, 188 |
| `attachment` | Out-of-band context injected into the stream: hook output, command output, file snapshots, etc. Dominant by count | 6, 157, 263, 1434 |
| `queue-operation` | User-message queue events: `enqueue` / `dequeue` | 2 each |
| `last-prompt` | Pointer to the current conversation leaf (see 1.4) | 2, 11, 15, 77 |
| `mode` | UI/interaction mode marker (e.g. `"normal"`) | present in lazyflow (52) |
| `permission-mode` | Permission mode marker (e.g. `bypassPermissions`) | present (52) |
| `ai-title` | Auto-generated session title | present (51) |
| `file-history-snapshot` | Snapshot of tracked-file backups keyed to a messageId | present (6) |
| `system` | System/hook events (e.g. `subtype: stop_hook_summary`) | present (11) |

Note: `summary` records (used by Claude Code compaction/resume) were **not** present
in the sampled files but are a documented Claude Code record type; a portability tool
should tolerate it.

### 1.2 Key sets per record type

**`user`** — keys:
`parentUuid, isSidechain, message, promptId, promptSource, type, uuid, timestamp,
permissionMode, userType, entrypoint, cwd, sessionId, version, gitBranch`
and, when the turn carries a tool result: `toolUseResult`, `sourceToolAssistantUUID`,
and (on a denial) `toolDenialKind`.

**`assistant`** — keys:
`parentUuid, isSidechain, message, requestId, type, uuid, timestamp, userType,
entrypoint, cwd, sessionId, version, gitBranch`; on API errors also `error` and
`isApiErrorMessage`.

**`attachment`** — keys:
`type, attachment, cwd, entrypoint, gitBranch, isSidechain, parentUuid, sessionId,
timestamp, userType, uuid, version`. The inner `attachment` object varies by source;
a hook-output attachment carries
`type, hookName, toolUseID, hookEvent, content, stdout, stderr, exitCode, command,
durationMs`.

**`queue-operation`** — `type, operation, timestamp, sessionId, content`.

**`last-prompt`** — `type, leafUuid, sessionId` (+ `lastPrompt` in some).

**`mode`** — `type, mode, sessionId`.
**`permission-mode`** — `type, permissionMode, sessionId`.
**`ai-title`** — `type, aiTitle, sessionId`.
**`file-history-snapshot`** — `type, messageId, snapshot, isSnapshotUpdate`
(`snapshot` = `{messageId, trackedFileBackups, timestamp}`).
**`system`** — `type, subtype, parentUuid, uuid, isSidechain, hookCount, hookInfos,
hookErrors, hookAdditionalContext, stopReason, preventedContinuation, level, hasOutput,
toolUseID, cwd, gitBranch, sessionId, session_id, timestamp, userType, entrypoint,
version`.

The envelope common to conversational records (`user`/`assistant`) carries
`cwd`, `gitBranch`, `version`, `sessionId`, `userType`, `entrypoint` on **every** row —
i.e. session metadata is repeated per-record, not stored once in a header.

### 1.3 How each conversational element is represented

The inner `message` object is (almost) a raw Anthropic Messages-API message.

**USER text message** (`type: "user"`, `message.role: "user"`, `message.content` is a
string or a list of text blocks):

```json
{ "parentUuid": "c5e81393-…", "uuid": "e53e9e1a-…", "type": "user",
  "promptSource": "sdk", "permissionMode": "default",
  "message": { "role": "user",
    "content": "# Postprocess today's ingested meeting summaries\n\nYou are running inside the `agent-skills` repository. …" } }
```

**ASSISTANT text message** — a `text` block inside `message.content` (a list):

```json
{ "type": "text",
  "text": "I'll start by loading the meeting-intelligence skill to follow its exact procedure." }
```

The assistant envelope (`message` minus `content`) is the raw API response:

```json
{ "model": "claude-sonnet-5", "id": "msg_011CczGe…", "type": "message",
  "role": "assistant", "stop_reason": "tool_use", "stop_sequence": null,
  "usage": { "input_tokens": 2, "cache_creation_input_tokens": 52415,
             "cache_read_input_tokens": 23758, "output_tokens": 88,
             "service_tier": "standard", … } }
```

**ASSISTANT reasoning / thinking block** — a `thinking` block in `message.content`,
with an encrypted signature:

```json
{ "type": "thinking", "thinking": "<REDACTED_THINKING_TEXT>",
  "signature": "<REDACTED_SIGNATURE>" }
```

**TOOL CALL** — a `tool_use` block in the assistant `message.content`:

```json
{ "type": "tool_use", "id": "toolu_014E29a6xmnvhVQqvU3Egwmw",
  "name": "Skill", "input": { "skill": "workflow/meeting-intelligence" },
  "caller": { "type": "direct" } }
```

**TOOL RESULT** — a **user** record whose `message.content` holds a `tool_result`
block; the raw/structured result is duplicated into the top-level `toolUseResult`:

```json
{ "type": "user",
  "message": { "role": "user", "content": [
    { "type": "tool_result",
      "content": "<tool_use_error>Unknown skill: workflow/meeting-intelligence</tool_use_error>",
      "is_error": true, "tool_use_id": "toolu_014E29a6xmnvhVQqvU3Egwmw" } ] },
  "toolUseResult": "…" }
```

`toolUseResult` may be a string OR a rich object. For an async Task/subagent dispatch
it is e.g. `{isAsync, status, agentId, description, resolvedModel, prompt, outputFile,
canReadOutputFile}`. TodoWrite and other structured tools likewise round-trip their
state through `toolUseResult` / the `tool_use.input`.

### 1.4 Thread / ordering structure

Claude Code is **both** append-ordered **and** an explicit linked list:

- Every `user`/`assistant`/`system` record has a `uuid` and a `parentUuid`.
- `parentUuid: null` marks the conversation root. In the 352-record relay file there
  were 335 uuid-bearing records and exactly **one** root — a single clean chain (a
  linked list, which is what enables branch/fork on resume).
- `last-prompt` records point at the current leaf via `leafUuid`, so the "head" of the
  active branch is recoverable without walking the whole file.
- `isSidechain: true` marks records belonging to a subagent branch; subagents also get
  their own file under `subagents/`.
- The tool_use → tool_result link is by `tool_use.id` == `tool_result.tool_use_id`
  (verified: 28 tool_use / 28 tool_result, zero unmatched in the relay file).

### 1.5 Session-level metadata — where each lives

| Metadatum | Location |
|---|---|
| `cwd` | On every conversational/attachment record (repeated) |
| `gitBranch` | Same — per record |
| `model` | Inside each `assistant.message.model` (per message, not a header) |
| model provider | Not stored explicitly (implicitly Anthropic) |
| `version` (CLI) | Per record (`version`) |
| permission / approval mode | `permission-mode` record; also `user.permissionMode` per turn |
| interaction mode | `mode` record |
| session id | `sessionId` on nearly every record; also the filename |
| tool schemas | **Not stored** in the transcript at all |
| base/system instructions | **Not stored** in the transcript (injected at runtime) |
| title | `ai-title` record |

### 1.6 Pending / half-finished state

- **Queued-but-undelivered user input:** `queue-operation` records with
  `operation: "enqueue"` / `"dequeue"` and the queued text in `content`. Observed pair
  `["enqueue","dequeue"]`. An `enqueue` without a matching `dequeue` = a message the
  user typed that was never delivered to the model.
- **Unmatched tool call = pending tool:** a `tool_use` id with no later `tool_result`
  = an interrupted/in-flight tool (none pending in sampled files, but structurally
  detectable).
- **`last-prompt.leafUuid`** identifies the branch head — an interrupted turn leaves
  the leaf pointing at the last assistant/tool record.
- **`system` / `stop_hook_summary`** with `preventedContinuation`/`stopReason` captures
  a hook that halted the turn.
- **Goal / todo state:** TodoWrite is an ordinary tool; its list lives in the
  `tool_use.input.todos` and/or `toolUseResult`, not in a dedicated top-level record.

---

## 2. Codex

Path: `~/.codex/sessions/YYYY/MM/DD/rollout-<ISO-ts>-<session-uuid>.jsonl`.

### 2.1 Top-level record types and frequency

Discriminator is top-level **`type`**; the payload lives under **`payload`** and has
its own inner `type`. Every record is `{timestamp, type, payload}`.

| `type` | Meaning | Counts |
|---|---|---|
| `session_meta` | One-time session header (see 2.5) | 1 per file |
| `turn_context` | Per-turn config snapshot (model, sandbox, approvals…) | 1, 1, 4 |
| `event_msg` | UI/telemetry events (task lifecycle, token counts, user/agent message echoes) | 4, 4, 24 |
| `response_item` | Actual model-conversation items (OpenAI Responses-API shape) | 3, 5, 10 |
| `world_state` | Snapshot of tool/world state (`{full, state}`) | 0–1 |

Inner `payload.type` values observed:
`session_meta` (n/a), `turn_context` (n/a), `event_msg → {task_started, task_complete,
token_count, user_message, agent_message}`, `response_item → {message}`.

### 2.2 Key sets

**`session_meta.payload`**: `session_id, id, timestamp, cwd, originator, cli_version,
source, thread_source, model_provider, base_instructions, context_window,
history_mode`. (Older 04/09 file had a smaller set without `context_window`/
`history_mode`/`thread_source`.)

**`turn_context.payload`**: `turn_id, cwd, workspace_roots, current_date, timezone,
approval_policy, approvals_reviewer, sandbox_policy, permission_profile,
collaboration_mode, comp_hash, file_system_sandbox_policy, model, multi_agent_mode,
multi_agent_version, personality, realtime_active, summary, truncation_policy`.

**`event_msg` payloads**:
- `task_started`: `type, turn_id, started_at, model_context_window, collaboration_mode_kind`
- `task_complete`: `type, turn_id, completed_at, duration_ms, last_agent_message`
- `token_count`: `type, info, rate_limits`
- `user_message`: `type, message, images, local_images, text_elements`
- `agent_message`: `type, message, phase, memory_citation`

**`response_item.payload` (message)**: `type, role, content, phase,
internal_chat_message_metadata_passthrough`.

### 2.3 How each conversational element is represented

**USER message** — `response_item` whose payload is a `message` with `role: "user"`
and content blocks of type `input_text`. (Codex also injects environment/permissions
context as `role: "developer"` input_text blocks.)

```json
{ "type": "message", "role": "user",
  "content": [ { "type": "input_text",
    "text": "<environment_context>\n  <cwd>/Users/&lt;user&gt;/Developer</cwd>\n  <shell>zsh</shell>\n  <current_date>&lt;REDACTED&gt;</current_date>\n  <timezone>&lt;REDACTED&gt;</timezone>\n</environment_context>" } ] }
```

There is *also* an `event_msg` echo of the raw user text:
`{ "type": "user_message", "message": "hi", "images": [], "local_images": [], "text_elements": [] }`.

**ASSISTANT text message** — `response_item` message with `role: "assistant"` and
`output_text` content; `phase: "final_answer"`:

```json
{ "type": "message", "role": "assistant",
  "content": [ { "type": "output_text", "text": "Hi." } ], "phase": "final_answer" }
```

Mirrored by `event_msg → agent_message`:
`{ "type": "agent_message", "message": "Hi.", "phase": "final_answer", "memory_citation": null }`.

**ASSISTANT reasoning, TOOL CALL, TOOL RESULT — NOT OBSERVED locally.** All three
Codex sessions on this machine were short chat sessions with no tool use, so no
`reasoning`, `function_call`, `function_call_output`, `local_shell_call`, or
`custom_tool_call` records exist to quote. These are **documented Codex/Responses-API
`response_item` payload types** the tool must still support (shapes below are the
canonical Responses-API forms, flagged as *unobserved-in-sample*):

```jsonc
// reasoning (unobserved here; cf. Hermes codex_reasoning_items which mirrors it)
{ "type": "reasoning", "id": "rs_…", "summary": [ { "type": "summary_text", "text": "…" } ],
  "encrypted_content": "<REDACTED>" }
// function_call (unobserved)
{ "type": "function_call", "name": "shell", "arguments": "{…}",
  "call_id": "call_…", "id": "fc_…" }
// function_call_output (unobserved)
{ "type": "function_call_output", "call_id": "call_…", "output": "…" }
```

### 2.4 Thread / ordering structure

Codex is **purely append-ordered** — there is **no `parentUuid`/uuid linked list**.
Ordering is file order + `timestamp`. Turn grouping is via `turn_id`
(`turn_context.turn_id`, `event_msg.task_started/complete.turn_id`). Tool linkage,
where present, is by `call_id` (function_call ↔ function_call_output). `response_item`
messages carry no per-item id linkage back to a parent.

### 2.5 Session-level metadata — where each lives

| Metadatum | Location |
|---|---|
| `cwd` | `session_meta.payload.cwd` and `turn_context.payload.cwd` |
| `model` | `turn_context.payload.model` (per turn) — **not** in session_meta |
| model provider | `session_meta.payload.model_provider` (e.g. `"openai"`) |
| base/system instructions | `session_meta.payload.base_instructions.text` (full prompt stored) |
| approval / sandbox mode | `turn_context.payload.approval_policy`, `sandbox_policy`, `permission_profile`, `file_system_sandbox_policy` |
| context window | `session_meta.payload.context_window` and `task_started.model_context_window` |
| CLI version | `session_meta.payload.cli_version` |
| session id | `session_meta.payload.session_id`/`id`; also filename |
| workspace roots | `turn_context.payload.workspace_roots` |
| timezone / current date | `turn_context.payload` |
| tool schemas | **Not stored** in the transcript |
| title | Not stored |

Codex is the only harness that **persists the full base/system instructions** in the
log.

### 2.6 Pending / half-finished state

- Turn lifecycle is explicit: `event_msg.task_started` vs `task_complete` per
  `turn_id`. A `task_started` with no matching `task_complete` = an interrupted turn.
- A `function_call` `response_item` with no matching `function_call_output`
  (`call_id`) = a pending/in-flight tool (structurally detectable; none in sample).
- No queued-user-message concept in the log.
- `world_state` (`{full, state}`) captures a point-in-time tool/world snapshot.

---

## 3. Hermes

Path: `~/.hermes/sessions/<timestamp>_<id>.jsonl`. (The `request_dump_*.json` files in
the same directory are raw provider-request dumps, not session transcripts, and are out
of scope.)

### 3.1 Top-level record types and frequency

Discriminator is top-level **`role`** — the OpenAI chat-completions shape, one message
per line, with a synthetic `session_meta` role for the header.

| `role` | Meaning | Counts |
|---|---|---|
| `session_meta` | One-time header: model, platform, and full tool schemas | 1 per file |
| `user` | User message | 5, 1, 1 |
| `assistant` | Assistant message (text and/or reasoning and/or tool_calls) | 9, 1, 1 |
| `tool` | Tool result | 6, 0, 0 |

### 3.2 Key sets

**`session_meta`**: `role, model, platform, timestamp, tools`.
**`user`**: `role, content, timestamp`.
**`assistant`**: `role, content, timestamp, finish_reason, reasoning,
codex_reasoning_items, tool_calls` (last three optional).
**`tool`**: `role, content, tool_call_id, timestamp`.

### 3.3 How each conversational element is represented

**USER message** — flat `content` string:

```json
{ "role": "user", "content": "Hi", "timestamp": "2026-04-16T00:41:29.491358" }
```

**ASSISTANT text message** — `content` string, `finish_reason: "stop"` (reasoning may
accompany it; `tool_calls` absent).

**ASSISTANT reasoning** — two parallel fields on the assistant record:
`reasoning` (plaintext summary string) and `codex_reasoning_items` (a list mirroring
the OpenAI Responses reasoning item, including the encrypted blob):

```json
{ "reasoning": "**Exploring reminder scheduling**\n\nIt looks like I need to understand …",
  "codex_reasoning_items": [
    { "type": "reasoning", "id": "rs_0ad90b24…",
      "encrypted_content": "<REDACTED>",
      "summary": [ { "type": "summary_text", "text": "**Exploring reminder scheduling** …" } ] } ] }
```

**TOOL CALL** — `assistant.tool_calls[]`, OpenAI shape, with an extra
`response_item_id` that ties back to the Codex/Responses backend
(`finish_reason: "tool_calls"`, `content: ""`):

```json
{ "role": "assistant", "content": "", "finish_reason": "tool_calls",
  "tool_calls": [
    { "id": "call_aQ1uhN99Ahqa75fFLmegzg5B",
      "call_id": "call_aQ1uhN99Ahqa75fFLmegzg5B",
      "response_item_id": "fc_0ad90b24…",
      "type": "function",
      "function": { "name": "session_search",
        "arguments": "{\"query\":\"\\\"reminder test\\\" OR reminder OR cronjob\",\"limit\":3}" } } ] }
```

**TOOL RESULT** — a `role: "tool"` record; `content` is a (usually JSON) string,
linked by `tool_call_id`:

```json
{ "role": "tool",
  "content": "{\"success\": true, \"query\": \"…\", \"results\": [ … ] }",
  "tool_call_id": "call_aQ1uhN99Ahqa75fFLmegzg5B",
  "timestamp": "2026-04-16T01:44:28.482145" }
```

### 3.4 Thread / ordering structure

**Purely append-ordered.** No uuid/parent linkage between records. Turn structure is
implicit in message order. Tool linkage is by `tool_calls[].id` == `tool.tool_call_id`
(verified: 6 calls / 6 results, zero unmatched). `finish_reason` (`stop` /
`tool_calls`) delimits assistant turns.

### 3.5 Session-level metadata — where each lives

| Metadatum | Location |
|---|---|
| `model` | `session_meta.model` (e.g. `"gpt-5.4"`) — one header value |
| platform | `session_meta.platform` (e.g. `"telegram"`) |
| **tool schemas** | `session_meta.tools` — **full JSON-schema tool definitions stored** (40 functions in sample) |
| `cwd` | **Not stored** |
| model provider | Not stored (implied by model name) |
| base/system instructions | **Not stored** in the transcript |
| approval / permission mode | **Not stored** |
| version | **Not stored** |
| session id | Filename only (`<timestamp>_<id>`) |
| title | Not stored |

Hermes is the only harness that **persists the full tool schema catalog** in the log.

### 3.6 Pending / half-finished state

- A `tool_calls` entry with no matching `role:"tool"` result = a pending/in-flight tool
  (structurally detectable via `tool_call_id`).
- `finish_reason` on the last assistant record shows whether the model stopped normally
  (`stop`) or was expecting tool results (`tool_calls`) — the latter as the final
  record = an interrupted turn awaiting tool execution.
- No queued-user-message concept; no explicit goal/todo state.

---

## 4. Cross-harness comparison

| Concern | Claude Code | Codex | Hermes |
|---|---|---|---|
| One-line format | JSONL, discriminator `type` | JSONL, `type` + inner `payload.type` | JSONL, discriminator `role` |
| User message | `type:user`, `message.role:user`, content str/blocks | `response_item` message `role:user`, `input_text` blocks (+`event_msg.user_message` echo) | `role:user`, flat `content` string |
| Assistant text | `type:assistant`, `text` block in `message.content` | `response_item` message `role:assistant`, `output_text` (+`event_msg.agent_message` echo) | `role:assistant`, `content` string |
| Reasoning / thinking | `thinking` block (`thinking` + `signature`) | `response_item` `reasoning` **(unobserved in sample)** | `reasoning` string + `codex_reasoning_items[]` (encrypted + summary) |
| Tool call | `tool_use` block (`id,name,input,caller`) | `response_item` `function_call` (`call_id,name,arguments`) **(unobserved)** | `assistant.tool_calls[]` (`id,call_id,response_item_id,function{name,arguments}`) |
| Tool result | `type:user` w/ `tool_result` block + `toolUseResult` | `response_item` `function_call_output` (`call_id,output`) **(unobserved)** | `role:tool` (`content,tool_call_id`) |
| Session meta location | Repeated on every record | `session_meta` + `turn_context` (payload) | `session_meta` header |
| Model | per-`assistant.message.model` | `turn_context.model` (per turn) | `session_meta.model` (once) |
| Model provider | implicit (Anthropic) | `session_meta.model_provider` | implicit |
| Base/system instructions | not stored | **stored** (`base_instructions.text`) | not stored |
| Tool schemas | not stored | not stored | **stored** (`session_meta.tools`) |
| Permission / approval mode | `permission-mode` record + per-turn `permissionMode` | `turn_context` (`approval_policy`, `sandbox_policy`, `permission_profile`) | not stored |
| cwd / git | per record (`cwd`,`gitBranch`) | `session_meta`/`turn_context` cwd; no git | not stored |
| Thread linkage | **explicit linked list** (`uuid`/`parentUuid`) + `last-prompt.leafUuid` + `isSidechain` | append-order + `turn_id` grouping; no per-item linkage | append-order; no linkage |
| Tool call↔result key | `tool_use.id` = `tool_result.tool_use_id` | `call_id` | `tool_calls[].id` = `tool.tool_call_id` |
| Pending user input | **`queue-operation` enqueue/dequeue** | none | none |
| Interrupted-turn signal | dangling tool_use; `last-prompt` leaf; `stop_hook_summary` | `task_started` w/o `task_complete`; dangling `call_id` | dangling `tool_call_id`; trailing `finish_reason:tool_calls` |
| Title | `ai-title` record | none | none |
| Subagents | separate `subagents/agent-*.jsonl` + `isSidechain` | none observed (`multi_agent_*` flags in turn_context) | none |

---

## 5. Gaps and asymmetries (lossy-conversion risks)

Things one harness records that another cannot represent — the unavoidable loss points
for a portability tool.

1. **Thread topology is not portable both ways.** Only Claude Code stores an explicit
   `uuid`/`parentUuid` linked list (supporting branches/forks and precise resume-leaf
   selection). Converting Claude Code → Codex/Hermes **flattens branches into a single
   append-order line** and drops fork history. Converting Codex/Hermes → Claude Code
   requires **synthesizing** uuids and a linear parent chain (safe, but any branch
   information that never existed cannot be recovered).

2. **Reasoning content is asymmetric and mostly one-way.** Claude Code thinking blocks
   carry an Anthropic `signature`; Codex/Hermes carry an OpenAI `encrypted_content`
   reasoning item. These encrypted/opaque blobs are provider-bound and **cannot be
   re-signed for a different provider** — reasoning survives as human-readable summary
   text at best, and any attempt to replay it into another model's context is lossy.
   Codex reasoning was unobserved in the local sample; Hermes exposes both a plaintext
   `reasoning` summary and the raw `codex_reasoning_items`, so Hermes is the richest
   reasoning source.

3. **Tool schemas exist in only one place.** Hermes stores the full function catalog in
   `session_meta.tools`; Claude Code and Codex store **no** tool schemas in the
   transcript. Hermes → others: schemas are simply dropped (harmless, since the target
   harness supplies its own). Others → Hermes: the tool catalog must be reconstructed
   from the tool names actually used, which is **incomplete** (only invoked tools, no
   parameter schemas for unused tools).

4. **Base/system instructions exist in only one place.** Only Codex persists
   `base_instructions.text`. Claude Code and Hermes inject system prompts at runtime and
   never write them. Codex → others: the base prompt is dropped. Others → Codex: it must
   be left empty or reconstructed, so an imported session cannot faithfully reproduce
   the original Codex system framing.

5. **Queued user input is Claude-Code-only.** `queue-operation` enqueue/dequeue (a
   typed-but-undelivered message) has **no representation** in Codex or Hermes. A
   half-finished Claude Code session with a pending `enqueue` loses that pending input
   on conversion.

6. **Permission / sandbox / approval state is unevenly modeled.** Codex has the richest
   model (`approval_policy`, `sandbox_policy`, `permission_profile`,
   `file_system_sandbox_policy`); Claude Code has a coarse `permissionMode` /
   `permission-mode`; Hermes stores none. Round-tripping through Hermes **erases**
   permission posture entirely.

7. **Per-turn vs per-session model.** Codex records `model` per `turn_context` and
   Claude Code per assistant message, so both can represent a model switch mid-session;
   Hermes stores a single `session_meta.model` and **cannot represent a mid-session
   model change**.

8. **Rich structured tool results.** Claude Code's `toolUseResult` carries typed
   structures (e.g. async Task dispatch metadata: `agentId, resolvedModel, outputFile,
   canReadOutputFile`) alongside the text `tool_result`. Codex `function_call_output`
   and Hermes `role:tool` carry a single (usually JSON-string) `output`/`content`.
   Converting away from Claude Code **collapses structured tool metadata into a string**.

9. **Attachments / hook telemetry / file-history snapshots are Claude-Code-only.**
   `attachment`, `system`/`stop_hook_summary`, and `file-history-snapshot` records
   (hook output, command output, tracked-file backups) have no analog elsewhere and are
   dropped on export.

10. **cwd / git / session titling.** Claude Code carries `cwd`+`gitBranch` on every
    record and an `ai-title`; Codex has `cwd`+`workspace_roots` (no git, no title);
    Hermes has neither cwd nor git nor title (only `platform`). Workspace-location and
    title context is partially or fully lost on any conversion into Hermes.

### Recommended lossless-core / lossy-extras split for the bridge

- **Losslessly portable core:** ordered turns of {user text, assistant text, tool call
  (name + arguments), tool result (payload), reasoning *summary text*}, plus the tool
  call↔result id linkage.
- **Lossy / harness-specific extras (preserve as sidecar metadata, do not assume the
  target can use them):** parentUuid branch topology, encrypted reasoning signatures,
  tool schemas, base/system instructions, queued input, permission/sandbox posture,
  structured `toolUseResult`, attachments/hooks/file snapshots, per-turn model changes,
  cwd/git/title.
