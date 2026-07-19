"""Session-id validation shared by every path that puts an id into a filesystem
path or a store key.

A session id can come from a CLI argument (``--session-id``) or an untrusted
source session. Interpolating it into a file path (``place_claude_code``) without
validation allows path traversal (``../../evil``), so all id-consuming paths must
reject anything outside a safe charset.
"""

from __future__ import annotations

import re

# UUIDs, Hermes ids (``20260718_124320_97f3660d``), and the tool's own ``sb_...``
# ids all fit this. Deliberately excludes ``/``, ``.``, and whitespace.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class UnsafeSessionIdError(ValueError):
    pass


def validate_session_id(session_id: str) -> str:
    """Return ``session_id`` unchanged if safe, else raise UnsafeSessionIdError.

    Rejects empty ids and any id containing characters outside
    ``[A-Za-z0-9_-]`` — in particular path separators and ``..`` sequences.
    """
    if not session_id or not _SAFE_ID.match(session_id):
        raise UnsafeSessionIdError(
            f"unsafe session id {session_id!r}: only letters, digits, '_' and '-' allowed"
        )
    return session_id
