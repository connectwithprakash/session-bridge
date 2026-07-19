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

    Lines that fail to parse, or parse to a non-object (list/str/number/null),
    are skipped — never fatal. This preserves all recoverable records when the
    tail of the file is a partial write or a stray line was introduced.
    """
    records: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                # Truncated/partial or malformed line: skip, keep the rest.
                continue
            if isinstance(obj, dict):
                records.append(obj)
            # Non-dict JSON (list/str/number/null) is not a session record; skip.
    return records
