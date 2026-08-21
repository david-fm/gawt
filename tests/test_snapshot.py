"""Tests for snapshot.py — partial commits (point 4), status, reconciliation."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gitagent import agents, edits, session, snapshot
from gitagent.errors import GitAgentError


def _target_content(repo, file):
    out = subprocess.run(
        ["git", "show", f"main:{file}"],
        cwd=str(repo), capture_output=True, text=True,
    )
    if out.returncode != 0:
        return None
    return out.stdout


def test_snapshot_point4_scenario(repo_with_gitagent):
    """The plan's section 9 example: a.py/b.py across sessions A and B."""
    repo, db = repo_with_gitagent
    sA = session.start_session("S1", db=db)
    sB = session.start_session("S2", db=db)
    a1 = agents.register_agent("a1", sA["session_id"], db=db)["agent_id"]
    a2 = agents.register_agent("a2", sB["session_id"], db=db)["agent_id"]

    # S1 (a1): writes a.py (id=1) and b.py (id=2)
    edits.write(a1, "a.py", "A1", db=db)
    edits.write(a1, "b.py", "B1", db=db)
    # S2 (a2) edits files created by a1 — must read them first (protocol A).
    edits.read(a2, "a.py", db=db)
    edits.read(a2, "b.py", db=db)
    # S2 (a2): edits b.py (id=3) and a.py (id=4)
    edits.edit(a2, "b.py", "B1", "B2", db=db)
    edits.edit(a2, "a.py", "A1", "A2", db=db)

    # S1 snapshots only a.py at boundary 4.
    res = snapshot.snapshot_session(
        sA["session_id"], "a part", files=["a.py"], boundary_edit_id=4, db=db
    )
    assert res["status"] == "snapshotted"

    # a.py on target combines edit1 + edit4, skip...
    assert _target_content(repo, "a.py") == "A2"
    # b.py untouched on target.
    assert _target_content(repo, "b.py") is None

    # Progress: S1 a.py advanced to 4; S1 b.py did NOT advance.
    prog_a = db.fetchone(
        "SELECT last_edit_id FROM snapshot_progress WHERE session_id = ? AND file = 'a.py'",
        (sA["session_id"],),
    )
    assert prog_a["last_edit_id"] == 4
    prog_b = db.fetchone(
        "SELECT last_edit_id FROM snapshot_progress WHERE session_id = ? AND file = 'b.py'",
        (sA["session_id"],),
    )
    assert prog_b is None or prog_b["last_edit_id"] == 0

    # Next S1 snapshot of b.py picks up both edits and advances progress.
    snapshot.snapshot_session(sB["session_id"], "b part", files=["b.py"], db=db)
    assert _target_content(repo, "b.py") == "B2"
    prog_b2 = db.fetchone(
        "SELECT last_edit_id FROM snapshot_progress WHERE session_id = ? AND file = 'b.py'",
        (sB["session_id"],),
    )
    assert prog_b2["last_edit_id"] >= 3


def test_snapshot_no_files_commits_all(repo_with_gitagent):
    repo, db = repo_with_gitagent
    s = session.start_session("all", db=db)
    aid = agents.register_agent("dev", s["session_id"], db=db)["agent_id"]
    edits.write(aid, "one.py", "1", db=db)
    edits.write(aid, "two.py", "2", db=db)

    snapshot.snapshot_session(s["session_id"], "all files", db=db)
    assert _target_content(repo, "one.py") == "1"
    assert _target_content(repo, "two.py") == "2"


def test_nothing_to_snapshot_idempotent(repo_with_gitagent):
    repo, db = repo_with_gitagent
    s = session.start_session("again", db=db)
    aid = agents.register_agent("dev", s["session_id"], db=db)["agent_id"]
    edits.write(aid, "x.py", "x", db=db)

    snapshot.snapshot_session(s["session_id"], "first", db=db)
    # Second snapshot of the same scope has no net changes -> clean error.
    with pytest.raises(GitAgentError, match="Nothing to snapshot"):
        snapshot.snapshot_session(s["session_id"], "second", files=["x.py"], db=db)


def test_snapshot_keeps_live_worktree(repo_with_gitagent):
    repo, db = repo_with_gitagent
    s = session.start_session("alive", db=db)
    aid = agents.register_agent("dev", s["session_id"], db=db)["agent_id"]
    edits.write(aid, "keep.py", "keep", db=db)

    snapshot.snapshot_session(s["session_id"], "keep it", db=db)
    wt = Path(s["worktree"])
    assert wt.exists()
    assert (wt / "keep.py").exists()


def test_snapshot_lands_commit_on_target(repo_with_gitagent):
    repo, db = repo_with_gitagent
    s = session.start_session("land", db=db)
    aid = agents.register_agent("dev", s["session_id"], db=db)["agent_id"]
    edits.write(aid, "land.py", "landed", db=db)

    res = snapshot.snapshot_session(s["session_id"], "landing", db=db)
    sha = res["sha"]
    assert len(sha) == 40
    # main must point at the snapshot commit
    out = subprocess.run(
        ["git", "rev-parse", "main"], cwd=str(repo), capture_output=True, text=True,
    )
    assert out.stdout.strip() == sha


def test_snapshot_boundary_excludes_later_edits(repo_with_gitagent):
    repo, db = repo_with_gitagent
    s = session.start_session("bound", db=db)
    aid = agents.register_agent("dev", s["session_id"], db=db)["agent_id"]
    edits.write(aid, "f.txt", "v1", db=db)
    edits.edit(aid, "f.txt", "v1", "v2", db=db)
    max_before = db.fetchone("SELECT MAX(id) AS m FROM edits")["m"]

    edits.edit(aid, "f.txt", "v2", "v3", db=db)
    snapshot.snapshot_session(
        s["session_id"], "frontier", files=["f.txt"], boundary_edit_id=max_before, db=db
    )
    # The boundary stops before the last edit: target sees v2, not v3.
    assert _target_content(repo, "f.txt") == "v2"