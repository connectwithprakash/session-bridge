"""Shared pending-state computation for all readers.

A tool call is "open" only if the source genuinely stopped mid-turn with that call
outstanding. Two real cases must both be handled, and they pull in opposite
directions on "does later activity mean abandoned?":

- OVERLAP (r22): call A issued, then call B issued, then B resolves and the session
  ends with A never answered. A is genuinely open even though a *different* call
  resolved after it — the session stopped with A outstanding.
- ABANDONED RETRY (r21): call A errored, was reissued, then the session moved on
  and other calls resolved after it. A's retry was dropped; reporting it would tell
  a resuming agent to blindly re-run stale work.
- INTERRUPTED RETRY (r22 case 7): call A resolved, reissued+resolved, reissued a
  third time and the session stopped there. That final retry is genuinely open.

The rule that satisfies all three keys on *this* call_id's own history, not a
global index: a call is open iff its current (most-recent) issue is unresolved AND
either it was never resolved in any cycle (overlap/interrupted-turn) OR its
unresolved issue is at the tail with no later result for any call (interrupted
retry). A resolved-then-reissued call whose retry is followed by other resolved
activity is abandoned, not open.
"""

from __future__ import annotations

from ..ir import BlockType, Message


def open_tool_calls(messages: tuple[Message, ...]) -> tuple[str, ...]:
    """Return call_ids genuinely open at the point the session stopped.

    See the module docstring for the three real cases this rule reconciles
    (overlap, abandoned retry, interrupted retry). Insertion order preserved.
    """
    last_issue_idx: dict[str, int] = {}   # call_id -> index of most recent issue
    current_cycle_resolved: dict[str, bool] = {}
    ever_resolved: set[str] = set()
    last_result_idx = -1

    for i, m in enumerate(messages):
        for b in m.content:
            if b.type is BlockType.TOOL_CALL and b.call_id:
                last_issue_idx[b.call_id] = i
                current_cycle_resolved[b.call_id] = False
            elif b.type is BlockType.TOOL_RESULT and b.call_id:
                last_result_idx = i
                ever_resolved.add(b.call_id)
                # Resolve the current cycle only if the result comes after the
                # call's most recent issue (a result before a reissue doesn't
                # resolve the reissue).
                if b.call_id in last_issue_idx and i >= last_issue_idx[b.call_id]:
                    current_cycle_resolved[b.call_id] = True

    return tuple(
        cid
        for cid, idx in last_issue_idx.items()
        if not current_cycle_resolved[cid]
        # Open iff the current cycle is unresolved AND either the call was never
        # resolved at all (overlap / interrupted turn) or its unresolved issue is
        # at the tail (interrupted retry). A resolved-then-reissued call whose
        # retry is superseded by later resolved activity is abandoned, not open.
        and (cid not in ever_resolved or idx > last_result_idx)
    )
