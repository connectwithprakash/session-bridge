"""Writer: IR -> Claude Code session JSONL.

Emits ``user`` / ``assistant`` records with the nested Anthropic ``message`` shape
and a synthesized ``uuid``/``parentUuid`` linked list (Claude Code's thread model).
When the source was append-ordered (Codex/Hermes), uuids are synthesized
deterministically and chained linearly. Tool results are placed inside ``user``
records per Claude Code convention.
"""

from __future__ import annotations

from typing import Any, Optional

from ..ir import BlockType, ConversionReport, Message, Role, Session
from ._common import report_losses


def _synth_uid(index: int) -> str:
    return f"sb-{index:06d}"


def _assistant_content(msg: Message) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for b in msg.content:
        if b.type is BlockType.REASONING:
            blocks.append({"type": "thinking", "thinking": b.text or "", "signature": ""})
        elif b.type is BlockType.TEXT:
            blocks.append({"type": "text", "text": b.text or ""})
        elif b.type is BlockType.TOOL_CALL:
            blocks.append(
                {"type": "tool_use", "id": b.call_id, "name": b.tool_name, "input": b.tool_input or {}}
            )
        elif b.type is BlockType.RAW:
            # Re-emit the original block verbatim (lossless same-harness
            # passthrough); if it lacks the original, degrade to a text placeholder
            # rather than dropping it silently.
            blocks.append(b.raw_block if b.raw_block is not None
                          else {"type": "text", "text": b.text or ""})
    return blocks


def _user_content(msg: Message) -> Any:
    """User content is a plain string when it's only text, else a block list
    (needed when it carries tool_result or RAW passthrough blocks)."""
    needs_blocks = any(
        b.type in (BlockType.TOOL_RESULT, BlockType.RAW) for b in msg.content
    )
    if not needs_blocks:
        return msg.text()
    blocks: list[dict[str, Any]] = []
    for b in msg.content:
        if b.type is BlockType.TEXT and b.text:
            blocks.append({"type": "text", "text": b.text})
        elif b.type is BlockType.TOOL_RESULT:
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": b.call_id,
                    "content": b.text or "",
                    "is_error": b.is_error,
                }
            )
        elif b.type is BlockType.RAW:
            blocks.append(b.raw_block if b.raw_block is not None
                          else {"type": "text", "text": b.text or ""})
    return blocks


def write_claude_code(session: Session) -> tuple[list[dict[str, Any]], ConversionReport]:
    report = report_losses(session, "claude-code")
    records: list[dict[str, Any]] = []

    # Preserve original uids/links when present; else synthesize a linear chain.
    # A message keeps its real parent link only when that parent was actually
    # emitted before it; otherwise (a real root, or a synthesized message such as
    # an injected handshake) it chains onto the emitted predecessor. This keeps a
    # prepended handshake as the new thread root instead of leaving it orphaned.
    have_links = any(m.parent_uid for m in session.messages)
    prev_uid: Optional[str] = None
    emitted_uids: set[str] = set()

    for i, msg in enumerate(session.messages):
        uid = msg.uid or _synth_uid(i)
        if have_links and msg.uid and msg.parent_uid and msg.parent_uid in emitted_uids:
            parent = msg.parent_uid
        else:
            # Real root, synthesized message, or a parent not yet emitted: attach
            # to the running predecessor so the thread stays a single connected chain.
            parent = prev_uid

        if msg.role is Role.USER or (
            msg.role is Role.TOOL  # a standalone tool result -> user record in CC
        ):
            rtype = "user"
            message = {"role": "user", "content": _user_content(msg)}
        elif msg.role is Role.ASSISTANT:
            rtype = "assistant"
            message = {
                "role": "assistant",
                "model": session.meta.model or "unknown",
                "content": _assistant_content(msg),
            }
        elif msg.role is Role.SYSTEM:
            # Claude Code has no persisted system record; fold into a user note.
            rtype = "user"
            message = {"role": "user", "content": msg.display_text()}
        else:
            continue

        records.append(
            {
                "parentUuid": parent,
                "type": rtype,
                "message": message,
                "uuid": uid,
                "timestamp": msg.timestamp,
                "sessionId": session.meta.session_id or "sb-session",
                "cwd": session.meta.cwd,
                "version": session.meta.version,
                "gitBranch": session.meta.extra.get("gitBranch"),
            }
        )
        prev_uid = uid
        emitted_uids.add(uid)

    return records, report
