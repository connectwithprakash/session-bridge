"""Regression tests for Round-13 finding: queue-operation 'remove' (Claude Code
auto-withdrawing an undelivered background-task notification) was ignored, so the
removed item was falsely reported as still-pending queued input."""

from session_bridge.readers.claude_code import _queued_messages


def test_remove_consumes_matching_queue_entry():
    # enqueue then remove the SAME content -> nothing pending
    records = [
        {"type": "queue-operation", "operation": "enqueue", "sessionId": "s", "content": "notif-A"},
        {"type": "queue-operation", "operation": "remove", "sessionId": "s", "content": "notif-A"},
    ]
    assert _queued_messages(records) == ()


def test_remove_matches_by_content_not_position():
    # two enqueues, remove the FIRST by content -> only the second remains
    records = [
        {"type": "queue-operation", "operation": "enqueue", "sessionId": "s", "content": "first"},
        {"type": "queue-operation", "operation": "enqueue", "sessionId": "s", "content": "second"},
        {"type": "queue-operation", "operation": "remove", "sessionId": "s", "content": "first"},
    ]
    assert _queued_messages(records) == ("second",)


def test_genuinely_pending_still_reported():
    # enqueue with no remove/dequeue -> still pending (regression guard)
    records = [
        {"type": "queue-operation", "operation": "enqueue", "sessionId": "s", "content": "real pending"},
    ]
    assert _queued_messages(records) == ("real pending",)


def test_remove_scoped_per_session():
    # a remove in session B must not consume session A's identical-content item
    records = [
        {"type": "queue-operation", "operation": "enqueue", "sessionId": "A", "content": "x"},
        {"type": "queue-operation", "operation": "enqueue", "sessionId": "B", "content": "x"},
        {"type": "queue-operation", "operation": "remove", "sessionId": "B", "content": "x"},
    ]
    # A's "x" still pending, B's removed
    assert _queued_messages(records) == ("x",)


def test_dequeue_still_works():
    # regression: dequeue path unchanged
    records = [
        {"type": "queue-operation", "operation": "enqueue", "sessionId": "s", "content": "a"},
        {"type": "queue-operation", "operation": "dequeue", "sessionId": "s"},
    ]
    assert _queued_messages(records) == ()


def test_popall_clears_the_whole_queue():
    # Round 14: popAll flushes the entire queue in one op (real Claude Code op).
    records = [
        {"type": "queue-operation", "operation": "enqueue", "sessionId": "s", "content": "a"},
        {"type": "queue-operation", "operation": "enqueue", "sessionId": "s", "content": "b"},
        {"type": "queue-operation", "operation": "popAll", "sessionId": "s", "content": "a"},
    ]
    assert _queued_messages(records) == ()


def test_popall_scoped_per_session():
    records = [
        {"type": "queue-operation", "operation": "enqueue", "sessionId": "A", "content": "keep"},
        {"type": "queue-operation", "operation": "enqueue", "sessionId": "B", "content": "flush"},
        {"type": "queue-operation", "operation": "popAll", "sessionId": "B", "content": "flush"},
    ]
    # only session B's queue is flushed; A's item remains pending
    assert _queued_messages(records) == ("keep",)
