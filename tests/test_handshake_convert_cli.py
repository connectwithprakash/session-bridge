from pathlib import Path

import pytest

from session_bridge.cli import main
from session_bridge.convert import convert, read_session
from session_bridge.handshake import build_handshake
from session_bridge.ir import Role
from session_bridge.writers.hermes import write_hermes

FIXTURES = Path(__file__).parent / "fixtures"


# ---- handshake ----

def test_handshake_lists_open_tool_calls():
    session = read_session("claude-code", FIXTURES / "claude_pending.jsonl")
    _, report = write_hermes(session)
    text = build_handshake(session, report, "hermes")
    assert "Open tool calls" in text
    assert "Bash" in text  # the open call's tool name
    assert "Queued user input" in text
    assert "also lint after" in text


def test_handshake_clean_session_says_no_pending():
    session = read_session("hermes", FIXTURES / "hermes_sample.jsonl")
    _, report = write_hermes(session)
    text = build_handshake(session, report, "hermes")
    assert "None" in text
    assert "continue normally" in text.lower()


# ---- convert ----

def test_convert_injects_handshake_as_first_message():
    result = convert("claude-code", "hermes", FIXTURES / "claude_pending.jsonl")
    # first non-meta record should be the injected system handshake
    non_meta = [r for r in result.records if r.get("role") != "session_meta"]
    assert non_meta[0]["role"] == "system"
    assert "resume handshake" in non_meta[0]["content"].lower()


def test_convert_no_handshake_flag():
    result = convert(
        "claude-code", "hermes", FIXTURES / "claude_pending.jsonl", inject_handshake=False
    )
    assert not any(r.get("role") == "system" for r in result.records)


def test_convert_rejects_unknown_target():
    with pytest.raises(ValueError):
        convert("claude-code", "nope", FIXTURES / "claude_sample.jsonl")


def test_convert_report_has_warnings_for_pending_source():
    result = convert("claude-code", "hermes", FIXTURES / "claude_pending.jsonl")
    assert not result.report.ok()


# ---- CLI ----

def test_cli_inspect(capsys):
    rc = main(["inspect", "--from", "hermes", str(FIXTURES / "hermes_sample.jsonl")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "source harness : hermes" in out
    assert "tool calls" in out


def test_cli_convert_writes_output(tmp_path, capsys):
    out = tmp_path / "converted.jsonl"
    hs = tmp_path / "handshake.md"
    rc = main([
        "convert", "--from", "codex", "--to", "claude-code",
        str(FIXTURES / "codex_sample.jsonl"),
        "-o", str(out), "--handshake-out", str(hs),
    ])
    assert rc == 0
    assert out.exists() and out.read_text().strip()
    assert hs.exists() and "resume handshake" in hs.read_text().lower()
    # output must be valid JSONL readable back as a claude-code session
    back = read_session("claude-code", out)
    assert any(m.role is Role.USER for m in back.messages)
