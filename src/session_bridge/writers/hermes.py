"""Writer: IR -> Hermes session JSONL.

Emits OpenAI-chat-completions records: a ``session_meta`` (with the tool catalog),
then user / assistant / tool records. Assistant tool calls are grouped into the
assistant record's ``tool_calls`` (OpenAI style); tool results become ``role:tool``
records keyed by ``tool_call_id``.
"""

from __future__ import annotations

import json
from typing import Any

from ..ir import BlockType, ConversionReport, ContentBlock, Message, Role, Session
from ._common import ERROR_MARKER, reconstruct_tool_schemas, report_losses


def _tool_record(block: ContentBlock, timestamp: Any) -> dict[str, Any]:
    content = block.text or ""
    if block.is_error:
        content = ERROR_MARKER + content
    return {
        "role": "tool",
        "content": content,
        "tool_call_id": block.call_id,
        "timestamp": timestamp,
    }


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
            has_result = any(b.type is BlockType.TOOL_RESULT for b in msg.content)
            # Preserve the turn even when empty (no text, no results) so message
            # count survives the round trip; only suppress the plain-user record
            # when the turn's content is entirely tool results.
            if text or not has_result:
                records.append({"role": "user", "content": text, "timestamp": msg.timestamp})
            for b in msg.content:
                if b.type is BlockType.TOOL_RESULT:
                    records.append(_tool_record(b, msg.timestamp))
        elif msg.role is Role.ASSISTANT:
            records.append(_assistant_record(msg))
        elif msg.role is Role.TOOL:
            for b in msg.content:
                if b.type is BlockType.TOOL_RESULT:
                    records.append(_tool_record(b, msg.timestamp))
        elif msg.role is Role.SYSTEM:
            records.append({"role": "system", "content": msg.text(), "timestamp": msg.timestamp})

    return records, report
