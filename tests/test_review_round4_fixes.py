"""Regression tests for Round-4 self-review findings."""

import json
import sqlite3

from session_bridge.ir import (
    BlockType,
    ContentBlock,
    Message,
    PendingState,
    Role,
    Session,
    SessionMeta,
    recover_tool_error,
)
from session_bridge.readers.claude_code import _queued_messages
from session_bridge.readers.codex import read_codex
from session_bridge.readers.hermes import read_hermes
from session_bridge.writers._common import report_losses
from session_bridge.writers.codex import write_codex
from session_bridge.writers.hermes import write_hermes
from session_bridge.writers.hermes_db import register_hermes_session


# ---- CRITICAL: is_error survives a multi-hop round trip ----

def test_recover_tool_error_helper():
    assert recover_tool_error("[tool error] boom") == ("boom", True)
    assert recover_tool_error("fine") == ("fine", False)


def test_is_error_survives_codex_round_trip(tmp_path):
    # a failed result written to codex bakes ERROR_MARKER; reading back recovers it
    s = Session(
        meta=SessionMeta(source_harness="claude-code", session_id="s", model="m"),
        messages=(Message(role=Role.TOOL, content=(
            ContentBlock.tool_result("c1", "boom", is_error=True),
        )),),
    )
    records, _ = write_codex(s)
    f = tmp_path / "c.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    back = read_codex(f)
    result = next(b for m in back.messages for b in m.content if b.type is BlockType.TOOL_RESULT)
    assert result.is_error is True
    assert result.text == "boom"  # marker stripped, not doubled


def test_is_error_survives_hermes_round_trip(tmp_path):
    s = Session(
        meta=SessionMeta(source_harness="x", model="m"),
        messages=(Message(role=Role.TOOL, content=(
            ContentBlock.tool_result("c1", "boom", is_error=True),
        )),),
    )
    records, _ = write_hermes(s)
    f = tmp_path / "h.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    back = read_hermes(f)
    result = next(b for m in back.messages for b in m.content if b.type is BlockType.TOOL_RESULT)
    assert result.is_error is True
    assert result.text == "boom"


# ---- HIGH: write_codex keeps a multi-block message as ONE turn ----

def test_codex_multiple_text_blocks_emit_one_message_record(tmp_path):
    # two TEXT blocks in one IR message must produce ONE codex message record
    # (not two), else the turn count inflates. A tool_call is a separate
    # response_item by Codex's own model, so it is not merged — only text is.
    s = Session(
        meta=SessionMeta(source_harness="claude-code", session_id="s", model="m"),
        messages=(
            Message(role=Role.ASSISTANT, content=(
                ContentBlock.text_block("line one"),
                ContentBlock.text_block("line two"),
            )),
        ),
    )
    records, _ = write_codex(s)
    msg_records = [
        r for r in records
        if r.get("type") == "response_item" and r["payload"].get("type") == "message"
    ]
    assert len(msg_records) == 1
    assert msg_records[0]["payload"]["content"][0]["text"] == "line one\nline two"


def test_codex_multi_text_block_message_is_one_turn(tmp_path):
    s = Session(
        meta=SessionMeta(source_harness="hermes", session_id="s", model="m"),
        messages=(Message(role=Role.USER, content=(
            ContentBlock.text_block("look"),
            ContentBlock.raw({"type": "image_url", "image_url": {"url": "x"}}, "image_url"),
        )),),
    )
    records, _ = write_codex(s)
    f = tmp_path / "c.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    back = read_codex(f)
    assert len(back.messages) == 1  # one user turn, not two


# ---- HIGH: per-turn model switch reported ----

def test_per_turn_model_switch_reported():
    s = Session(
        meta=SessionMeta(source_harness="claude-code", model="claude-sonnet-4"),
        messages=(
            Message(role=Role.ASSISTANT, content=(ContentBlock.text_block("a"),),
                    raw={"message": {"model": "claude-sonnet-4"}}),
            Message(role=Role.ASSISTANT, content=(ContentBlock.text_block("b"),),
                    raw={"message": {"model": "claude-opus-4-8"}}),
        ),
    )
    report = report_losses(s, "claude-code")
    assert any("models used across turns" in w for w in report.warnings)


def test_single_model_not_reported():
    s = Session(
        meta=SessionMeta(source_harness="claude-code", model="m"),
        messages=(
            Message(role=Role.ASSISTANT, content=(ContentBlock.text_block("a"),),
                    raw={"message": {"model": "m"}}),
        ),
    )
    report = report_losses(s, "claude-code")
    assert not any("models used across turns" in w for w in report.warnings)


# ---- MEDIUM: duplicate queued messages not undercounted ----

def test_duplicate_queued_messages_counted():
    records = [
        {"type": "queue-operation", "operation": "enqueue", "sessionId": "s", "content": "continue"},
        {"type": "queue-operation", "operation": "enqueue", "sessionId": "s", "content": "continue"},
        {"type": "queue-operation", "operation": "enqueue", "sessionId": "s", "content": "continue"},
        {"type": "queue-operation", "operation": "dequeue", "sessionId": "s"},
    ]
    # 3 enqueued, 1 dequeued -> 2 still pending (both identical)
    assert _queued_messages(records) == ("continue", "continue")


# ---- MEDIUM: hermes_db message_count = IR turns, not rows ----

def _hermes_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, model TEXT, "
        "started_at REAL NOT NULL, message_count INTEGER DEFAULT 0, tool_call_count INTEGER DEFAULT 0, "
        "title TEXT, cwd TEXT, archived INTEGER NOT NULL DEFAULT 0);"
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES sessions(id), "
        "role TEXT NOT NULL, content TEXT, tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, "
        "timestamp REAL NOT NULL, reasoning TEXT);"
    )
    conn.commit()
    conn.close()


def test_hermes_db_message_count_is_turns_not_rows(tmp_path):
    db = tmp_path / "state.db"
    _hermes_db(db)
    s = Session(
        meta=SessionMeta(source_harness="claude-code", model="m"),
        messages=(
            Message(role=Role.ASSISTANT, content=(
                ContentBlock.tool_call("c1", "A", {}),
                ContentBlock.tool_call("c2", "B", {}),
            )),
            Message(role=Role.USER, content=(
                ContentBlock.tool_result("c1", "one"),
                ContentBlock.tool_result("c2", "two"),
            )),
        ),
    )
    register_hermes_session(s, str(db), "s1", started_at=1.0)
    conn = sqlite3.connect(db)
    mc = conn.execute("SELECT message_count FROM sessions WHERE id='s1'").fetchone()[0]
    rows = conn.execute("SELECT COUNT(*) FROM messages WHERE session_id='s1'").fetchone()[0]
    conn.close()
    assert mc == 2            # 2 IR turns
    assert rows >= 3          # more rows (2 results split out) — count is NOT rows
