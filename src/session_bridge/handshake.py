"""Resume handshake.

Structural file conversion alone cannot resume a session that stopped mid-turn:
the target harness will happily replay a transcript, but it won't *know* that a
tool call was left open, that a user message was queued but never processed, or
that a stated goal is still in flight. The handshake turns that pending state
(plus the lossy-conversion warnings) into an explicit instruction block that is
injected as the first message of the resumed session, so the receiving agent
picks up deliberately instead of guessing.

This is the "handshake protocol, not just file conversion" the research flagged.
"""

from __future__ import annotations

from typing import Any

from .ir import BlockType, ContentBlock, ConversionReport, Message, Role, Session

HANDSHAKE_TITLE = "# Session resume handshake"

# An unforgeable marker embedded in every handshake body. Detection matches on
# THIS, not the human-readable title: a real user message could legitimately
# start with the title (e.g. someone quoting a prior handshake), and stripping it
# would silently delete real content. The HTML comment is invisible in rendered
# Markdown and vanishingly unlikely to appear by coincidence in genuine input.
HANDSHAKE_MARKER = "<!-- session-bridge:handshake -->"


def is_handshake_message(message: Message) -> bool:
    # Match on the embedded marker, not the title text or the role: role is
    # unreliable (SYSTEM can round-trip back as USER once a writer folds it) and
    # the title alone collides with legitimate user prose.
    return any(
        b.type is BlockType.TEXT and HANDSHAKE_MARKER in (b.text or "")
        for b in message.content
    )


def strip_prior_handshakes(session: Session) -> Session:
    """Remove any handshake messages a previous conversion hop injected, so a
    fresh handshake replaces them instead of stacking."""
    kept = tuple(m for m in session.messages if not is_handshake_message(m))
    return session.with_messages(kept)


def _open_call_details(session: Session) -> list[tuple[str, str, str]]:
    """(call_id, tool_name, arguments-preview) for each unresolved call.

    ``pending.open_tool_calls`` is computed positionally (a reissued call_id that
    was resolved once then reissued is open again). Pick the LAST issuing block
    per open id so a reissued call shows its current args, not the resolved
    earlier occurrence.
    """
    open_ids = set(session.pending.open_tool_calls)
    last_block: dict[str, Any] = {}
    for m in session.messages:
        for b in m.content:
            if b.type is BlockType.TOOL_CALL and b.call_id in open_ids:
                last_block[b.call_id] = b
    out = []
    for call_id in session.pending.open_tool_calls:
        b = last_block.get(call_id)
        if b is None:
            continue
        args = b.tool_input or {}
        preview = ", ".join(f"{k}={v!r}" for k, v in list(args.items())[:4])
        out.append((call_id, b.tool_name or "?", preview))
    return out


def build_handshake(session: Session, report: ConversionReport, target: str) -> str:
    """Render a human+agent readable resume preamble in Markdown."""
    src = session.meta.source_harness
    lines: list[str] = []
    lines.append(HANDSHAKE_MARKER)
    lines.append(HANDSHAKE_TITLE)
    lines.append("")
    lines.append(f"This session was exported from **{src}** and resumed in **{target}** "
                 f"by session-bridge. Read this before continuing.")
    lines.append("")

    # Context recap.
    lines.append("## Original context")
    if session.meta.cwd:
        lines.append(f"- Working directory: `{session.meta.cwd}`")
    if session.meta.model:
        lines.append(f"- Source model: `{session.meta.model}`")
    real_turns = sum(1 for m in session.messages if not is_handshake_message(m))
    lines.append(f"- Turns carried over: {real_turns}")
    lines.append("")

    pending = session.pending
    if pending.is_empty():
        lines.append("## Pending state")
        lines.append("- None. The source stopped at a clean turn boundary; continue normally.")
        lines.append("")
    else:
        lines.append("## Pending state — resolve these before proceeding")
        open_calls = _open_call_details(session)
        if open_calls:
            lines.append("")
            lines.append("### Open tool calls (issued, no result)")
            for call_id, name, preview in open_calls:
                lines.append(f"- `{name}`({preview}) — call_id `{call_id}`. "
                             f"Re-run this tool (or decide it is no longer needed) "
                             f"before continuing the turn.")
        if pending.queued_user_messages:
            lines.append("")
            lines.append("### Queued user input (typed but never processed)")
            for q in pending.queued_user_messages:
                lines.append(f"- {q}")
        if pending.active_goal:
            lines.append("")
            lines.append(f"### Active goal\n- {pending.active_goal}")
        lines.append("")

    # Lossy conversion notes.
    if report.warnings:
        lines.append("## Conversion notes (what did not transfer losslessly)")
        for w in report.warnings:
            lines.append(f"- {w}")
        lines.append("")

    lines.append("## Instruction")
    if pending.is_empty():
        lines.append("Resume the conversation from the last turn above.")
    else:
        lines.append("First satisfy the pending state above, then resume from the "
                     "last turn. Do not silently skip open tool calls or queued input.")
    return "\n".join(lines)


def handshake_message(session: Session, report: ConversionReport, target: str) -> Message:
    """The handshake as an IR system message, ready to prepend before writing."""
    return Message(
        role=Role.SYSTEM,
        content=(ContentBlock.text_block(build_handshake(session, report, target)),),
    )
