"""Regression tests for Round-21 finding: open_tool_calls must not report a
mid-session errored/abandoned call as 'pending — resolve before continuing' when
the session moved on and ended cleanly. Only trailing-unresolved calls are open."""

from session_bridge.ir import BlockType, ContentBlock, Message, Role
from session_bridge.readers._pending import open_tool_calls


def _asst(call_id):
    return Message(role=Role.ASSISTANT, content=(ContentBlock.tool_call(call_id, "Edit", {}),))


def _result(call_id, is_error=False):
    return Message(role=Role.TOOL, content=(ContentBlock.tool_result(call_id, "r", is_error=is_error),))


def test_abandoned_error_retry_not_reported_when_superseded():
    # c1 errors, is reissued (never resolved), then the session moves on with
    # resolved exchanges and ends cleanly -> c1 is abandoned, not pending.
    msgs = (
        _asst("c1"), _result("c1", is_error=True), _asst("c1"),  # reissue, abandoned
        _asst("d1"), _result("d1"),
        _asst("d2"), _result("d2"),
    )
    assert open_tool_calls(msgs) == ()


def test_trailing_unresolved_call_is_open():
    # a genuinely interrupted session: last call has no result
    msgs = (
        _asst("d1"), _result("d1"),
        _asst("LAST"),  # no result after -> genuinely open
    )
    assert open_tool_calls(msgs) == ("LAST",)


def test_trailing_errored_reissue_is_open():
    # if the abandoned-looking reissue IS the tail (no later result), it's open
    msgs = (
        _asst("c1"), _result("c1", is_error=True),
        _asst("c1"),  # reissue at the tail, nothing after
    )
    assert open_tool_calls(msgs) == ("c1",)


def test_no_results_at_all_all_open():
    # a session that only issued calls and never got any result -> all open
    msgs = (_asst("a"), _asst("b"))
    assert set(open_tool_calls(msgs)) == {"a", "b"}


def test_multiple_trailing_open_calls():
    # parallel calls in the final turn, unresolved -> all open
    msgs = (
        _asst("d1"), _result("d1"),
        Message(role=Role.ASSISTANT, content=(
            ContentBlock.tool_call("p1", "A", {}),
            ContentBlock.tool_call("p2", "B", {}),
        )),
    )
    assert set(open_tool_calls(msgs)) == {"p1", "p2"}


def test_all_resolved_none_open():
    msgs = (_asst("a"), _result("a"), _asst("b"), _result("b"))
    assert open_tool_calls(msgs) == ()
