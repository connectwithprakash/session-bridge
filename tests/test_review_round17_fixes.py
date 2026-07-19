"""Regression tests for Round-17 finding: Claude Code's '<synthetic>' model
placeholder (stamped on locally-synthesized messages) was forwarded as the
session model, producing a non-routable model id with no/misleading warning."""

import json

from session_bridge.readers.claude_code import read_claude_code
from session_bridge.writers._common import report_losses


def _cc(tmp_path, *msgs):
    f = tmp_path / "s.jsonl"
    lines = []
    for i, (role, model) in enumerate(msgs):
        content = [{"type": "text", "text": f"m{i}"}] if role == "assistant" else "hi"
        lines.append(json.dumps({
            "parentUuid": None if i == 0 else f"u{i-1}", "type": role,
            "uuid": f"u{i}", "cwd": "/t", "sessionId": "s",
            "message": {"role": role, "model": model, "content": content} if model
                       else {"role": role, "content": content},
        }))
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f


def test_synthetic_not_adopted_when_real_model_exists(tmp_path):
    # <synthetic> appears first, a real model later -> real model wins
    f = _cc(tmp_path, ("assistant", "<synthetic>"), ("assistant", "claude-opus-4-8"))
    session = read_claude_code(f)
    assert session.meta.model == "claude-opus-4-8"


def test_synthetic_only_yields_no_model(tmp_path):
    f = _cc(tmp_path, ("assistant", "<synthetic>"))
    session = read_claude_code(f)
    assert session.meta.model is None  # never the placeholder


def test_synthetic_only_reports_no_routable_model(tmp_path):
    f = _cc(tmp_path, ("assistant", "<synthetic>"))
    session = read_claude_code(f)
    report = report_losses(session, "codex")
    assert any("no real model id recorded" in w for w in report.warnings)


def test_synthetic_not_counted_as_model_switch(tmp_path):
    # one real model + <synthetic> must NOT read as a 2-model switch
    f = _cc(tmp_path, ("assistant", "claude-opus-4-8"), ("assistant", "<synthetic>"))
    session = read_claude_code(f)
    report = report_losses(session, "codex")
    assert not any("models used across turns" in w for w in report.warnings)
    # and the single real model is what's kept
    assert session.meta.model == "claude-opus-4-8"


def test_synthetic_never_leaks_to_writer_output(tmp_path):
    from session_bridge.convert import convert
    f = _cc(tmp_path, ("assistant", "<synthetic>"))
    result = convert("claude-code", "codex", f, inject_handshake=False)
    tc = next(r for r in result.records if r["type"] == "turn_context")
    assert tc["payload"]["model"] != "<synthetic>"
