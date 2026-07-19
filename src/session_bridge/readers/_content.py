"""Shared content-part normalization for readers.

Codex and Hermes both carry message ``content`` as either a plain string or a
list of typed parts (OpenAI multi-modal shape). Text parts become TEXT blocks;
any other part (image, etc.) becomes a RAW passthrough block rather than being
silently dropped — matching how the Claude Code reader already handles unknown
blocks, so all three readers preserve+report unrepresentable content uniformly.
"""

from __future__ import annotations

from typing import Any

from ..ir import ContentBlock

# Part ``type`` values that carry plain text across Codex and Hermes.
_TEXT_PART_TYPES = {"input_text", "output_text", "text"}


def content_blocks(content: Any) -> tuple[ContentBlock, ...]:
    """Normalize a message ``content`` (str or list of parts) to IR blocks.

    A string yields one TEXT block (empty string yields no block). A list yields
    a TEXT block per text part and a RAW block per non-text part, in order.
    """
    if isinstance(content, str):
        return (ContentBlock.text_block(content),) if content else ()
    if not isinstance(content, list):
        return ()

    blocks: list[ContentBlock] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype in _TEXT_PART_TYPES:
            text = part.get("text", "")
            if text:
                blocks.append(ContentBlock.text_block(text))
        elif ptype:
            blocks.append(ContentBlock.raw(part, ptype))
    return tuple(blocks)
