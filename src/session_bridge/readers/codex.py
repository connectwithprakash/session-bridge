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
)
from ._pending import open_tool_calls


def _load_lines(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def _content_text(content: Any) -> str:
    """Join input_text / output_text blocks of a Codex message."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for b in content:
        if isinstance(b, dict) and b.get("type") in ("input_text", "output_text", "text"):
            parts.append(b.get("text", ""))
    return "\n".join(parts)


def _reasoning_text(payload: dict[str, Any]) -> str:
    summary = payload.get("summary")
    if isinstance(summary, list):
        return "\n".join(
            s.get("text", "") for s in summary if isinstance(s, dict)
        )
    if isinstance(payload.get("content"), str):
        return payload["content"]
    return ""


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
    records = _load_lines(path)

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
                role = Role.USER if payload.get("role") == "user" else Role.ASSISTANT
                text = _content_text(payload.get("content"))
                if text:
                    messages.append(
                        Message(role=role, content=(ContentBlock.text_block(text),),
                                timestamp=ts, raw=rec)
                    )
            elif ptype == "reasoning":
                text = _reasoning_text(payload)
                if text:
                    messages.append(
                        Message(role=Role.ASSISTANT,
                                content=(ContentBlock.reasoning(text),),
                                timestamp=ts, raw=rec)
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
                if isinstance(out, dict):
                    out = out.get("content", json.dumps(out))
                messages.append(
                    Message(
                        role=Role.TOOL,
                        content=(
                            ContentBlock.tool_result(
                                call_id=payload.get("call_id", ""),
                                text=out if isinstance(out, str) else json.dumps(out),
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
