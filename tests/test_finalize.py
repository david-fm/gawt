from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gitagent import agents, finalize, gitwrap, proposals, review, session, store
from gitagent.errors import GitAgentError


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def _edit(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _commit_count(repo: Path) -> int:
    return len(_git(["log", "--pretty=%H"], repo).splitlines())


def test_finalize_produces_single_commit_on_main_and_resets(
    feature_branch: Path,
) -> None:
    """New model: finalize lands on main (the target), not the feature branch."""
    session.init(feature_branch)
    session.start(feature_branch)

    agents.spawn(feature_branch, agent_id="a_backend")
    agents.spawn(feature_branch, agent_id="a_tests")

    p = store.paths(feature_branch, "test-feature")
    be_wt = Path(store.load_agent(p, "a_backend").worktree)
    _edit(be_wt / "src" / "limiter.py", "RATE = 200\n")
    p_be = proposals.propose(
        feature_branch, agent_id="a_backend", title="limiter", confidence=0.85
    )

    te_wt = Path(store.load_agent(p, "a_tests").worktree)
    _edit(te_wt / "tests" / "test_limiter.py", "def test_ok(): assert True\n")
    p_te = proposals.propose(
        feature_branch, agent_id="a_tests", title="tests", confidence=0.9
    )

    before = _commit_count(feature_branch)
    review.accept(feature_branch, proposal_id=p_be.id)
    review.accept(feature_branch, proposal_id=p_te.id)
    review.integrate(feature_branch)

    sha = finalize.finalize(
        feature_branch, message="feat(auth): add rate limiting with tests"
    )

    # Exactly one new commit on main (the target)
    assert gitwrap.current_branch(feature_branch) == "main"
    assert _commit_count(feature_branch) == before + 1
    assert (
        _git(["log", "-1", "--pretty=%s"], feature_branch).strip()
        == "feat(auth): add rate limiting with tests"
    )
    assert gitwrap.current_sha(feature_branch) == sha

    # Files landed on main
    assert (feature_branch / "src" / "limiter.py").exists()
    assert (feature_branch / "tests" / "test_limiter.py").exists()

    # Session cleaned, feature branch removed
    assert store.load_session(p) is None
    assert not gitwrap.branch_exists("ga/test-feature", feature_branch)

    assert any(e.get("event") == "finalize" for e in store.read_log(p))


def test_finalize_landing_on_main_is_allowed(repo: Path) -> None:
    """In the new model, finalize lands on main by default."""
    session.init(repo)
    # main is a valid target — just need a feature branch for the session.
    _git(["checkout", "-q", "-b", "ga/test-feature"], repo)
    session.start(repo)
    agents.spawn(repo, agent_id="a1")
    p = store.paths(repo, "test-feature")
    wt = Path(store.load_agent(p, "a1").worktree)
    _edit(wt / "src", "x = 1\n")
    p_obj = proposals.propose(repo, agent_id="a1", title="x")
    review.accept(repo, proposal_id=p_obj.id)

    finalize.finalize(repo, message="feat: x")
    assert gitwrap.current_branch(repo) == "main"
    assert (repo / "src").exists()


def test_finalize_with_no_proposals_fails(feature_branch: Path) -> None:
    session.init(feature_branch)
    session.start(feature_branch)
    with pytest.raises(GitAgentError):
        finalize.finalize(feature_branch, message="nothing here")


def test_finalize_without_integrate_also_runs_it(feature_branch: Path) -> None:
    session.init(feature_branch)
    session.start(feature_branch)
    agents.spawn(feature_branch, agent_id="a1")
    p = store.paths(feature_branch, "test-feature")
    wt = Path(store.load_agent(p, "a1").worktree)
    _edit(wt / "src" / "x.py", "x = 1\n")
    p_obj = proposals.propose(feature_branch, agent_id="a1", title="x")
    review.accept(feature_branch, proposal_id=p_obj.id)

    sha = finalize.finalize(feature_branch, message="feat: x")
    assert (feature_branch / "src" / "x.py").exists()
    assert gitwrap.current_sha(feature_branch) == sha


def test_finalize_with_only_pending_fails(feature_branch: Path) -> None:
    session.init(feature_branch)
    session.start(feature_branch)
    with pytest.raises(GitAgentError, match="No integrated"):
        finalize.finalize(feature_branch, message="x")


def test_finalize_keep_feature_branch(feature_branch: Path) -> None:
    """--keep-feature-branch preserves the ga/<feature> branch after finalize."""
    session.init(feature_branch)
    session.start(feature_branch)
    agents.spawn(feature_branch, agent_id="a1")
    p = store.paths(feature_branch, "test-feature")
    wt = Path(store.load_agent(p, "a1").worktree)
    _edit(wt / "src" / "x.py", "x = 1\n")
    p_obj = proposals.propose(feature_branch, agent_id="a1", title="x")
    review.accept(feature_branch, proposal_id=p_obj.id)
    review.integrate(feature_branch)

    sha = finalize.finalize(
        feature_branch, message="feat: x", keep_feature_branch=True,
    )

    # We're on main after finalize; the commit is on main.
    assert gitwrap.current_branch(feature_branch) == "main"
    assert gitwrap.current_sha(feature_branch) == sha
    # Feature branch preserved
    assert gitwrap.branch_exists("ga/test-feature", feature_branch)
    # But .gitagent/features/<key>/ is cleaned up
    assert store.load_session(p) is None


def test_conflict_is_marked_at_integrate_time(feature_branch: Path) -> None:
    session.init(feature_branch)
    session.start(feature_branch)

    agents.spawn(feature_branch, agent_id="a1")
    agents.spawn(feature_branch, agent_id="a2")

    p = store.paths(feature_branch, "test-feature")
    wt1 = Path(store.load_agent(p, "a1").worktree)
    wt2 = Path(store.load_agent(p, "a2").worktree)
    _edit(wt1 / "shared.txt", "line = ALPHA\n")
    _edit(wt2 / "shared.txt", "line = BETA\n")

    p1 = proposals.propose(feature_branch, agent_id="a1", title="alpha")
    p2 = proposals.propose(feature_branch, agent_id="a2", title="beta")

    review.accept(feature_branch, proposal_id=p1.id)
    review.accept(feature_branch, proposal_id=p2.id)

    summary = review.integrate(feature_branch)
    assert p1.id in summary["applied"]
    assert p2.id in summary["conflicted"]

    r1 = store.load_review(p, p1.id)
    r2 = store.load_review(p, p2.id)
    assert r1.state.value == "integrated"
    assert r2.state.value == "conflict"
    assert store.load_session(p) is not None


def test_abort_after_partial_work_cleans_everything(feature_branch: Path) -> None:
    session.init(feature_branch)
    session.start(feature_branch)
    agents.spawn(feature_branch, agent_id="a1")
    p = store.paths(feature_branch, "test-feature")
    wt = Path(store.load_agent(p, "a1").worktree)
    _edit(wt / "src" / "x.py", "x = 1\n")
    proposals.propose(feature_branch, agent_id="a1", title="x")

    session.abort(feature_branch)

    assert store.load_session(p) is None
    assert _commit_count(feature_branch) == 1
    assert not (feature_branch / "src" / "x.py").exists()
