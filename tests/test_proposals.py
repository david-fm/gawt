from __future__ import annotations

from pathlib import Path

import pytest

from gitagent import agents, gitwrap, proposals, review, store
from gitagent.errors import GitAgentError


def _edit(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


FEATURE = "test-feature"


def _paths(started: Path) -> store.Paths:
    return store.paths(started, FEATURE)


def _int_sha(started: Path, session: object) -> str:
    return gitwrap.current_sha(Path(session.integration_worktree))  # type: ignore[attr-defined]


def test_spawn_creates_isolated_worktree(started: Path) -> None:
    a = agents.spawn(started, agent_id="a_backend", role="impl", feature=FEATURE)
    assert a.id == "a_backend"
    assert Path(a.worktree).is_dir()
    assert (Path(a.worktree) / "README.md").exists()
    assert agents.list_agents(started, feature=FEATURE)[0]["id"] == "a_backend"


def test_spawn_duplicate_fails(started: Path) -> None:
    agents.spawn(started, agent_id="a1", feature=FEATURE)
    with pytest.raises(GitAgentError):
        agents.spawn(started, agent_id="a1", feature=FEATURE)


def test_propose_captures_patch_and_manifest(started: Path) -> None:
    p = _paths(started)
    agents.spawn(started, agent_id="a1", feature=FEATURE)
    wt = Path(store.load_agent(p, "a1").worktree)
    _edit(wt / "src" / "hello.py", "print('hi')\n")
    p_obj = proposals.propose(
        started, agent_id="a1", title="Add hello", confidence=0.8, feature=FEATURE
    )
    assert p_obj.agent_id == "a1"
    assert "src/hello.py" in p_obj.files
    patch = proposals.read_patch(started, proposal_id=p_obj.id, feature=FEATURE)
    assert "+++ b/src/hello.py" in patch
    assert store.load_review(p, p_obj.id).state.value == "pending"


def test_propose_with_no_changes_fails(started: Path) -> None:
    agents.spawn(started, agent_id="a_empty", feature=FEATURE)
    with pytest.raises(GitAgentError):
        proposals.propose(started, agent_id="a_empty", title="nothing", feature=FEATURE)


def test_accept_marks_but_does_not_apply(started: Path) -> None:
    p = _paths(started)
    agents.spawn(started, agent_id="a1", feature=FEATURE)
    wt = Path(store.load_agent(p, "a1").worktree)
    _edit(wt / "src" / "mod.py", "x = 1\n")
    p_obj = proposals.propose(started, agent_id="a1", title="mod", feature=FEATURE)
    session = store.load_session(p)

    sha_before = _int_sha(started, session)

    review.accept(started, proposal_id=p_obj.id, feature=FEATURE)

    r = store.load_review(p, p_obj.id)
    assert r.state.value == "accepted"
    assert r.integrated is False
    assert _int_sha(started, store.load_session(p)) == sha_before
    int_wt = Path(session.integration_worktree)
    assert not (int_wt / "src" / "mod.py").exists()


def test_integrate_applies_accepted(started: Path) -> None:
    p = _paths(started)
    agents.spawn(started, agent_id="a1", feature=FEATURE)
    wt = Path(store.load_agent(p, "a1").worktree)
    _edit(wt / "src" / "mod.py", "x = 1\n")
    p_obj = proposals.propose(started, agent_id="a1", title="mod", feature=FEATURE)
    session = store.load_session(p)

    sha_before = _int_sha(started, session)
    review.accept(started, proposal_id=p_obj.id, feature=FEATURE)
    assert _int_sha(started, store.load_session(p)) == sha_before

    summary = review.integrate(started, feature=FEATURE)
    assert p_obj.id in summary["applied"]
    assert summary["conflicted"] == []

    r = store.load_review(p, p_obj.id)
    assert r.state.value == "integrated"
    assert r.integrated is True
    assert _int_sha(started, store.load_session(p)) != sha_before


def test_integrate_is_idempotent(started: Path) -> None:
    p = _paths(started)
    agents.spawn(started, agent_id="a1", feature=FEATURE)
    wt = Path(store.load_agent(p, "a1").worktree)
    _edit(wt / "src" / "mod.py", "x = 1\n")
    p_obj = proposals.propose(started, agent_id="a1", title="mod", feature=FEATURE)
    review.accept(started, proposal_id=p_obj.id, feature=FEATURE)
    review.integrate(started, feature=FEATURE)
    s1 = review.integrate(started, feature=FEATURE)
    assert s1["applied"] == []
    assert s1["skipped"] == [p_obj.id]


def test_integrate_skips_pending_rejected_revise(started: Path) -> None:
    p = _paths(started)
    agents.spawn(started, agent_id="a1", feature=FEATURE)
    wt = Path(store.load_agent(p, "a1").worktree)
    _edit(wt / "src" / "a.py", "a\n")
    _edit(wt / "src" / "b.py", "b\n")
    _edit(wt / "src" / "c.py", "c\n")
    pa = proposals.propose(started, agent_id="a1", title="a", feature=FEATURE)
    pb = proposals.propose(started, agent_id="a1", title="b", feature=FEATURE)
    pc = proposals.propose(started, agent_id="a1", title="c", feature=FEATURE)

    review.reject(started, proposal_id=pb.id, reason="no", feature=FEATURE)
    review.revise(started, proposal_id=pc.id, feedback="more", feature=FEATURE)

    summary = review.integrate(started, feature=FEATURE)
    assert summary["applied"] == []
    assert summary["conflicted"] == []
    assert summary["skipped"] == []

    review.accept(started, proposal_id=pa.id, feature=FEATURE)
    s2 = review.integrate(started, feature=FEATURE)
    assert s2["applied"] == [pa.id]


def test_reject_does_not_apply(started: Path) -> None:
    p = _paths(started)
    agents.spawn(started, agent_id="a1", feature=FEATURE)
    wt = Path(store.load_agent(p, "a1").worktree)
    _edit(wt / "src" / "mod.py", "x = 1\n")
    p_obj = proposals.propose(started, agent_id="a1", title="mod", feature=FEATURE)
    review.reject(started, proposal_id=p_obj.id, reason="bad", feature=FEATURE)
    r = store.load_review(p, p_obj.id)
    assert r.state.value == "rejected"
    assert r.integrated is False
    int_wt = Path(store.load_session(p).integration_worktree)
    assert not (int_wt / "src" / "mod.py").exists()
    s = review.integrate(started, feature=FEATURE)
    assert p_obj.id not in s["applied"]


def test_revise_reopens_iteration(started: Path) -> None:
    p = _paths(started)
    agents.spawn(started, agent_id="a1", feature=FEATURE)
    wt = Path(store.load_agent(p, "a1").worktree)
    _edit(wt / "src" / "mod.py", "x = 1\n")
    p_obj = proposals.propose(started, agent_id="a1", title="mod", feature=FEATURE)
    review.revise(started, proposal_id=p_obj.id, feedback="add edge cases", feature=FEATURE)
    r = store.load_review(p, p_obj.id)
    assert r.state.value == "revise"
    assert r.feedback == "add edge cases"
    s = review.integrate(started, feature=FEATURE)
    assert p_obj.id not in s["applied"]


def test_accept_rejected_fails(started: Path) -> None:
    p = _paths(started)
    agents.spawn(started, agent_id="a1", feature=FEATURE)
    wt = Path(store.load_agent(p, "a1").worktree)
    _edit(wt / "src" / "mod.py", "x = 1\n")
    p_obj = proposals.propose(started, agent_id="a1", title="mod", feature=FEATURE)
    review.reject(started, proposal_id=p_obj.id, feature=FEATURE)
    with pytest.raises(GitAgentError):
        review.accept(started, proposal_id=p_obj.id, feature=FEATURE)


def test_reject_already_integrated_fails(started: Path) -> None:
    p = _paths(started)
    agents.spawn(started, agent_id="a1", feature=FEATURE)
    wt = Path(store.load_agent(p, "a1").worktree)
    _edit(wt / "src" / "mod.py", "x = 1\n")
    p_obj = proposals.propose(started, agent_id="a1", title="mod", feature=FEATURE)
    review.accept(started, proposal_id=p_obj.id, feature=FEATURE)
    review.integrate(started, feature=FEATURE)
    with pytest.raises(GitAgentError):
        review.reject(started, proposal_id=p_obj.id, feature=FEATURE)


def test_revise_already_integrated_fails(started: Path) -> None:
    p = _paths(started)
    agents.spawn(started, agent_id="a1", feature=FEATURE)
    wt = Path(store.load_agent(p, "a1").worktree)
    _edit(wt / "src" / "mod.py", "x = 1\n")
    p_obj = proposals.propose(started, agent_id="a1", title="mod", feature=FEATURE)
    review.accept(started, proposal_id=p_obj.id, feature=FEATURE)
    review.integrate(started, feature=FEATURE)
    with pytest.raises(GitAgentError):
        review.revise(started, proposal_id=p_obj.id, feedback="too late", feature=FEATURE)
