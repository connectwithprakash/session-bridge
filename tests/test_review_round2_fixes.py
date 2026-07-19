"""Regression tests for Round-2 self-review findings."""

import json
import sqlite3

import pytest

from session_bridge._ids import UnsafeSessionIdError, validate_session_id
from session_bridge.convert import convert
from session_bridge.handshake import build_handshake, is_handshake_message
from session_bridge.ir import (
    BlockType,
    ContentBlock,
    Message,
    PendingState,
    Role,
    Session,
    SessionMeta,
)
from session_bridge.place import place_claude_code
from session_bridge.readers._jsonl import load_records
from session_bridge.readers.claude_code import read_claude_code
from session_bridge.readers.codex import read_codex
from session_bridge.writers._common import report_losses
from session_bridge.writers.claude_code import write_claude_code
from session_bridge.writers.codex import write_codex
from session_bridge.writers.hermes import write_hermes


# ---- CRITICAL: path traversal via session id ----

def test_validate_session_id_rejects_traversal():
    for bad in ["../../evil", "a/b", "..", "", "with space", "x/../y"]:
        with pytest.raises(UnsafeSessionIdError):
            validate_session_id(bad)
    for good in ["019f78cd-3661", "sb_123_abcdef", "abc123"]:
        assert validate_session_id(good) == good


def test_cli_convert_place_rejects_traversal_cleanly(tmp_path):
    from session_bridge.cli import main

    src = tmp_path / "in.jsonl"
    src.write_text(
        '{"parentUuid":null,"type":"user","message":{"role":"user","content":"x"},"uuid":"u1","cwd":"/t","sessionId":"s"}\n',
        encoding="utf-8",
    )
    rc = main([
        "convert", "--from", "claude-code", "--to", "claude-code", str(src),
        "-o", str(tmp_path / "out.jsonl"),
        "--place-claude-cwd", str(tmp_path), "--session-id", "../../evil",
    ])
    assert rc == 2  # clean rejection, not a traceback


def test_place_claude_code_rejects_traversal_id(tmp_path):
    home = tmp_path / ".claude"
    cwd = str(tmp_path / "work")
    import os
    os.makedirs(cwd, exist_ok=True)
    with pytest.raises(UnsafeSessionIdError):
        place_claude_code([], cwd, "../../evil", claude_home=home)
    # nothing escaped
    assert not (tmp_path / "evil.jsonl").exists()


# ---- interior corrupt line must fail loudly; tail truncation tolerated ----

