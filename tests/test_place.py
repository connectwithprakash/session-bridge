import json
import os

from session_bridge.place import claude_project_dir, encode_cwd, place_claude_code


def test_encode_cwd_replaces_separators():
    assert encode_cwd("/Users/x/Developer/foo") == "-Users-x-Developer-foo"


def test_encode_cwd_resolves_symlinks(tmp_path):
    # a real directory resolves to itself; the point is realpath is applied
    d = tmp_path / "proj"
    d.mkdir()
    encoded = encode_cwd(str(d))
    assert encoded == os.path.realpath(str(d)).replace("/", "-")


def test_claude_project_dir_layout(tmp_path):
    home = tmp_path / ".claude"
    d = claude_project_dir("/Users/x/foo", claude_home=home)
    assert d == home / "projects" / "-Users-x-foo"


def test_place_writes_transcript_and_rewrites_ids(tmp_path):
    home = tmp_path / ".claude"
    cwd = str(tmp_path / "work")
    os.makedirs(cwd, exist_ok=True)
    records = [
        {"type": "user", "message": {"role": "user", "content": "hi"}, "uuid": "u1",
         "parentUuid": None, "sessionId": "old", "cwd": "/somewhere/else"},
        {"type": "assistant", "message": {"role": "assistant", "content": []}, "uuid": "a1",
         "parentUuid": "u1", "sessionId": "old", "cwd": "/somewhere/else"},
    ]
    path = place_claude_code(records, cwd, "new-sess-id", claude_home=home)

    assert path.name == "new-sess-id.jsonl"
    assert path.parent == claude_project_dir(cwd, claude_home=home)

    written = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    assert all(r["sessionId"] == "new-sess-id" for r in written)
    real_cwd = os.path.realpath(cwd)
    assert all(r["cwd"] == real_cwd for r in written)
    # thread linkage preserved
    assert [r["uuid"] for r in written] == ["u1", "a1"]
    assert [r["parentUuid"] for r in written] == [None, "u1"]


def test_place_creates_missing_directory(tmp_path):
    home = tmp_path / ".claude"
    cwd = str(tmp_path / "fresh")
    os.makedirs(cwd, exist_ok=True)
    assert not (home / "projects").exists()
    path = place_claude_code([], cwd, "sid", claude_home=home)
    assert path.parent.exists()
