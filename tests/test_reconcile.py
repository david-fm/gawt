"""Tests for crash reconciliation — disk changes with no edit log rows."""
from __future__ import annotations

from pathlib import Path

from gitagent import agents, session, snapshot


def test_reconcile_inserts_adjusted_row(repo_with_gitagent):
    repo, db = repo_with_gitagent
    s = session.start_session("crash", db=db)
    agents.register_agent("dev", s["session_id"], db=db)

    # Simulate a crash mid-write: file changed on disk with NO edit log row.
    wt = Path(s["worktree"])
    (wt / "orphan.txt").write_text("wrote-out-of-band\n")

    before = db.fetchone("SELECT COUNT(*) AS c FROM edits WHERE file = 'orphan.txt'")["c"]
    assert before == 0

    st = snapshot.snapshot_status(s["session_id"], db=db)
    assert st["pending_files_count"] == 1

    # A synthetic 'adjusted' row was inserted so the pheromone stays gapless.
    row = db.fetchone(
        "SELECT * FROM edits WHERE file = 'orphan.txt' LIMIT 1"
    )
    assert row is not None
    assert row["op"] == "adjusted"
    assert row["agent_id"] == "<unknown>"
    assert row["intent_id"] is None


def test_reconcile_is_idempotent(repo_with_gitagent):
    repo, db = repo_with_gitagent
    s = session.start_session("crash2", db=db)
    agents.register_agent("dev", s["session_id"], db=db)

    wt = Path(s["worktree"])
    (wt / "orphan2.txt").write_text("stuff\n")

    snapshot.snapshot_status(s["session_id"], db=db)
    snapshot.snapshot_status(s["session_id"], db=db)

    rows = db.fetchall("SELECT * FROM edits WHERE file = 'orphan2.txt'")
    assert len(rows) == 1


def test_adjusted_row_does_not_break_replay(repo_with_gitagent):
    repo, db = repo_with_gitagent
    s = session.start_session("crash3", db=db)
    agents.register_agent("dev", s["session_id"], db=db)

    wt = Path(s["worktree"])
    (wt / "adj.txt").write_text("seen-by-snapshot\n")

    # Reconciliation, then a snapshot with a boundary that includes the
    # synthetic row must replay cleanly (adjusted rows are no-ops).
    snapshot.snapshot_status(s["session_id"], db=db)
    res = snapshot.snapshot_session(
        s["session_id"], "adjusted tools", files=["adj.txt"], db=db
    )
    assert res["status"] == "snapshotted"