def test_interior_corrupt_line_raises(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text(
        '{"type":"user","message":{"role":"user","content":"a"},"uuid":"u1","parentUuid":null}\n'
        "{ this is broken json interior line\n"
        '{"type":"user","message":{"role":"user","content":"b"},"uuid":"u2","parentUuid":"u1"}\n',
        encoding="utf-8",
    )
    with pytest.raises(json.JSONDecodeError):
        load_records(f)


def test_tail_truncation_still_tolerated(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text(
        '{"type":"user","message":{"role":"user","content":"a"},"uuid":"u1","parentUuid":null}\n'
        '{"type":"user","message":{"role":"user","content":"b",',  # truncated tail
        encoding="utf-8",
    )
    recs = load_records(f)
    assert len(recs) == 1


# ---- RAW passthrough: same-harness lossless, cross-harness degrades+reports ----

def _image_session():
    return Session(
        meta=SessionMeta(source_harness="claude-code", model="m"),
        messages=(Message(role=Role.USER, content=(
            ContentBlock.raw({"type": "image", "source": {"data": "AAA"}}, "image"),
        )),),
    )


def test_raw_block_round_trips_lossless_same_harness(tmp_path):
    records, report = write_claude_code(_image_session())
    f = tmp_path / "cc.jsonl"
    # give the record a uuid/cwd so it re-reads
    f.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    back = read_claude_code(f)
    block = back.messages[0].content[0]
    assert block.type is BlockType.RAW
    assert block.raw_block == {"type": "image", "source": {"data": "AAA"}}
    # same-harness: no loss reported
    assert not any(("no IR representation" in w) or ("degrade to a text placeholder" in w)
                   for w in report.warnings)


def test_raw_block_degrades_and_reports_cross_harness():
    _, report = write_hermes(_image_session())
    assert any("no IR representation" in w or "placeholder" in w for w in report.warnings)
    records, _ = write_hermes(_image_session())
    user = next(r for r in records if r.get("role") == "user")
    assert "image" in user["content"]  # placeholder present, not silently dropped


# ---- handshake accumulation on multi-hop ----

def test_handshake_not_accumulated_across_hops(tmp_path):
    src = tmp_path / "o.jsonl"
    src.write_text(
        '{"type":"user","message":{"role":"user","content":"hi"},"uuid":"u1","parentUuid":null,"cwd":"/t","sessionId":"s"}\n',
        encoding="utf-8",
    )
    hop1 = convert("claude-code", "claude-code", src)
    f1 = tmp_path / "hop1.jsonl"
    f1.write_text("\n".join(json.dumps(r) for r in hop1.records) + "\n", encoding="utf-8")
    hop2 = convert("claude-code", "claude-code", f1)
    # exactly one handshake in hop2's session, not two
    session2 = read_claude_code(f1)
    from session_bridge.handshake import strip_prior_handshakes
    stripped = strip_prior_handshakes(session2)
    handshakes = [m for m in session2.messages if is_handshake_message(m)]
    assert len(handshakes) == 1
    assert all(not is_handshake_message(m) for m in stripped.messages)


# ---- _open_call_details: reissued call shows only the open occurrence ----

def test_open_call_details_uses_last_issue():
    session = Session(
        meta=SessionMeta(source_harness="x"),
        messages=(
            Message(role=Role.ASSISTANT, content=(ContentBlock.tool_call("c1", "Bash", {"cmd": "first"}),)),
            Message(role=Role.TOOL, content=(ContentBlock.tool_result("c1", "done"),)),
            Message(role=Role.ASSISTANT, content=(ContentBlock.tool_call("c1", "Bash", {"cmd": "second"}),)),
        ),
        pending=PendingState(open_tool_calls=("c1",)),
    )
    text = build_handshake(session, report_losses(session, "hermes"), "hermes")
    assert "second" in text
    assert "first" not in text  # resolved earlier occurrence not listed


# ---- _TARGET_CAPS honesty: claude-code loss of permission/queued reported ----

def test_permission_loss_reported_for_claude_code_target():
    session = Session(
        meta=SessionMeta(source_harness="codex", permission_mode="on-request"),
        messages=(),
    )
    report = report_losses(session, "claude-code")
    assert any("permission" in w.lower() for w in report.warnings)


def test_queued_input_reported_for_claude_code_target():
    session = Session(
        meta=SessionMeta(source_harness="claude-code"),
        messages=(),
        pending=PendingState(queued_user_messages=("later",)),
    )
    report = report_losses(session, "claude-code")
    assert any("queued" in w.lower() for w in report.warnings)


# ---- codex reader: string "false" success flag ----

def test_codex_string_false_success_marks_error(tmp_path):
    f = tmp_path / "c.jsonl"
    f.write_text(
        json.dumps({"timestamp": "t", "type": "session_meta", "payload": {"id": "s", "cwd": "/t"}}) + "\n"
        + json.dumps({"timestamp": "t", "type": "response_item", "payload": {
            "type": "function_call_output", "call_id": "c1",
            "output": {"content": "x", "success": "false"}}}) + "\n",
        encoding="utf-8",
    )
    session = read_codex(f)
    result = next(b for m in session.messages for b in m.content if b.type is BlockType.TOOL_RESULT)
    assert result.is_error is True


# ---- codex empty reasoning round-trips (count stable) ----

def test_codex_empty_reasoning_round_trips(tmp_path):
    s = Session(
        meta=SessionMeta(source_harness="x", session_id="s", model="m"),
        messages=(
            Message(role=Role.ASSISTANT, content=(ContentBlock.reasoning(""),)),
            Message(role=Role.USER, content=(ContentBlock.text_block("hi"),)),
        ),
    )
    records, _ = write_codex(s)
    f = tmp_path / "c.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    back = read_codex(f)
    assert len(back.messages) == 2
