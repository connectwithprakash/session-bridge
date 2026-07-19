"""Regression tests for Round-7 self-review findings."""

from session_bridge.ir import ContentBlock, Message, Role, Session, SessionMeta
from session_bridge.writers._common import report_losses


def _assistant(*blocks):
    return Session(
        meta=SessionMeta(source_harness="hermes", model="m"),
        messages=(Message(role=Role.ASSISTANT, content=tuple(blocks)),),
    )


# ---- HIGH: Hermes cannot preserve non-canonical intra-message order; report it ----

def test_hermes_scrambled_block_order_reported():
    s = _assistant(
        ContentBlock.tool_call("c1", "Bash", {"cmd": "ls"}),
        ContentBlock.text_block("explaining after the call"),
        ContentBlock.reasoning("hidden thought"),
    )
    report = report_losses(s, "hermes")
    assert any("order Hermes cannot preserve" in w for w in report.warnings)
    assert not report.ok()


def test_hermes_text_before_reasoning_reported():
    # simplest scramble: text then reasoning (reader would emit reasoning first)
    s = _assistant(
        ContentBlock.text_block("answer"),
        ContentBlock.reasoning("thought"),
    )
    report = report_losses(s, "hermes")
    assert any("order Hermes cannot preserve" in w for w in report.warnings)


def test_hermes_canonical_order_not_reported():
    # reasoning -> text -> tool_call is exactly what the reader reconstructs
    s = _assistant(
        ContentBlock.reasoning("thought"),
        ContentBlock.text_block("answer"),
        ContentBlock.tool_call("c1", "Bash", {}),
    )
    report = report_losses(s, "hermes")
    assert not any("order Hermes cannot preserve" in w for w in report.warnings)


def test_hermes_single_block_not_reported():
    s = _assistant(ContentBlock.text_block("just text"))
    report = report_losses(s, "hermes")
    assert not any("order Hermes cannot preserve" in w for w in report.warnings)


def test_other_targets_not_flagged_for_ordering():
    # claude-code / codex preserve order, so the Hermes-specific warning must not fire
    s = _assistant(
        ContentBlock.tool_call("c1", "Bash", {}),
        ContentBlock.text_block("after"),
    )
    for target in ("claude-code", "codex"):
        report = report_losses(s, target)
        assert not any("order Hermes cannot preserve" in w for w in report.warnings)
