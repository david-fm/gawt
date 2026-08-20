"""Tests for edits.py — informed read, locks, informed rejection, STALE_WRITE."""
from __future__ import annotations

from pathlib import Path

import pytest

from gitagent import agents, edits, locks, session
from gitagent.errors import GitAgentError


def _setup_agent(repo_with_gitagent):
    repo, db = repo_with_gitagent
    s = session.start_session("edit-test", db=db)
    aid = agents.register_agent("dev", db=db)["agent_id"]
    return repo, db, aid, s["session_id"]


def test_read_amplified(repo_with_gitagent):
    repo, db, aid, sid = _setup_agent(repo_with_gitagent)
    edits.write(aid, "hello.txt", "hello world\n", db=db)

    r = edits.read(aid, "hello.txt", db=db)
    assert r["content"] == "hello world\n"
    assert len(r["sha256"]) == 64
    assert r["base_sha"]
    assert "diff" in r
    assert "warning" in r
    assert isinstance(r["edits"], list)
    assert r["edits"][0]["op"] == "write"


def test_edit_exact_match(repo_with_gitagent):
    repo, db, aid, sid = _setup_agent(repo_with_gitagent)
    edits.write(aid, "src/app.py", "def hello():\n    pass\n", db=db)
    edits.edit(aid, "src/app.py", "pass", "return 42", db=db)
    r = edits.read(aid, "src/app.py", db=db)
    assert "return 42" in r["content"]
    assert "pass" not in r["content"]


def test_edit_replace_all(repo_with_gitagent):
    repo, db, aid, sid = _setup_agent(repo_with_gitagent)
    edits.write(aid, "file.txt", "aaa bbb aaa", db=db)
    edits.edit(aid, "file.txt", "aaa", "xxx", replace_all=True, db=db)
    assert edits.read(aid, "file.txt", db=db)["content"] == "xxx bbb xxx"


def test_replace_all_recorded(repo_with_gitagent):
    repo, db, aid, sid = _setup_agent(repo_with_gitagent)
    edits.write(aid, "file.txt", "aa aa", db=db)
    edits.edit(aid, "file.txt", "aa", "x", replace_all=True, db=db)
    rows = [e for e in edits.list_edits(db=db) if e["op"] == "edit"]
    assert rows and rows[0]["replace_all"] == 1


def test_edit_old_string_not_found(repo_with_gitagent):
    _, db, aid, _ = _setup_agent(repo_with_gitagent)
    edits.write(aid, "file.txt", "abc", db=db)
    with pytest.raises(GitAgentError, match="old_string_not_found"):
        edits.edit(aid, "file.txt", "xyz", "123", db=db)


def test_edit_ambiguous_match(repo_with_gitagent):
    _, db, aid, _ = _setup_agent(repo_with_gitagent)
    edits.write(aid, "file.txt", "aaa bbb aaa", db=db)
    with pytest.raises(GitAgentError, match="ambiguous_match"):
        edits.edit(aid, "file.txt", "aaa", "xxx", db=db)


def test_delete_file(repo_with_gitagent):
    repo, db, aid, sid = _setup_agent(repo_with_gitagent)
    edits.write(aid, "to_delete.txt", "bye", db=db)
    wt = Path(repo / ".gitagent" / "worktree")
    assert (wt / "to_delete.txt").exists()
    edits.delete_file(aid, "to_delete.txt", db=db)
    assert not (wt / "to_delete.txt").exists()


def test_path_escape_rejected(repo_with_gitagent):
    _, db, aid, _ = _setup_agent(repo_with_gitagent)
    with pytest.raises(GitAgentError, match="escapes worktree"):
        edits.write(aid, "../x.py", "y", db=db)


def test_lock_rejects_other_agent_write(repo_with_gitagent):
    repo, db, aid1, sid = _setup_agent(repo_with_gitagent)
    aid2 = agents.register_agent("b", db=db)["agent_id"]

    # Agent 1 grabs the lock and holds it.
    locks.acquire("locked.txt", aid1, sid, db=db)
    resp = edits.write(aid2, "locked.txt", "should-not-apply", db=db)

    assert resp["status"] == "rejected"
    assert resp["blocked_by"]["agent_id"] == aid1
    assert "read" in resp

    # The file was NOT created.
    wt = Path(repo / ".gitagent" / "worktree")
    assert not (wt / "locked.txt").exists()


def test_rejection_does_not_record_edit(repo_with_gitagent):
    repo, db, aid, sid = _setup_agent(repo_with_gitagent)
    aid2 = agents.register_agent("b", db=db)["agent_id"]
    locks.acquire("x.py", aid, sid, db=db)
    edits.write(aid2, "x.py", "nope", db=db)
    file_edits = [e for e in edits.list_edits(db=db) if e["file"] == "x.py"]
    assert file_edits == []


def test_stale_write_rejected(repo_with_gitagent):
    _, db, aid, _ = _setup_agent(repo_with_gitagent)
    edits.write(aid, "stale.txt", "before", db=db)
    original = edits.read(aid, "stale.txt", db=db)
    edits.write(aid, "stale.txt", "changed", db=db)
    resp = edits.edit(
        aid, "stale.txt", "changed", "new",
        expected_sha256=original["sha256"], db=db,
    )
    assert resp["status"] == "rejected"
    assert resp["reason"] == "STALE_WRITE"


def test_list_edits_limit(repo_with_gitagent):
    _, db, aid, _ = _setup_agent(repo_with_gitagent)
    edits.write(aid, "a.py", "x", db=db)
    edits.edit(aid, "a.py", "x", "y", db=db)
    assert len(edits.list_edits(db=db)) == 2
    assert len(edits.list_edits(limit=1, db=db)) == 1