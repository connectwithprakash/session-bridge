"""Regression tests for Round-18 reviewer-B finding: cross-harness writers
emitted an empty tool-result body for parts-only content (image / tool_reference),
contradicting the ConversionReport's 'degrades to a text placeholder' claim."""

import sqlite3

from session_bridge.ir import ContentBlock, Message, Role, Session, SessionMeta
from session_bridge.writers._common import tool_result_text
from session_bridge.writers.codex import write_codex
from session_bridge.writers.hermes import write_hermes
from session_bridge.writers.hermes_db import register_hermes_session

_IMG = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "ZZ"}}


def _parts_only_session():
    return Session(
        meta=SessionMeta(source_harness="claude-code", model="m"),
        messages=(Message(role=Role.TOOL, content=(
            ContentBlock.tool_result("c1", "", result_parts=(_IMG,)),
        )),),
    )


def test_helper_placeholder_for_parts_only():
    b = ContentBlock.tool_result("c1", "", result_parts=(_IMG,))
    assert "image" in tool_result_text(b) and tool_result_text(b) != ""
    # text present -> returned as-is
    b2 = ContentBlock.tool_result("c1", "real output", result_parts=(_IMG,))
    assert tool_result_text(b2) == "real output"
    # no parts, no text -> empty (unchanged)
    b3 = ContentBlock.tool_result("c1", "")
    assert tool_result_text(b3) == ""


def test_codex_parts_only_result_not_empty():
    records, _ = write_codex(_parts_only_session())
    out = next(r for r in records if r["payload"].get("type") == "function_call_output")
    assert out["payload"]["output"] != ""
    assert "image" in out["payload"]["output"]


def test_hermes_parts_only_result_not_empty():
    records, _ = write_hermes(_parts_only_session())
    tr = next(r for r in records if r.get("role") == "tool")
    assert tr["content"] != "" and "image" in tr["content"]


def test_hermes_db_parts_only_result_not_empty(tmp_path):
    db = tmp_path / "state.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, model TEXT, "
        "started_at REAL NOT NULL, message_count INTEGER DEFAULT 0, tool_call_count INTEGER DEFAULT 0, "
        "title TEXT, cwd TEXT, archived INTEGER NOT NULL DEFAULT 0);"
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES sessions(id), "
        "role TEXT NOT NULL, content TEXT, tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, "
        "timestamp REAL NOT NULL, reasoning TEXT);"
    )
    conn.commit(); conn.close()
    register_hermes_session(_parts_only_session(), str(db), "s1", started_at=0.0)
    conn = sqlite3.connect(db)
    content = conn.execute("SELECT content FROM messages WHERE tool_call_id='c1'").fetchone()[0]
    conn.close()
    assert content and "image" in content


def test_multiple_part_kinds_summarized():
    b = ContentBlock.tool_result("c1", "", result_parts=(
        {"type": "tool_reference", "tool_name": "A"},
        {"type": "tool_reference", "tool_name": "B"},
        _IMG,
    ))
    txt = tool_result_text(b)
    assert "2 tool_reference" in txt and "1 image" in txt
