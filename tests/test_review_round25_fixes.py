"""Regression test for Round-25 finding: the register backup used
``shutil.copy2``, which copies only the main ``.db`` file. Hermes runs in WAL
mode with the gateway daemon holding the DB open, so committed rows can live in
the sibling ``-wal`` file unflushed — a main-file-only copy silently omits them
(the backup can even lack tables entirely). The backup is the safety net before
a mutating write to the user's real store, so an incomplete backup is a
data-loss risk. Fixed by using SQLite's online backup API, which reads through
the live database state (main file + WAL)."""

import sqlite3

from session_bridge.writers.hermes_db import backup_hermes_db


def _wal_db_with_uncheckpointed_row(path):
    """A WAL-mode DB whose committed row is still in the -wal file, with a
    concurrent open connection (as the running Hermes gateway would hold)."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, started_at REAL NOT NULL);"
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, "
        "role TEXT NOT NULL, timestamp REAL NOT NULL);"
    )
    conn.commit()
    # A second live connection keeps the DB open so SQLite does not opportunistically
    # checkpoint the WAL into the main file — this mirrors the live daemon.
    holder = sqlite3.connect(path)
    holder.execute("SELECT count(*) FROM sessions")
    conn.execute("INSERT INTO sessions VALUES ('committed', 'claude-code', 1.0)")
    conn.commit()
    return conn, holder


def test_backup_preserves_committed_row_still_in_wal(tmp_path):
    db = tmp_path / "state.db"
    conn, holder = _wal_db_with_uncheckpointed_row(str(db))
    try:
        backup = tmp_path / "state.db.backup"
        backup_hermes_db(str(db), str(backup))

        b = sqlite3.connect(str(backup))
        try:
            # the row committed into the WAL must be present in the backup
            n = b.execute("SELECT COUNT(*) FROM sessions WHERE id='committed'").fetchone()[0]
        finally:
            b.close()
        assert n == 1, "backup dropped a committed row that was still in the -wal file"
    finally:
        conn.close()
        holder.close()


def test_backup_captures_full_schema_from_wal(tmp_path):
    # The starkest form of the bug: with a concurrent holder, even the schema
    # created in the same WAL is absent from a main-file-only copy. The API-based
    # backup must carry both tables.
    db = tmp_path / "state.db"
    conn, holder = _wal_db_with_uncheckpointed_row(str(db))
    try:
        backup = tmp_path / "state.db.backup"
        backup_hermes_db(str(db), str(backup))
        b = sqlite3.connect(str(backup))
        try:
            tables = {
                r[0]
                for r in b.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        finally:
            b.close()
        assert {"sessions", "messages"} <= tables
    finally:
        conn.close()
        holder.close()
