"""Tests for session.py — multi-session, shared worktree, target per worktree."""
from __future__ import annotations

from pathlib import Path

import pytest

from gitagent import session
from gitagent.errors import GitAgentError


def _open_sessions(db) -> list[dict]:
    return [dict(r) for r in db.fetchall("SELECT * FROM session WHERE state='open'")]


def test_start_and_get_session(repo_with_gitagent):
    repo, db = repo_with_gitagent
    result = session.start_session("test-feature", db=db)
    assert "session_id" in result
    assert result["base_sha"]
    assert result["worktree"] == str(repo / ".gitagent" / "worktree")

    s = session.get_session(result["session_id"], db=db)
    assert s is not None
    assert s["feature"] == "test-feature"
    assert s["state"] == "open"


def test_two_sessions_share_worktree(repo_with_gitagent):
    repo, db = repo_with_gitagent
    a = session.start_session("first", db=db)
    b = session.start_session("second", db=db)

    assert a["worktree"] == b["worktree"]
    assert a["session_id"] != b["session_id"]
    assert len(_open_sessions(db)) == 2


def test_later_session_ignores_new_target(repo_with_gitagent):
    repo, db = repo_with_gitagent
    a = session.start_session("first", target_branch="main", db=db)
    b = session.start_session("second", target_branch="other", db=db)
    # The worktree-level target (from the first session) wins.
    assert b["target_branch"] == a["target_branch"]


def test_abort_last_session_removes_worktree(repo_with_gitagent):
    repo, db = repo_with_gitagent
    a = session.start_session("solo", db=db)
    wt = Path(a["worktree"])
    assert wt.exists()

    session.abort_session(a["session_id"], db=db)
    assert not wt.exists()
    assert session.get_session(a["session_id"], db=db)["state"] == "aborted"


def test_abort_keeps_worktree_with_other_open(repo_with_gitagent):
    repo, db = repo_with_gitagent
    a = session.start_session("a", db=db)
    b = session.start_session("b", db=db)
    wt = Path(a["worktree"])
    assert wt.exists()

    session.abort_session(a["session_id"], db=db)
    # Another session is still open -> worktree survives.
    assert wt.exists()
    assert session.get_session(b["session_id"], db=db)["state"] == "open"


def test_list_sessions(repo_with_gitagent):
    repo, db = repo_with_gitagent
    session.start_session("one", db=db)
    session.start_session("two", db=db)
    lst = session.list_sessions(db=db)
    assert len(lst) == 2


def test_abort_requires_existing_open(repo_with_gitagent):
    repo, db = repo_with_gitagent
    with pytest.raises(GitAgentError):
        session.abort_session("s_nope", db=db)