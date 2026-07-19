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
    recover_tool_error,
)
from ._content import content_blocks
from ._jsonl import load_records
from ._pending import open_tool_calls


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
    # Reasoning presence has two signals in real Hermes data: the flat visible
    # `reasoning` string, and a sibling `codex_reasoning_items` list (opaque
    # encrypted extended-thinking, mirroring Codex). A real record often has
    # reasoning=null WITH codex_reasoning_items present — emit a reasoning block
    # from whichever signal exists so a real reasoning turn isn't silently lost.
    codex_items = record.get("codex_reasoning_items")
    if isinstance(reasoning, str) and reasoning.strip():
        blocks.append(ContentBlock.reasoning(reasoning))
    elif isinstance(codex_items, list) and codex_items:
        # Content is opaque/encrypted; preserve presence with empty visible text.
        blocks.append(ContentBlock.reasoning(""))
    # Text parts -> TEXT, non-text parts -> RAW passthrough (not dropped).
    blocks.extend(content_blocks(record.get("content")))
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
    records = load_records(path)

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
            # Text parts -> TEXT, non-text parts (image_url, ...) -> RAW passthrough.
            blocks = content_blocks(rec.get("content"))
            messages.append(
                Message(
                    role=Role.USER,
                    content=blocks,
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
        elif role == "system":
            # write_hermes emits SYSTEM messages (e.g. the injected handshake) as
            # role:system; read them back rather than dropping the whole class.
            blocks = content_blocks(rec.get("content"))
            messages.append(
                Message(
                    role=Role.SYSTEM,
                    content=blocks,
                    timestamp=rec.get("timestamp"),
                    raw=rec,
                )
            )
        elif role == "tool":
            text, marked = recover_tool_error(rec.get("content", "") or "")
            messages.append(
                Message(
                    role=Role.TOOL,
                    content=(
                        ContentBlock.tool_result(
                            call_id=rec.get("tool_call_id", ""),
                            text=text,
                            is_error=marked,
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
