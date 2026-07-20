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

# Bound the length so a charset-valid but absurd id doesn't reach the filesystem
# and raise a raw OSError ("File name too long", errno 63) at write time. 128 is
# comfortably above every real id format (a UUID is 36 chars).
_MAX_ID_LEN = 128


class UnsafeSessionIdError(ValueError):
    pass


def validate_session_id(session_id: str) -> str:
    """Return ``session_id`` unchanged if safe, else raise UnsafeSessionIdError.

    Rejects empty ids, ids longer than ``_MAX_ID_LEN``, and any id containing
    characters outside ``[A-Za-z0-9_-]`` — in particular path separators and
    ``..`` sequences.
    """
    if not session_id or not _SAFE_ID.match(session_id):
        raise UnsafeSessionIdError(
            f"unsafe session id {session_id!r}: only letters, digits, '_' and '-' allowed"
        )
    if len(session_id) > _MAX_ID_LEN:
        raise UnsafeSessionIdError(
            f"session id too long ({len(session_id)} chars; max {_MAX_ID_LEN})"
        )
    return session_id
