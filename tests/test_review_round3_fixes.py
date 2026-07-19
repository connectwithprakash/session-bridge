"""Regression tests for Round-3 self-review findings."""

import json

from session_bridge.cli import main
from session_bridge.handshake import (
    HANDSHAKE_MARKER,
    build_handshake,
    is_handshake_message,
    strip_prior_handshakes,
)
from session_bridge.ir import (
    BlockType,
    ContentBlock,
    Message,
    Role,
    Session,
    SessionMeta,
)
from session_bridge.readers.codex import read_codex
from session_bridge.readers.hermes import read_hermes
from session_bridge.writers._common import report_losses
from session_bridge.writers.codex import write_codex
from session_bridge.writers.hermes import write_hermes


# ---- HIGH-A: handshake title collision no longer deletes real user content ----

def test_user_message_with_handshake_title_not_stripped():
    # a real user message that merely starts with the human title (no marker)
    msg = Message(role=Role.USER, content=(
        ContentBlock.text_block("# Session resume handshake\nlet's discuss the design"),
    ))
    session = Session(meta=SessionMeta(source_harness="x"), messages=(msg,))
    kept = strip_prior_handshakes(session)
    assert len(kept.messages) == 1  # NOT stripped
    assert not is_handshake_message(msg)


def test_real_handshake_has_marker_and_is_detected():
    session = Session(meta=SessionMeta(source_harness="claude-code", model="m"), messages=())
    text = build_handshake(session, report_losses(session, "hermes"), "hermes")
    assert HANDSHAKE_MARKER in text
    msg = Message(role=Role.SYSTEM, content=(ContentBlock.text_block(text),))
    assert is_handshake_message(msg)


# ---- HIGH-B: Hermes SYSTEM record round-trips (not dropped) ----

def test_hermes_system_message_round_trips(tmp_path):
    s = Session(
        meta=SessionMeta(source_harness="x", model="m"),
        messages=(
            Message(role=Role.SYSTEM, content=(ContentBlock.text_block("system note"),)),
            Message(role=Role.USER, content=(ContentBlock.text_block("hi"),)),
        ),
    )
    records, _ = write_hermes(s)
    f = tmp_path / "h.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    back = read_hermes(f)
    roles = [m.role for m in back.messages]
    assert Role.SYSTEM in roles
    sys_msg = next(m for m in back.messages if m.role is Role.SYSTEM)
    assert "system note" in sys_msg.text()


# ---- HIGH-B: Codex & Hermes readers RAW-wrap non-text content, not drop ----

def test_codex_reader_raw_wraps_non_text_part(tmp_path):
    f = tmp_path / "c.jsonl"
    f.write_text(
        json.dumps({"timestamp": "t", "type": "session_meta", "payload": {"id": "s", "cwd": "/t"}}) + "\n"
        + json.dumps({"timestamp": "t", "type": "response_item", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "see this"},
                        {"type": "input_image", "image_url": "x"}]}}) + "\n",
        encoding="utf-8",
    )
    session = read_codex(f)
    types = [b.type for b in session.messages[0].content]
    assert BlockType.TEXT in types and BlockType.RAW in types
    raw = next(b for b in session.messages[0].content if b.type is BlockType.RAW)
    assert raw.raw_block == {"type": "input_image", "image_url": "x"}


def test_hermes_reader_raw_wraps_non_text_part(tmp_path):
    f = tmp_path / "h.jsonl"
    f.write_text(
        json.dumps({"role": "session_meta", "model": "m", "tools": []}) + "\n"
        + json.dumps({"role": "user", "content": [
            {"type": "text", "text": "look"},
            {"type": "image_url", "image_url": {"url": "x"}}]}) + "\n",
        encoding="utf-8",
    )
    session = read_hermes(f)
    types = [b.type for b in session.messages[0].content]
    assert BlockType.TEXT in types and BlockType.RAW in types


def test_non_text_drop_is_reported_cross_harness(tmp_path):
    # a Hermes-read session with a RAW block, converted to codex, must warn
    f = tmp_path / "h.jsonl"
    f.write_text(
        json.dumps({"role": "session_meta", "model": "m", "tools": []}) + "\n"
        + json.dumps({"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}) + "\n",
        encoding="utf-8",
    )
    session = read_hermes(f)
    _, report = write_codex(session)
    assert any("no IR representation" in w or "placeholder" in w for w in report.warnings)


# ---- MEDIUM: gitBranch preserved from earlier record ----

def test_git_branch_not_clobbered_by_later_record(tmp_path):
    from session_bridge.readers.claude_code import read_claude_code

    f = tmp_path / "cc.jsonl"
    f.write_text(
        json.dumps({"type": "user", "parentUuid": None, "uuid": "u1", "cwd": "/t",
                    "sessionId": "s", "gitBranch": "feature-x",
                    "message": {"role": "user", "content": "a"}}) + "\n"
        + json.dumps({"type": "assistant", "parentUuid": "u1", "uuid": "a1", "cwd": "/t",
                      "sessionId": "s",
                      "message": {"role": "assistant", "content": [{"type": "text", "text": "b"}]}}) + "\n",
        encoding="utf-8",
    )
    session = read_claude_code(f)
    assert session.meta.extra.get("gitBranch") == "feature-x"


# ---- MEDIUM: Codex convert stamps a real (non-2000) timestamp via CLI ----

def test_cli_convert_codex_uses_real_timestamp(tmp_path):
    src = tmp_path / "in.jsonl"
    src.write_text(
        '{"type":"user","message":{"role":"user","content":"hi"},"uuid":"u1","parentUuid":null,"cwd":"/t","sessionId":"s"}\n',
        encoding="utf-8",
    )
    out = tmp_path / "out.jsonl"
    rc = main(["convert", "--from", "claude-code", "--to", "codex", str(src), "-o", str(out)])
    assert rc == 0
    meta = json.loads(out.read_text().splitlines()[0])
    ts = meta["payload"]["timestamp"]
    assert not ts.startswith("2000-")  # real time, not the placeholder epoch


# ---- LOW: deepcopy immutability for raw_block and tool_input ----

def test_raw_block_deep_copied():
    original = {"type": "image", "source": {"data": "AAA"}}
    b = ContentBlock.raw(original, "image")
    original["source"]["data"] = "MUTATED"
    assert b.raw_block["source"]["data"] == "AAA"


def test_tool_input_deep_copied():
    original = {"nested": {"k": "v"}}
    b = ContentBlock.tool_call("c1", "t", original)
    original["nested"]["k"] = "MUTATED"
    assert b.tool_input["nested"]["k"] == "v"


# ---- LOW: CLI clean error on missing file ----

def test_cli_missing_file_clean_exit(capsys):
    rc = main(["convert", "--from", "claude-code", "--to", "hermes", "/nonexistent/x.jsonl"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "error" in err.lower()
