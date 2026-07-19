"""Regression tests for Round-5 self-review findings."""

import json

from session_bridge.ir import (
    ERROR_MARKER,
    BlockType,
    ContentBlock,
    Message,
    Role,
    Session,
    SessionMeta,
    recover_tool_error,
)
from session_bridge.readers.codex import read_codex
from session_bridge.writers._common import report_losses
from session_bridge.writers.codex import write_codex


# ---- HIGH: write_codex preserves block order (reasoning before text/call) ----

def test_codex_preserves_reasoning_text_call_order(tmp_path):
    s = Session(
        meta=SessionMeta(source_harness="claude-code", session_id="s", model="m"),
        messages=(
            Message(role=Role.ASSISTANT, content=(
                ContentBlock.reasoning("let me think"),
                ContentBlock.text_block("here is the answer"),
                ContentBlock.tool_call("c1", "Bash", {"cmd": "ls"}),
            )),
        ),
    )
    records, _ = write_codex(s)
    f = tmp_path / "c.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    back = read_codex(f)
    # flatten block types in emission order
    types = [b.type for m in back.messages for b in m.content]
    assert types == [BlockType.REASONING, BlockType.TEXT, BlockType.TOOL_CALL]


def test_codex_text_between_two_calls_keeps_position(tmp_path):
    s = Session(
        meta=SessionMeta(source_harness="claude-code", session_id="s", model="m"),
        messages=(
            Message(role=Role.ASSISTANT, content=(
                ContentBlock.tool_call("c1", "A", {}),
                ContentBlock.text_block("between"),
                ContentBlock.tool_call("c2", "B", {}),
            )),
        ),
    )
    records, _ = write_codex(s)
    # response_item payload types in order
    payload_types = [
        r["payload"].get("type") for r in records if r.get("type") == "response_item"
    ]
    assert payload_types == ["function_call", "message", "function_call"]


def test_codex_adjacent_text_still_coalesced(tmp_path):
    # two ADJACENT text blocks still merge to one message (count not inflated)
    s = Session(
        meta=SessionMeta(source_harness="x", session_id="s", model="m"),
        messages=(
            Message(role=Role.ASSISTANT, content=(
                ContentBlock.text_block("one"),
                ContentBlock.text_block("two"),
            )),
        ),
    )
    records, _ = write_codex(s)
    msg_records = [
        r for r in records
        if r.get("type") == "response_item" and r["payload"].get("type") == "message"
    ]
    assert len(msg_records) == 1


# ---- HIGH: RAW loss to claude-code from a FOREIGN source is reported ----

def _codex_sourced_raw_session():
    return Session(
        meta=SessionMeta(source_harness="codex", model="m"),
        messages=(Message(role=Role.USER, content=(
            ContentBlock.raw({"type": "input_image", "image_url": "x"}, "input_image"),
        )),),
    )


def test_foreign_raw_to_claude_code_is_reported():
    from session_bridge.writers.claude_code import write_claude_code
    _, report = write_claude_code(_codex_sourced_raw_session())
    # codex-sourced RAW written to claude-code is NOT lossless -> must warn
    assert any("no IR representation" in w or "placeholder" in w for w in report.warnings)


def test_native_raw_same_harness_still_not_reported():
    from session_bridge.writers.claude_code import write_claude_code
    s = Session(
        meta=SessionMeta(source_harness="claude-code", model="m"),
        messages=(Message(role=Role.USER, content=(
            ContentBlock.raw({"type": "image", "source": {"data": "x"}}, "image"),
        )),),
    )
    _, report = write_claude_code(s)
    assert not any("no IR representation" in w for w in report.warnings)


# ---- HIGH: Codex per-turn model switch reported ----

def test_codex_per_turn_model_switch_reported(tmp_path):
    f = tmp_path / "c.jsonl"
    f.write_text(
        json.dumps({"timestamp": "t", "type": "session_meta", "payload": {"id": "s", "cwd": "/t"}}) + "\n"
        + json.dumps({"timestamp": "t", "type": "turn_context", "payload": {"turn_id": "t1", "model": "gpt-A"}}) + "\n"
        + json.dumps({"timestamp": "t", "type": "response_item", "payload": {
            "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "a"}]}}) + "\n"
        + json.dumps({"timestamp": "t", "type": "turn_context", "payload": {"turn_id": "t2", "model": "gpt-B"}}) + "\n"
        + json.dumps({"timestamp": "t", "type": "response_item", "payload": {
            "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "b"}]}}) + "\n",
        encoding="utf-8",
    )
    session = read_codex(f)
    assert session.meta.extra.get("turn_models") == ["gpt-A", "gpt-B"]
    report = report_losses(session, "claude-code")
    assert any("models used across turns" in w for w in report.warnings)


# ---- MEDIUM: ERROR_MARKER is unforgeable ----

def test_error_marker_no_false_positive():
    # genuine tool output that starts with the human phrase but lacks the token
    text, is_err = recover_tool_error("[tool error] test failed but success:true")
    assert is_err is False
    assert text == "[tool error] test failed but success:true"


def test_error_marker_real_marker_recovered():
    text, is_err = recover_tool_error(ERROR_MARKER + "genuine failure")
    assert is_err is True
    assert text == "genuine failure"
