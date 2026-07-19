"""Reader: Codex (rollout-*.jsonl) session -> IR.

Codex wraps everything in ``{timestamp, type, payload}``:
- ``session_meta``: payload has {id, cwd, model_provider, base_instructions:{text}, cli_version}.
- ``turn_context``: per-turn {turn_id, model, cwd, approval_policy, sandbox_policy}.
  Model lives here, not in session_meta.
- ``event_msg``: UI-facing turn events (task_started/task_complete/user_message/
  agent_message/token_count). These DUPLICATE content that also appears as
  ``response_item`` records, so we ignore event_msg for content to avoid doubling.
- ``response_item``: the canonical conversation, OpenAI Responses shape:
    - {type:message, role:user|assistant, content:[{type:input_text|output_text, text}]}
    - {type:reasoning, summary:[{type:summary_text, text}]}
    - {type:function_call, name, arguments:<json-string>, call_id}
    - {type:function_call_output, call_id, output}

Purely append-ordered; no parent linkage (grouped by turn_id).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..ir import (
    ContentBlock,
    Message,
    PendingState,
    Role,
    Session,
    SessionMeta,
    recover_tool_error,
)
from ._content import content_blocks
from ._jsonl import load_records
from ._pending import open_tool_calls

_ROLE_FROM_CODEX = {
    "user": Role.USER,
    "assistant": Role.ASSISTANT,
    "system": Role.SYSTEM,
    "developer": Role.SYSTEM,
}


def _reasoning_text(payload: dict[str, Any]) -> str:
    """Codex reasoning appears in either ``summary`` or ``content``, and each may
    be a list of ``{type, text}`` blocks (real sessions) or a plain string.
    Prefer non-empty ``content`` (the full reasoning) over ``summary``."""

    def _join(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "\n".join(
                b.get("text", "") for b in value if isinstance(b, dict) and b.get("text")
            )
        return ""

    content = _join(payload.get("content"))
    if content.strip():
        return content
    return _join(payload.get("summary"))


def _parse_arguments(raw_args: Any) -> dict[str, Any]:
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str) and raw_args.strip():
        try:
            parsed = json.loads(raw_args)
            return parsed if isinstance(parsed, dict) else {"_value": parsed}
        except json.JSONDecodeError:
            return {"_raw": raw_args}
    return {}


def read_codex(path: str | Path) -> Session:
    path = Path(path)
    records = load_records(path)

    meta = SessionMeta(source_harness="codex", model_provider="openai")
    messages: list[Message] = []

    for rec in records:
        rtype = rec.get("type")
        payload = rec.get("payload", {})
        if not isinstance(payload, dict):
            continue
        ts = rec.get("timestamp")

        if rtype == "session_meta":
            base = payload.get("base_instructions")
            instr = base.get("text") if isinstance(base, dict) else base
            meta = SessionMeta(
                source_harness="codex",
                session_id=payload.get("id") or payload.get("session_id"),
                cwd=payload.get("cwd"),
                model_provider=payload.get("model_provider", "openai"),
                system_instructions=instr,
                version=payload.get("cli_version"),
            )
        elif rtype == "turn_context":
            # Model and approval policy live here; keep the first seen.
            from dataclasses import replace

            updates: dict[str, Any] = {}
            if not meta.model and payload.get("model"):
                updates["model"] = payload["model"]
            if not meta.permission_mode and payload.get("approval_policy"):
                updates["permission_mode"] = payload["approval_policy"]
            if not meta.cwd and payload.get("cwd"):
                updates["cwd"] = payload["cwd"]
            if updates:
                meta = replace(meta, **updates)
        elif rtype == "response_item":
            ptype = payload.get("type")
            if ptype == "message":
                role = _ROLE_FROM_CODEX.get(payload.get("role"), Role.ASSISTANT)
                # Text parts -> TEXT blocks, non-text parts -> RAW passthrough
                # (not silently dropped). Empty content is preserved as () so the
                # turn/message count round-trips.
                content = content_blocks(payload.get("content"))
                messages.append(
                    Message(role=role, content=content, timestamp=ts, raw=rec)
                )
            elif ptype == "reasoning":
                text = _reasoning_text(payload)
                # Preserve the turn even when the reasoning summary is empty
                # (redacted), matching the empty-message handling, so a reasoning
                # record round-trips instead of vanishing.
                content = (ContentBlock.reasoning(text),) if text else ()
                messages.append(
                    Message(role=Role.ASSISTANT, content=content, timestamp=ts, raw=rec)
                )
            elif ptype == "function_call":
                messages.append(
                    Message(
                        role=Role.ASSISTANT,
                        content=(
                            ContentBlock.tool_call(
                                call_id=payload.get("call_id", ""),
                                tool_name=payload.get("name", ""),
                                tool_input=_parse_arguments(payload.get("arguments")),
                            ),
                        ),
                        timestamp=ts,
                        raw=rec,
                    )
                )
            elif ptype == "function_call_output":
                out = payload.get("output", "")
                is_error = False
                if isinstance(out, dict):
                    # A dict output can carry an explicit failure flag; preserve it
                    # so a failed tool call is not reported as successful. Accept
                    # both the JSON bool False and a string "false".
                    success = out.get("success")
                    failed = (
                        success is False
                        or (isinstance(success, str) and success.strip().lower() == "false")
                        or bool(out.get("error"))
                    )
                    if failed:
                        is_error = True
                    out = out.get("content", json.dumps(out))
                text = out if isinstance(out, str) else json.dumps(out)
                # Recover an error baked into text by a prior hop (a writer with no
                # native error flag prefixes ERROR_MARKER), so failure survives.
                text, marked = recover_tool_error(text)
                messages.append(
                    Message(
                        role=Role.TOOL,
                        content=(
                            ContentBlock.tool_result(
                                call_id=payload.get("call_id", ""),
                                text=text,
                                is_error=is_error or marked,
                            ),
                        ),
                        timestamp=ts,
                        raw=rec,
                    )
                )
        # event_msg intentionally ignored (duplicates response_item content)

    msgs = tuple(messages)
    pending = PendingState(open_tool_calls=open_tool_calls(msgs))
    return Session(meta=meta, messages=msgs, tools=(), pending=pending)
