"""Regression tests for Round-1 self-review findings."""

import json
import sqlite3

import pytest

from session_bridge.convert import convert
from session_bridge.ir import (
    BlockType,
    ContentBlock,
    Message,
    Role,
    Session,
    SessionMeta,
)
from session_bridge.readers._jsonl import load_records
from session_bridge.readers.claude_code import read_claude_code
from session_bridge.readers.codex import read_codex
from session_bridge.readers.hermes import read_hermes
from session_bridge.writers._common import ERROR_MARKER
from session_bridge.writers.codex import write_codex
from session_bridge.writers.hermes_db import (
    HermesRegistrationError,
    register_hermes_session,
)


# ---- robust JSONL loading (crash class) ----

def test_loader_skips_non_dict_and_truncated_lines(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text(
        '{"type":"user","message":{"role":"user","content":"a"},"uuid":"u1","parentUuid":null}\n'
        "null\n"
        '"a bare string"\n'
        "[1,2,3]\n"
        '{"type":"assistant","message":{"role":"assistant","content":[]},"uuid":"a1","parentUuid":"u1"}\n'
        '{"type":"user","message":{"role":"user","content":"tr',  # truncated final line, no newline
        encoding="utf-8",
    )
    recs = load_records(f)
    # 2 valid dict records survive; non-dict + truncated skipped, no crash
    assert len(recs) == 2
    assert all(isinstance(r, dict) for r in recs)


def test_reader_survives_truncated_final_line(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text(
        '{"type":"user","message":{"role":"user","content":"hello"},"uuid":"u1","parentUuid":null}\n'
        '{"type":"assistant","message":{"role":"assist',  # partial write
        encoding="utf-8",
    )
    session = read_claude_code(f)
    assert len(session.messages) == 1
    assert session.messages[0].text() == "hello"


# ---- Codex SYSTEM handshake role ----

def test_codex_writer_emits_system_role_not_assistant():
    s = Session(
        meta=SessionMeta(source_harness="hermes", session_id="x", model="m"),
        messages=(Message(role=Role.SYSTEM, content=(ContentBlock.text_block("resume note"),)),),
    )
    records, _ = write_codex(s)
    msg = next(r for r in records if r["payload"].get("type") == "message")
    assert msg["payload"]["role"] == "system"


def test_convert_to_codex_handshake_is_system(tmp_path):
    src = tmp_path / "o.jsonl"
    src.write_text(
        '{"type":"user","message":{"role":"user","content":"hi"},"uuid":"u1","parentUuid":null,"cwd":"/t","sessionId":"s"}\n',
        encoding="utf-8",
    )
    result = convert("claude-code", "codex", src)  # handshake injected by default
    msgs = [r for r in result.records if r["payload"].get("type") == "message"]
    system_msgs = [m for m in msgs if m["payload"]["role"] == "system"]
    assert system_msgs and "resume handshake" in system_msgs[0]["payload"]["content"][0]["text"].lower()


# ---- Codex empty-message round-trip ----

def test_codex_empty_message_round_trips(tmp_path):
    s = Session(
        meta=SessionMeta(source_harness="x", session_id="s", model="m"),
        messages=(
            Message(role=Role.USER, content=()),
            Message(role=Role.ASSISTANT, content=(ContentBlock.text_block("hi"),)),
        ),
    )
    records, _ = write_codex(s)
    f = tmp_path / "c.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    back = read_codex(f)
    assert len(back.messages) == 2  # empty user turn preserved


# ---- Hermes DB parallel tool results ----

def _hermes_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, model TEXT, "
        "started_at REAL NOT NULL, message_count INTEGER DEFAULT 0, tool_call_count INTEGER DEFAULT 0, "
        "title TEXT, cwd TEXT, archived INTEGER NOT NULL DEFAULT 0);"
        "CREATE UNIQUE INDEX idx_title ON sessions(title) WHERE title IS NOT NULL;"
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES sessions(id), "
        "role TEXT NOT NULL, content TEXT, tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, "
        "timestamp REAL NOT NULL, reasoning TEXT);"
    )
    conn.commit()
    conn.close()


def test_hermes_db_parallel_tool_results_kept_distinct(tmp_path):
    db = tmp_path / "state.db"
    _hermes_db(db)
    s = Session(
        meta=SessionMeta(source_harness="claude-code", model="m"),
        messages=(
            Message(role=Role.ASSISTANT, content=(
                ContentBlock.tool_call("c1", "A", {}),
                ContentBlock.tool_call("c2", "B", {}),
            )),
            # one IR message carrying two parallel results (Claude Code pattern)
            Message(role=Role.USER, content=(
                ContentBlock.tool_result("c1", "result one"),
                ContentBlock.tool_result("c2", "result two"),
            )),
        ),
    )
    register_hermes_session(s, str(db), "sess1", started_at=1.0)
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT tool_call_id, content FROM messages WHERE role='tool' ORDER BY tool_call_id"
    ).fetchall()
    conn.close()
    assert rows == [("c1", "result one"), ("c2", "result two")]


