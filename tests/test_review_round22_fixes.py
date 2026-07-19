"""Regression tests for Round-22 finding B: the register CLI never surfaced a
ConversionReport, so a session with a resume-breaking gap (a tool call with no
matching result) was registered silently. (Finding A — the open_tool_calls
false-negative — is covered in test_review_round21_fixes.py.)"""

import json
import sqlite3

from session_bridge.ir import ContentBlock, Message, Role, Session, SessionMeta
from session_bridge.readers._pending import open_tool_calls
from session_bridge.writers._common import report_losses


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
    conn.commit(); conn.close()


def test_no_result_call_warned_as_resume_risk():
    # a tool call that never gets a result anywhere is a resume risk; report_losses
    # must warn (it is the same set open_tool_calls computes).
    s = Session(
        meta=SessionMeta(source_harness="claude-code", model="m"),
        messages=(
            Message(role=Role.ASSISTANT, content=(ContentBlock.tool_call("c1", "Edit", {}),)),
        ),
    )
    from session_bridge.ir import PendingState
    s = Session(meta=s.meta, messages=s.messages,
                pending=PendingState(open_tool_calls=open_tool_calls(s.messages)))
    report = report_losses(s, "hermes")
    assert any("no matching result" in w for w in report.warnings)


def test_fully_resolved_no_resume_risk_warning():
    s = Session(
        meta=SessionMeta(source_harness="claude-code", model="m"),
        messages=(
            Message(role=Role.ASSISTANT, content=(ContentBlock.tool_call("c1", "Bash", {}),)),
            Message(role=Role.TOOL, content=(ContentBlock.tool_result("c1", "ok"),)),
        ),
    )
    report = report_losses(s, "hermes")
    assert not any("no matching result" in w for w in report.warnings)


def test_cli_register_surfaces_conversion_notes(tmp_path, capsys):
    from session_bridge.cli import main

    db = tmp_path / "state.db"
    _hermes_db(db)
    # source session with a tool call that never gets a result (resume risk)
    src = tmp_path / "in.jsonl"
    src.write_text(
        json.dumps({"parentUuid": None, "type": "assistant", "uuid": "a1", "cwd": "/t", "sessionId": "s",
                    "message": {"role": "assistant", "model": "m",
                                "content": [{"type": "tool_use", "id": "c1", "name": "Edit", "input": {}}]}}) + "\n",
        encoding="utf-8",
    )
    rc = main(["register", "--from", "claude-code", str(src), "--db", str(db),
               "--model", "gpt-x", "--no-backup", "--session-id", "reg1"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "conversion note" in err.lower()
    assert "no matching result" in err


def test_cli_register_clean_session_still_registers(tmp_path, capsys):
    from session_bridge.cli import main

    db = tmp_path / "state.db"
    _hermes_db(db)
    src = tmp_path / "in.jsonl"
    src.write_text(
        json.dumps({"parentUuid": None, "type": "user", "uuid": "u1", "cwd": "/t", "sessionId": "s",
                    "message": {"role": "user", "content": "hello"}}) + "\n",
        encoding="utf-8",
    )
    rc = main(["register", "--from", "claude-code", str(src), "--db", str(db),
               "--model", "gpt-x", "--no-backup", "--session-id", "reg2"])
    assert rc == 0
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM sessions WHERE id='reg2'").fetchone()[0]
    conn.close()
    assert n == 1
