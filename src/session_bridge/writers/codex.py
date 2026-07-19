"""Writer: IR -> Codex rollout JSONL.

Emits ``{timestamp, type, payload}`` records: a ``session_meta`` (with
base_instructions), a ``turn_context`` (model + approval policy), then
``response_item`` records in the OpenAI Responses shape (message / reasoning /
function_call / function_call_output).
"""

from __future__ import annotations

import json
from typing import Any

from ..ir import BlockType, ConversionReport, Role, Session
from ._common import ERROR_MARKER, report_losses


_ROLE_TO_CODEX = {Role.USER: "user", Role.ASSISTANT: "assistant", Role.SYSTEM: "system"}


def _codex_role(role: Role) -> str:
    return _ROLE_TO_CODEX.get(role, "user")


def _msg_payload(role: str, text: str) -> dict[str, Any]:
    # Assistant emits output_text; user/system emit input_text.
    block_type = "output_text" if role == "assistant" else "input_text"
    return {"type": "message", "role": role, "content": [{"type": block_type, "text": text}]}


def write_codex(
    session: Session, *, timestamp: str = "2000-01-01T00:00:00.000Z"
) -> tuple[list[dict[str, Any]], ConversionReport]:
    """Render the IR into a Codex rollout.

    ``timestamp`` is the ISO time stamped on session_meta. Codex treats a rollout
    whose session_meta lacks a valid top-level timestamp as empty and refuses to
    resume it, so a non-null value is required; the caller should pass the real
    current time (scripts cannot call the clock directly).
    """
    report = report_losses(session, "codex")
    records: list[dict[str, Any]] = [
        {
            "timestamp": timestamp,
            "type": "session_meta",
            "payload": {
                # Real Codex session_meta carries both session_id and id (same
                # value); resume/discovery keys on these, so emit both.
                "session_id": session.meta.session_id,
                "id": session.meta.session_id,
                "timestamp": timestamp,
                "cwd": session.meta.cwd,
                "originator": "codex-cli",
                "cli_version": session.meta.version or "0.144.5",
                "source": "cli",
                "thread_source": "user",
                "model_provider": session.meta.model_provider or "openai",
                "base_instructions": {"text": session.meta.system_instructions or ""},
            },
        },
        {
            "timestamp": None,
            "type": "turn_context",
            "payload": {
                "turn_id": "t1",
                "model": session.meta.model or "unknown",
                "cwd": session.meta.cwd,
                "approval_policy": session.meta.permission_mode or "on-request",
            },
        },
    ]

    def add(payload: dict[str, Any], ts: Any) -> None:
        records.append({"timestamp": ts, "type": "response_item", "payload": payload})

    for msg in session.messages:
        ts = msg.timestamp
        role = _codex_role(msg.role)
        emitted = False
        # Single ordered pass. Coalesce only ADJACENT TEXT/RAW blocks into one
        # message record (so consecutive text doesn't inflate the turn count) and
        # flush that buffer when a non-text block interrupts — this preserves the
        # original block order (e.g. reasoning -> text -> tool_call) instead of
        # hoisting all text to the front.
        text_buf: list[str] = []

        def flush_text() -> None:
            nonlocal emitted
            if text_buf:
                add(_msg_payload(role, "\n".join(text_buf)), ts)
                text_buf.clear()
                emitted = True

        for b in msg.content:
            if b.type is BlockType.TEXT or b.type is BlockType.RAW:
                text_buf.append(b.text or "")
            elif b.type is BlockType.REASONING:
                flush_text()
                add({"type": "reasoning", "summary": [{"type": "summary_text", "text": b.text or ""}]}, ts)
                emitted = True
            elif b.type is BlockType.TOOL_CALL:
                flush_text()
                add(
                    {
                        "type": "function_call",
                        "name": b.tool_name,
                        "arguments": json.dumps(b.tool_input or {}),
                        "call_id": b.call_id,
                    },
                    ts,
                )
                emitted = True
            elif b.type is BlockType.TOOL_RESULT:
                flush_text()
                output = b.text or ""
                if b.is_error:
                    output = ERROR_MARKER + output
                add({"type": "function_call_output", "call_id": b.call_id, "output": output}, ts)
                emitted = True
        flush_text()
        # Preserve an otherwise-empty message so message count survives the round trip.
        if not emitted and msg.role in (Role.USER, Role.ASSISTANT, Role.SYSTEM):
            add(_msg_payload(role, ""), ts)

    return records, report
