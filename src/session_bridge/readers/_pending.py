"""Shared pending-state computation for all readers.

An "open" tool call is a TOOL_CALL whose ``call_id`` is unresolved AND was issued
at the *tail* of the session — i.e. the source genuinely stopped mid-turn with
that call outstanding. A call that errored (or was reissued) deep in history but
was then SUPERSEDED by later resolved activity is *abandoned*, not pending: the
session moved on and ended cleanly. Reporting an abandoned call as "resolve this
before continuing" would tell a resuming agent to blindly re-run stale work, so
staleness is scoped to the trailing turn, not the whole transcript.
"""

from __future__ import annotations

from ..ir import BlockType, Message


def open_tool_calls(messages: tuple[Message, ...]) -> tuple[str, ...]:
    """Return call_ids that are genuinely open at the point the session stopped.

    Positional scan: a TOOL_CALL opens a call_id (a reissue reopens it), a
    TOOL_RESULT resolves it. A call left unresolved is reported ONLY if it was
    issued at or after the last resolved tool exchange — otherwise later resolved
    activity superseded it (an abandoned error-retry mid-session), so it is not
    pending. This prevents a long, error-retry-heavy session that ended cleanly
    from falsely reporting stale calls as mid-turn work to re-run.
    """
    # Index of the last message carrying any TOOL_RESULT (the last completed
    # exchange). Unresolved calls issued before this were superseded by it.
    last_result_idx = -1
    for i, m in enumerate(messages):
        if any(b.type is BlockType.TOOL_RESULT and b.call_id for b in m.content):
            last_result_idx = i

    # call_id -> (currently unresolved, index of most recent issue)
    open_now: dict[str, bool] = {}
    issued_at: dict[str, int] = {}
    for i, m in enumerate(messages):
        for b in m.content:
            if b.type is BlockType.TOOL_CALL and b.call_id:
                open_now.pop(b.call_id, None)
                open_now[b.call_id] = True
                issued_at[b.call_id] = i
            elif b.type is BlockType.TOOL_RESULT and b.call_id:
                if b.call_id in open_now:
                    open_now[b.call_id] = False
    return tuple(
        cid
        for cid, is_open in open_now.items()
        # Genuinely open: unresolved AND not superseded by a later resolved
        # exchange (issued at/after the last result, or there were no results).
        if is_open and issued_at.get(cid, -1) >= last_result_idx
    )
