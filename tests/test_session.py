from __future__ import annotations

from pathlib import Path

import pytest

from gitagent import gitwrap, session, store
from gitagent.errors import GitAgentError


def test_init_creates_dirs_and_gitignore(repo: Path) -> None:
    session.init(repo)
    assert store.initialized(repo)
    for sub in ("agents", "proposals", "integration", "locks"):
        assert (repo / ".gitagent" / sub).is_dir()
    gi = (repo / ".gitignore").read_text()
    assert ".gitagent/" in gi


def test_init_is_idempotent_error(repo: Path) -> None:
    session.init(repo)
    with pytest.raises(GitAgentError):
        session.init(repo)


def test_start_creates_session_and_integration_worktree(repo: Path) -> None:
    session.init(repo)
    s = session.start(repo, feature="auth-rl")
    assert s.feature == "auth-rl"
    assert s.base_sha == gitwrap.current_sha(repo)
    assert s.base_branch == "main"
    assert gitwrap.branch_exists(s.integration_branch, repo)
    assert Path(s.integration_worktree).is_dir()
    assert store.load_session(repo) is not None


def test_start_requires_clean_branch_state(repo: Path) -> None:
    session.init(repo)
    session.start(repo, feature="a")
    with pytest.raises(GitAgentError):
        session.start(repo, feature="b")


def test_status_snapshot_empty_session(repo: Path) -> None:
    session.init(repo)
    snap = session.status_snapshot(repo)
    assert snap["session"] is None


def test_abort_resets_state_but_keeps_log(repo: Path) -> None:
    from gitagent import agents

    session.init(repo)
    session.start(repo, feature="x")
    agents.spawn(repo, agent_id="a1")
    assert store.agent_ids(repo) == ["a1"]

    session.abort(repo)

    assert store.load_session(repo) is None
    assert store.agent_ids(repo) == []
    assert not gitwrap.branch_exists("gitagent/integration/s_x", repo)
    assert store.read_log(repo)  # log retained
    # re-initialised empty structure still present
    assert (repo / ".gitagent" / "agents").is_dir()


def test_log_records_events(repo: Path) -> None:
    session.init(repo)
    session.start(repo, feature="logged")
    events = [e["event"] for e in session.log_entries(repo)]
    assert "init" in events
    assert "start" in events
