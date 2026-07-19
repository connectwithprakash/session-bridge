"""Reader: Hermes session JSONL -> IR.

Hermes stores an OpenAI-chat-completion-style log:
- ``session_meta``: {model, platform, tools:[{type:function, function:{name,description,parameters}}], timestamp}
- ``user``:      {content, timestamp}
- ``assistant``: {content, reasoning, tool_calls:[{id/call_id, function:{name, arguments:<json-string>}}], finish_reason, timestamp}
- ``tool``:      {content, tool_call_id, timestamp}   # a tool result, linked by tool_call_id

Records are append-ordered; there is no explicit parent linkage, so IR
``parent_uid`` is left None and order is list order.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..ir import (
    ContentBlock,
    Message,
    PendingState,
    Role,
    Session,
    SessionMeta,
    ToolSchema,
)
from ._pending import open_tool_calls


def _load_lines(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def _parse_tool_schemas(raw_tools: Any) -> tuple[ToolSchema, ...]:
    schemas: list[ToolSchema] = []
    if not isinstance(raw_tools, list):
        return ()
    for entry in raw_tools:
        fn = entry.get("function", entry) if isinstance(entry, dict) else {}
        name = fn.get("name")
        if not name:
            continue
        schemas.append(
            ToolSchema(
                name=name,
                description=fn.get("description"),
                parameters=fn.get("parameters"),
            )
        )
    return tuple(schemas)


def _parse_arguments(raw_args: Any) -> dict[str, Any]:
    """Hermes stores tool arguments as a JSON string (OpenAI style)."""
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str) and raw_args.strip():
        try:
            parsed = json.loads(raw_args)
            return parsed if isinstance(parsed, dict) else {"_value": parsed}
        except json.JSONDecodeError:
            return {"_raw": raw_args}
    return {}


def _assistant_blocks(record: dict[str, Any]) -> tuple[ContentBlock, ...]:
    blocks: list[ContentBlock] = []
    reasoning = record.get("reasoning")
    if isinstance(reasoning, str) and reasoning.strip():
        blocks.append(ContentBlock.reasoning(reasoning))
    content = record.get("content")
    if isinstance(content, str) and content.strip():
        blocks.append(ContentBlock.text_block(content))
    for call in record.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        call_id = call.get("call_id") or call.get("id") or ""
        fn = call.get("function", {})
        blocks.append(
            ContentBlock.tool_call(
                call_id=call_id,
                tool_name=fn.get("name", ""),
                tool_input=_parse_arguments(fn.get("arguments")),
            )
        )
    return tuple(blocks)


def read_hermes(path: str | Path) -> Session:
    path = Path(path)
    records = _load_lines(path)

    meta = SessionMeta(source_harness="hermes")
    tools: tuple[ToolSchema, ...] = ()
    messages: list[Message] = []

    for rec in records:
        role = rec.get("role")
        if role == "session_meta":
            meta = SessionMeta(
                source_harness="hermes",
                model=rec.get("model"),
                version=rec.get("platform"),
                extra={"platform": rec.get("platform")},
            )
            tools = _parse_tool_schemas(rec.get("tools"))
        elif role == "user":
            messages.append(
                Message(
                    role=Role.USER,
                    content=(ContentBlock.text_block(rec.get("content", "")),),
                    timestamp=rec.get("timestamp"),
                    raw=rec,
                )
            )
        elif role == "assistant":
            messages.append(
                Message(
                    role=Role.ASSISTANT,
                    content=_assistant_blocks(rec),
                    timestamp=rec.get("timestamp"),
                    raw=rec,
                )
            )
        elif role == "tool":
            messages.append(
                Message(
                    role=Role.TOOL,
                    content=(
                        ContentBlock.tool_result(
                            call_id=rec.get("tool_call_id", ""),
                            text=rec.get("content", ""),
                        ),
                    ),
                    timestamp=rec.get("timestamp"),
                    raw=rec,
                )
            )
        # unknown roles are ignored but preserved via raw if needed later

    msgs = tuple(messages)
    pending = PendingState(open_tool_calls=open_tool_calls(msgs))
    return Session(meta=meta, messages=msgs, tools=tools, pending=pending)
