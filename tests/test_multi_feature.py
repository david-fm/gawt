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


@pytest.fixture
def repo_with_two_features(tmp_path: Path) -> Path:
    """A fresh repo with two feature branches and a clean `main`.

    `ga/feature-a` and `ga/feature-b` both branch from the same initial commit.
    """
    r = tmp_path / "repo"
    r.mkdir()
    _git(["init", "-q", "-b", "main"], r)
    _git(["config", "user.email", "t@t.t"], r)
    _git(["config", "user.name", "tester"], r)
    _git(["config", "commit.gpgsign", "false"], r)
    (r / "README.md").write_text("# repo\n", encoding="utf-8")
    _git(["add", "-A"], r)
    _git(["commit", "-qm", "initial"], r)

    session.init(r)

    _git(["checkout", "-q", "-b", "ga/feature-a"], r)
    session.start(r)
    agents.spawn(r, agent_id="a_a")
    p_a = store.paths(r, "feature-a")
    wt_a = Path(store.load_agent(p_a, "a_a").worktree)
    _edit(wt_a / "a.txt", "from feature a\n")
    p_obj_a = proposals.propose(r, agent_id="a_a", title="alpha")
    review.accept(r, proposal_id=p_obj_a.id)
    review.integrate(r)
    finalize.finalize(r, message="feat(a): alpha")

    _git(["checkout", "-q", "-b", "ga/feature-b"], r)
    session.start(r)
    agents.spawn(r, agent_id="b_b")
    p_b = store.paths(r, "feature-b")
    wt_b = Path(store.load_agent(p_b, "b_b").worktree)
    _edit(wt_b / "b.txt", "from feature b\n")
    p_obj_b = proposals.propose(r, agent_id="b_b", title="beta")
    review.accept(r, proposal_id=p_obj_b.id)
    review.integrate(r)
    finalize.finalize(r, message="feat(b): beta")

    return r


def test_two_features_each_hold_their_own_commit(repo_with_two_features: Path) -> None:
    r = repo_with_two_features
    # currently on ga/feature-b with the last commit being "feat(b): beta"
    assert gitwrap.current_branch(r) == "ga/feature-b"
    assert _git(["log", "-1", "--pretty=%s"], r).strip() == "feat(b): beta"
    assert (r / "b.txt").read_text() == "from feature b\n"

    _git(["checkout", "-q", "ga/feature-a"], r)
    assert _git(["log", "-1", "--pretty=%s"], r).strip() == "feat(a): alpha"
    assert (r / "a.txt").read_text() == "from feature a\n"
    assert not (r / "b.txt").exists()

    _git(["checkout", "-q", "main"], r)
    # main is untouched: only the initial commit
    assert _git(["log", "--pretty=%H"], r).strip().count("\n") == 0
    assert not (r / "a.txt").exists()
    assert not (r / "b.txt").exists()


def test_superagent_merges_features_to_main_with_git(repo_with_two_features: Path) -> None:
    """The end-to-end story: superagent finishes, then merges each feature
    branch into main with normal git. Each feature contributes its own commit.
    """
    r = repo_with_two_features
    _git(["checkout", "-q", "main"], r)
    _git(["merge", "--squash", "ga/feature-a"], r)
    _git(["commit", "-qm", "feat(a): alpha"], r)
    _git(["merge", "--squash", "ga/feature-b"], r)
    _git(["commit", "-qm", "feat(b): beta"], r)

    # main now has 3 commits: initial + a + b
    subjects = _git(["log", "--pretty=%s"], r).splitlines()
    assert subjects == [
        "feat(b): beta",
        "feat(a): alpha",
        "initial",
    ]
    assert (r / "a.txt").read_text() == "from feature a\n"
    assert (r / "b.txt").read_text() == "from feature b\n"


def test_features_summary_lists_both(repo_with_two_features: Path) -> None:
    r = repo_with_two_features
    items = session.features_summary(r)
    keys = {it["key"] for it in items}
    assert keys == {"feature-a", "feature-b"}
    # After finalize+teardown, sessions are gone, but feature dirs remain
    for it in items:
        assert it["session"] is None
        assert it["proposals"] == 0
        assert it["agents"] == 0


def test_each_feature_starts_fresh_session(repo_with_two_features: Path) -> None:
    """After finalize, switching back to a feature branch and running `start`
    opens a brand new session (the audit log is shared)."""
    r = repo_with_two_features
    _git(["checkout", "-q", "ga/feature-a"], r)
    s = session.start(r)
    assert s.id.startswith("s_")
    # session.json exists under the feature dir
    p = store.paths(r, "feature-a")
    assert store.load_session(p) is not None
    # log is shared/global
    log_events = [e["event"] for e in store.read_log(p)]
    assert log_events.count("start") >= 3  # initial two, plus this one


def test_start_on_main_fails_cleanly(repo_with_two_features: Path) -> None:
    r = repo_with_two_features
    _git(["checkout", "-q", "main"], r)
    with pytest.raises(GitAgentError, match="feature branch"):
        session.start(r)


def test_no_crosstalk_between_features(repo_with_two_features: Path) -> None:
    """Proposals/agents of one feature are invisible to another."""
    r = repo_with_two_features
    # On main, can't operate
    _git(["checkout", "-q", "ga/feature-a"], r)
    s1 = session.start(r)
    p1 = store.paths(r, "feature-a")
    agents.spawn(r, agent_id="new_a")
    wt = Path(store.load_agent(p1, "new_a").worktree)
    _edit(wt / "extra.txt", "extra\n")
    proposals.propose(r, agent_id="new_a", title="extra")

    _git(["checkout", "-q", "ga/feature-b"], r)
    s2 = session.start(r)
    p2 = store.paths(r, "feature-b")
    assert s2.id != s1.id
    assert store.agent_ids(p2) == []
    assert store.proposal_ids(p2) == []
