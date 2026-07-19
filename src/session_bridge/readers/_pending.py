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

    Per-call-id scan (no global index): a call is OPEN iff it is unresolved at the
    end AND was never previously resolved. The distinction that matters:

    - A call that NEVER received any result and is unresolved at the end is
      genuinely open — the session stopped with it outstanding (even if other,
      later calls resolved afterward). This is the interrupted-turn case.
    - A call that DID receive a result (even an error result) and was then
      reissued without a new result is ABANDONED, not open: the tool ran and
      returned once, the retry was dropped as the session moved on. Reporting it
      would tell a resuming agent to blindly re-run superseded work.

    So an error result counts as resolving the call; only a fresh, never-answered
    call is reported. Insertion order preserved.
    """
    ever_resolved: set[str] = set()
    unresolved_now: dict[str, bool] = {}  # call_id -> outstanding since last issue
    for m in messages:
        for b in m.content:
            if b.type is BlockType.TOOL_CALL and b.call_id:
                unresolved_now.pop(b.call_id, None)
                unresolved_now[b.call_id] = True
            elif b.type is BlockType.TOOL_RESULT and b.call_id:
                ever_resolved.add(b.call_id)
                if b.call_id in unresolved_now:
                    unresolved_now[b.call_id] = False
    return tuple(
        cid
        for cid, outstanding in unresolved_now.items()
        # Genuinely open: outstanding at the end AND never got any result at all.
        # (A reissue after a prior result leaves it outstanding but ever_resolved,
        # so it is treated as abandoned, not open.)
        if outstanding and cid not in ever_resolved
    )
