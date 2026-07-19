"""Regression tests for the code-review findings (3 HIGH, 2 MEDIUM)."""

from pathlib import Path

from session_bridge.convert import convert
from session_bridge.ir import (
    BlockType,
    ContentBlock,
    Message,
    Role,
    Session,
    SessionMeta,
)
from session_bridge.readers._pending import open_tool_calls
from session_bridge.readers.claude_code import _queued_messages, read_claude_code
from session_bridge.writers._common import ERROR_MARKER
from session_bridge.writers.codex import write_codex
from session_bridge.writers.hermes import write_hermes

FIXTURES = Path(__file__).parent / "fixtures"


# ---- HIGH 1: handshake must not be orphaned on claude-code -> claude-code ----

def test_handshake_is_connected_root_for_claude_to_claude():
    result = convert("claude-code", "claude-code", FIXTURES / "claude_sample.jsonl")
    msg_records = [r for r in result.records if r.get("type") in ("user", "assistant")]
    uuids = {r["uuid"] for r in msg_records}
    parents = {r["uuid"]: r["parentUuid"] for r in msg_records}

    # exactly one root (parentUuid None) — the handshake
    roots = [uid for uid, p in parents.items() if p is None]
    assert len(roots) == 1, f"expected single root, got {roots}"
    handshake_uid = roots[0]

    # the real conversation's original root (u1) must now point at the handshake
    assert parents.get("u1") == handshake_uid

    # every non-root parent must reference an existing emitted uuid (connected chain)
    for uid, p in parents.items():
        if p is not None:
            assert p in uuids, f"{uid} points at missing parent {p}"


def test_real_branch_links_preserved_without_handshake():
    # without injection, original parentUuid chain is preserved verbatim
    result = convert(
        "claude-code", "claude-code", FIXTURES / "claude_sample.jsonl",
        inject_handshake=False,
    )
    parents = {r["uuid"]: r["parentUuid"] for r in result.records if r.get("type") in ("user", "assistant")}
    assert parents == {"u1": None, "a1": "u1", "u2": "a1", "a2": "u2"}


# ---- HIGH 2: is_error must survive (as marker) + be reported ----

def _error_session():
    return Session(
        meta=SessionMeta(source_harness="claude-code", model="m"),
        messages=(
            Message(role=Role.ASSISTANT, content=(ContentBlock.tool_call("c1", "Bash", {"cmd": "x"}),)),
            Message(role=Role.TOOL, content=(ContentBlock.tool_result("c1", "boom", is_error=True),)),
        ),
    )


def test_is_error_marked_and_reported_hermes():
    records, report = write_hermes(_error_session())
    tool_rec = next(r for r in records if r.get("role") == "tool")
    assert tool_rec["content"].startswith(ERROR_MARKER)
    assert any("failed tool result" in w.lower() for w in report.warnings)


def test_is_error_marked_and_reported_codex():
    records, report = write_codex(_error_session())
    out = next(r for r in records if r["payload"].get("type") == "function_call_output")
    assert out["payload"]["output"].startswith(ERROR_MARKER)
    assert any("failed tool result" in w.lower() for w in report.warnings)


def test_is_error_not_reported_when_target_is_claude_code():
    from session_bridge.writers.claude_code import write_claude_code

    records, report = write_claude_code(_error_session())
    # claude-code keeps a native is_error flag, so no loss warning
    assert not any("failed tool result" in w.lower() for w in report.warnings)


# ---- HIGH 3: reissued call_id must reopen ----

def test_reissued_call_id_is_open():
    msgs = (
        Message(role=Role.ASSISTANT, content=(ContentBlock.tool_call("c1", "t", {}),)),
        Message(role=Role.TOOL, content=(ContentBlock.tool_result("c1", "done"),)),
        Message(role=Role.ASSISTANT, content=(ContentBlock.tool_call("c1", "t", {}),)),  # reissued, unresolved
    )
    assert open_tool_calls(msgs) == ("c1",)


def test_resolved_call_stays_closed():
    msgs = (
        Message(role=Role.ASSISTANT, content=(ContentBlock.tool_call("c1", "t", {}),)),
        Message(role=Role.TOOL, content=(ContentBlock.tool_result("c1", "done"),)),
    )
    assert open_tool_calls(msgs) == ()


# ---- MEDIUM 4: queue dequeue scoped by sessionId ----

def test_queued_messages_scoped_by_session():
    records = [
        {"type": "queue-operation", "operation": "enqueue", "sessionId": "S1", "content": "A"},
        {"type": "queue-operation", "operation": "enqueue", "sessionId": "S2", "content": "B"},
        {"type": "queue-operation", "operation": "dequeue", "sessionId": "S2"},
    ]
    # S2's dequeue consumes B, not A; A remains pending
    assert _queued_messages(records) == ("A",)


# ---- MEDIUM 5: empty user message preserved through hermes/codex ----

def test_empty_user_message_preserved_hermes():
    session = Session(
        meta=SessionMeta(source_harness="hermes"),
        messages=(Message(role=Role.USER, content=()),),
    )
    records, _ = write_hermes(session)
    assert any(r.get("role") == "user" for r in records)


def test_empty_user_message_preserved_codex():
    session = Session(
        meta=SessionMeta(source_harness="codex"),
        messages=(Message(role=Role.USER, content=()),),
    )
    records, _ = write_codex(session)
    user_recs = [r for r in records if r["type"] == "response_item" and r["payload"].get("role") == "user"]
    assert len(user_recs) == 1
