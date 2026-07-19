from pathlib import Path

from session_bridge.ir import BlockType, Role
from session_bridge.readers.codex import read_codex

FIXTURES = Path(__file__).parent / "fixtures"


def test_meta_from_session_meta_and_turn_context():
    session = read_codex(FIXTURES / "codex_sample.jsonl")
    assert session.meta.source_harness == "codex"
    assert session.meta.session_id == "cx-1"
    assert session.meta.cwd == "/Users/x/dev"
    assert session.meta.model_provider == "openai"
    # model comes from turn_context
    assert session.meta.model == "gpt-5-codex"
    # base_instructions.text becomes system_instructions
    assert "Codex" in (session.meta.system_instructions or "")
    # approval policy captured
    assert session.meta.permission_mode == "on-request"


def test_event_msg_records_are_not_messages():
    session = read_codex(FIXTURES / "codex_sample.jsonl")
    # only response_item message/reasoning/function_call* produce IR content;
    # event_msg (task_started/complete) and turn_context do not.
    roles = [m.role for m in session.messages]
    assert Role.USER in roles
    assert Role.ASSISTANT in roles


def test_user_message_from_input_text():
    session = read_codex(FIXTURES / "codex_sample.jsonl")
    user = session.messages[0]
    assert user.role is Role.USER
    assert user.content[0].type is BlockType.TEXT
    assert user.content[0].text == "list the python files"


def test_reasoning_block():
    session = read_codex(FIXTURES / "codex_sample.jsonl")
    reasoning_texts = [
        b.text
        for m in session.messages
        for b in m.content
        if b.type is BlockType.REASONING
    ]
    # summary-shaped reasoning
    assert any("run ls" in (t or "") for t in reasoning_texts)
    # content[]-shaped reasoning (the shape real Codex sessions actually use)
    assert any("Running ls now" in (t or "") for t in reasoning_texts)


def test_world_state_record_is_ignored_not_fatal():
    # a real Codex session interleaves world_state records; they must not crash
    # parsing nor become messages.
    session = read_codex(FIXTURES / "codex_sample.jsonl")
    assert all(m.role in (Role.USER, Role.ASSISTANT, Role.TOOL) for m in session.messages)


def test_function_call_and_output():
    session = read_codex(FIXTURES / "codex_sample.jsonl")
    calls = [b for m in session.messages for b in m.content if b.type is BlockType.TOOL_CALL]
    results = [b for m in session.messages for b in m.content if b.type is BlockType.TOOL_RESULT]
    assert len(calls) == 1 and len(results) == 1
    assert calls[0].tool_name == "shell"
    assert calls[0].tool_input == {"command": "ls *.py"}
    assert calls[0].call_id == "fc_1"
    assert results[0].call_id == "fc_1"
    assert "main.py" in results[0].text


def test_assistant_output_text():
    session = read_codex(FIXTURES / "codex_sample.jsonl")
    assistant_text = [
        b.text
        for m in session.messages
        if m.role is Role.ASSISTANT
        for b in m.content
        if b.type is BlockType.TEXT
    ]
    assert any("two python files" in (t or "") for t in assistant_text)


def test_pending_open_function_call():
    session = read_codex(FIXTURES / "codex_pending.jsonl")
    assert "fc_open" in session.pending.open_tool_calls
