"""Shared writer helpers: lossy-conversion detection and tool-schema recovery.

Each writer renders the portable conversation core, then calls ``report_losses``
to emit a ConversionReport warning for every gap-and-asymmetry (from the schema
analysis) that applies to this specific source->target pair. The warnings are the
"lossy sidecar": they tell the operator exactly what did not survive so the resume
handshake can compensate.
"""

from __future__ import annotations

from ..ir import BlockType, ConversionReport, Session, ToolSchema

# Which targets can hold which features.
_TARGET_CAPS = {
    "claude-code": {"thread_topology", "queued_input", "permission", "per_turn_model", "error_flag"},
    "codex": {"system_instructions", "permission", "per_turn_model"},
    "hermes": {"tool_schemas"},
}

# Prefix used to keep a failed-tool-call signal visible when the target format has
# no native error flag (Codex function_call_output / Hermes role:tool).
ERROR_MARKER = "[tool error] "


def report_losses(session: Session, target: str) -> ConversionReport:
    report = ConversionReport()
    caps = _TARGET_CAPS.get(target, set())
    src = session.meta.source_harness

    # 1. Thread topology (Claude Code only) flattened when target lacks it.
    has_topology = any(m.parent_uid for m in session.messages)
    if has_topology and "thread_topology" not in caps:
        report.warn(
            f"thread topology (parentUuid links) from {src} is flattened to append "
            f"order for {target}; branch/fork history is lost."
        )

    # 2. Reasoning signatures never survive a provider change.
    has_reasoning = any(
        b.type is BlockType.REASONING for m in session.messages for b in m.content
    )
    if has_reasoning and src != target:
        report.warn(
            "reasoning is carried as summary text only; provider-bound signatures / "
            "encrypted reasoning cannot be re-signed for the target model."
        )

    # 3. Tool schemas: only Hermes holds them.
    if session.tools and "tool_schemas" not in caps:
        report.warn(
            f"{len(session.tools)} tool schema(s) from {src} are dropped; {target} "
            f"supplies its own tool definitions at runtime."
        )
    if not session.tools and "tool_schemas" in caps:
        report.warn(
            "target Hermes expects a tool catalog but the source stored none; "
            "reconstructing an incomplete catalog from invoked tool names only."
        )

    # 4. System/base instructions: only Codex holds them.
    if session.meta.system_instructions and "system_instructions" not in caps:
        report.warn(
            "base/system instructions are dropped; target injects its own system "
            "prompt at runtime."
        )
    if not session.meta.system_instructions and "system_instructions" in caps:
        report.warn(
            "target Codex expects base_instructions but the source stored none; "
            "system framing will be empty on import."
        )

    # 5. Queued (undelivered) user input: Claude Code only.
    if session.pending.queued_user_messages and "queued_input" not in caps:
        report.warn(
            f"{len(session.pending.queued_user_messages)} queued/undelivered user "
            f"message(s) have no representation in {target}; surfaced in the resume "
            f"handshake instead."
        )

    # 7. Failed tool results: only Claude Code has a native is_error flag.
    if "error_flag" not in caps:
        error_results = sum(
            1
            for m in session.messages
            for b in m.content
            if b.type is BlockType.TOOL_RESULT and b.is_error
        )
        if error_results:
            report.warn(
                f"{error_results} failed tool result(s): {target} has no native error "
                f"flag; failure is preserved as a '{ERROR_MARKER.strip()}' text prefix only."
            )

    # 6. Permission posture erased by Hermes.
    if session.meta.permission_mode and "permission" not in caps:
        report.warn(
            f"permission/approval posture ('{session.meta.permission_mode}') is not "
            f"representable in {target} and is erased."
        )

    # 8/pending. Open tool calls need a handshake, not a plain transcript.
    if session.pending.open_tool_calls:
        report.warn(
            f"{len(session.pending.open_tool_calls)} open tool call(s) with no result; "
            f"resume requires the handshake preamble to satisfy or abandon them."
        )

    return report


def reconstruct_tool_schemas(session: Session) -> tuple[ToolSchema, ...]:
    """When a target needs a tool catalog but the source had none, synthesize a
    minimal catalog from the tool names actually invoked (no parameter schemas)."""
    if session.tools:
        return session.tools
    names: list[str] = []
    seen: set[str] = set()
    for m in session.messages:
        for b in m.content:
            if b.type is BlockType.TOOL_CALL and b.tool_name and b.tool_name not in seen:
                seen.add(b.tool_name)
                names.append(b.tool_name)
    return tuple(ToolSchema(name=n, description=None, parameters=None) for n in names)
