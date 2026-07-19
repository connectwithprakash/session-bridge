"""Regression tests for Round-8 self-review finding: the Hermes ordering check
must include RAW (bucketed with TEXT) and TOOL_RESULT (emitted after text)."""

from session_bridge.ir import ContentBlock, Message, Role, Session, SessionMeta
from session_bridge.writers._common import report_losses

_ORDER_WARN = "order Hermes cannot preserve"


def _session(role, *blocks):
    return Session(
        meta=SessionMeta(source_harness="claude-code", model="m"),
        messages=(Message(role=role, content=tuple(blocks)),),
    )


def test_raw_after_tool_call_reported():
    # the reviewer's exact repro: text, tool_call, RAW -> RAW relocates before call
    s = _session(
        Role.ASSISTANT,
        ContentBlock.text_block("let me search"),
        ContentBlock.tool_call("c1", "Bash", {"cmd": "ls"}),
        ContentBlock.raw({"type": "server_tool_use", "id": "x"}, "server_tool_use"),
    )
    report = report_losses(s, "hermes")
    assert any(_ORDER_WARN in w for w in report.warnings)


def test_raw_before_reasoning_reported():
    s = _session(
        Role.ASSISTANT,
        ContentBlock.raw({"type": "image", "source": {}}, "image"),
        ContentBlock.reasoning("thought"),
    )
    report = report_losses(s, "hermes")
    assert any(_ORDER_WARN in w for w in report.warnings)


def test_tool_result_before_text_reported():
    # user message: tool_result then a follow-up text comment -> Hermes emits text
    # row before tool rows, reordering; must be reported
    s = _session(
        Role.USER,
        ContentBlock.tool_result("c1", "output"),
        ContentBlock.text_block("comment after the result"),
    )
    report = report_losses(s, "hermes")
    assert any(_ORDER_WARN in w for w in report.warnings)


def test_canonical_with_raw_not_reported():
    # text+raw (same rank) before tool_call = canonical, no warning
    s = _session(
        Role.ASSISTANT,
        ContentBlock.text_block("a"),
        ContentBlock.raw({"type": "image", "source": {}}, "image"),
        ContentBlock.tool_call("c1", "Bash", {}),
    )
    report = report_losses(s, "hermes")
    assert not any(_ORDER_WARN in w for w in report.warnings)


def test_raw_ordering_not_flagged_for_other_targets():
    s = _session(
        Role.ASSISTANT,
        ContentBlock.tool_call("c1", "Bash", {}),
        ContentBlock.raw({"type": "image", "source": {}}, "image"),
    )
    for target in ("claude-code", "codex"):
        report = report_losses(s, target)
        assert not any(_ORDER_WARN in w for w in report.warnings)
