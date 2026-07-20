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


def _branches(repo: Path) -> list[str]:
    return [
        b.strip()
        for b in _git(["branch", "--format=%(refname:short)"], repo).splitlines()
    ]


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
    s = session.start(feature_branch, feature_name="test-feature")
    assert s.feature == "test-feature"
    assert s.feature_key == "test-feature"
    assert s.base_sha == gitwrap.current_sha(feature_branch)
    # No feature branch is created in the user's repo.
    assert "ga/test-feature" not in _branches(feature_branch)
    assert gitwrap.current_branch(feature_branch) == "main"
    # The integration worktree exists but is detached (no branch of its own).
    assert Path(s.integration_worktree).is_dir()
    assert store.load_session(store.paths(feature_branch, "test-feature")) is not None


def test_start_requires_feature_name(repo: Path) -> None:
    """Starting without --feature must fail (branchless model)."""
    session.init(repo)
    with pytest.raises(GitAgentError, match="feature name is required"):
        session.start(repo)


def test_start_refuses_second_session_on_same_feature(started: Path) -> None:
    with pytest.raises(GitAgentError, match="already active"):
        session.start(started, feature_name="test-feature")


def test_status_snapshot_empty_session(feature_branch: Path) -> None:
    session.init(feature_branch)
    snap = session.status_snapshot(feature_branch, feature_name="test-feature")
    assert snap["session"] is None
    assert snap["branch"] == "main"


def test_abort_resets_state_but_keeps_log(feature_branch: Path) -> None:
    from gitagent import agents

    session.init(feature_branch)
    session.start(feature_branch, feature_name="test-feature")
    agents.spawn(feature_branch, agent_id="a1", feature="test-feature")

    p = store.paths(feature_branch, "test-feature")
    assert store.agent_ids(p) == ["a1"]

    session.abort(feature_branch, feature_name="test-feature")

    assert store.load_session(p) is None
    assert store.agent_ids(p) == []
    # The user's branches are untouched: still only main.
    assert _branches(feature_branch) == ["main"]
    assert store.read_log(p)  # log retained
    # session dir still present, just empty
    assert (p.feature / "agents").is_dir()


def test_log_records_events(started: Path) -> None:
    events = [e["event"] for e in session.log_entries(started)]
    assert "init" in events
    assert "start" in events


def test_two_features_coexist(repo: Path) -> None:
    """Two features finalized sequentially both land on main; user stays on main."""
    from gitagent import agents, proposals, review
    from gitagent import finalize as fin

    session.init(repo)

    # Feature A
    session.start(repo, feature_name="feature-a")
    agents.spawn(repo, agent_id="a_a", feature="feature-a")
    wt_a = Path(store.load_agent(store.paths_for_feature(repo, "feature-a"), "a_a").worktree)
    (wt_a / "a.txt").write_text("from feature a\n", encoding="utf-8")
    p_a = proposals.propose(repo, agent_id="a_a", title="alpha", feature="feature-a")
    review.accept(repo, proposal_id=p_a.id, feature="feature-a")
    fin.finalize(repo, message="feat(a): alpha", feature="feature-a")
    assert gitwrap.current_branch(repo) == "main"

    # Feature B
    session.start(repo, feature_name="feature-b")
    agents.spawn(repo, agent_id="b_b", feature="feature-b")
    wt_b = Path(store.load_agent(store.paths_for_feature(repo, "feature-b"), "b_b").worktree)
    (wt_b / "b.txt").write_text("from feature b\n", encoding="utf-8")
    p_b = proposals.propose(repo, agent_id="b_b", title="beta", feature="feature-b")
    review.accept(repo, proposal_id=p_b.id, feature="feature-b")
    fin.finalize(repo, message="feat(b): beta", feature="feature-b")
    assert gitwrap.current_branch(repo) == "main"

    # Both commits are on main
    subjects = _git(["log", "--pretty=%s"], repo).splitlines()
    assert "feat(a): alpha" in subjects
    assert "feat(b): beta" in subjects
    assert (repo / "a.txt").read_text() == "from feature a\n"
    assert (repo / "b.txt").read_text() == "from feature b\n"

    # Feature directories cleaned up after finalize
    assert store.list_features(repo) == []


def test_no_branches_created_during_full_lifecycle(repo: Path) -> None:
    """gitagent must never create branches in the user's repo."""
    from gitagent import agents, proposals, review
    from gitagent import finalize as fin

    session.init(repo)
    session.start(repo, feature_name="solo")
    agents.spawn(repo, agent_id="x", feature="solo")
    wt = Path(store.load_agent(store.paths_for_feature(repo, "solo"), "x").worktree)
    (wt / "x.txt").write_text("hi\n", encoding="utf-8")
    pid = proposals.propose(repo, agent_id="x", title="t", feature="solo")
    review.accept(repo, proposal_id=pid.id, feature="solo")
    fin.finalize(repo, message="feat: solo", feature="solo")

    assert _branches(repo) == ["main"]


def test_list_features_command_shape(feature_branch: Path) -> None:
    session.init(feature_branch)
    items = session.features_summary(feature_branch)
    assert items == []  # nothing started yet

    session.start(feature_branch, feature_name="test-feature")
    items = session.features_summary(feature_branch)
    assert len(items) == 1
    assert items[0]["feature"] == "test-feature"
    assert items[0]["state"] == "open"


def test_status_outside_feature_branch_returns_no_session(repo: Path) -> None:
    session.init(repo)
    snap = session.status_snapshot(repo)
    assert snap["initialized"] is True
    assert snap["session"] is None
    assert snap["branch"] == "main"
