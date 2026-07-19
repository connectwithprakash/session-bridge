"""Shared pending-state computation for all readers.

An "open" tool call is a TOOL_CALL block whose ``call_id`` never appears as a
TOOL_RESULT anywhere later in the session. That is the harness-agnostic signal
that the source stopped mid-turn — the resume target must satisfy those calls
before continuing.
"""

from __future__ import annotations

from ..ir import BlockType, Message


def open_tool_calls(messages: tuple[Message, ...]) -> tuple[str, ...]:
    issued: list[str] = []
    resolved: set[str] = set()
    for m in messages:
        for b in m.content:
            if b.type is BlockType.TOOL_CALL and b.call_id:
                issued.append(b.call_id)
            elif b.type is BlockType.TOOL_RESULT and b.call_id:
                resolved.add(b.call_id)
    # Preserve issue order, drop resolved, dedupe.
    seen: set[str] = set()
    out: list[str] = []
    for cid in issued:
        if cid not in resolved and cid not in seen:
            seen.add(cid)
            out.append(cid)
    return tuple(out)
