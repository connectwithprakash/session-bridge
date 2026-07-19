# session-bridge

Local-first, cross-harness **agent-session portability**. Export a coding-agent
session from one harness and resume it in another when the original hits a usage
limit or stops.

Supports three harnesses today:

| Harness | Session store |
|---|---|
| Claude Code | `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl` |
| Codex | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` |
| Hermes | `~/.hermes/sessions/<ts>_<id>.jsonl` |

No cloud. Everything runs against files already on your disk.

## Why

Each harness writes an incompatible session log, and nothing bridges them:
Claude Code `/export` is lossy plain text, OpenCode import/export is buggy across
versions. session-bridge normalizes any supported session into one intermediate
representation (IR), then renders it into another harness's shape — and, crucially,
carries the *pending state* (open tool calls, queued input) forward through a
**resume handshake** so the receiving agent picks up deliberately instead of
guessing.

## How it works

```
source.jsonl ─▶ reader ─▶ IR (Session: messages, tools, pending) ─▶ writer ─▶ target.jsonl
                                        │
                                        └─▶ resume handshake (prepended system message)
```

- **IR** (`ir.py`) is the union of what the three harnesses can express: threaded
  messages with typed content blocks (text / reasoning / tool_call / tool_result),
  session metadata, tool schemas, and explicit pending state.
- **Readers** (`readers/`) normalize each harness into the IR.
- **Writers** (`writers/`) render the IR into a target harness and emit a
  `ConversionReport` naming every asymmetry that could not transfer losslessly.
- **Handshake** (`handshake.py`) turns detected pending state + conversion notes
  into a resume preamble injected as the first message of the resumed session.

## Install

```bash
cd session-bridge
python3 -m pip install -e .
```

## Usage

Inspect a session's structure:

```bash
session-bridge inspect --from claude-code ~/.claude/projects/<dir>/<uuid>.jsonl
```

Convert between harnesses:

```bash
session-bridge convert --from hermes --to claude-code SESSION.jsonl \
  -o resumed.jsonl --handshake-out resume.md
```

Conversion notes (lossy asymmetries) are printed to stderr; the handshake is
prepended to the output by default (use `--no-handshake` to disable).

## What transfers, and what doesn't

The conversation core — user/assistant text, reasoning summaries, tool calls,
tool results, and call↔result linkage — transfers between all three harnesses.
The following are **inherently lossy** and are reported per conversion (see
`docs/schema-reference.md` for the full analysis):

1. **Thread topology** — only Claude Code has `parentUuid` branches; converting
   away flattens forks, converting in synthesizes a linear chain.
2. **Reasoning signatures** — provider-bound; reasoning survives as summary text.
3. **Tool schemas** — only Hermes stores them; reconstructed from invoked names otherwise.
4. **Base/system instructions** — only Codex stores them.
5. **Queued user input** — only Claude Code records it; surfaced in the handshake.
6. **Permission/sandbox posture** — richest in Codex, absent in Hermes.
7. **Per-turn model switches** — Hermes stores a single session model.

## Known limitations

- Codex tool-call records (`function_call` / `function_call_output`) are handled
  per the documented Responses shape but were not present in local sample data;
  covered by fixtures, pending validation against a tool-using Codex session.
- Queued-input detection is conservative: it may over-report undelivered input
  rather than silently drop it (the safe direction for resume).
- Pending-state resumption produces a handshake for a human/agent to act on; it
  does not itself re-execute open tool calls.

## Development

```bash
python3 -m pytest        # 46 tests: IR, three readers, writers/round-trips, handshake, CLI
```

Real captured sessions may contain secrets; `fixtures/real/` is gitignored and
tests run only against synthetic, faithful fixtures.
