"""Tests for edits.py — atomic write, edit/write/read/delete, conflict detection."""
from __future__ import annotations

from pathlib import Path

import pytest

from gitagent import agents, edits, session
from gitagent.errors import GitAgentError


def _setup_agent(repo_with_gitagent):
    repo, db = repo_with_gitagent
    session.start_session("edit-test", db=db)
    aid = agents.register_agent("dev", db=db)["agent_id"]
    return repo, db, aid


def test_write_and_read(repo_with_gitagent):
    repo, db, aid = _setup_agent(repo_with_gitagent)

    edits.write(aid, "hello.txt", "hello world\n", db=db)
    r = edits.read(aid, "hello.txt", db=db)
    assert r["content"] == "hello world\n"
    assert len(r["sha256"]) == 64


def test_edit_exact_match(repo_with_gitagent):
    repo, db, aid = _setup_agent(repo_with_gitagent)

    edits.write(aid, "src/app.py", "def hello():\n    pass\n", db=db)
    edits.edit(aid, "src/app.py", "pass", "return 42", db=db)

    r = edits.read(aid, "src/app.py", db=db)
    assert "return 42" in r["content"]
    assert "pass" not in r["content"]


def test_edit_old_string_not_found(repo_with_gitagent):
    repo, db, aid = _setup_agent(repo_with_gitagent)

    edits.write(aid, "file.txt", "abc", db=db)
    with pytest.raises(GitAgentError, match="old_string_not_found"):
        edits.edit(aid, "file.txt", "xyz", "123", db=db)


def test_edit_ambiguous_match(repo_with_gitagent):
    repo, db, aid = _setup_agent(repo_with_gitagent)

    edits.write(aid, "file.txt", "aaa bbb aaa", db=db)
    with pytest.raises(GitAgentError, match="ambiguous_match"):
        edits.edit(aid, "file.txt", "aaa", "xxx", db=db)


def test_edit_replace_all(repo_with_gitagent):
    repo, db, aid = _setup_agent(repo_with_gitagent)

    edits.write(aid, "file.txt", "aaa bbb aaa", db=db)
    edits.edit(aid, "file.txt", "aaa", "xxx", replace_all=True, db=db)

    r = edits.read(aid, "file.txt", db=db)
    assert r["content"] == "xxx bbb xxx"


def test_delete_file(repo_with_gitagent):
    repo, db, aid = _setup_agent(repo_with_gitagent)

    edits.write(aid, "to_delete.txt", "bye", db=db)
    wt = Path(repo / ".gitagent" / "worktree")
    assert (wt / "to_delete.txt").exists()

    edits.delete_file(aid, "to_delete.txt", db=db)
    assert not (wt / "to_delete.txt").exists()


def test_path_escape_rejected(repo_with_gitagent):
    repo, db, aid = _setup_agent(repo_with_gitagent)

    with pytest.raises(GitAgentError, match="escapes worktree"):
        edits.write(aid, "../../etc/passwd", "pwned", db=db)


def test_path_prefix_escape_rejected(repo_with_gitagent):
    repo, db, aid = _setup_agent(repo_with_gitagent)
    with pytest.raises(GitAgentError, match="escapes worktree"):
        edits.write(aid, "../worktree-escape/file.txt", "pwned", db=db)


def test_stale_edit_rejected(repo_with_gitagent):
    repo, db, aid = _setup_agent(repo_with_gitagent)
    edits.write(aid, "stale.txt", "before", db=db)
    original = edits.read(aid, "stale.txt", db=db)
    edits.write(aid, "stale.txt", "changed", db=db)
    with pytest.raises(GitAgentError, match="STALE_WRITE"):
        edits.edit(
            aid,
            "stale.txt",
            "changed",
            "new",
            expected_sha256=original["sha256"],
            db=db,
        )


def test_atomic_write_no_half_state(repo_with_gitagent):
    repo, db, aid = _setup_agent(repo_with_gitagent)

    edits.write(aid, "atomic.txt", "version1", db=db)
    edits.write(aid, "atomic.txt", "version2", db=db)

    r = edits.read(aid, "atomic.txt", db=db)
    assert r["content"] == "version2"
    # No .tmp files left behind
    wt = Path(repo / ".gitagent" / "worktree")
    tmp_files = list(wt.glob(".atomic.txt.*.tmp"))
    assert tmp_files == []


def test_list_edits(repo_with_gitagent):
    repo, db, aid = _setup_agent(repo_with_gitagent)

    edits.write(aid, "a.py", "x", db=db)
    edits.write(aid, "b.py", "y", db=db)

    all_edits = edits.list_edits(db=db)
    assert len(all_edits) == 2

    filtered = edits.list_edits(file="a.py", db=db)
    assert len(filtered) == 1
    assert filtered[0]["file"] == "a.py"
