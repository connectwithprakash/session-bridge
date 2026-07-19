from session_bridge.ir import (
    BlockType,
    ContentBlock,
    ConversionReport,
    Message,
    PendingState,
    Role,
    Session,
    SessionMeta,
    ToolSchema,
)


def test_content_block_factories():
    t = ContentBlock.text_block("hi")
    assert t.type is BlockType.TEXT and t.text == "hi"

    r = ContentBlock.reasoning("thinking")
    assert r.type is BlockType.REASONING and r.text == "thinking"

    c = ContentBlock.tool_call("call_1", "grep", {"pattern": "x"})
    assert c.type is BlockType.TOOL_CALL
    assert c.call_id == "call_1" and c.tool_name == "grep"
    assert c.tool_input == {"pattern": "x"}

    res = ContentBlock.tool_result("call_1", "match", is_error=False)
    assert res.type is BlockType.TOOL_RESULT and res.call_id == "call_1"
    assert res.text == "match" and res.is_error is False


def test_tool_call_input_is_copied_not_aliased():
    src = {"pattern": "x"}
    c = ContentBlock.tool_call("id", "grep", src)
    src["pattern"] = "mutated"
    assert c.tool_input == {"pattern": "x"}


def test_message_text_joins_only_text_blocks():
    m = Message(
        role=Role.ASSISTANT,
        content=(
            ContentBlock.reasoning("secret thoughts"),
            ContentBlock.text_block("line 1"),
            ContentBlock.tool_call("id", "t", {}),
            ContentBlock.text_block("line 2"),
        ),
    )
    assert m.text() == "line 1\nline 2"


def test_pending_state_empty():
    assert PendingState().is_empty()
    assert not PendingState(open_tool_calls=("c1",)).is_empty()
    assert not PendingState(active_goal="ship it").is_empty()
    assert not PendingState(queued_user_messages=("later",)).is_empty()


def test_session_with_messages_is_immutable_copy():
    meta = SessionMeta(source_harness="claude-code", session_id="s1")
    original = Session(meta=meta, messages=())
    m = Message(role=Role.USER, content=(ContentBlock.text_block("hi"),))
    updated = original.with_messages((m,))
    assert original.messages == ()
    assert updated.messages == (m,)
    assert updated.meta is meta


def test_frozen_dataclass_rejects_mutation():
    m = Message(role=Role.USER, content=())
    try:
        m.role = Role.ASSISTANT  # type: ignore[misc]
    except Exception as exc:  # FrozenInstanceError subclasses Exception
        assert "cannot assign" in str(exc).lower() or "frozen" in type(exc).__name__.lower()
    else:
        raise AssertionError("expected frozen dataclass to reject mutation")


def test_conversion_report():
    r = ConversionReport()
    assert r.ok()
    r.warn("dropped tool schema for foo")
    assert not r.ok()
    assert r.warnings == ["dropped tool schema for foo"]


def test_tool_schema_holds_parameters():
    s = ToolSchema(name="grep", description="search", parameters={"type": "object"})
    assert s.name == "grep"
    assert s.parameters == {"type": "object"}