def test_hermes_db_error_result_marked(tmp_path):
    db = tmp_path / "state.db"
    _hermes_db(db)
    s = Session(
        meta=SessionMeta(source_harness="x", model="m"),
        messages=(Message(role=Role.TOOL, content=(
            ContentBlock.tool_result("c1", "boom", is_error=True),
        )),),
    )
    register_hermes_session(s, str(db), "s1", started_at=1.0)
    conn = sqlite3.connect(db)
    content = conn.execute("SELECT content FROM messages WHERE tool_call_id='c1'").fetchone()[0]
    conn.close()
    assert content.startswith(ERROR_MARKER)


def test_hermes_db_wraps_integrity_error(tmp_path):
    db = tmp_path / "state.db"
    _hermes_db(db)
    s = Session(meta=SessionMeta(source_harness="x"), messages=())
    register_hermes_session(s, str(db), "dup", title="", started_at=1.0)
    # empty-string title bypasses the advisory pre-check but trips the UNIQUE
    # index at INSERT; must surface as HermesRegistrationError, not raw sqlite3
    with pytest.raises(HermesRegistrationError):
        register_hermes_session(s, str(db), "dup2", title="", started_at=1.0)


# ---- Hermes reader list-valued content ----

def test_hermes_reader_handles_list_user_content(tmp_path):
    f = tmp_path / "h.jsonl"
    f.write_text(
        json.dumps({"role": "session_meta", "model": "m", "tools": []}) + "\n"
        + json.dumps({"role": "user", "content": [
            {"type": "text", "text": "look at this"},
            {"type": "image_url", "image_url": {"url": "x"}},
        ]}) + "\n",
        encoding="utf-8",
    )
    session = read_hermes(f)
    # no crash; text part extracted, image part skipped
    assert session.messages[0].text() == "look at this"


# ---- Codex function_call_output dict is_error ----

def test_codex_reader_marks_failed_dict_output(tmp_path):
    f = tmp_path / "c.jsonl"
    f.write_text(
        json.dumps({"timestamp": "t", "type": "session_meta", "payload": {"id": "s", "cwd": "/t"}}) + "\n"
        + json.dumps({"timestamp": "t", "type": "response_item", "payload": {
            "type": "function_call", "name": "sh", "arguments": "{}", "call_id": "c1"}}) + "\n"
        + json.dumps({"timestamp": "t", "type": "response_item", "payload": {
            "type": "function_call_output", "call_id": "c1",
            "output": {"content": "boom", "success": False}}}) + "\n",
        encoding="utf-8",
    )
    session = read_codex(f)
    result = next(b for m in session.messages for b in m.content if b.type is BlockType.TOOL_RESULT)
    assert result.is_error is True
    assert "boom" in result.text


# ---- Claude reader unknown block preserved visibly ----

def test_claude_reader_preserves_unknown_block(tmp_path):
    f = tmp_path / "cc.jsonl"
    f.write_text(
        json.dumps({"type": "user", "parentUuid": None, "uuid": "u1", "cwd": "/t", "sessionId": "s",
                    "message": {"role": "user", "content": [
                        {"type": "image", "source": {"data": "..."}}]}}) + "\n",
        encoding="utf-8",
    )
    session = read_claude_code(f)
    # content not silently empty; preserved as a RAW passthrough block that keeps
    # the original verbatim (lossless same-harness) with a placeholder for display
    assert session.messages[0].content
    block = session.messages[0].content[0]
    assert block.type is BlockType.RAW
    assert block.raw_kind == "image"
    assert block.raw_block == {"type": "image", "source": {"data": "..."}}
    assert "image" in (block.text or "")
