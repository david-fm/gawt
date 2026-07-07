from __future__ import annotations

from pathlib import Path

import pytest

from gitagent import agents, gitwrap, proposals, review, store
from gitagent.errors import GitAgentError


def _edit(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _rev(repo: Path, ref: str) -> str:
    return gitwrap.run(["rev-parse", ref], cwd=repo).strip()


def test_spawn_creates_isolated_worktree(started: Path) -> None:
    a = agents.spawn(started, agent_id="a_backend", role="impl")
    assert a.id == "a_backend"
    assert a.branch == f"agent/a_backend/{store.load_session(started).id}"
    assert Path(a.worktree).is_dir()
    assert (Path(a.worktree) / "README.md").exists()
    assert agents.list_agents(started)[0]["id"] == "a_backend"


def test_spawn_duplicate_fails(started: Path) -> None:
    agents.spawn(started, agent_id="a1")
    with pytest.raises(GitAgentError):
        agents.spawn(started, agent_id="a1")


def test_propose_captures_patch_and_manifest(started: Path) -> None:
    agents.spawn(started, agent_id="a1")
    wt = Path(store.load_agent(started, "a1").worktree)
    _edit(wt / "src" / "hello.py", "print('hi')\n")
    p = proposals.propose(started, agent_id="a1", title="Add hello", confidence=0.8)
    assert p.agent_id == "a1"
    assert "src/hello.py" in p.files
    patch = proposals.read_patch(started, proposal_id=p.id)
    assert "+++ b/src/hello.py" in patch
    assert store.load_review(started, p.id).state.value == "pending"


def test_propose_with_no_changes_fails(started: Path) -> None:
    agents.spawn(started, agent_id="a_empty")
    with pytest.raises(GitAgentError):
        proposals.propose(started, agent_id="a_empty", title="nothing")


def test_accept_marks_but_does_not_apply(started: Path) -> None:
    agents.spawn(started, agent_id="a1")
    wt = Path(store.load_agent(started, "a1").worktree)
    _edit(wt / "src" / "mod.py", "x = 1\n")
    p = proposals.propose(started, agent_id="a1", title="mod")
    session = store.load_session(started)

    sha_before = _rev(started, session.integration_branch)

    review.accept(started, proposal_id=p.id)

    r = store.load_review(started, p.id)
    assert r.state.value == "accepted"
    assert r.integrated is False  # Option B: accept does NOT apply
    assert _rev(started, session.integration_branch) == sha_before  # branch unchanged
    int_wt = Path(session.integration_worktree)
    assert not (int_wt / "src" / "mod.py").exists()


def test_integrate_applies_accepted(started: Path) -> None:
    agents.spawn(started, agent_id="a1")
    wt = Path(store.load_agent(started, "a1").worktree)
    _edit(wt / "src" / "mod.py", "x = 1\n")
    p = proposals.propose(started, agent_id="a1", title="mod")
    session = store.load_session(started)

    sha_before = _rev(started, session.integration_branch)
    review.accept(started, proposal_id=p.id)
    assert _rev(started, session.integration_branch) == sha_before  # still not applied

    summary = review.integrate(started)
    assert p.id in summary["applied"]
    assert summary["conflicted"] == []

    r = store.load_review(started, p.id)
    assert r.state.value == "integrated"
    assert r.integrated is True
    assert _rev(started, session.integration_branch) != sha_before


def test_integrate_is_idempotent(started: Path) -> None:
    agents.spawn(started, agent_id="a1")
    wt = Path(store.load_agent(started, "a1").worktree)
    _edit(wt / "src" / "mod.py", "x = 1\n")
    p = proposals.propose(started, agent_id="a1", title="mod")
    review.accept(started, proposal_id=p.id)
    review.integrate(started)
    s1 = review.integrate(started)  # second run
    assert s1["applied"] == []
    assert s1["skipped"] == [p.id]


def test_integrate_skips_pending_rejected_revise(started: Path) -> None:
    agents.spawn(started, agent_id="a1")
    wt = Path(store.load_agent(started, "a1").worktree)
    _edit(wt / "src" / "a.py", "a\n")
    _edit(wt / "src" / "b.py", "b\n")
    _edit(wt / "src" / "c.py", "c\n")
    pa = proposals.propose(started, agent_id="a1", title="a")
    pb = proposals.propose(started, agent_id="a1", title="b")
    pc = proposals.propose(started, agent_id="a1", title="c")

    # leave pa pending, reject pb, revise pc, then integrate
    review.reject(started, proposal_id=pb.id, reason="no")
    review.revise(started, proposal_id=pc.id, feedback="more")

    summary = review.integrate(started)
    assert summary["applied"] == []
    assert summary["conflicted"] == []
    assert summary["skipped"] == []

    # only an accepted one gets applied
    review.accept(started, proposal_id=pa.id)
    s2 = review.integrate(started)
    assert s2["applied"] == [pa.id]


def test_reject_does_not_apply(started: Path) -> None:
    agents.spawn(started, agent_id="a1")
    wt = Path(store.load_agent(started, "a1").worktree)
    _edit(wt / "src" / "mod.py", "x = 1\n")
    p = proposals.propose(started, agent_id="a1", title="mod")
    review.reject(started, proposal_id=p.id, reason="bad")
    r = store.load_review(started, p.id)
    assert r.state.value == "rejected"
    assert r.integrated is False
    int_wt = Path(store.load_session(started).integration_worktree)
    assert not (int_wt / "src" / "mod.py").exists()
    # even after integrate, rejected is skipped
    s = review.integrate(started)
    assert p.id not in s["applied"]


def test_revise_reopens_iteration(started: Path) -> None:
    agents.spawn(started, agent_id="a1")
    wt = Path(store.load_agent(started, "a1").worktree)
    _edit(wt / "src" / "mod.py", "x = 1\n")
    p = proposals.propose(started, agent_id="a1", title="mod")
    review.revise(started, proposal_id=p.id, feedback="add edge cases")
    r = store.load_review(started, p.id)
    assert r.state.value == "revise"
    assert r.feedback == "add edge cases"
    s = review.integrate(started)
    assert p.id not in s["applied"]


def test_accept_rejected_fails(started: Path) -> None:
    agents.spawn(started, agent_id="a1")
    wt = Path(store.load_agent(started, "a1").worktree)
    _edit(wt / "src" / "mod.py", "x = 1\n")
    p = proposals.propose(started, agent_id="a1", title="mod")
    review.reject(started, proposal_id=p.id)
    with pytest.raises(GitAgentError):
        review.accept(started, proposal_id=p.id)


def test_reject_already_integrated_fails(started: Path) -> None:
    agents.spawn(started, agent_id="a1")
    wt = Path(store.load_agent(started, "a1").worktree)
    _edit(wt / "src" / "mod.py", "x = 1\n")
    p = proposals.propose(started, agent_id="a1", title="mod")
    review.accept(started, proposal_id=p.id)
    review.integrate(started)
    with pytest.raises(GitAgentError):
        review.reject(started, proposal_id=p.id)


def test_revise_already_integrated_fails(started: Path) -> None:
    agents.spawn(started, agent_id="a1")
    wt = Path(store.load_agent(started, "a1").worktree)
    _edit(wt / "src" / "mod.py", "x = 1\n")
    p = proposals.propose(started, agent_id="a1", title="mod")
    review.accept(started, proposal_id=p.id)
    review.integrate(started)
    with pytest.raises(GitAgentError):
        review.revise(started, proposal_id=p.id, feedback="too late")
