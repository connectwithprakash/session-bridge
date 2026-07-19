"""Register a session into Hermes's SQLite store so `--resume` can find it.

Unlike Claude Code (which resumes straight from a transcript file), Hermes keeps
its sessions in ``~/.hermes/state.db`` across two tables:

- ``sessions``: one row per session. Required columns are ``id``, ``source``,
  ``started_at``; the rest are optional/defaulted.
- ``messages``: one row per turn, keyed by ``session_id``. Required columns are
  ``session_id``, ``role``, ``timestamp``.

The ``.jsonl`` files under ``~/.hermes/sessions/`` are request dumps/exports, not
the source of truth, so registering a session means writing these two tables.

This module never targets a hard-coded path: the caller passes the DB path, so a
sandbox copy and the real store use the same code. Writing to the real store is
gated behind an explicit opt-in in the CLI, with a backup taken first.

Verified against a real ``~/.hermes/state.db``: writing the ``sessions`` and
``messages`` rows is sufficient for full context resume. The session appears in
``hermes sessions list``, and ``hermes --resume <id>`` replays the registered
history so the model recalls it. Two conditions matter:

- ``started_at`` must be a real timestamp, or the session sorts below the default
  list limit.
- the stored ``model`` must be one Hermes has a provider for; a cross-harness
  source model id (e.g. an Anthropic ``claude-*`` id) that Hermes cannot route
  makes the resumed turn fall back and lose context. Use the ``model`` argument to
  set a Hermes-valid id.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

from ..ir import BlockType, Message, Role, Session
from ._common import ERROR_MARKER, tool_result_text

_ROLE_TO_DB = {
    Role.USER: "user",
    Role.ASSISTANT: "assistant",
    Role.TOOL: "tool",
    Role.SYSTEM: "system",
}


class HermesRegistrationError(RuntimeError):
    pass


def _require_schema(conn: sqlite3.Connection) -> None:
    """Fail loudly if the DB isn't a Hermes state store, rather than creating
    bogus tables in whatever file we were handed."""
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    missing = {"sessions", "messages"} - tables
    if missing:
        raise HermesRegistrationError(
            f"not a Hermes state.db (missing tables: {sorted(missing)})"
        )


def _title_conflict(conn: sqlite3.Connection, title: Optional[str]) -> bool:
    if not title:
        return False
    row = conn.execute(
        "SELECT 1 FROM sessions WHERE title = ? LIMIT 1", (title,)
    ).fetchone()
    return row is not None


def _message_rows(
    session_id: str, messages: tuple[Message, ...], base_ts: float = 0.0
) -> list[dict[str, Any]]:
    """Flatten IR messages into Hermes messages-table rows.

    Timestamps are monotonic floats offset from ``base_ts`` (the session's
    started_at) so ordering is stable and last-active lands near session start
    even when the source timestamps are missing or non-numeric.
    """
    rows: list[dict[str, Any]] = []
    ts = base_ts
    for m in messages:
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        result_blocks = []
        # Position of the first non-result block, so the single coalesced
        # non-result row (text/reasoning/tool_calls) lands relative to the tool
        # rows at the point the message's non-result content began — fixing the
        # old always-tool-rows-first bug for the common single-run shapes
        # ([text, result] and [result, text]).
        #
        # LIMITATION: all non-result blocks collapse into ONE row at the FIRST
        # non-result position, so a message that interleaves text on BOTH sides
        # of a result ([text, result, text]) places the merged text before the
        # result. No reader produces that shape (tool results always arrive in
        # their own turn), so this is not reachable today; documented rather than
        # solved with per-run row splitting to avoid speculative complexity.
        first_nonresult_pos = None
        seq = 0

        for b in m.content:
            if b.type is BlockType.TOOL_RESULT:
                result_blocks.append((seq, b))
                seq += 1
            else:
                if first_nonresult_pos is None:
                    first_nonresult_pos = seq
                    seq += 1  # reserve one slot for the coalesced non-result row
                if b.type is BlockType.TEXT or b.type is BlockType.RAW:
                    text_parts.append(b.text or "")
                elif b.type is BlockType.REASONING:
                    reasoning_parts.append(b.text or "")
                elif b.type is BlockType.TOOL_CALL:
                    tool_calls.append(
                        {
                            "id": b.call_id,
                            "call_id": b.call_id,
                            "type": "function",
                            "function": {
                                "name": b.tool_name,
                                "arguments": json.dumps(b.tool_input or {}),
                            },
                        }
                    )

        # Build (position, row) pairs, then emit in position order so the row
        # sequence mirrors the source block order.
        pending: list[tuple[int, dict[str, Any]]] = []
        for pos, b in result_blocks:
            content = tool_result_text(b)  # placeholder for parts-only results
            if b.is_error:
                content = ERROR_MARKER + content
            pending.append((pos, {
                "session_id": session_id,
                "role": "tool",
                "content": content,
                "tool_call_id": b.call_id,
                "tool_calls": None,
                "tool_name": None,
                "reasoning": None,
            }))

        has_nonresult = text_parts or reasoning_parts or tool_calls
        if has_nonresult or not result_blocks:
            pos = first_nonresult_pos if first_nonresult_pos is not None else seq
            pending.append((pos, {
                "session_id": session_id,
                "role": _ROLE_TO_DB.get(m.role, "user"),
                "content": "\n".join(text_parts) if text_parts else None,
                "tool_call_id": None,
                "tool_calls": json.dumps(tool_calls) if tool_calls else None,
                "tool_name": None,
                "reasoning": "\n".join(reasoning_parts) if reasoning_parts else None,
            }))

        for _, row in sorted(pending, key=lambda pr: pr[0]):
            row["timestamp"] = ts
            rows.append(row)
            ts += 1.0
    return rows


def register_hermes_session(
    session: Session,
    db_path: str,
    session_id: str,
    *,
    source: str = "cli",
    title: Optional[str] = None,
    started_at: float = 0.0,
    model: Optional[str] = None,
) -> None:
    """Insert one ``sessions`` row and its ``messages`` rows into ``db_path``.

    ``started_at`` is a Unix epoch float; pass a real current time so the session
    sorts to the top of ``hermes sessions list`` (which orders by activity and
    truncates to a default limit). Left 0.0 the session registers correctly but
    sinks to the bottom of the list. Message timestamps are offset from this base
    so ordering is stable.

    ``model`` overrides the stored model id. This matters for resume: Hermes routes
    the resumed turn to the session's ``model``, so a cross-harness source model id
    (e.g. an Anthropic ``claude-*`` id from a Claude Code session) that Hermes has
    no provider for will fail to continue with context. Pass a model id Hermes is
    configured for (verified: with a valid model, ``hermes --resume`` replays the
    registered history and the model recalls it). Defaults to the session's own
    model.

    Raises HermesRegistrationError if the DB is not a Hermes store, the session id
    already exists, or the title collides (the store has a UNIQUE title index).
    All writes happen in a single transaction.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        _require_schema(conn)

        existing = conn.execute(
            "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if existing:
            raise HermesRegistrationError(f"session id already exists: {session_id}")
        if _title_conflict(conn, title):
            raise HermesRegistrationError(f"title already in use: {title!r}")

        msgs = _message_rows(session_id, session.messages, base_ts=started_at)
        tool_call_count = sum(
            1 for m in session.messages for b in m.content if b.type is BlockType.TOOL_CALL
        )
        # message_count reflects logical IR turns, not emitted DB rows: one IR
        # message with N parallel tool results expands to N+ rows, and counting
        # rows would inflate the stat shown in `hermes sessions list`.
        message_count = len(session.messages)

        try:
            with conn:  # transaction: commit on success, rollback on error
                conn.execute(
                    """
                    INSERT INTO sessions
                        (id, source, model, started_at, message_count,
                         tool_call_count, title, cwd, archived)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        session_id,
                        source,
                        model or session.meta.model,
                        started_at,
                        message_count,
                        tool_call_count,
                        title,
                        session.meta.cwd,
                    ),
                )
                conn.executemany(
                    """
                    INSERT INTO messages
                        (session_id, role, content, tool_call_id, tool_calls,
                         tool_name, timestamp, reasoning)
                    VALUES
                        (:session_id, :role, :content, :tool_call_id, :tool_calls,
                         :tool_name, :timestamp, :reasoning)
                    """,
                    msgs,
                )
        except sqlite3.IntegrityError as exc:
            # The pre-checks above are advisory (not atomic with the write): a
            # concurrent registration or an empty-string title can still trip a
            # UNIQUE constraint here. Surface the module's contracted exception
            # type rather than leaking a raw sqlite3 error to callers. The
            # transaction has already rolled back.
            raise HermesRegistrationError(
                f"registration conflict for session {session_id}: {exc}"
            ) from exc
    finally:
        conn.close()
