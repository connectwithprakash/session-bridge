"""Tests for --stub-open-calls: appending a synthetic interrupted tool_result for
genuinely-open tool calls so the output is a provider-valid transcript on resume.

Grounded in documented provider behavior: OpenAI Responses returns 400 'No tool
output found for function call' for an unmatched function_call, and Anthropic
rejects a tool_use with no tool_result. The industry-standard fix is an error
tool_result marking the call interrupted."""

import json

from session_bridge.convert import convert
from session_bridge.handshake import INTERRUPTED_RESULT_TEXT, stub_open_tool_calls
from session_bridge.ir import (
    BlockType,
    ContentBlock,
    Message,
    PendingState,
    Role,
    Session,
    SessionMeta,
)
from session_bridge.readers._pending import open_tool_calls
from session_bridge.readers.codex import read_codex


def _open_session():
    msgs = (
        Message(role=Role.ASSISTANT, content=(
            ContentBlock.text_block("running it"),
            ContentBlock.tool_call("OPEN", "Bash", {"cmd": "make"}),
        )),
    )
    return Session(
        meta=SessionMeta(source_harness="claude-code", session_id="s", model="m"),
        messages=msgs,
        pending=PendingState(open_tool_calls=open_tool_calls(msgs)),
    )


def test_stub_adds_error_result_for_open_call():
    stubbed = stub_open_tool_calls(_open_session())
    results = [b for m in stubbed.messages for b in m.content if b.type is BlockType.TOOL_RESULT]
    assert any(b.call_id == "OPEN" and b.is_error for b in results)
    assert any(INTERRUPTED_RESULT_TEXT in (b.text or "") for b in results)
    # pending open calls cleared (now resolved as interrupted)
    assert stubbed.pending.open_tool_calls == ()


def test_stub_is_noop_when_no_open_calls():
    s = Session(
        meta=SessionMeta(source_harness="x", model="m"),
        messages=(
            Message(role=Role.ASSISTANT, content=(ContentBlock.tool_call("c1", "B", {}),)),
            Message(role=Role.TOOL, content=(ContentBlock.tool_result("c1", "ok"),)),
        ),
        pending=PendingState(),
    )
    assert stub_open_tool_calls(s) is s  # unchanged (no open calls)


def test_stub_multiple_open_calls():
    msgs = (
        Message(role=Role.ASSISTANT, content=(
            ContentBlock.tool_call("A", "X", {}),
            ContentBlock.tool_call("B", "Y", {}),
        )),
    )
    s = Session(meta=SessionMeta(source_harness="claude-code"), messages=msgs,
                pending=PendingState(open_tool_calls=open_tool_calls(msgs)))
    stubbed = stub_open_tool_calls(s)
    resolved = {b.call_id for m in stubbed.messages for b in m.content
                if b.type is BlockType.TOOL_RESULT}
    assert resolved == {"A", "B"}


def test_convert_stub_makes_transcript_resumable(tmp_path):
    src = tmp_path / "in.jsonl"
    src.write_text(
        json.dumps({"parentUuid": None, "type": "assistant", "uuid": "a1", "cwd": "/t",
                    "sessionId": "s", "message": {"role": "assistant", "model": "m",
                    "content": [{"type": "tool_use", "id": "OPEN", "name": "Bash", "input": {}}]}}) + "\n",
        encoding="utf-8",
    )
    # with stubbing: the open call gets a result -> valid transcript
    result = convert("claude-code", "codex", src, inject_handshake=False, stub_open_calls=True)
    out = tmp_path / "out.jsonl"
    out.write_text("\n".join(json.dumps(r) for r in result.records) + "\n", encoding="utf-8")
    back = read_codex(out)
    results = [b for m in back.messages for b in m.content if b.type is BlockType.TOOL_RESULT]
    assert any(b.call_id == "OPEN" for b in results)
    # the report (built pre-stub) still discloses the interruption
    assert any("no matching result" in w for w in result.report.warnings)


def _hermes_db(path):
    import sqlite3
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, model TEXT, "
        "started_at REAL NOT NULL, message_count INTEGER DEFAULT 0, tool_call_count INTEGER DEFAULT 0, "
        "title TEXT, cwd TEXT, archived INTEGER NOT NULL DEFAULT 0);"
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES sessions(id), "
        "role TEXT NOT NULL, content TEXT, tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, "
        "timestamp REAL NOT NULL, reasoning TEXT);"
    )
    conn.commit(); conn.close()


def _open_call_source(tmp_path):
    src = tmp_path / "in.jsonl"
    src.write_text(
        json.dumps({"parentUuid": None, "type": "assistant", "uuid": "a1", "cwd": "/t",
                    "sessionId": "s", "message": {"role": "assistant", "model": "m",
                    "content": [{"type": "tool_use", "id": "OPEN", "name": "Bash", "input": {}}]}}) + "\n",
        encoding="utf-8",
    )
    return src


def test_register_stub_open_calls_makes_db_resumable(tmp_path):
    # r24: register must have the same open-call remediation convert has.
    import sqlite3
    from session_bridge.cli import main

    db = tmp_path / "state.db"
    _hermes_db(db)
    rc = main(["register", "--from", "claude-code", str(_open_call_source(tmp_path)),
               "--db", str(db), "--model", "gpt-x", "--no-backup",
               "--session-id", "reg_stub", "--stub-open-calls"])
    assert rc == 0
    conn = sqlite3.connect(db)
    tool_rows = conn.execute(
        "SELECT tool_call_id FROM messages WHERE session_id='reg_stub' AND role='tool'"
    ).fetchall()
    conn.close()
    # the formerly-open call now has a matching tool row -> resumable
    assert ("OPEN",) in tool_rows


def test_register_without_stub_leaves_call_open(tmp_path):
    import sqlite3
    from session_bridge.cli import main

    db = tmp_path / "state.db"
    _hermes_db(db)
    rc = main(["register", "--from", "claude-code", str(_open_call_source(tmp_path)),
               "--db", str(db), "--model", "gpt-x", "--no-backup", "--session-id", "reg_plain"])
    assert rc == 0
    conn = sqlite3.connect(db)
    tool_rows = conn.execute(
        "SELECT tool_call_id FROM messages WHERE session_id='reg_plain' AND role='tool'"
    ).fetchall()
    conn.close()
    # default: no synthetic result (but the warning was printed — see round 22 tests)
    assert ("OPEN",) not in tool_rows


def test_convert_without_stub_leaves_call_open(tmp_path):
    src = tmp_path / "in.jsonl"
    src.write_text(
        json.dumps({"parentUuid": None, "type": "assistant", "uuid": "a1", "cwd": "/t",
                    "sessionId": "s", "message": {"role": "assistant", "model": "m",
                    "content": [{"type": "tool_use", "id": "OPEN", "name": "Bash", "input": {}}]}}) + "\n",
        encoding="utf-8",
    )
    result = convert("claude-code", "codex", src, inject_handshake=False, stub_open_calls=False)
    out = tmp_path / "out.jsonl"
    out.write_text("\n".join(json.dumps(r) for r in result.records) + "\n", encoding="utf-8")
    back = read_codex(out)
    # no synthetic result added (default behavior)
    assert not any(b.type is BlockType.TOOL_RESULT for m in back.messages for b in m.content)
