"""Regression tests for Round-9 self-review findings."""

import sqlite3

from session_bridge.ir import ContentBlock, Message, Role, Session, SessionMeta
from session_bridge.writers.hermes import write_hermes
from session_bridge.writers.hermes_db import register_hermes_session


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


# ---- HIGH: hermes_db preserves text-vs-tool_result order ----

def test_hermes_db_text_before_result_preserved(tmp_path):
    db = tmp_path / "state.db"
    _hermes_db(db)
    # text THEN tool_result in one message: the text row must come first
    s = Session(
        meta=SessionMeta(source_harness="claude-code", model="m"),
        messages=(Message(role=Role.USER, content=(
            ContentBlock.text_block("running the check now"),
            ContentBlock.tool_result("c1", "file1.txt"),
        )),),
    )
    register_hermes_session(s, str(db), "s1", started_at=0.0)
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE session_id='s1' ORDER BY timestamp"
    ).fetchall()
    conn.close()
    assert rows[0][0] == "user" and "running the check" in rows[0][1]
    assert rows[1][0] == "tool" and "file1.txt" in rows[1][1]


def test_hermes_db_result_before_text_preserved(tmp_path):
    db = tmp_path / "state.db"
    _hermes_db(db)
    # tool_result THEN text: the tool row must come first
    s = Session(
        meta=SessionMeta(source_harness="claude-code", model="m"),
        messages=(Message(role=Role.USER, content=(
            ContentBlock.tool_result("c1", "output"),
            ContentBlock.text_block("comment after"),
        )),),
    )
    register_hermes_session(s, str(db), "s1", started_at=0.0)
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE session_id='s1' ORDER BY timestamp"
    ).fetchall()
    conn.close()
    assert rows[0][0] == "tool"
    assert rows[1][0] == "user" and "comment after" in rows[1][1]


def test_hermes_db_matches_jsonl_writer_order(tmp_path):
    # the two Hermes writers must agree on ordering for the same session
    db = tmp_path / "state.db"
    _hermes_db(db)
    s = Session(
        meta=SessionMeta(source_harness="claude-code", model="m"),
        messages=(Message(role=Role.USER, content=(
            ContentBlock.text_block("first"),
            ContentBlock.tool_result("c1", "res"),
        )),),
    )
    jsonl_records, _ = write_hermes(s)
    jsonl_roles = [r["role"] for r in jsonl_records if r.get("role") in ("user", "tool")]

    register_hermes_session(s, str(db), "s1", started_at=0.0)
    conn = sqlite3.connect(db)
    db_roles = [r[0] for r in conn.execute(
        "SELECT role FROM messages WHERE session_id='s1' ORDER BY timestamp"
    ).fetchall()]
    conn.close()
    assert jsonl_roles == db_roles == ["user", "tool"]


def test_hermes_db_parallel_results_still_distinct(tmp_path):
    # the r1/r4 invariant must still hold after the ordering rewrite
    db = tmp_path / "state.db"
    _hermes_db(db)
    s = Session(
        meta=SessionMeta(source_harness="claude-code", model="m"),
        messages=(Message(role=Role.USER, content=(
            ContentBlock.tool_result("c1", "one"),
            ContentBlock.tool_result("c2", "two"),
        )),),
    )
    register_hermes_session(s, str(db), "s1", started_at=0.0)
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT tool_call_id, content FROM messages WHERE role='tool' ORDER BY tool_call_id"
    ).fetchall()
    conn.close()
    assert rows == [("c1", "one"), ("c2", "two")]


# ---- MEDIUM: TOOL-role stray text not dropped by JSONL writer ----

def test_hermes_tool_message_stray_text_preserved():
    s = Session(
        meta=SessionMeta(source_harness="x", model="m"),
        messages=(Message(role=Role.TOOL, content=(
            ContentBlock.tool_result("c1", "result"),
            ContentBlock.text_block("stray comment"),
        )),),
    )
    records, _ = write_hermes(s)
    contents = " ".join(str(r.get("content", "")) for r in records)
    assert "stray comment" in contents  # not silently dropped
