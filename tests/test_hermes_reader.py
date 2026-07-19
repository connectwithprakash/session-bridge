from pathlib import Path

from session_bridge.ir import BlockType, Role
from session_bridge.readers.hermes import read_hermes

FIXTURES = Path(__file__).parent / "fixtures"


def test_reads_meta_and_tools():
    session = read_hermes(FIXTURES / "hermes_sample.jsonl")
    assert session.meta.source_harness == "hermes"
    assert session.meta.model == "gpt-5-codex"
    tool_names = {t.name for t in session.tools}
    assert tool_names == {"session_search", "skill_view"}
    search = next(t for t in session.tools if t.name == "session_search")
    assert search.parameters["properties"]["query"]["type"] == "string"


def test_message_roles_and_order():
    session = read_hermes(FIXTURES / "hermes_sample.jsonl")
    roles = [m.role for m in session.messages]
    assert roles == [Role.USER, Role.ASSISTANT, Role.TOOL, Role.ASSISTANT]


def test_assistant_reasoning_and_tool_call_blocks():
    session = read_hermes(FIXTURES / "hermes_sample.jsonl")
    assistant = session.messages[1]
    types = [b.type for b in assistant.content]
    assert BlockType.REASONING in types
    assert BlockType.TEXT in types
    assert BlockType.TOOL_CALL in types
    call = next(b for b in assistant.content if b.type == BlockType.TOOL_CALL)
    assert call.tool_name == "session_search"
    # arguments JSON string must be parsed into a dict
    assert call.tool_input == {"query": "cron jobs"}
    assert call.call_id == "call_abc"


def test_empty_reasoning_produces_no_reasoning_block():
    session = read_hermes(FIXTURES / "hermes_sample.jsonl")
    final = session.messages[3]
    assert all(b.type is not BlockType.REASONING for b in final.content)


def test_tool_result_links_by_call_id():
    session = read_hermes(FIXTURES / "hermes_sample.jsonl")
    tool_msg = session.messages[2]
    assert tool_msg.role is Role.TOOL
    block = tool_msg.content[0]
    assert block.type is BlockType.TOOL_RESULT
    assert block.call_id == "call_abc"
    assert "Found 2 sessions" in block.text


def test_pending_state_detects_open_tool_call():
    session = read_hermes(FIXTURES / "hermes_pending.jsonl")
    assert "call_open" in session.pending.open_tool_calls


def test_no_pending_when_all_calls_resolved():
    session = read_hermes(FIXTURES / "hermes_sample.jsonl")
    assert session.pending.open_tool_calls == ()
