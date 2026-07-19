"""Regression tests for Round-12 finding: non-text parts inside a
tool_result.content list (e.g. an image from Read of a PNG) were silently
dropped instead of preserved as RAW passthrough."""

import json

from session_bridge.convert import convert
from session_bridge.ir import BlockType
from session_bridge.readers.claude_code import read_claude_code

_IMAGE_PART = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}


def _session_with_image_tool_result(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text(
        json.dumps({
            "parentUuid": None, "type": "assistant", "uuid": "a1", "cwd": "/t", "sessionId": "s",
            "message": {"role": "assistant", "model": "m", "content": [
                {"type": "tool_use", "id": "tu1", "name": "Read", "input": {"file_path": "x.png"}}]},
        }) + "\n"
        + json.dumps({
            "parentUuid": "a1", "type": "user", "uuid": "u1", "cwd": "/t", "sessionId": "s",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu1", "content": [_IMAGE_PART]}]},
        }) + "\n",
        encoding="utf-8",
    )
    return f


def test_image_in_tool_result_preserved_as_raw(tmp_path):
    session = read_claude_code(_session_with_image_tool_result(tmp_path))
    raws = [b for m in session.messages for b in m.content if b.type is BlockType.RAW]
    assert len(raws) == 1
    assert raws[0].raw_kind == "image"
    assert raws[0].raw_block["source"]["data"] == "AAAA"
    # the tool_result block still exists (linkage preserved)
    results = [b for m in session.messages for b in m.content if b.type is BlockType.TOOL_RESULT]
    assert len(results) == 1 and results[0].call_id == "tu1"


def test_image_tool_result_survives_same_harness_round_trip(tmp_path):
    src = _session_with_image_tool_result(tmp_path)
    result = convert("claude-code", "claude-code", src, inject_handshake=False)
    out = tmp_path / "out.jsonl"
    out.write_text("\n".join(json.dumps(r) for r in result.records) + "\n", encoding="utf-8")
    back = read_claude_code(out)
    raws = [b for m in back.messages for b in m.content if b.type is BlockType.RAW]
    assert len(raws) == 1 and raws[0].raw_block["source"]["data"] == "AAAA"


def test_image_tool_result_loss_reported_cross_harness(tmp_path):
    src = _session_with_image_tool_result(tmp_path)
    result = convert("claude-code", "hermes", src, inject_handshake=False)
    assert any("no IR representation" in w or "placeholder" in w for w in result.report.warnings)


def test_mixed_text_and_image_tool_result(tmp_path):
    # text + image in one tool_result: text goes to the result, image to RAW
    f = tmp_path / "s.jsonl"
    f.write_text(
        json.dumps({
            "parentUuid": None, "type": "user", "uuid": "u1", "cwd": "/t", "sessionId": "s",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu1", "content": [
                    {"type": "text", "text": "here is the screenshot"},
                    _IMAGE_PART,
                ]}]},
        }) + "\n",
        encoding="utf-8",
    )
    session = read_claude_code(f)
    result = next(b for m in session.messages for b in m.content if b.type is BlockType.TOOL_RESULT)
    assert result.text == "here is the screenshot"
    raws = [b for m in session.messages for b in m.content if b.type is BlockType.RAW]
    assert len(raws) == 1


def test_plain_text_tool_result_unaffected(tmp_path):
    # a normal string-content tool_result must not produce a RAW block
    f = tmp_path / "s.jsonl"
    f.write_text(
        json.dumps({
            "parentUuid": None, "type": "user", "uuid": "u1", "cwd": "/t", "sessionId": "s",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu1", "content": "plain output"}]},
        }) + "\n",
        encoding="utf-8",
    )
    session = read_claude_code(f)
    assert not any(b.type is BlockType.RAW for m in session.messages for b in m.content)
    result = next(b for m in session.messages for b in m.content if b.type is BlockType.TOOL_RESULT)
    assert result.text == "plain output"
