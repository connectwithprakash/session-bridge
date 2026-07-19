"""Top-level conversion API: read a source session, optionally prepend a resume
handshake, and render it to a target harness."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .handshake import build_handshake, handshake_message
from .ir import ConversionReport, Session
from .readers.claude_code import read_claude_code
from .readers.codex import read_codex
from .readers.hermes import read_hermes
from .writers.claude_code import write_claude_code
from .writers.codex import write_codex
from .writers.hermes import write_hermes

READERS: dict[str, Callable[[str | Path], Session]] = {
    "claude-code": read_claude_code,
    "codex": read_codex,
    "hermes": read_hermes,
}

WRITERS: dict[str, Callable[[Session], tuple[list[dict[str, Any]], ConversionReport]]] = {
    "claude-code": write_claude_code,
    "codex": write_codex,
    "hermes": write_hermes,
}

HARNESSES = tuple(READERS.keys())


@dataclass
class ConversionResult:
    session: Session
    records: list[dict[str, Any]]
    report: ConversionReport
    handshake: str


def read_session(source: str, path: str | Path) -> Session:
    if source not in READERS:
        raise ValueError(f"unknown source harness '{source}'; choose from {HARNESSES}")
    return READERS[source](path)


def convert(
    source: str,
    target: str,
    path: str | Path,
    *,
    inject_handshake: bool = True,
) -> ConversionResult:
    """Convert a session file from ``source`` to ``target``.

    When ``inject_handshake`` is set (default), a resume-handshake system message is
    prepended so the target agent resolves pending state before continuing.
    """
    if target not in WRITERS:
        raise ValueError(f"unknown target harness '{target}'; choose from {HARNESSES}")

    session = read_session(source, path)

    # Build the report first (from the untouched session) so the handshake text can
    # cite the real losses, then optionally prepend the handshake message.
    _, report = WRITERS[target](session)
    handshake_text = build_handshake(session, report, target)

    to_write = session
    if inject_handshake:
        hs = handshake_message(session, report, target)
        to_write = session.with_messages((hs,) + session.messages)

    records, _ = WRITERS[target](to_write)
    return ConversionResult(
        session=session, records=records, report=report, handshake=handshake_text
    )


def dump_jsonl(records: list[dict[str, Any]], path: str | Path) -> None:
    path = Path(path)
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
