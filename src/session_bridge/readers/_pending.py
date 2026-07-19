"""Shared pending-state computation for all readers.

An "open" tool call is a TOOL_CALL block whose ``call_id`` never appears as a
TOOL_RESULT anywhere later in the session. That is the harness-agnostic signal
that the source stopped mid-turn — the resume target must satisfy those calls
before continuing.
"""

from __future__ import annotations

from ..ir import BlockType, Message


def open_tool_calls(messages: tuple[Message, ...]) -> tuple[str, ...]:
    """Positional scan: a call is open iff, after its most recent issue, no
    matching result has yet appeared. Walking in order (rather than comparing
    against a global resolved-set) correctly handles a call_id that is resolved
    once and then reissued and left open — the reissue reopens it."""
    open_now: dict[str, bool] = {}  # call_id -> currently unresolved; insertion-ordered
    for m in messages:
        for b in m.content:
            if b.type is BlockType.TOOL_CALL and b.call_id:
                # Re-issuing marks it open again (and moves it to the end of order).
                open_now.pop(b.call_id, None)
                open_now[b.call_id] = True
            elif b.type is BlockType.TOOL_RESULT and b.call_id:
                if b.call_id in open_now:
                    open_now[b.call_id] = False
    return tuple(cid for cid, is_open in open_now.items() if is_open)
