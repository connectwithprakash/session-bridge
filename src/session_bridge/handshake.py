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

from .ir import BlockType, ContentBlock, ConversionReport, Message, Role, Session


def _open_call_details(session: Session) -> list[tuple[str, str, str]]:
    """(call_id, tool_name, arguments-preview) for each unresolved call."""
    open_ids = set(session.pending.open_tool_calls)
    out = []
    for m in session.messages:
        for b in m.content:
            if b.type is BlockType.TOOL_CALL and b.call_id in open_ids:
                args = b.tool_input or {}
                preview = ", ".join(f"{k}={v!r}" for k, v in list(args.items())[:4])
                out.append((b.call_id, b.tool_name or "?", preview))
    return out


def build_handshake(session: Session, report: ConversionReport, target: str) -> str:
    """Render a human+agent readable resume preamble in Markdown."""
    src = session.meta.source_harness
    lines: list[str] = []
    lines.append("# Session resume handshake")
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
    lines.append(f"- Turns carried over: {len(session.messages)}")
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
