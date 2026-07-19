"""Regression tests for Round-14 findings: (A) queue-op popAll [in the r13 file],
and (B) a tool_result with non-text parts must NOT create a phantom turn / shift
subsequent roles in any writer."""

import json
import sqlite3

from session_bridge.convert import convert
from session_bridge.ir import BlockType, ContentBlock, Message, Role, Session, SessionMeta
from session_bridge.readers.claude_code import read_claude_code
from session_bridge.readers.codex import read_codex
from session_bridge.readers.hermes import read_hermes
from session_bridge.writers.codex import write_codex
from session_bridge.writers.hermes import write_hermes
from session_bridge.writers.hermes_db import register_hermes_session

_IMG = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "ZZ"}}


def _session():
    # a real shape: assistant, then a user turn carrying a tool_result WITH an
    # image part, then a following assistant turn.
    return Session(
        meta=SessionMeta(source_harness="claude-code", session_id="s", model="m"),
        messages=(
            Message(role=Role.ASSISTANT, content=(ContentBlock.tool_call("c1", "Read", {"f": "x.png"}),)),
            Message(role=Role.USER, content=(
                ContentBlock.tool_result("c1", "read it", result_parts=(_IMG,)),
            )),
            Message(role=Role.ASSISTANT, content=(ContentBlock.text_block("the image shows X"),)),
        ),
    )


def test_codex_no_phantom_turn_from_tool_result_parts(tmp_path):
    records, _ = write_codex(_session())
    f = tmp_path / "c.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    back = read_codex(f)
    # roles must stay assistant(call) / tool(result) / assistant(text) — no
    # fabricated user turn, no role shift.
    roles = [m.role for m in back.messages]
    assert Role.USER not in roles  # no phantom user turn
    # the final assistant text is intact and attributed to assistant
    assert any(m.role is Role.ASSISTANT and "image shows X" in m.text() for m in back.messages)


def test_hermes_no_phantom_user_row_from_tool_result_parts(tmp_path):
    records, _ = write_hermes(_session())
    # no fabricated user record carrying the image placeholder
    user_texts = [r.get("content", "") for r in records if r.get("role") == "user"]
    assert not any("unsupported" in t for t in user_texts)
    # the tool result is present and the final assistant text follows it
    roles = [r["role"] for r in records if r.get("role") in ("assistant", "user", "tool")]
    assert roles == ["assistant", "tool", "assistant"]


def test_hermes_db_no_fabricated_row_from_tool_result_parts(tmp_path):
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
    register_hermes_session(_session(), str(db), "s1", started_at=0.0)
    conn = sqlite3.connect(db)
    roles = [r[0] for r in conn.execute(
        "SELECT role FROM messages WHERE session_id='s1' ORDER BY timestamp"
    ).fetchall()]
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE session_id='s1' ORDER BY timestamp"
    ).fetchall()
    conn.close()
    assert roles == ["assistant", "tool", "assistant"]  # no fabricated user/assistant row
    # the image placeholder legitimately appears in the TOOL row (r18b/r19), but
    # must NOT appear in any non-tool row (that would be the phantom-turn bug).
    assert not any("unsupported" in (c or "") for role, c in rows if role != "tool")


def test_same_harness_round_trip_preserves_image_and_turn_count(tmp_path):
    # claude->claude: image survives on the result, message count stable
    src = tmp_path / "in.jsonl"
    src.write_text(
        json.dumps({"parentUuid": None, "type": "user", "uuid": "u1", "cwd": "/t", "sessionId": "s",
                    "message": {"role": "user", "content": [
                        {"type": "tool_result", "tool_use_id": "c1", "content": [
                            {"type": "text", "text": "ok"}, _IMG]}]}}) + "\n",
        encoding="utf-8",
    )
    orig = read_claude_code(src)
    result = convert("claude-code", "claude-code", src, inject_handshake=False)
    out = tmp_path / "out.jsonl"
    out.write_text("\n".join(json.dumps(r) for r in result.records) + "\n", encoding="utf-8")
    back = read_claude_code(out)
    assert len(back.messages) == len(orig.messages)
    res = next(b for m in back.messages for b in m.content if b.type is BlockType.TOOL_RESULT)
    assert res.result_parts and res.result_parts[0]["source"]["data"] == "ZZ"
