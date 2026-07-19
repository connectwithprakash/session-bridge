from pathlib import Path

from session_bridge.ir import BlockType, Role
from session_bridge.readers.claude_code import read_claude_code

FIXTURES = Path(__file__).parent / "fixtures"


def test_meta_from_message_records():
    session = read_claude_code(FIXTURES / "claude_sample.jsonl")
    assert session.meta.source_harness == "claude-code"
    assert session.meta.session_id == "sess-1"
    assert session.meta.cwd == "/Users/x/proj"
    assert session.meta.model == "claude-opus-4-8"
    assert session.meta.version == "2.0.0"
    assert session.meta.permission_mode == "default"


def test_control_records_are_not_messages():
    session = read_claude_code(FIXTURES / "claude_sample.jsonl")
    # queue-operation and ai-title must not appear as messages
    assert len(session.messages) == 4
    assert [m.role for m in session.messages] == [
        Role.USER,
        Role.ASSISTANT,
        Role.USER,
        Role.ASSISTANT,
    ]


def test_thread_linkage_via_parent_uid():
    session = read_claude_code(FIXTURES / "claude_sample.jsonl")
    uids = [m.uid for m in session.messages]
    parents = [m.parent_uid for m in session.messages]
    assert uids == ["u1", "a1", "u2", "a2"]
    assert parents == [None, "u1", "a1", "u2"]


def test_assistant_blocks_thinking_text_tooluse():
    session = read_claude_code(FIXTURES / "claude_sample.jsonl")
    assistant = session.messages[1]
    types = [b.type for b in assistant.content]
    assert types == [BlockType.REASONING, BlockType.TEXT, BlockType.TOOL_CALL]
    call = assistant.content[2]
    assert call.tool_name == "Grep"
    assert call.tool_input == {"pattern": "TODO"}
    assert call.call_id == "tu_1"


def test_tool_result_inside_user_record():
    session = read_claude_code(FIXTURES / "claude_sample.jsonl")
    user_with_result = session.messages[2]
    assert user_with_result.role is Role.USER
    block = user_with_result.content[0]
    assert block.type is BlockType.TOOL_RESULT
    assert block.call_id == "tu_1"
    assert block.is_error is False
    assert "found 3 TODOs" in block.text


def test_string_content_user_message():
    session = read_claude_code(FIXTURES / "claude_sample.jsonl")
    first = session.messages[0]
    assert first.content[0].type is BlockType.TEXT
    assert first.content[0].text == "search for TODO comments"


def test_pending_open_tool_call_and_queued_message():
    session = read_claude_code(FIXTURES / "claude_pending.jsonl")
    assert "tu_open" in session.pending.open_tool_calls
    assert "also lint after" in session.pending.queued_user_messages


def test_queue_enqueue_dequeue_pair_is_not_pending():
    # sample has an enqueue immediately followed by a dequeue -> delivered, not pending
    session = read_claude_code(FIXTURES / "claude_sample.jsonl")
    assert session.pending.queued_user_messages == ()
