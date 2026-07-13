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
    """Starting on 'main' (or any non-`ga/...` branch) must fail."""
    session.init(repo)
    with pytest.raises(GitAgentError, match="feature branch"):
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
    """The headline feature: two feature branches hold their own sessions.

    feature A and feature B can be developed in parallel; switching branches
    reveals the right session.
    """
    from gitagent import agents, proposals, review
    from gitagent import finalize as fin

    session.init(repo)

    # Feature A
    _git(["checkout", "-q", "-b", "ga/feature-a"], repo)
    session.start(repo)
    agents.spawn(repo, agent_id="a_a")
    wt_a = Path(store.load_agent(store.paths(repo, "feature-a"), "a_a").worktree)
    (wt_a / "a.txt").write_text("from feature a\n", encoding="utf-8")
    p_a = proposals.propose(repo, agent_id="a_a", title="alpha")
    review.accept(repo, proposal_id=p_a.id)
    sha_a = fin.finalize(repo, message="feat(a): alpha")
    assert gitwrap.current_branch(repo) == "ga/feature-a"

    # Feature B, in parallel (no need to switch first if user works on B now)
    _git(["checkout", "-q", "-b", "ga/feature-b"], repo)
    session.start(repo)
    agents.spawn(repo, agent_id="b_b")
    wt_b = Path(store.load_agent(store.paths(repo, "feature-b"), "b_b").worktree)
    (wt_b / "b.txt").write_text("from feature b\n", encoding="utf-8")
    p_b = proposals.propose(repo, agent_id="b_b", title="beta")
    review.accept(repo, proposal_id=p_b.id)
    sha_b = fin.finalize(repo, message="feat(b): beta")
    assert gitwrap.current_branch(repo) == "ga/feature-b"

    # Both feature branches hold their own commit
    assert _git(["log", "-1", "--pretty=%H"], repo).strip() == sha_b
    _git(["checkout", "-q", "ga/feature-a"], repo)
    assert _git(["log", "-1", "--pretty=%H"], repo).strip() == sha_a

    # Both feature directories exist in .gitagent
    assert "feature-a" in store.list_features(repo)
    assert "feature-b" in store.list_features(repo)


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
