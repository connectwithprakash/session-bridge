# Tutorial: resume a stalled session in another harness

You were mid-task in one coding agent and it stopped: usage limit hit, a crash,
or you just want to continue in a different tool. This walks you from that dead
session file to a live, resumable one in another harness.

The whole flow is five steps:

```
  find session ─▶ inspect ─▶ convert ─▶ read handshake ─▶ resume
     (locate)     (sanity)   (translate)  (what to do)   (in target)
```

There's a real end-to-end example at the end (Hermes → Claude Code) with actual
command output.

---

## 0. Install

```bash
cd session-bridge
python3 -m pip install -e .
```

This puts a `session-bridge` command on your path. Verify:

```bash
session-bridge --help
```

---

## 1. Find your session file

Each harness stores sessions in its own directory. Find the one you want to
resume (usually the most recent):

| Harness | Where sessions live | Find the latest |
|---|---|---|
| Claude Code | `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl` | `ls -t ~/.claude/projects/*/*.jsonl \| head` |
| Codex | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | `ls -t ~/.codex/sessions/**/rollout-*.jsonl \| head` |
| Hermes | `~/.hermes/sessions/<timestamp>_<id>.jsonl` | `ls -t ~/.hermes/sessions/*.jsonl \| head` |

Claude Code's directory name is your working directory with slashes replaced by
dashes, so a session for `~/Developer/foo` lives under a folder like
`-Users-you-Developer-foo`.

---

## 2. Inspect it (sanity check before converting)

`inspect` parses the session and prints its structure: how many turns, how many
tool calls, and whether it stopped mid-turn with **pending state**.

```bash
session-bridge inspect --from hermes ~/.hermes/sessions/<file>.jsonl
```

Read the `pending state` block at the bottom:

- `open tool calls: []` and `queued user input: 0` mean it stopped cleanly, so
  resuming is straightforward.
- Anything non-empty means the source stopped mid-turn with that state
  outstanding at the tail: a tool call issued but never answered, or input
  queued but never delivered. Only trailing-unresolved calls count; an
  errored-then-abandoned call from deep in a long session (where the session
  later moved on and ended cleanly) is not reported. The handshake (step 4)
  carries the genuinely-outstanding state forward.

---

## 3. Convert to the target harness

```bash
session-bridge convert \
  --from hermes --to claude-code \
  ~/.hermes/sessions/<file>.jsonl \
  -o resumed.jsonl \
  --handshake-out resume.md
```

- `--from` / `--to` are any of `claude-code`, `codex`, `hermes`.
- `-o` is the converted session file, in the target's format.
- `--handshake-out` writes the resume instructions to a separate Markdown file
  (optional but recommended; see step 4).

**Conversion notes print to stderr.** These are the things that could not
transfer losslessly (dropped tool schemas, flattened thread branches, reasoning
signatures that can't cross providers). They are informational. The
*conversation core* always survives; the notes just tell you what degraded.

Add `--no-handshake` if you want a raw transcript with no injected resume message.

---

## 4. Read the resume handshake

The handshake (`resume.md`, and by default also prepended as the first message of
`resumed.jsonl`) is the instruction block that makes resumption deliberate. It
tells the receiving agent what to do before continuing:

- **Original context:** source model, working directory, turn count.
- **Pending state:** open tool calls to re-run or abandon, and queued user input
  that was never processed. If the source stopped cleanly, this says "continue
  normally."
- **Conversion notes:** what didn't transfer, so the agent doesn't assume a
  dropped tool schema or lost branch was intentional.

If there are open tool calls, the handshake names each one (tool + arguments) so
you or the agent can re-run it before proceeding.

---

## 5. Resume in the target harness

**Claude Code target (one command).** Add `--place-claude-cwd <dir>` to the
convert in step 3. session-bridge writes the transcript to the exact path Claude
Code resolves and prints the resume command:

```bash
session-bridge convert --from hermes --to claude-code SESSION.jsonl \
  --place-claude-cwd ~/Developer/myproject
# placed resumable session -> ~/.claude/projects/.../<uuid>.jsonl
# resume with:  (cd ~/Developer/myproject && claude --resume <uuid>)
```

Run that printed command and the session continues live. Verified end-to-end: a
converted session resumed in a real `claude` process and recalled a fact that
existed only in the converted transcript.

**Hermes target (one command).** Hermes keeps sessions in a SQLite store, so a
dropped-in file is not enough. Use `register` instead of manual placement; it
backs up the store first and prints the resume command:

```bash
session-bridge register --from claude-code SESSION.jsonl \
  --model moonshotai/kimi-k3 --title "resumed session"
# resume with:  hermes --resume sb_...
```

Set `--model` to a model Hermes is configured for, or the resumed turn loses
context. Verified end-to-end: the resumed session recalls its prior history.

Because the handshake is the first message, once a target loads the session the
agent picks up knowing exactly where the previous one left off.

---

## Full worked example: Hermes → Claude Code

A real 20-turn Hermes session (6 tool calls) resumed into Claude Code.

**Inspect:**

```
$ session-bridge inspect --from hermes ~/.hermes/sessions/20260416_004124_10f12c82.jsonl
source harness : hermes
session id     : None
model          : gpt-5.4  (provider: None)
cwd            : None
permission     : None
messages       : 20
  text blocks    : 10
  reasoning      : 4
  tool calls     : 6
  tool results   : 6
tool schemas   : 40
pending state  :
  open tool calls    : []
  queued user input  : 0
  active goal        : -
```

Clean stop (`open tool calls: []`), 6 tool calls, 40 tool schemas available.

**Convert:**

```
$ session-bridge convert --from hermes --to claude-code \
    ~/.hermes/sessions/20260416_004124_10f12c82.jsonl \
    -o resumed.jsonl --handshake-out resume.md

2 conversion note(s):
  - reasoning is carried as summary text only; provider-bound signatures /
    encrypted reasoning cannot be re-signed for the target model.
  - 40 tool schema(s) from hermes are dropped; claude-code supplies its own
    tool definitions at runtime.
wrote resume handshake -> resume.md
wrote 21 records -> resumed.jsonl
```

21 records is the 20 original turns plus 1 injected handshake. The two notes are
the expected Hermes→Claude Code asymmetries: reasoning survives as text (not a
re-signable blob), and Hermes's 40 tool schemas are dropped because Claude Code
supplies its own at runtime.

**Handshake (`resume.md`):**

```markdown
# Session resume handshake

This session was exported from **hermes** and resumed in **claude-code**
by session-bridge. Read this before continuing.

## Original context
- Source model: `gpt-5.4`
- Turns carried over: 20

## Pending state
- None. The source stopped at a clean turn boundary; continue normally.

## Conversion notes (what did not transfer losslessly)
- reasoning is carried as summary text only; ...
- 40 tool schema(s) from hermes are dropped; ...
```

`resumed.jsonl` is now a valid Claude Code session. Drop it into
`~/.claude/projects/<dir>/` and continue.

---

## Troubleshooting

- **`unknown source harness`:** `--from`/`--to` must be exactly `claude-code`,
  `codex`, or `hermes`.
- **Inspect shows 0 messages:** you likely pointed at a non-session `.jsonl`
  (e.g. a log). Confirm the path matches the table in step 1.
- **Conversion notes look alarming:** they're informational, not errors. The
  conversation core (messages, tool calls, results) always transfers; the notes
  only flag degraded extras.
- **Codex sessions that used tools:** the Codex tool-call path follows the
  documented format but has not yet been validated against a real tool-using
  Codex log (see README "Known limitations").

For the format-level detail behind all this, see
[`docs/schema-reference.md`](docs/schema-reference.md).
```
