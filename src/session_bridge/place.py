"""Place a converted session where a harness's resume flow will find it.

Currently implemented for Claude Code, whose ``--resume <uuid>`` resolves a
session directly from ``~/.claude/projects/<encoded-cwd>/<uuid>.jsonl`` when
``claude`` is launched from the matching cwd. Placement therefore means:

1. encode the target cwd the way Claude Code does (path separators -> ``-``),
2. rewrite each record's ``sessionId`` and ``cwd`` to the chosen values,
3. write ``<uuid>.jsonl`` into that directory.

Hermes is intentionally not supported here: it indexes sessions in a SQLite
store, so a file drop is not enough (see README / issue #1).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from ._ids import validate_session_id


class UnsafeCwdError(ValueError):
    """The target cwd cannot be encoded into a safe project-dir name."""


# encode_cwd collapses the whole cwd into ONE directory-name component (every
# ``/`` -> ``-``), so its length is bounded by the filesystem's per-component
# limit (255 on macOS/Linux). Guard well under that so mkdir raises a clean
# UnsafeCwdError instead of a raw OSError (ENAMETOOLONG) — matching how
# validate_session_id guards the id half of the same path.
_MAX_ENCODED_CWD_LEN = 200


def encode_cwd(cwd: str) -> str:
    """Reproduce Claude Code's project-dir encoding: resolve the real path
    (macOS symlinks such as /tmp -> /private/tmp matter) and replace every
    path separator with ``-``."""
    real = os.path.realpath(os.path.expanduser(cwd))
    return real.replace("/", "-")


def claude_project_dir(cwd: str, claude_home: Optional[Path] = None) -> Path:
    home = claude_home or Path(os.path.expanduser("~/.claude"))
    return home / "projects" / encode_cwd(cwd)


class SessionExistsError(FileExistsError):
    """A transcript already exists at the target session-id path."""


def place_claude_code(
    records: list[dict[str, Any]],
    cwd: str,
    session_id: str,
    *,
    claude_home: Optional[Path] = None,
    overwrite: bool = False,
) -> Path:
    """Write ``records`` as a resumable Claude Code session for ``cwd``.

    Returns the transcript path. Rewrites ``sessionId``/``cwd`` on message
    records so the transcript is internally consistent with where it lives.

    Fails closed if a transcript already exists at the target path (a reused
    session id) unless ``overwrite`` is set: silently clobbering a placed
    transcript would destroy a possibly-precious recovered session, mirroring
    the duplicate-id guard ``register_hermes_session`` already enforces.
    """
    validate_session_id(session_id)  # reject path-traversal ids before touching the fs
    encoded = encode_cwd(cwd)
    if len(encoded) > _MAX_ENCODED_CWD_LEN:
        raise UnsafeCwdError(
            f"target cwd encodes to a {len(encoded)}-char directory name "
            f"(max {_MAX_ENCODED_CWD_LEN}); use a shorter path"
        )
    directory = claude_project_dir(cwd, claude_home)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{session_id}.jsonl"
    if target.exists() and not overwrite:
        raise SessionExistsError(
            f"a session transcript already exists at {target}; "
            f"choose a different --session-id or pass --force to overwrite"
        )

    real_cwd = os.path.realpath(os.path.expanduser(cwd))
    lines: list[str] = []
    for rec in records:
        rec = dict(rec)  # immutable-friendly copy
        if rec.get("type") in ("user", "assistant"):
            rec["sessionId"] = session_id
            rec["cwd"] = real_cwd
        lines.append(json.dumps(rec, ensure_ascii=False))

    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target
