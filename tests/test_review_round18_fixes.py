"""Regression tests for Round-18 findings: (1) isApiErrorMessage records are
Claude Code error notices, not assistant turns; (2) Optional import."""

import json
import typing

from session_bridge.ir import BlockType, Role
from session_bridge.readers import claude_code
from session_bridge.readers.claude_code import _real_model, read_claude_code


def test_api_error_message_not_ingested_as_assistant_turn(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text(
        json.dumps({
            "parentUuid": None, "type": "user", "uuid": "u1", "cwd": "/t", "sessionId": "s",
            "message": {"role": "user", "content": "do a thing"},
        }) + "\n"
        + json.dumps({  # Claude Code's own error notice, stamped type:assistant
            "parentUuid": "u1", "type": "assistant", "uuid": "e1", "cwd": "/t", "sessionId": "s",
            "isApiErrorMessage": True, "error": "server_error",
            "message": {"role": "assistant", "model": "<synthetic>",
                        "content": [{"type": "text", "text": "API Error: Connection closed mid-response."}]},
        }) + "\n"
        + json.dumps({
            "parentUuid": "e1", "type": "assistant", "uuid": "a1", "cwd": "/t", "sessionId": "s",
            "message": {"role": "assistant", "model": "claude-opus-4-8",
                        "content": [{"type": "text", "text": "real answer"}]},
        }) + "\n",
        encoding="utf-8",
    )
    session = read_claude_code(f)
    # the error notice must NOT appear as a message
    texts = [b.text for m in session.messages for b in m.content if b.type is BlockType.TEXT]
    assert not any((t or "").startswith("API Error") for t in texts)
    # the real user turn and real assistant turn survive
    assert "do a thing" in texts
    assert "real answer" in texts
    # 2 real messages, not 3
    assert len(session.messages) == 2


def test_api_error_message_does_not_set_session_model(tmp_path):
    # the error notice's <synthetic> model must not become the session model,
    # and since the record is skipped entirely, the real model wins
    f = tmp_path / "s.jsonl"
    f.write_text(
        json.dumps({
            "parentUuid": None, "type": "assistant", "uuid": "e1", "cwd": "/t", "sessionId": "s",
            "isApiErrorMessage": True, "error": "rate_limit",
            "message": {"role": "assistant", "model": "<synthetic>",
                        "content": [{"type": "text", "text": "API Error: rate limit"}]},
        }) + "\n"
        + json.dumps({
            "parentUuid": "e1", "type": "assistant", "uuid": "a1", "cwd": "/t", "sessionId": "s",
            "message": {"role": "assistant", "model": "claude-sonnet-5",
                        "content": [{"type": "text", "text": "hi"}]},
        }) + "\n",
        encoding="utf-8",
    )
    session = read_claude_code(f)
    assert session.meta.model == "claude-sonnet-5"


def test_optional_annotation_resolves():
    # _real_model uses Optional; get_type_hints must not raise NameError
    hints = typing.get_type_hints(_real_model)
    assert "return" in hints


def test_real_model_filters_placeholder():
    assert _real_model("<synthetic>") is None
    assert _real_model("") is None
    assert _real_model(None) is None
    assert _real_model("claude-opus-4-8") == "claude-opus-4-8"
