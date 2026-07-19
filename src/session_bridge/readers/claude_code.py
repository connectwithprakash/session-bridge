"""Reader: Claude Code session JSONL -> IR.

Claude Code interleaves control events with message entries:
- ``queue-operation`` {operation:enqueue|dequeue, content, sessionId}:
  user input queued while a turn is running. An enqueue with no matching
  dequeue is an undelivered (pending) user message.
- ``user`` / ``assistant`` {parentUuid, uuid, message:{...}, cwd, gitBranch,
  version, permissionMode, sessionId}: the actual turns. ``message`` is the
  Anthropic API message; ``content`` is a str (user) or a list of typed blocks
  (``text`` / ``thinking`` / ``tool_use`` / ``tool_result``). Note tool_result
  blocks arrive inside ``user`` records.
- ``ai-title`` / ``last-prompt`` / ``attachment``: metadata, not turns.

Thread order is an explicit parentUuid linked list; we preserve both uid and
parent_uid on each IR message and emit them in file order (which matches the
chain in practice).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..ir import (
    UNSUPPORTED_BLOCK_MARKER,
    ContentBlock,
    Message,
    PendingState,
    Role,
    Session,
    SessionMeta,
)
from ._jsonl import load_records
from ._pending import open_tool_calls

_MESSAGE_TYPES = {"user", "assistant"}


def _blocks_from_content(content: Any) -> tuple[ContentBlock, ...]:
    """Normalize an Anthropic message ``content`` (str or block list) to IR blocks."""
    if isinstance(content, str):
        return (ContentBlock.text_block(content),) if content else ()
    if not isinstance(content, list):
        return ()

    blocks: list[ContentBlock] = []
    for b in content:
        if not isinstance(b, dict):
            continue
        bt = b.get("type")
        if bt == "text":
            blocks.append(ContentBlock.text_block(b.get("text", "")))
        elif bt in ("thinking", "redacted_thinking"):
            blocks.append(ContentBlock.reasoning(b.get("thinking", "")))
        elif bt == "tool_use":
            inp = b.get("input")
            blocks.append(
                ContentBlock.tool_call(
                    call_id=b.get("id", ""),
                    tool_name=b.get("name", ""),
                    tool_input=inp if isinstance(inp, dict) else {"_value": inp},
                )
            )
        elif bt == "tool_result":
            result = b.get("content", "")
            if isinstance(result, list):
                # Anthropic tool_result content can itself be a block list.
                result = "\n".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in result
                )
            blocks.append(
                ContentBlock.tool_result(
                    call_id=b.get("tool_use_id", ""),
                    text=result if isinstance(result, str) else json.dumps(result),
                    is_error=bool(b.get("is_error", False)),
                )
            )
        elif bt:
            # Unknown block type (e.g. image, document, server_tool_use). Keep the
            # original block verbatim as a RAW passthrough so a same-harness writer
            # re-emits it losslessly; cross-harness writers degrade it to a
            # reported placeholder rather than dropping it silently.
            blocks.append(ContentBlock.raw(b, bt))
    return tuple(blocks)


def _extract_meta(record: dict[str, Any], base: SessionMeta) -> SessionMeta:
    """Fill session meta from the first message record that carries it."""
    msg = record.get("message", {})
    return SessionMeta(
        source_harness="claude-code",
        session_id=base.session_id or record.get("sessionId"),
        cwd=base.cwd or record.get("cwd"),
        model=base.model or (msg.get("model") if isinstance(msg, dict) else None),
        model_provider="anthropic",
        permission_mode=base.permission_mode or record.get("permissionMode"),
        version=base.version or record.get("version"),
        extra={"gitBranch": record.get("gitBranch")} if record.get("gitBranch") else {},
    )


def _queued_messages(records: list[dict[str, Any]]) -> tuple[str, ...]:
    """Enqueued user inputs with no matching later dequeue are undelivered.

    Matching is scoped per ``sessionId`` so a dequeue from one session cannot
    consume another session's queued item when a file mixes sessions. Emission
    order follows first-enqueue order across sessions.
    """
    per_session: dict[str, list[str]] = {}
    order: list[str] = []
    for rec in records:
        if rec.get("type") != "queue-operation":
            continue
        sid = rec.get("sessionId", "")
        op = rec.get("operation")
        content = rec.get("content", "")
        queue = per_session.setdefault(sid, [])
        if op == "enqueue" and content:
            queue.append(content)
            order.append(content)
        elif op == "dequeue" and queue:
            # A dequeue consumes this session's oldest queued item.
            queue.pop(0)
    remaining = {c for q in per_session.values() for c in q}
    # Preserve first-enqueue order, keep only still-pending items, dedupe.
    seen: set[str] = set()
    out: list[str] = []
    for c in order:
        if c in remaining and c not in seen:
            seen.add(c)
            out.append(c)
    return tuple(out)


def read_claude_code(path: str | Path) -> Session:
    path = Path(path)
    records = load_records(path)

    meta = SessionMeta(source_harness="claude-code")
    messages: list[Message] = []

    for rec in records:
        rtype = rec.get("type")
        if rtype not in _MESSAGE_TYPES:
            continue
        meta = _extract_meta(rec, meta)
        msg = rec.get("message", {})
        content = msg.get("content") if isinstance(msg, dict) else None
        role = Role.USER if rtype == "user" else Role.ASSISTANT
        messages.append(
            Message(
                role=role,
                content=_blocks_from_content(content),
                uid=rec.get("uuid"),
                parent_uid=rec.get("parentUuid"),
                timestamp=rec.get("timestamp"),
                raw=rec,
            )
        )

    msgs = tuple(messages)
    pending = PendingState(
        open_tool_calls=open_tool_calls(msgs),
        queued_user_messages=_queued_messages(records),
    )
    return Session(meta=meta, messages=msgs, tools=(), pending=pending)
