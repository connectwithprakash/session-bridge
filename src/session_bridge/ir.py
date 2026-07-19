"""Intermediate representation (IR) for cross-harness session portability.

Every harness reader normalizes into this model; every writer renders from it.
The IR is the union of what Claude Code, Codex, and Hermes can express, so that
conversion is lossless where the target supports a feature and *explicitly* lossy
(recorded in ``ConversionReport``) where it does not.

Design rules:
- Immutable value objects (frozen dataclasses). Transforms return new objects.
- Content is a list of typed blocks, not a flat string, so reasoning and tool
  calls survive round-trips instead of being flattened into text.
- ``raw`` fields preserve the source record so a writer can fall back to
  harness-specific detail the IR does not model.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Optional

# Marker a reader substitutes for a source content block the IR cannot represent
# (e.g. a Claude Code image block). Lives here so readers and writers share one
# definition; report_losses scans for it to report the loss.
UNSUPPORTED_BLOCK_MARKER = "[unsupported "

# Prefix a writer prepends to a failed tool result when the target format has no
# native error flag (Codex / Hermes). Readers recover is_error from it so a
# failure survives a multi-hop round trip instead of reading back as success.
# The token is deliberately unforgeable (like HANDSHAKE_MARKER) so that genuine
# tool output which merely starts with "[tool error]" is NOT mistaken for a
# bridge-inserted failure marker. The human-readable prefix follows the token so
# the result still reads naturally.
ERROR_MARKER_TOKEN = "<!-- session-bridge:tool-error -->"
ERROR_MARKER = ERROR_MARKER_TOKEN + "[tool error] "


def recover_tool_error(text: str) -> tuple[str, bool]:
    """If ``text`` carries the error marker, strip it and report an error.

    Lets a reader reconstruct ``is_error`` for a result whose failure was baked
    into text by a prior hop's writer (the target had no native error flag).
    Matches only the unforgeable token, so genuine output beginning with
    '[tool error]' is not misclassified."""
    if text.startswith(ERROR_MARKER):
        return text[len(ERROR_MARKER):], True
    return text, False


class Role(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class BlockType(str, Enum):
    TEXT = "text"
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    RAW = "raw"


@dataclass(frozen=True)
class ContentBlock:
    """One typed unit inside a message.

    - TEXT: ``text`` holds the prose.
    - REASONING: ``text`` holds the thinking content.
    - TOOL_CALL: ``tool_name`` + ``tool_input`` + ``call_id``.
    - TOOL_RESULT: ``call_id`` links back to the call; ``text`` holds the
      result payload; ``is_error`` marks a failed call.
    - RAW: a source content block the IR has no typed representation for (e.g. a
      Claude Code image/document block). ``raw_block`` holds the original block
      verbatim and ``raw_kind`` its source type, so a same-harness writer can
      re-emit it losslessly; a cross-harness writer degrades it to a reported
      placeholder. ``text`` holds that human-readable placeholder.
    """

    type: BlockType
    text: Optional[str] = None
    tool_name: Optional[str] = None
    tool_input: Optional[dict[str, Any]] = None
    call_id: Optional[str] = None
    is_error: bool = False
    raw_block: Optional[dict[str, Any]] = None
    raw_kind: Optional[str] = None
    # On a TOOL_RESULT: the original non-text parts (e.g. an image) of a
    # tool_result whose content was a block list. Carried WITH the result rather
    # than as sibling RAW blocks, so a same-harness writer re-emits them inside
    # the result's own content list and no writer mistakes them for a new turn.
    result_parts: tuple[dict[str, Any], ...] = ()

    @staticmethod
    def text_block(text: str) -> "ContentBlock":
        return ContentBlock(type=BlockType.TEXT, text=text)

    @staticmethod
    def raw(raw_block: dict[str, Any], raw_kind: str) -> "ContentBlock":
        return ContentBlock(
            type=BlockType.RAW,
            text=f"{UNSUPPORTED_BLOCK_MARKER}{raw_kind} block]",
            raw_block=copy.deepcopy(raw_block),
            raw_kind=raw_kind,
        )

    @staticmethod
    def reasoning(text: str) -> "ContentBlock":
        return ContentBlock(type=BlockType.REASONING, text=text)

    @staticmethod
    def tool_call(call_id: str, tool_name: str, tool_input: dict[str, Any]) -> "ContentBlock":
        return ContentBlock(
            type=BlockType.TOOL_CALL,
            call_id=call_id,
            tool_name=tool_name,
            tool_input=copy.deepcopy(tool_input),
        )

    @staticmethod
    def tool_result(
        call_id: str,
        text: str,
        is_error: bool = False,
        result_parts: tuple[dict[str, Any], ...] = (),
    ) -> "ContentBlock":
        return ContentBlock(
            type=BlockType.TOOL_RESULT,
            call_id=call_id,
            text=text,
            is_error=is_error,
            result_parts=tuple(copy.deepcopy(p) for p in result_parts),
        )


@dataclass(frozen=True)
class Message:
    """A single turn.

    ``uid`` is a stable identifier within the session. ``parent_uid`` records
    explicit thread linkage (Claude Code) so a non-linear thread survives; when a
    harness is purely append-ordered, ``parent_uid`` is left ``None`` and order is
    the list order.
    """

    role: Role
    content: tuple[ContentBlock, ...]
    uid: Optional[str] = None
    parent_uid: Optional[str] = None
    timestamp: Optional[str] = None
    raw: Optional[dict[str, Any]] = None

    def text(self) -> str:
        """Concatenated TEXT blocks (convenience for display, not round-trip)."""
        return "\n".join(b.text or "" for b in self.content if b.type == BlockType.TEXT)

    def display_text(self) -> str:
        """TEXT blocks plus RAW placeholders — the text a writer that cannot hold
        RAW should emit, so a RAW block degrades to its placeholder instead of
        vanishing when a message is flattened to a single string."""
        return "\n".join(
            b.text or ""
            for b in self.content
            if b.type in (BlockType.TEXT, BlockType.RAW)
        )


@dataclass(frozen=True)
class ToolSchema:
    """A tool the source session had available.

    Kept so a target harness can be told which tools the resumed session expects,
    and so portability gaps (target lacks this tool) can be detected.
    """

    name: str
    description: Optional[str] = None
    parameters: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class PendingState:
    """Half-finished state at the point the source session stopped.

    - ``open_tool_calls``: call_ids issued with no matching tool_result.
    - ``queued_user_messages``: user inputs enqueued but never processed
      (Claude Code queue-operation enqueue with no dequeue+turn).
    - ``active_goal``: a stated goal/todo still in flight, if the harness records one.
    """

    open_tool_calls: tuple[str, ...] = ()
    queued_user_messages: tuple[str, ...] = ()
    active_goal: Optional[str] = None

    def is_empty(self) -> bool:
        return not (self.open_tool_calls or self.queued_user_messages or self.active_goal)


@dataclass(frozen=True)
class SessionMeta:
    source_harness: str
    session_id: Optional[str] = None
    cwd: Optional[str] = None
    model: Optional[str] = None
    model_provider: Optional[str] = None
    system_instructions: Optional[str] = None
    permission_mode: Optional[str] = None
    version: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Session:
    """A fully normalized session, harness-agnostic."""

    meta: SessionMeta
    messages: tuple[Message, ...]
    tools: tuple[ToolSchema, ...] = ()
    pending: PendingState = field(default_factory=PendingState)

    def with_messages(self, messages: tuple[Message, ...]) -> "Session":
        return replace(self, messages=messages)


@dataclass
class ConversionReport:
    """Records what a reader/writer could not represent losslessly.

    Not frozen: accumulated during a conversion, then reported to the user.
    """

    warnings: list[str] = field(default_factory=list)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def ok(self) -> bool:
        return not self.warnings
