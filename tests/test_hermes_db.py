import json
import sqlite3

import pytest

from session_bridge.ir import ContentBlock, Message, Role, Session, SessionMeta
from session_bridge.writers.hermes_db import (
    HermesRegistrationError,
    register_hermes_session,
)

# Minimal schema mirroring the real Hermes state.db columns this writer touches,
# including the UNIQUE title index and the FK from messages -> sessions.
_SESSIONS_DDL = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    model TEXT,
    started_at REAL NOT NULL,
    message_count INTEGER DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0,
    title TEXT,
    cwd TEXT,
    archived INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX idx_sessions_title_unique ON sessions(title) WHERE title IS NOT NULL;
"""
_MESSAGES_DDL = """
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,
    content TEXT,
    tool_call_id TEXT,
    tool_calls TEXT,
    tool_name TEXT,
    timestamp REAL NOT NULL,
    reasoning TEXT
);
"""


def _make_hermes_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(_SESSIONS_DDL + _MESSAGES_DDL)
    conn.commit()
    conn.close()


def _sample_session():
    return Session(
        meta=SessionMeta(source_harness="claude-code", model="claude-opus-4-8", cwd="/tmp/x"),
        messages=(
            Message(role=Role.USER, content=(ContentBlock.text_block("find TODOs"),)),
            Message(
                role=Role.ASSISTANT,
                content=(
                    ContentBlock.reasoning("grep for TODO"),
                    ContentBlock.text_block("searching"),
                    ContentBlock.tool_call("c1", "Grep", {"pattern": "TODO"}),
                ),
            ),
            Message(role=Role.TOOL, content=(ContentBlock.tool_result("c1", "3 found"),)),
            Message(role=Role.ASSISTANT, content=(ContentBlock.text_block("There are 3 TODOs."),)),
        ),
    )


def test_registers_session_and_messages(tmp_path):
    db = tmp_path / "state.db"
    _make_hermes_db(db)
    register_hermes_session(_sample_session(), str(db), "sess-1", title="Find TODOs")

    conn = sqlite3.connect(db)
    srow = conn.execute(
        "SELECT source, model, message_count, tool_call_count, title FROM sessions WHERE id='sess-1'"
    ).fetchone()
    assert srow == ("cli", "claude-opus-4-8", 4, 1, "Find TODOs")

    rows = conn.execute(
        "SELECT role, content, tool_calls, tool_call_id, reasoning FROM messages "
        "WHERE session_id='sess-1' ORDER BY timestamp"
    ).fetchall()
    conn.close()

    assert [r[0] for r in rows] == ["user", "assistant", "tool", "assistant"]
    # assistant row carries reasoning + a tool_calls JSON array with parsed args
    assistant = rows[1]
    tc = json.loads(assistant[2])
    assert tc[0]["function"]["name"] == "Grep"
    assert json.loads(tc[0]["function"]["arguments"]) == {"pattern": "TODO"}
    assert assistant[4] == "grep for TODO"
    # tool row linked by call id
    tool = rows[2]
    assert tool[3] == "c1" and "3 found" in tool[1]


def test_rejects_non_hermes_db(tmp_path):
    db = tmp_path / "random.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE unrelated (x INTEGER)")
    conn.commit()
    conn.close()
    with pytest.raises(HermesRegistrationError, match="not a Hermes"):
        register_hermes_session(_sample_session(), str(db), "sess-1")


def test_rejects_duplicate_session_id(tmp_path):
    db = tmp_path / "state.db"
    _make_hermes_db(db)
    register_hermes_session(_sample_session(), str(db), "dup")
    with pytest.raises(HermesRegistrationError, match="already exists"):
        register_hermes_session(_sample_session(), str(db), "dup")


def test_rejects_title_conflict(tmp_path):
    db = tmp_path / "state.db"
    _make_hermes_db(db)
    register_hermes_session(_sample_session(), str(db), "s1", title="Same")
    with pytest.raises(HermesRegistrationError, match="title already in use"):
        register_hermes_session(_sample_session(), str(db), "s2", title="Same")


def test_failed_insert_rolls_back(tmp_path):
    # A title conflict is checked pre-transaction; force a mid-transaction failure
    # by pointing at a DB whose messages table is missing a column the writer needs.
    db = tmp_path / "state.db"
    conn = sqlite3.connect(db)
    conn.executescript(_SESSIONS_DDL + "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id), role TEXT NOT NULL, timestamp REAL NOT NULL);")
    conn.commit()
    conn.close()
    with pytest.raises(sqlite3.OperationalError):
        register_hermes_session(_sample_session(), str(db), "s1")
    # the sessions insert must have rolled back
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM sessions WHERE id='s1'").fetchone()[0]
    conn.close()
    assert n == 0
