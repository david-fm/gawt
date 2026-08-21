"""Tests for edits.py — informed read (no diff), last-read validation, rejection."""
from __future__ import annotations

from pathlib import Path

import pytest

from gitagent import agents, edits, intents, locks, session
from gitagent.errors import GitAgentError


def _setup_agent(repo_with_gitagent):
    repo, db = repo_with_gitagent
    s = session.start_session("edit-test", db=db)
    aid = agents.register_agent("dev", db=db)["agent_id"]
    return repo, db, aid, s["session_id"]


def test_read_informed(repo_with_gitagent):
    repo, db, aid, sid = _setup_agent(repo_with_gitagent)
    edits.write(aid, "hello.txt", "hello world\n", db=db)

    r = edits.read(aid, "hello.txt", db=db)
    assert r["content"] == "hello world\n"
    assert len(r["sha256"]) == 64
    assert r["base_sha"]
    assert "warning" in r
    assert isinstance(r["edits"], list)
    assert r["edits"][0]["op"] == "write"
    # No fat diff payload in the read — it lives in snapshot_status only.
    assert "diff" not in r


def test_read_records_last_read(repo_with_gitagent):
    _, db, aid, _ = _setup_agent(repo_with_gitagent)
    edits.write(aid, "f.txt", "x", db=db)
    r = edits.read(aid, "f.txt", db=db)
    row = db.fetchone(
        "SELECT * FROM last_reads WHERE agent_id = ? AND file = 'f.txt'",
        (aid,),
    )
    assert row is not None
    assert row["sha256"] == r["sha256"]


def test_read_no_note_when_current(repo_with_gitagent):
    _, db, aid, _ = _setup_agent(repo_with_gitagent)
    edits.write(aid, "f.txt", "x", db=db)
    r = edits.read(aid, "f.txt", db=db)
    assert "note" not in r


def test_read_note_when_stale(repo_with_gitagent):
    repo, db, aid, sid = _setup_agent(repo_with_gitagent)
    aid2 = agents.register_agent("other", db=db)["agent_id"]

    edits.write(aid, "f.txt", "v1", db=db)
    edits.read(aid, "f.txt", db=db)  # agent records last_read = v1
    edits.read(aid2, "f.txt", db=db)  # the other agent also reads before writing
    edits.write(aid2, "f.txt", "v2", db=db)  # another agent changes it

    r = edits.read(aid, "f.txt", db=db)
    assert "note" in r  # your last read is no longer current
    assert r["content"] == "v2"


def test_edit_exact_match(repo_with_gitagent):
    _, db, aid, _ = _setup_agent(repo_with_gitagent)
    edits.write(aid, "src/app.py", "def hello():\n    pass\n", db=db)
    edits.edit(aid, "src/app.py", "pass", "return 42", db=db)
    assert "return 42" in edits.read(aid, "src/app.py", db=db)["content"]


def test_edit_replace_all(repo_with_gitagent):
    _, db, aid, _ = _setup_agent(repo_with_gitagent)
    edits.write(aid, "file.txt", "aaa bbb aaa", db=db)
    edits.edit(aid, "file.txt", "aaa", "xxx", replace_all=True, db=db)
    assert edits.read(aid, "file.txt", db=db)["content"] == "xxx bbb xxx"


def test_replace_all_recorded(repo_with_gitagent):
    _, db, aid, _ = _setup_agent(repo_with_gitagent)
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
    repo, db, aid, _ = _setup_agent(repo_with_gitagent)
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
    repo, db, aid, sid = _setup_agent(repo_with_gitagent)
    aid2 = agents.register_agent("b", db=db)["agent_id"]

    locks.acquire("locked.txt", aid, sid, db=db)
    resp = edits.write(aid2, "locked.txt", "should-not-apply", db=db)

    assert resp["status"] == "rejected"
    assert resp["blocked_by"]["agent_id"] == aid
    assert "read" in resp
    assert not (Path(repo / ".gitagent" / "worktree") / "locked.txt").exists()


def test_write_after_own_read_applies(repo_with_gitagent):
    _, db, aid, _ = _setup_agent(repo_with_gitagent)
    edits.write(aid, "f.txt", "v1", db=db)
    edits.read(aid, "f.txt", db=db)  # record own read
    resp = edits.write(aid, "f.txt", "v2", db=db)
    assert resp["ok"] is True


