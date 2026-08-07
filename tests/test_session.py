"""Tests for session.py — start, finalize, abort, singleton invariant."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gitagent import session
from gitagent.errors import GitAgentError


def test_start_and_get_session(repo_with_gitagent):
    repo, db = repo_with_gitagent
    result = session.start_session("test-feature", db=db)
    assert "session_id" in result
    assert result["base_sha"]

    s = session.get_session(db)
    assert s is not None
    assert s["feature"] == "test-feature"
    assert s["state"] == "open"


def test_singleton_invariant(repo_with_gitagent):
    repo, db = repo_with_gitagent
    session.start_session("first", db=db)
    with pytest.raises(GitAgentError, match="already open"):
        session.start_session("second", db=db)


def test_abort_session(repo_with_gitagent):
    repo, db = repo_with_gitagent
    session.start_session("to-abort", db=db)
    session.abort_session(db=db)

    s = session.get_session(db)
    assert s is None

    # Check state in DB
    row = db.fetchone("SELECT state FROM session WHERE feature = 'to-abort'")
    assert row["state"] == "aborted"


def test_finalize_session(repo_with_gitagent):
    repo, db = repo_with_gitagent
    result = session.start_session("finalize-me", db=db)
    wt = Path(result["worktree"])

    # Make a change in the worktree
    (wt / "hello.txt").write_text("hello world\n")
    subprocess.run(["git", "add", "."], cwd=str(wt), check=True, capture_output=True)

    sha = session.finalize_session("feat: hello", db=db)
    assert sha
    assert len(sha) == 40  # full SHA

    # Session is finalized
    s = session.get_session(db)
    assert s is None

    # Worktree removed
    assert not wt.exists()

    # Commit exists on target branch
    log = subprocess.run(
        ["git", "log", "--oneline", "-1", "main"],
        cwd=str(repo), capture_output=True, text=True, check=True,
    )
    assert "hello" in log.stdout


def test_finalize_clean_worktree(repo_with_gitagent):
    repo, db = repo_with_gitagent
    session.start_session("clean", db=db)
    with pytest.raises(GitAgentError, match="clean"):
        session.finalize_session("msg", db=db)
