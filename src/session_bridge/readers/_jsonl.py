"""Shared, defensive JSONL loading for all readers.

Session logs are appended live by a running harness, so a real file can end in a
half-written final line (the process died mid-flush — exactly the situation this
tool exists to recover from) or contain a hand-edited/tool-mangled line. Loading
must therefore be tolerant: skip lines that are not a JSON object rather than
letting one bad line abort the whole read and lose every valid record before it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_records(path: str | Path) -> list[dict[str, Any]]:
    """Return the JSON-object records in a JSONL file.

    Tolerates the one benign corruption a live-appended log actually produces: a
    truncated/partial **final** line (the process died mid-flush). That last line
    is skipped so every complete record before it survives.

    A parse failure on an **interior** line is NOT benign — it means a real
    record was corrupted and silently skipping it would drop a turn and orphan
    the messages that referenced it. Interior parse failures re-raise rather than
    vanish. Non-object JSON (list/str/number/null) on any line is not a session
    record and is skipped quietly.
    """
    raw_lines = [ln.strip() for ln in open(path, encoding="utf-8")]
    # Index of the last non-empty line: a parse failure there is a tail truncation.
    last_content_idx = max((i for i, ln in enumerate(raw_lines) if ln), default=-1)

    records: list[dict[str, Any]] = []
    for i, line in enumerate(raw_lines):
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            if i == last_content_idx:
                # Truncated final line (mid-write crash): safe to drop.
                continue
            # Corrupted interior line: a real record is being lost. Fail loudly
            # rather than silently returning a partial, orphan-linked session.
            raise
        if isinstance(obj, dict):
            records.append(obj)
        # Non-dict JSON is not a session record; skip.
    return records
