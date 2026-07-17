from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gitagent import gitwrap, session, store
from gitagent.errors import GitAgentError


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def test_init_creates_dirs_and_gitignore(repo: Path) -> None:
    session.init(repo)
    assert store.initialized(repo)
    for sub in ("features",):
        assert (repo / ".gitagent" / sub).is_dir()
    gi = (repo / ".gitignore").read_text()
    assert ".gitagent/" in gi


def test_init_is_idempotent_error(repo: Path) -> None:
    session.init(repo)
    with pytest.raises(GitAgentError):
        session.init(repo)


def test_start_creates_session_and_integration_worktree(feature_branch: Path) -> None:
    session.init(feature_branch)
    s = session.start(feature_branch)
    assert s.feature == "test-feature"
    assert s.feature_key == "test-feature"
    assert s.branch == "ga/test-feature"
    assert s.base_sha == gitwrap.current_sha(feature_branch)
    assert gitwrap.branch_exists(s.integration_branch, feature_branch)
    assert Path(s.integration_worktree).is_dir()
    assert store.load_session(store.paths(feature_branch, "test-feature")) is not None


def test_start_requires_feature_branch(repo: Path) -> None:
    """Starting on 'main' (or any non-`ga/...` branch) must fail without --feature."""
    session.init(repo)
    with pytest.raises(GitAgentError, match="Could not determine"):
        session.start(repo)


def test_start_refuses_second_session_on_same_branch(started: Path) -> None:
    with pytest.raises(GitAgentError, match="already active"):
        session.start(started)


def test_status_snapshot_empty_session(feature_branch: Path) -> None:
    session.init(feature_branch)
    snap = session.status_snapshot(feature_branch)
    assert snap["session"] is None
    assert snap["branch"] == "ga/test-feature"


def test_abort_resets_state_but_keeps_log(feature_branch: Path) -> None:
    from gitagent import agents

    session.init(feature_branch)
    session.start(feature_branch)
    agents.spawn(feature_branch, agent_id="a1")

    p = store.paths(feature_branch, "test-feature")
    assert store.agent_ids(p) == ["a1"]

    session.abort(feature_branch)

    assert store.load_session(p) is None
    assert store.agent_ids(p) == []
    s_log = next(e for e in store.read_log(p) if e.get("event") == "start")
    assert not gitwrap.branch_exists(
        s_log["session"] and f"gitagent/integration/test-feature/{s_log['session']}",
        feature_branch,
    )
    assert store.read_log(p)  # log retained
    # session dir still present, just empty
    assert (p.feature / "agents").is_dir()


def test_log_records_events(started: Path) -> None:
    events = [e["event"] for e in session.log_entries(started)]
    assert "init" in events
    assert "start" in events


def test_two_features_coexist(repo: Path) -> None:
    """Two features finalized sequentially both land on main."""
    from gitagent import agents, proposals, review
    from gitagent import finalize as fin

    session.init(repo)

    # Feature A — started via --feature (no manual checkout needed)
    session.start(repo, feature_name="feature-a")
    agents.spawn(repo, agent_id="a_a", feature="feature-a")
    wt_a = Path(store.load_agent(store.paths_for_feature(repo, "feature-a"), "a_a").worktree)
    (wt_a / "a.txt").write_text("from feature a\n", encoding="utf-8")
    p_a = proposals.propose(repo, agent_id="a_a", title="alpha", feature="feature-a")
    review.accept(repo, proposal_id=p_a.id, feature="feature-a")
    sha_a = fin.finalize(repo, message="feat(a): alpha", feature="feature-a")
    assert gitwrap.current_branch(repo) == "main"

    # Feature B — in parallel
    session.start(repo, feature_name="feature-b")
    agents.spawn(repo, agent_id="b_b", feature="feature-b")
    wt_b = Path(store.load_agent(store.paths_for_feature(repo, "feature-b"), "b_b").worktree)
    (wt_b / "b.txt").write_text("from feature b\n", encoding="utf-8")
    p_b = proposals.propose(repo, agent_id="b_b", title="beta", feature="feature-b")
    review.accept(repo, proposal_id=p_b.id, feature="feature-b")
    sha_b = fin.finalize(repo, message="feat(b): beta", feature="feature-b")
    assert gitwrap.current_branch(repo) == "main"

    # Both commits are on main
    subjects = _git(["log", "--pretty=%s"], repo).splitlines()
    assert "feat(a): alpha" in subjects
    assert "feat(b): beta" in subjects
    assert (repo / "a.txt").read_text() == "from feature a\n"
    assert (repo / "b.txt").read_text() == "from feature b\n"

    # Feature directories cleaned up after finalize
    assert store.list_features(repo) == []


def test_list_features_command_shape(feature_branch: Path) -> None:
    session.init(feature_branch)
    items = session.features_summary(feature_branch)
    assert items == []  # nothing started yet

    session.start(feature_branch)
    items = session.features_summary(feature_branch)
    assert len(items) == 1
    assert items[0]["feature"] == "test-feature"
    assert items[0]["state"] == "open"
    assert items[0]["branch"] == "ga/test-feature"


def test_status_outside_feature_branch_returns_no_session(repo: Path) -> None:
    session.init(repo)
    snap = session.status_snapshot(repo)
    assert snap["initialized"] is True
    assert snap["session"] is None
    assert snap["branch"] == "main"
