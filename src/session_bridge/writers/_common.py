"""Shared writer helpers: lossy-conversion detection and tool-schema recovery.

Each writer renders the portable conversation core, then calls ``report_losses``
to emit a ConversionReport warning for every gap-and-asymmetry (from the schema
analysis) that applies to this specific source->target pair. The warnings are the
"lossy sidecar": they tell the operator exactly what did not survive so the resume
handshake can compensate.
"""

from __future__ import annotations

from ..ir import (
    ERROR_MARKER,  # re-exported for writers that import it from here
    UNSUPPORTED_BLOCK_MARKER,
    BlockType,
    ConversionReport,
    Session,
    ToolSchema,
)

__all__ = ["ERROR_MARKER", "report_losses", "reconstruct_tool_schemas"]

# Which targets can hold which features. Only list a capability the corresponding
# writer actually EMITS. Notably NOT claimed:
#   - per_turn_model: the IR has no per-message model field, so every writer
#     stamps the single session model on every turn; a mid-session model switch
#     is therefore always lossy and must be reported (no writer implements it).
#   - claude-code permissionMode / queue-operation: the claude writer emits neither.
_TARGET_CAPS = {
    "claude-code": {"thread_topology", "error_flag", "raw_passthrough"},
    "codex": {"system_instructions", "permission"},
    "hermes": {"tool_schemas"},
}


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
                f"flag; failure is preserved as a '[tool error]' text prefix only."
            )

    # 7b. Per-turn model switch: the IR has no per-message model field, so a
    # session that used more than one model collapses to session.meta.model on
    # write. Detect it from either Claude Code's per-message raw.message.model or
    # Codex's per-turn models (recorded by the reader in meta.extra["turn_models"]).
    models_seen = {
        m.raw.get("message", {}).get("model")
        for m in session.messages
        if isinstance(m.raw, dict) and isinstance(m.raw.get("message"), dict)
        and m.raw["message"].get("model")
    }
    models_seen.update(session.meta.extra.get("turn_models") or [])
    models_seen.discard(None)
    if len(models_seen) > 1:
        report.warn(
            f"{len(models_seen)} models used across turns "
            f"({', '.join(sorted(str(x) for x in models_seen))}); the IR keeps only one "
            f"session model, so per-turn model attribution is lost."
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

    # 9. RAW passthrough blocks (a source block the IR can't type, e.g. an image).
    # Re-emission is lossless ONLY when writing back to the SAME harness the block
    # came from (its raw_block is that harness's native shape). A same-named target
    # that received the block from a different harness cannot render the foreign
    # shape, so that path is lossy and must be reported. Gate on source==target,
    # not on the target capability alone.
    lossless_raw = "raw_passthrough" in caps and src == target
    if not lossless_raw:
        raw_blocks = sum(
            1 for m in session.messages for b in m.content if b.type is BlockType.RAW
        )
        if raw_blocks:
            report.warn(
                f"{raw_blocks} content block(s) with no IR representation "
                f"(e.g. image/document) degrade to a text placeholder in {target}."
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
