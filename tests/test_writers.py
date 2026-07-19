"""Writer + round-trip tests.

Strategy: read a real-shaped fixture into IR, write it to a target harness, then
read that output back with the target's reader and assert the conversation core
(roles, text, tool call names/inputs, tool results, call-id linkage) survives.
Lossy asymmetries must be surfaced as ConversionReport warnings.
"""

from pathlib import Path

from session_bridge.ir import BlockType, Role
from session_bridge.readers.claude_code import read_claude_code
from session_bridge.readers.codex import read_codex
from session_bridge.readers.hermes import read_hermes
from session_bridge.writers.claude_code import write_claude_code
from session_bridge.writers.codex import write_codex
from session_bridge.writers.hermes import write_hermes

FIXTURES = Path(__file__).parent / "fixtures"


def _write_lines(tmp_path, name, records):
    import json

    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return p


def _core(session):
    """A comparable summary of the conversation core."""
    out = []
    for m in session.messages:
        for b in m.content:
            if b.type is BlockType.TEXT:
                out.append((m.role, "text", (b.text or "").strip()))
            elif b.type is BlockType.TOOL_CALL:
                out.append((Role.ASSISTANT, "call", b.tool_name, tuple(sorted((b.tool_input or {}).items()))))
            elif b.type is BlockType.TOOL_RESULT:
                out.append(("result", b.call_id, (b.text or "").strip()))
    return out


# ---- Hermes -> Hermes round trip (richest source) ----

def test_hermes_roundtrip_preserves_core(tmp_path):
    src = read_hermes(FIXTURES / "hermes_sample.jsonl")
    records, report = write_hermes(src)
    out_path = _write_lines(tmp_path, "out.jsonl", records)
    back = read_hermes(out_path)
    assert _core(back) == _core(src)


def test_hermes_write_preserves_tool_schemas(tmp_path):
    src = read_hermes(FIXTURES / "hermes_sample.jsonl")
    records, _ = write_hermes(src)
    out_path = _write_lines(tmp_path, "out.jsonl", records)
    back = read_hermes(out_path)
    assert {t.name for t in back.tools} == {t.name for t in src.tools}


# ---- Claude Code -> Hermes (cross-harness) ----

def test_claude_to_hermes_preserves_conversation(tmp_path):
    src = read_claude_code(FIXTURES / "claude_sample.jsonl")
    records, report = write_hermes(src)
    out_path = _write_lines(tmp_path, "out.jsonl", records)
    back = read_hermes(out_path)
    # user text and assistant tool call survive
    assert back.messages[0].text() == "search for TODO comments"
    call = next(b for m in back.messages for b in m.content if b.type is BlockType.TOOL_CALL)
    assert call.tool_name == "Grep" and call.tool_input == {"pattern": "TODO"}


def test_claude_to_hermes_warns_on_dropped_thread_topology(tmp_path):
    src = read_claude_code(FIXTURES / "claude_sample.jsonl")
    _, report = write_hermes(src)
    # Claude Code has parent_uid linkage; Hermes cannot hold it -> warning
    assert any("thread" in w.lower() or "topolog" in w.lower() or "parent" in w.lower()
               for w in report.warnings)


# ---- Codex -> Claude Code (cross-harness, synthesizes uuids) ----

def test_codex_to_claude_synthesizes_uuids_and_links(tmp_path):
    src = read_codex(FIXTURES / "codex_sample.jsonl")
    records, report = write_claude_code(src)
    out_path = _write_lines(tmp_path, "out.jsonl", records)
    back = read_claude_code(out_path)
    # every message got a uid, and each non-first links to a parent
    msg_records = [r for r in records if r.get("type") in ("user", "assistant")]
    assert all(r.get("uuid") for r in msg_records)
    assert msg_records[0].get("parentUuid") is None
    assert all(r.get("parentUuid") for r in msg_records[1:])
    # conversation core survives
    assert _core(back) == _core(src)


# ---- IR -> Codex (cross-harness) ----

def test_hermes_to_codex_preserves_core(tmp_path):
    src = read_hermes(FIXTURES / "hermes_sample.jsonl")
    records, report = write_codex(src)
    out_path = _write_lines(tmp_path, "out.jsonl", records)
    back = read_codex(out_path)
    assert _core(back) == _core(src)


def test_to_codex_warns_when_no_system_instructions(tmp_path):
    # Hermes has no base_instructions; Codex expects them -> warning
    src = read_hermes(FIXTURES / "hermes_sample.jsonl")
    _, report = write_codex(src)
    assert any("instruction" in w.lower() for w in report.warnings)


def test_to_hermes_warns_when_source_has_open_tool_call(tmp_path):
    src = read_claude_code(FIXTURES / "claude_pending.jsonl")
    _, report = write_hermes(src)
    # an unresolved tool call is flagged as a resume risk (reworded r22)
    assert any("no matching result" in w.lower() or "open tool call" in w.lower()
               or "pending" in w.lower()
               for w in report.warnings)
