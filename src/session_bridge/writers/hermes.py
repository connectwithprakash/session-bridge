"""Writer: IR -> Hermes session JSONL.

Emits OpenAI-chat-completions records: a ``session_meta`` (with the tool catalog),
then user / assistant / tool records. Assistant tool calls are grouped into the
assistant record's ``tool_calls`` (OpenAI style); tool results become ``role:tool``
records keyed by ``tool_call_id``.
"""

from __future__ import annotations

import json
from typing import Any

from ..ir import BlockType, ConversionReport, Message, Role, Session
from ._common import reconstruct_tool_schemas, report_losses


def _tool_catalog(session: Session) -> list[dict[str, Any]]:
    schemas = reconstruct_tool_schemas(session)
    catalog = []
    for t in schemas:
        catalog.append(
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description or "",
                    "parameters": t.parameters or {"type": "object", "properties": {}},
                },
            }
        )
    return catalog


def _assistant_record(msg: Message) -> dict[str, Any]:
    text_parts = []
    reasoning_parts = []
    tool_calls = []
    for b in msg.content:
        if b.type is BlockType.TEXT:
            text_parts.append(b.text or "")
        elif b.type is BlockType.REASONING:
            reasoning_parts.append(b.text or "")
        elif b.type is BlockType.TOOL_CALL:
            tool_calls.append(
                {
                    "id": b.call_id,
                    "call_id": b.call_id,
                    "type": "function",
                    "function": {
                        "name": b.tool_name,
                        "arguments": json.dumps(b.tool_input or {}),
                    },
                }
            )
    rec: dict[str, Any] = {
        "role": "assistant",
        "content": "\n".join(text_parts),
        "reasoning": "\n".join(reasoning_parts),
        "timestamp": msg.timestamp,
    }
    if tool_calls:
        rec["tool_calls"] = tool_calls
        rec["finish_reason"] = "tool_calls"
    else:
        rec["finish_reason"] = "stop"
    return rec


def write_hermes(session: Session) -> tuple[list[dict[str, Any]], ConversionReport]:
    report = report_losses(session, "hermes")
    records: list[dict[str, Any]] = [
        {
            "role": "session_meta",
            "model": session.meta.model or "unknown",
            "platform": "hermes",
            "timestamp": None,
            "tools": _tool_catalog(session),
        }
    ]

    for msg in session.messages:
        if msg.role is Role.USER:
            # A user record may carry tool_result blocks (Claude Code convention);
            # split those into Hermes role:tool records.
            text = msg.text()
            if text:
                records.append({"role": "user", "content": text, "timestamp": msg.timestamp})
            for b in msg.content:
                if b.type is BlockType.TOOL_RESULT:
                    records.append(
                        {
                            "role": "tool",
                            "content": b.text or "",
                            "tool_call_id": b.call_id,
                            "timestamp": msg.timestamp,
                        }
                    )
        elif msg.role is Role.ASSISTANT:
            records.append(_assistant_record(msg))
        elif msg.role is Role.TOOL:
            for b in msg.content:
                if b.type is BlockType.TOOL_RESULT:
                    records.append(
                        {
                            "role": "tool",
                            "content": b.text or "",
                            "tool_call_id": b.call_id,
                            "timestamp": msg.timestamp,
                        }
                    )
        elif msg.role is Role.SYSTEM:
            records.append({"role": "system", "content": msg.text(), "timestamp": msg.timestamp})

    return records, report
