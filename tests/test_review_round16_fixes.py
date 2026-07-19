"""Regression tests for Round-16 finding: the Hermes reader ignored the
`codex_reasoning_items` field, silently dropping reasoning on real records that
have reasoning=null but carry opaque extended-thinking items."""

import json

from session_bridge.ir import BlockType
from session_bridge.readers.hermes import read_hermes


def _write(tmp_path, *records):
    f = tmp_path / "h.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return f


def test_codex_reasoning_items_recovers_reasoning(tmp_path):
    # reasoning=null but codex_reasoning_items present -> a reasoning block exists
    f = _write(
        tmp_path,
        {"role": "session_meta", "model": "m", "tools": []},
        {"role": "assistant", "content": "answer", "reasoning": None,
         "codex_reasoning_items": [{"type": "reasoning", "encrypted_content": "opaque"}]},
    )
    session = read_hermes(f)
    reasoning = [b for m in session.messages for b in m.content if b.type is BlockType.REASONING]
    assert len(reasoning) == 1


def test_visible_reasoning_still_preferred(tmp_path):
    # when the flat reasoning string is present, use it (not an empty block)
    f = _write(
        tmp_path,
        {"role": "session_meta", "model": "m", "tools": []},
        {"role": "assistant", "content": "a", "reasoning": "thinking hard",
         "codex_reasoning_items": [{"type": "reasoning"}]},
    )
    session = read_hermes(f)
    reasoning = [b for m in session.messages for b in m.content if b.type is BlockType.REASONING]
    assert len(reasoning) == 1 and reasoning[0].text == "thinking hard"


def test_no_reasoning_signal_no_block(tmp_path):
    # neither signal present -> no reasoning block (no false positive)
    f = _write(
        tmp_path,
        {"role": "session_meta", "model": "m", "tools": []},
        {"role": "assistant", "content": "plain", "reasoning": None},
    )
    session = read_hermes(f)
    assert not any(b.type is BlockType.REASONING for m in session.messages for b in m.content)


def test_empty_codex_items_no_block(tmp_path):
    # empty codex_reasoning_items list is not a reasoning signal
    f = _write(
        tmp_path,
        {"role": "session_meta", "model": "m", "tools": []},
        {"role": "assistant", "content": "plain", "reasoning": None, "codex_reasoning_items": []},
    )
    session = read_hermes(f)
    assert not any(b.type is BlockType.REASONING for m in session.messages for b in m.content)
