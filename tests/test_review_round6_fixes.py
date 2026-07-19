"""Regression tests for Round-6 self-review findings."""

import json

from session_bridge.cli import main
from session_bridge.ir import ContentBlock, Message, Role, Session, SessionMeta
from session_bridge.writers.claude_code import write_claude_code


# ---- HIGH: no uuid collision when a real sb- uid already exists ----

def test_write_claude_code_no_uuid_collision():
    # message 0 has no uid (would synth to sb-000000); message 1 already carries
    # the real uid sb-000000 from a prior conversion. Output uuids must be unique.
    s = Session(
        meta=SessionMeta(source_harness="claude-code", model="m"),
        messages=(
            Message(role=Role.USER, content=(ContentBlock.text_block("new"),)),  # no uid
            Message(role=Role.ASSISTANT, content=(ContentBlock.text_block("old"),),
                    uid="sb-000000", parent_uid=None),
        ),
    )
    records, _ = write_claude_code(s)
    uuids = [r["uuid"] for r in records]
    assert len(uuids) == len(set(uuids)), f"duplicate uuids: {uuids}"


def test_write_claude_code_synth_uids_unique_across_many():
    # several no-uid messages interleaved with real sb- uids that would collide
    msgs = [
        Message(role=Role.USER, content=(ContentBlock.text_block("a"),)),
        Message(role=Role.ASSISTANT, content=(ContentBlock.text_block("b"),), uid="sb-000001"),
        Message(role=Role.USER, content=(ContentBlock.text_block("c"),)),
        Message(role=Role.ASSISTANT, content=(ContentBlock.text_block("d"),), uid="sb-000000"),
    ]
    s = Session(meta=SessionMeta(source_harness="claude-code", model="m"), messages=tuple(msgs))
    records, _ = write_claude_code(s)
    uuids = [r["uuid"] for r in records]
    assert len(uuids) == len(set(uuids))


# ---- HIGH: CLI register on a non-sqlite file exits cleanly ----

def test_cli_register_bad_db_clean_exit(tmp_path, capsys):
    src = tmp_path / "in.jsonl"
    src.write_text(
        '{"type":"user","message":{"role":"user","content":"hi"},"uuid":"u1","parentUuid":null,"cwd":"/t","sessionId":"s"}\n',
        encoding="utf-8",
    )
    bad_db = tmp_path / "not_a_db.db"
    bad_db.write_text("this is not a sqlite database", encoding="utf-8")
    rc = main([
        "register", "--from", "claude-code", str(src),
        "--db", str(bad_db), "--no-backup",
    ])
    assert rc == 2  # clean, not a traceback
    err = capsys.readouterr().err
    assert "error" in err.lower()
