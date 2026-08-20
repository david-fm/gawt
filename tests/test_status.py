"""Tests for snapshot_status — whole-worktree status + pheromone + progress."""
from __future__ import annotations

from gitagent import agents, edits, session, snapshot


def test_status_lists_changed_files(repo_with_gitagent):
    repo, db = repo_with_gitagent
    s = session.start_session("status", db=db)
    aid = agents.register_agent("dev", s["session_id"], db=db)["agent_id"]
    edits.write(aid, "alpha.py", "a", db=db)
    edits.write(aid, "beta.py", "b", db=db)

    st = snapshot.snapshot_status(s["session_id"], db=db)
    assert st["target_branch"] == "main"
    assert st["base_sha"]
    assert st["pending_files_count"] == 2
    assert st["warning"]
    files = {f["file"]: f for f in st["files"]}
    assert set(files) == {"alpha.py", "beta.py"}
    assert files["alpha.py"]["status"] == "added"
    assert "diff" in files["alpha.py"]
    assert files["alpha.py"]["edits"][0]["op"] == "write"


def test_status_after_snapshot_shows_remaining_only(repo_with_gitagent):
    repo, db = repo_with_gitagent
    s = session.start_session("status2", db=db)
    aid = agents.register_agent("dev", s["session_id"], db=db)["agent_id"]
    edits.write(aid, "done.py", "done", db=db)
    edits.write(aid, "pending.py", "waiting", db=db)

    snapshot.snapshot_session(s["session_id"], "partial", files=["done.py"], db=db)
    st = snapshot.snapshot_status(s["session_id"], db=db)
    files = {f["file"] for f in st["files"]}
    assert files == {"pending.py"}

    # done.py progress advanced, pending.py kept its slot with last_edit_id 0.
    prog = {
        p["file"]: p["snapshot_progress"]
        for p in snapshot.snapshot_status(s["session_id"], db=db)["files"]
    }
    assert "pending.py" in prog