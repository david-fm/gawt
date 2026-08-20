"""Tests for replay.py — reconstruct a file at a snapshot boundary."""
from __future__ import annotations

import pytest

from gitagent import agents, edits, replay, session
from gitagent.errors import GitAgentError


def _setup(repo_with_gitagent):
    repo, db = repo_with_gitagent
    session.start_session("replay-test", db=db)
    aid = agents.register_agent("dev", db=db)["agent_id"]
    return repo, db, aid


def _reconstruct(db, repo, file, boundary):
    return replay.reconstruct(file, boundary, db=db, target_ref="main", repo=repo)


def test_replay_write_edits_in_order(repo_with_gitagent):
    repo, db, aid = _setup(repo_with_gitagent)
    edits.write(aid, "f.txt", "hello", db=db)
    edits.edit(aid, "f.txt", "hello", "hello world", db=db)
    edits.edit(aid, "f.txt", "world", "mundo", db=db)

    content = _reconstruct(db, repo, "f.txt", 10**6)
    assert content == "hello mundo"


def test_replay_respects_boundary(repo_with_gitagent):
    repo, db, aid = _setup(repo_with_gitagent)
    edits.write(aid, "f.txt", "one", db=db)
    edits.edit(aid, "f.txt", "one", "two", db=db)
    edits.edit(aid, "f.txt", "two", "three", db=db)

    # The first two rows only.
    max_id = db.fetchone("SELECT MAX(id) AS m FROM edits")["m"]
    boundary = max_id - 1
    content = _reconstruct(db, repo, "f.txt", boundary)
    assert content == "two"


def test_replay_replace_all(repo_with_gitagent):
    repo, db, aid = _setup(repo_with_gitagent)
    edits.write(aid, "g.txt", "aa aa", db=db)
    edits.edit(aid, "g.txt", "aa", "x", replace_all=True, db=db)
    assert _reconstruct(db, repo, "g.txt", 10**6) == "x x"


def test_replay_delete(repo_with_gitagent):
    repo, db, aid = _setup(repo_with_gitagent)
    edits.write(aid, "h.txt", "content", db=db)
    edits.delete_file(aid, "h.txt", db=db)
    assert _reconstruct(db, repo, "h.txt", 10**6) is None


def test_replay_mismatch_raises(repo_with_gitagent):
    repo, db, aid = _setup(repo_with_gitagent)
    edits.write(aid, "m.txt", "abc", db=db)
    # Simulate out-of-band corruption: an edit row whose old_string does not
    # exist in the reconstructed content.
    sid = db.fetchone("SELECT session_id FROM edits WHERE file = 'm.txt'")["session_id"]
    db.execute(
        """INSERT INTO edits
           (agent_id, session_id, file, op, old_string, new_string,
            intent_id, replace_all, ts)
           VALUES (?, ?, 'm.txt', 'edit', 'zzz', 'yyy', NULL, 0, ?)""",
        (aid, sid, "2026-01-01T00:00:00"),
    )
    db.commit()
    with pytest.raises(GitAgentError, match="REPLAY_MISMATCH"):
        _reconstruct(db, repo, "m.txt", 10**6)