def test_write_never_read_existing_rejected(repo_with_gitagent):
    """Option A: writing a file you never read is stale (not silent clobber)."""
    _, db, aid, _ = _setup_agent(repo_with_gitagent)
    aid2 = agents.register_agent("other", db=db)["agent_id"]

    edits.write(aid, "f.txt", "v1", db=db)      # other agent owns it
    resp = edits.write(aid2, "f.txt", "v2", db=db)  # never read -> reject
    assert resp["status"] == "rejected"
    assert resp["reason"] == "STALE_WRITE"


def test_creator_can_rewrite_own_file(repo_with_gitagent):
    """A file's creator (its own last_reads row set on create) may rewrite it."""
    _, db, aid, _ = _setup_agent(repo_with_gitagent)
    edits.write(aid, "f.txt", "v1", db=db)   # sets last_reads for the creator
    edits.write(aid, "f.txt", "v2", db=db)
    assert edits.read(aid, "f.txt", db=db)["content"] == "v2"


def test_edit_never_read_existing_rejected(repo_with_gitagent):
    """Editing a file you never read is refused (must read first for old_string)."""
    _, db, aid, _ = _setup_agent(repo_with_gitagent)
    aid2 = agents.register_agent("other", db=db)["agent_id"]

    edits.write(aid, "f.txt", "abc", db=db)
    resp = edits.edit(aid2, "f.txt", "abc", "xyz", db=db)
    assert resp["status"] == "rejected"
    assert resp["reason"] == "STALE_WRITE"


def test_delete_never_read_existing_rejected(repo_with_gitagent):
    """Deleting a file you never read is refused too."""
    _, db, aid, _ = _setup_agent(repo_with_gitagent)
    aid2 = agents.register_agent("other", db=db)["agent_id"]

    edits.write(aid, "f.txt", "keep", db=db)
    resp = edits.delete_file(aid2, "f.txt", db=db)
    assert resp["status"] == "rejected"
    assert resp["reason"] == "STALE_WRITE"


def test_write_stale_after_other_writes(repo_with_gitagent):
    repo, db, aid, _ = _setup_agent(repo_with_gitagent)
    aid2 = agents.register_agent("other", db=db)["agent_id"]

    edits.write(aid, "f.txt", "v1", db=db)
    edits.read(aid, "f.txt", db=db)        # A bases its write on v1
    edits.read(aid2, "f.txt", db=db)       # B reads before writing
    edits.write(aid2, "f.txt", "v2", db=db)  # B changes it

    resp = edits.write(aid, "f.txt", "A-wins", db=db)
    assert resp["status"] == "rejected"
    assert resp["reason"] == "STALE_WRITE"
    assert edits.read(aid, "f.txt", db=db)["content"] == "v2"


def test_edit_stale_after_other_writes(repo_with_gitagent):
    _, db, aid, _ = _setup_agent(repo_with_gitagent)
    aid2 = agents.register_agent("other", db=db)["agent_id"]

    edits.write(aid, "f.txt", "v1", db=db)
    edits.read(aid, "f.txt", db=db)
    edits.read(aid2, "f.txt", db=db)
    edits.write(aid2, "f.txt", "v2", db=db)
    resp = edits.edit(aid, "f.txt", "hello", "world", db=db)
    assert resp["status"] == "rejected"
    assert resp["reason"] == "STALE_WRITE"


def test_delete_stale_after_other_writes(repo_with_gitagent):
    _, db, aid, _ = _setup_agent(repo_with_gitagent)
    aid2 = agents.register_agent("other", db=db)["agent_id"]

    edits.write(aid, "f.txt", "v1", db=db)
    edits.read(aid, "f.txt", db=db)
    edits.read(aid2, "f.txt", db=db)
    edits.write(aid2, "f.txt", "v2", db=db)
    resp = edits.delete_file(aid, "f.txt", db=db)
    assert resp["status"] == "rejected"
    assert resp["reason"] == "STALE_WRITE"


def test_read_edits_include_intent_and_role(repo_with_gitagent):
    _, db, aid, _ = _setup_agent(repo_with_gitagent)
    intents.start_intent(aid, "implement rate limiter", db=db)
    edits.write(aid, "lim.py", "x", db=db)
    r = edits.read(aid, "lim.py", db=db)
    e = r["edits"][0]
    assert e["op"] == "write"
    assert e["role"] == "dev"
    assert e["intent"] == "implement rate limiter"


def test_list_edits_limit(repo_with_gitagent):
    _, db, aid, _ = _setup_agent(repo_with_gitagent)
    edits.write(aid, "a.py", "x", db=db)
    edits.edit(aid, "a.py", "x", "y", db=db)
    assert len(edits.list_edits(db=db)) == 2
    assert len(edits.list_edits(limit=1, db=db)) == 1