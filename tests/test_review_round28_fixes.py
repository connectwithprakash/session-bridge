"""Regression tests for Round-28 findings:

- Reviewer B [HIGH]: `register` (Hermes DB writer) silently dropped tool schemas.
  report_losses is keyed by target NAME, and "hermes" claims the tool_schemas
  capability because the Hermes *file* writer emits a tools catalog — but the
  DB writer (state.db) has no tool-catalog column, so a hermes-sourced session's
  schemas were dropped with no warning. Fixed by passing the DB writer's real
  (empty) capabilities via caps_override so the existing warning fires.
- Reviewer A [HIGH]: place_claude_code bounded the session-id length but not the
  cwd; encode_cwd collapses the whole cwd into one directory-name component, so
  a very long cwd raised a raw OSError (ENAMETOOLONG) at mkdir — uncaught when
  place_claude_code is used as a library. Fixed with an UnsafeCwdError guard.
"""

import pytest

from session_bridge.ir import (
    Message,
    Role,
    Session,
    SessionMeta,
    ToolSchema,
)
from session_bridge.place import UnsafeCwdError, place_claude_code
from session_bridge.writers._common import HERMES_DB_CAPS, report_losses


def _session_with_tools(n=3):
    tools = tuple(
        ToolSchema(name=f"tool{i}", description="d", parameters={"type": "object"})
        for i in range(n)
    )
    return Session(
        meta=SessionMeta(source_harness="hermes", model="m"),
        messages=(Message(role=Role.USER, content=()),),
        tools=tools,
    )


def test_register_caps_warn_dropped_tool_schemas():
    s = _session_with_tools(5)
    report = report_losses(s, "hermes", caps_override=HERMES_DB_CAPS)
    assert any("tool schema" in w for w in report.warnings), \
        "register path (DB caps) must warn that tool schemas are dropped"


def test_hermes_file_caps_still_keep_tool_schemas():
    # The file writer legitimately keeps schemas -> no drop warning on that path.
    s = _session_with_tools(5)
    report = report_losses(s, "hermes")  # default (file-writer) caps
    assert not any("tool schema" in w for w in report.warnings)


def test_caps_override_none_uses_target_defaults():
    # Passing caps_override=None must behave exactly like omitting it.
    s = _session_with_tools(2)
    assert (
        [w for w in report_losses(s, "hermes").warnings]
        == [w for w in report_losses(s, "hermes", caps_override=None).warnings]
    )


def test_place_rejects_over_long_cwd(tmp_path):
    home = tmp_path / ".claude"
    with pytest.raises(UnsafeCwdError):
        place_claude_code(
            [{"type": "user", "message": {"role": "user", "content": "x"}}],
            "/tmp/" + "x" * 300,
            "sid",
            claude_home=home,
        )


def test_place_normal_cwd_still_works(tmp_path):
    home = tmp_path / ".claude"
    p = place_claude_code(
        [{"type": "user", "message": {"role": "user", "content": "x"}}],
        "/tmp/proj",
        "sid",
        claude_home=home,
    )
    assert p.exists()


def test_cli_register_warns_tool_schema_loss(tmp_path, capsys):
    """End-to-end: registering a hermes session that carries tool schemas must
    print the tool-schema-drop warning (it did not before r28)."""
    import json
    import sqlite3

    from session_bridge.cli import main

    db = tmp_path / "state.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, model TEXT, "
        "started_at REAL NOT NULL, message_count INTEGER DEFAULT 0, tool_call_count INTEGER DEFAULT 0, "
        "title TEXT, cwd TEXT, archived INTEGER NOT NULL DEFAULT 0);"
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, "
        "role TEXT NOT NULL, content TEXT, tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, "
        "timestamp REAL NOT NULL, reasoning TEXT);"
    )
    conn.commit()
    conn.close()

    # A minimal Hermes session file carrying a tool catalog. Hermes records are
    # flat with a top-level `role` (see readers/hermes.py), not payload-wrapped.
    src = tmp_path / "hermes.jsonl"
    src.write_text(
        json.dumps({"role": "session_meta", "model": "gpt-x",
                    "tools": [{"type": "function", "function": {
                        "name": "Bash", "description": "run",
                        "parameters": {"type": "object"}}}]}) + "\n"
        + json.dumps({"role": "user", "content": "hi"}) + "\n",
        encoding="utf-8",
    )
    rc = main(["register", "--from", "hermes", str(src), "--db", str(db),
               "--model", "gpt-x", "--no-backup", "--session-id", "r28"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "tool schema" in err
