"""Writer: IR -> Codex rollout JSONL.

Emits ``{timestamp, type, payload}`` records: a ``session_meta`` (with
base_instructions), a ``turn_context`` (model + approval policy), then
``response_item`` records in the OpenAI Responses shape (message / reasoning /
function_call / function_call_output).
"""

from __future__ import annotations

from typing import Any

from ..ir import BlockType, ConversionReport, Role, Session
from ._common import report_losses


def _msg_payload(role: str, text: str) -> dict[str, Any]:
    block_type = "input_text" if role == "user" else "output_text"
    return {"type": "message", "role": role, "content": [{"type": block_type, "text": text}]}


def write_codex(session: Session) -> tuple[list[dict[str, Any]], ConversionReport]:
    report = report_losses(session, "codex")
    records: list[dict[str, Any]] = [
        {
            "timestamp": None,
            "type": "session_meta",
            "payload": {
                "id": session.meta.session_id,
                "cwd": session.meta.cwd,
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
        for b in msg.content:
            if b.type is BlockType.TEXT:
                role = "user" if msg.role is Role.USER else "assistant"
                add(_msg_payload(role, b.text or ""), ts)
            elif b.type is BlockType.REASONING:
                add({"type": "reasoning", "summary": [{"type": "summary_text", "text": b.text or ""}]}, ts)
            elif b.type is BlockType.TOOL_CALL:
                import json

                add(
                    {
                        "type": "function_call",
                        "name": b.tool_name,
                        "arguments": json.dumps(b.tool_input or {}),
                        "call_id": b.call_id,
                    },
                    ts,
                )
            elif b.type is BlockType.TOOL_RESULT:
                add({"type": "function_call_output", "call_id": b.call_id, "output": b.text or ""}, ts)

    return records, report
