"""Regression tests for Round-13 finding: queue-operation 'remove' (Claude Code
auto-withdrawing an undelivered background-task notification) was ignored, so the
removed item was falsely reported as still-pending queued input."""

from session_bridge.readers.claude_code import _queued_messages


def test_remove_content_less_withdraws_the_newest(tmp_path=None):
    # Real Claude Code 'remove' records carry NO content and immediately follow
    # their own enqueue -> they withdraw the most-recently-enqueued pending item.
    records = [
        {"type": "queue-operation", "operation": "enqueue", "sessionId": "s", "content": "notif-A"},
        {"type": "queue-operation", "operation": "remove", "sessionId": "s"},  # no content
    ]
    assert _queued_messages(records) == ()


def test_remove_pops_newest_when_queue_has_multiple():
    # older user input enqueued, then a notification enqueued and immediately
    # removed -> the older user input must remain (the bug popped it instead).
    records = [
        {"type": "queue-operation", "operation": "enqueue", "sessionId": "s", "content": "older user input"},
        {"type": "queue-operation", "operation": "enqueue", "sessionId": "s", "content": "notification"},
        {"type": "queue-operation", "operation": "remove", "sessionId": "s"},  # withdraws newest
    ]
    assert _queued_messages(records) == ("older user input",)


def test_remove_honors_content_match_when_present():
    # defensive: if a remove DOES carry content, an exact match wins over LIFO
    records = [
        {"type": "queue-operation", "operation": "enqueue", "sessionId": "s", "content": "first"},
        {"type": "queue-operation", "operation": "enqueue", "sessionId": "s", "content": "second"},
        {"type": "queue-operation", "operation": "remove", "sessionId": "s", "content": "first"},
    ]
    assert _queued_messages(records) == ("second",)


def test_genuinely_pending_still_reported():
    records = [
        {"type": "queue-operation", "operation": "enqueue", "sessionId": "s", "content": "real pending"},
    ]
    assert _queued_messages(records) == ("real pending",)


def test_remove_scoped_per_session():
    # a content-less remove in session B must not consume session A's item
    records = [
        {"type": "queue-operation", "operation": "enqueue", "sessionId": "A", "content": "keep"},
        {"type": "queue-operation", "operation": "enqueue", "sessionId": "B", "content": "drop"},
        {"type": "queue-operation", "operation": "remove", "sessionId": "B"},
    ]
    assert _queued_messages(records) == ("keep",)


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
