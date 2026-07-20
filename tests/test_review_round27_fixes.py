"""Regression tests for Round-27 findings (CLI/placement robustness):

- place_claude_code silently overwrote an existing transcript at the same
  session-id path -> silent data loss for a recovered session. Now fails closed
  unless overwrite=True (--force), mirroring register's duplicate-id guard.
- a charset-valid but over-long --session-id reached the filesystem and raised
  a raw OSError (ENAMETOOLONG). Now bounded in validate_session_id.
- multi-line non-JSON input raised an uncaught json.JSONDecodeError traceback;
  main() now catches it and exits 2 cleanly.
- --place-claude-cwd with --to hermes wrote the -o output file before detecting
  the invalid combo (stray file on error). The check now runs first.
- the printed resume command interpolated an unquoted cwd/session-id.
"""

import json

import pytest

from session_bridge._ids import UnsafeSessionIdError, validate_session_id
from session_bridge.place import SessionExistsError, place_claude_code


def _rec(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def test_place_fails_closed_on_existing_session_id(tmp_path):
    home = tmp_path / ".claude"
    p1 = place_claude_code([_rec("FIRST")], "/tmp/proj", "dup", claude_home=home)
    assert "FIRST" in p1.read_text()
    with pytest.raises(SessionExistsError):
        place_claude_code([_rec("SECOND")], "/tmp/proj", "dup", claude_home=home)
    # the original transcript is untouched
    assert "FIRST" in p1.read_text()
    assert "SECOND" not in p1.read_text()


def test_place_overwrite_flag_allows_replace(tmp_path):
    home = tmp_path / ".claude"
    place_claude_code([_rec("FIRST")], "/tmp/proj", "dup", claude_home=home)
    p2 = place_claude_code([_rec("SECOND")], "/tmp/proj", "dup",
                           claude_home=home, overwrite=True)
    assert "SECOND" in p2.read_text()
    assert "FIRST" not in p2.read_text()


def test_over_long_session_id_rejected_before_fs():
    with pytest.raises(UnsafeSessionIdError):
        validate_session_id("a" * 5000)


def test_max_length_session_id_accepted():
    validate_session_id("a" * 128)  # at the cap, still valid
    with pytest.raises(UnsafeSessionIdError):
        validate_session_id("a" * 129)


def test_cli_multiline_non_json_clean_exit(tmp_path, capsys):
    from session_bridge.cli import main

    bad = tmp_path / "bad.jsonl"
    bad.write_text("not json\nmore text\n", encoding="utf-8")
    rc = main(["inspect", "--from", "codex", str(bad)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "JSON parse error" in err
    assert "Traceback" not in err


def test_cli_over_long_session_id_clean_exit(tmp_path, capsys):
    from session_bridge.cli import main

    src = tmp_path / "in.jsonl"
    src.write_text(
        json.dumps({"parentUuid": None, "type": "user", "uuid": "u1", "cwd": "/t",
                    "sessionId": "s", "message": {"role": "user", "content": "hi"}}) + "\n",
        encoding="utf-8",
    )
    rc = main(["convert", "--from", "claude-code", "--to", "claude-code", str(src),
               "-o", str(tmp_path / "o.jsonl"), "--place-claude-cwd", str(tmp_path),
               "--session-id", "a" * 5000])
    assert rc == 2
    assert "too long" in capsys.readouterr().err


def test_cli_place_with_hermes_target_writes_no_output(tmp_path):
    from session_bridge.cli import main

    src = tmp_path / "in.jsonl"
    src.write_text(
        json.dumps({"parentUuid": None, "type": "user", "uuid": "u1", "cwd": "/t",
                    "sessionId": "s", "message": {"role": "user", "content": "hi"}}) + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "stray.jsonl"
    rc = main(["convert", "--from", "claude-code", "--to", "hermes", str(src),
               "-o", str(out), "--place-claude-cwd", str(tmp_path)])
    assert rc == 2
    # the invalid combo must be detected before any output file is written
    assert not out.exists()
