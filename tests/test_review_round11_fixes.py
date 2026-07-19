"""Regression tests for Round-11 finding: empty-text reasoning blocks.

Real Claude Code extended-thinking blocks have empty visible text (content is in
an opaque signature), so dropping empty-text reasoning loses ~all real reasoning.
"""

import json

from session_bridge.convert import convert
from session_bridge.ir import BlockType, ContentBlock, Message, Role, Session, SessionMeta
from session_bridge.readers.codex import read_codex
from session_bridge.writers._common import report_losses
from session_bridge.writers.codex import write_codex


def _reasoning_count(session):
    return sum(1 for m in session.messages for b in m.content if b.type is BlockType.REASONING)


def test_codex_preserves_empty_text_reasoning_block(tmp_path):
    # write an assistant message with an empty-text reasoning block to codex,
    # read it back: the reasoning block must survive (not be dropped)
    s = Session(
        meta=SessionMeta(source_harness="claude-code", session_id="s", model="m"),
        messages=(Message(role=Role.ASSISTANT, content=(
            ContentBlock.reasoning(""),
            ContentBlock.text_block("the answer"),
        )),),
    )
    records, _ = write_codex(s)
    f = tmp_path / "c.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    back = read_codex(f)
    assert _reasoning_count(back) == 1


def test_empty_reasoning_survives_claude_codex_claude(tmp_path):
    src = tmp_path / "in.jsonl"
    src.write_text(
        json.dumps({
            "parentUuid": None, "type": "assistant", "uuid": "a1", "cwd": "/t", "sessionId": "s",
            "message": {"role": "assistant", "model": "m", "content": [
                {"type": "thinking", "thinking": "", "signature": "AbC=="},
                {"type": "text", "text": "answer"},
            ]},
        }) + "\n",
        encoding="utf-8",
    )
    h1 = convert("claude-code", "codex", src, inject_handshake=False)
    f1 = tmp_path / "codex.jsonl"
    f1.write_text("\n".join(json.dumps(r) for r in h1.records) + "\n", encoding="utf-8")
    h2 = convert("codex", "claude-code", f1, inject_handshake=False)
    f2 = tmp_path / "back.jsonl"
    f2.write_text("\n".join(json.dumps(r) for r in h2.records) + "\n", encoding="utf-8")
    from session_bridge.readers.claude_code import read_claude_code
    assert _reasoning_count(read_claude_code(f2)) == 1


def test_hermes_reports_empty_reasoning_loss():
    s = Session(
        meta=SessionMeta(source_harness="claude-code", model="m"),
        messages=(Message(role=Role.ASSISTANT, content=(
            ContentBlock.reasoning(""),
            ContentBlock.text_block("answer"),
        )),),
    )
    report = report_losses(s, "hermes")
    assert any("no visible text" in w for w in report.warnings)
    assert not report.ok()


def test_codex_target_does_not_falsely_warn_empty_reasoning():
    # the empty-reasoning warning is Hermes-specific (Codex preserves it), so a
    # codex target must NOT emit it
    s = Session(
        meta=SessionMeta(source_harness="claude-code", model="m"),
        messages=(Message(role=Role.ASSISTANT, content=(ContentBlock.reasoning(""),)),),
    )
    report = report_losses(s, "codex")
    assert not any("no visible text" in w for w in report.warnings)
