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


def test_finalize_produces_single_commit_and_resets(repo: Path) -> None:
    session.init(repo)
    session.start(repo, feature="auth-rate-limiting")

    agents.spawn(repo, agent_id="a_backend")
    agents.spawn(repo, agent_id="a_tests")

    be_wt = Path(store.load_agent(repo, "a_backend").worktree)
    _edit(be_wt / "src" / "limiter.py", "RATE = 200\n")
    p_be = proposals.propose(repo, agent_id="a_backend", title="limiter", confidence=0.85)

    te_wt = Path(store.load_agent(repo, "a_tests").worktree)
    _edit(te_wt / "tests" / "test_limiter.py", "def test_ok(): assert True\n")
    p_te = proposals.propose(repo, agent_id="a_tests", title="tests", confidence=0.9)

    before = _commit_count(repo)
    review.accept(repo, proposal_id=p_be.id)
    review.accept(repo, proposal_id=p_te.id)
    review.integrate(repo)  # Option B: integrate applies

    sha = finalize.finalize(repo, message="feat(auth): add rate limiting with tests")

    assert _commit_count(repo) == before + 1
    assert (
        _git(["log", "-1", "--pretty=%s"], repo).strip()
        == "feat(auth): add rate limiting with tests"
    )
    assert gitwrap.current_sha(repo) == sha

    assert (repo / "src" / "limiter.py").exists()
    assert (repo / "tests" / "test_limiter.py").exists()

    assert store.load_session(repo) is None
    s_log = [e for e in store.read_log(repo) if e.get("event") == "start"][0]
    assert not gitwrap.branch_exists(f"gitagent/integration/{s_log['session']}", repo)
    assert not gitwrap.branch_exists(f"agent/a_backend/{s_log['session']}", repo)
    assert not gitwrap.branch_exists(f"agent/a_tests/{s_log['session']}", repo)

    assert any(e.get("event") == "finalize" for e in store.read_log(repo))


def test_finalize_with_no_proposals_fails(repo: Path) -> None:
    session.init(repo)
    session.start(repo, feature="empty")
    with pytest.raises(GitAgentError):
        finalize.finalize(repo, message="nothing here")


def test_finalize_without_integrate_also_runs_it(repo: Path) -> None:
    """finalize should call integrate internally so the user doesn't have to."""
    session.init(repo)
    session.start(repo, feature="auto-integrate")
    agents.spawn(repo, agent_id="a1")
    wt = Path(store.load_agent(repo, "a1").worktree)
    _edit(wt / "src" / "x.py", "x = 1\n")
    p = proposals.propose(repo, agent_id="a1", title="x")
    review.accept(repo, proposal_id=p.id)
    # NOTE: no explicit review.integrate() call

    sha = finalize.finalize(repo, message="feat: x")
    assert (repo / "src" / "x.py").exists()
    assert gitwrap.current_sha(repo) == sha
    # (proposal state is wiped by the default teardown; the file landing on the
    # real branch is proof enough that integrate ran during finalize)


def test_finalize_with_only_pending_fails(repo: Path) -> None:
    """accept marks, but if you skip integrate and try finalize, it should still work
    (finalize calls integrate). But if you have no accepted proposals at all, fail."""
    session.init(repo)
    session.start(repo, feature="nothing-accepted")
    with pytest.raises(GitAgentError, match="No integrated"):
        finalize.finalize(repo, message="x")


def test_finalize_no_reset_keeps_state(repo: Path) -> None:
    session.init(repo)
    session.start(repo, feature="keep")
    agents.spawn(repo, agent_id="a1")
    wt = Path(store.load_agent(repo, "a1").worktree)
    _edit(wt / "src" / "x.py", "x = 1\n")
    p = proposals.propose(repo, agent_id="a1", title="x")
    review.accept(repo, proposal_id=p.id)
    review.integrate(repo)

    sha = finalize.finalize(repo, message="feat: x", no_reset=True)

    assert gitwrap.current_sha(repo) == sha
    assert store.load_session(repo) is not None
    s = store.load_session(repo)
    assert gitwrap.branch_exists(s.integration_branch, repo)


def test_conflict_is_marked_at_integrate_time(repo: Path) -> None:
    session.init(repo)
    session.start(repo, feature="conflict")

    agents.spawn(repo, agent_id="a1")
    agents.spawn(repo, agent_id="a2")

    wt1 = Path(store.load_agent(repo, "a1").worktree)
    wt2 = Path(store.load_agent(repo, "a2").worktree)
    _edit(wt1 / "shared.txt", "line = ALPHA\n")
    _edit(wt2 / "shared.txt", "line = BETA\n")

    p1 = proposals.propose(repo, agent_id="a1", title="alpha")
    p2 = proposals.propose(repo, agent_id="a2", title="beta")

    # Option B: accept only marks; conflict is detected at integrate time
    review.accept(repo, proposal_id=p1.id)
    review.accept(repo, proposal_id=p2.id)

    summary = review.integrate(repo)
    assert p1.id in summary["applied"]
    assert p2.id in summary["conflicted"]

    r1 = store.load_review(repo, p1.id)
    r2 = store.load_review(repo, p2.id)
    assert r1.state.value == "integrated"
    assert r2.state.value == "conflict"
    # session is still alive; superagent can revise or abort
    assert store.load_session(repo) is not None


def test_abort_after_partial_work_cleans_everything(repo: Path) -> None:
    session.init(repo)
    session.start(repo, feature="partial")
    agents.spawn(repo, agent_id="a1")
    wt = Path(store.load_agent(repo, "a1").worktree)
    _edit(wt / "src" / "x.py", "x = 1\n")
    proposals.propose(repo, agent_id="a1", title="x")

    session.abort(repo)

    assert store.load_session(repo) is None
    assert _commit_count(repo) == 1
    assert not (repo / "src" / "x.py").exists()
