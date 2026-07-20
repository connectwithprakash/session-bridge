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
    PLACEHOLDER_MODELS,
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
    # NB: this is the Hermes *file* writer (writers/hermes.py), which emits a
    # tools catalog. The Hermes *DB* writer (writers/hermes_db.py, used by
    # `register`) has no tool-catalog column, so it does NOT hold tool_schemas.
    # report_losses is keyed by target NAME, not by writer, so the register path
    # must pass caps_override to declare the DB writer's real (narrower)
    # capabilities — otherwise dropped tool schemas go unreported.
    "hermes": {"tool_schemas"},
}

# The Hermes SQLite store (register path) persists messages/turns but no tool
# catalog, so it drops tool schemas the file writer would keep.
HERMES_DB_CAPS: frozenset = frozenset()


def report_losses(
    session: Session, target: str, caps_override=None
) -> ConversionReport:
    report = ConversionReport()
    caps = caps_override if caps_override is not None else _TARGET_CAPS.get(target, set())
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
    # Placeholder ids (e.g. Claude Code's "<synthetic>") are not real models;
    # don't count them as a distinct model in the switch check.
    models_seen -= PLACEHOLDER_MODELS
    if len(models_seen) > 1:
        report.warn(
            f"{len(models_seen)} models used across turns "
            f"({', '.join(sorted(str(x) for x in models_seen))}); the IR keeps only one "
            f"session model, so per-turn model attribution is lost."
        )
    # If the reader found no real model (only placeholders / none), the target's
    # model field will be empty — warn so resume-model selection isn't silently
    # wrong (a resume target routes inference by this field).
    if not session.meta.model and not models_seen:
        raw_models = {
            m.raw.get("message", {}).get("model")
            for m in session.messages
            if isinstance(m.raw, dict) and isinstance(m.raw.get("message"), dict)
        }
        if raw_models & PLACEHOLDER_MODELS:
            report.warn(
                "no real model id recorded (source only had a placeholder such as "
                "'<synthetic>'); the target session has no routable model and resume "
                "will need one supplied."
            )

    # 6. Permission posture erased by Hermes.
    if session.meta.permission_mode and "permission" not in caps:
        report.warn(
            f"permission/approval posture ('{session.meta.permission_mode}') is not "
            f"representable in {target} and is erased."
        )

    # 8. Tool calls with no matching result anywhere in the source (a gap in the
    # original transcript — either a genuinely-interrupted turn or a pre-existing
    # dropped result). Providers reject a tool_calls entry with no matching result
    # on the next turn, and Hermes's resume-repair does not stub it, so this breaks
    # `--resume`. `pending.open_tool_calls` already computes exactly this set
    # (never-resolved, tail-outstanding); report it as a first-class loss so the
    # operator knows before relying on a converted/registered session.
    if session.pending.open_tool_calls:
        report.warn(
            f"{len(session.pending.open_tool_calls)} tool call(s) have no matching "
            f"result; a resumed session may be rejected by the provider until these "
            f"are satisfied or removed (surfaced in the handshake for convert)."
        )

    # 9. RAW passthrough blocks (a source block the IR can't type, e.g. an image).
    # Re-emission is lossless ONLY when writing back to the SAME harness the block
    # came from (its raw_block is that harness's native shape). A same-named target
    # that received the block from a different harness cannot render the foreign
    # shape, so that path is lossy and must be reported. Gate on source==target,
    # not on the target capability alone.
    lossless_raw = "raw_passthrough" in caps and src == target
    if not lossless_raw:
        # Count standalone RAW blocks AND non-text parts carried on tool_results
        # (result_parts) — both are source content the IR can't type and that a
        # non-same-harness target degrades to a placeholder.
        raw_blocks = sum(
            1
            for m in session.messages
            for b in m.content
            if b.type is BlockType.RAW
        )
        result_part_count = sum(
            len(b.result_parts)
            for m in session.messages
            for b in m.content
            if b.type is BlockType.TOOL_RESULT
        )
        total = raw_blocks + result_part_count
        if total:
            report.warn(
                f"{total} non-text content part(s) (e.g. image/document) that "
                f"{target} cannot render degrade to a text placeholder."
            )

    # 10. Hermes block ordering. Hermes stores a turn's blocks in separate fields
    # / rows, so the reader reconstructs them in a fixed rank order:
    #   reasoning(0) -> text/raw(1) -> tool_call/tool_result(2).
    # A message whose blocks interleave these in any other order loses that
    # ordering even same-harness; report it rather than claim lossless. RAW is
    # ranked with TEXT (the writer buckets them together) and TOOL_RESULT with
    # TOOL_CALL (both emitted after text), so the check covers assistant AND
    # user/tool messages, not just the assistant reasoning/text/tool_call case.
    if target == "hermes":
        _order = {
            BlockType.REASONING: 0,
            BlockType.TEXT: 1,
            BlockType.RAW: 1,
            BlockType.TOOL_CALL: 2,
            BlockType.TOOL_RESULT: 2,
        }
        scrambled = 0
        for m in session.messages:
            ranks = [_order[b.type] for b in m.content if b.type in _order]
            if len(ranks) >= 2 and ranks != sorted(ranks):
                scrambled += 1
        if scrambled:
            report.warn(
                f"{scrambled} message(s) interleave block types in an order Hermes "
                f"cannot preserve (it stores reasoning/text/tool in separate fields); "
                f"they reconstruct as reasoning->text->tool."
            )

        # 11. Empty-text reasoning to Hermes. Hermes stores reasoning as a flat
        # string field that is empty for both "no reasoning" and "reasoning block
        # with no visible text" (the shape real extended-thinking uses), so an
        # empty-text reasoning block cannot be recovered on read-back. Report it.
        empty_reasoning = sum(
            1
            for m in session.messages
            for b in m.content
            if b.type is BlockType.REASONING and not (b.text or "").strip()
        )
        if empty_reasoning:
            report.warn(
                f"{empty_reasoning} reasoning block(s) with no visible text cannot be "
                f"represented in Hermes (its reasoning field can't distinguish an "
                f"empty-text reasoning block from no reasoning); they are dropped."
            )

    return report


def tool_result_text(block) -> str:
    """Text a cross-harness writer should emit for a TOOL_RESULT block.

    A tool_result may carry non-text parts (image, tool references) on
    ``result_parts`` — alongside text, or as its whole body. Those targets can't
    render the parts, so a placeholder describing them is appended (or is the
    whole body when there is no text). This surfaces the loss the ConversionReport
    already warns about for EVERY part, not just parts-only results, instead of
    silently dropping parts that sat next to text.
    """
    text = block.text or ""
    if not block.result_parts:
        return text
    kinds: dict[str, int] = {}
    for p in block.result_parts:
        k = p.get("type", "part") if isinstance(p, dict) else "part"
        kinds[k] = kinds.get(k, 0) + 1
    summary = ", ".join(f"{n} {k}" for k, n in kinds.items())
    placeholder = f"{UNSUPPORTED_BLOCK_MARKER}non-text tool result: {summary}]"
    return f"{text}\n{placeholder}" if text else placeholder


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
