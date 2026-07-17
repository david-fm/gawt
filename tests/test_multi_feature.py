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
def repo_with_two_finalized_features(tmp_path: Path) -> Path:
    """A repo where two features were started from main and finalized onto main.

    In the new model, finalize lands directly on main — no ga/ branches survive.
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

    # Feature A: started from main via --feature flag (no git checkout)
    s_a = session.start(r, feature_name="feature-a")
    agents.spawn(r, agent_id="a_a", feature="feature-a")
    p_a = store.paths_for_feature(r, "feature-a")
    wt_a = Path(store.load_agent(p_a, "a_a").worktree)
    _edit(wt_a / "a.txt", "from feature a\n")
    p_obj_a = proposals.propose(r, agent_id="a_a", title="alpha", feature="feature-a")
    review.accept(r, proposal_id=p_obj_a.id, feature="feature-a")
    finalize.finalize(r, message="feat(a): alpha", feature="feature-a")

    # Feature B: started from main via --feature flag (no git checkout)
    s_b = session.start(r, feature_name="feature-b")
    agents.spawn(r, agent_id="b_b", feature="feature-b")
    p_b = store.paths_for_feature(r, "feature-b")
    wt_b = Path(store.load_agent(p_b, "b_b").worktree)
    _edit(wt_b / "b.txt", "from feature b\n")
    p_obj_b = proposals.propose(r, agent_id="b_b", title="beta", feature="feature-b")
    review.accept(r, proposal_id=p_obj_b.id, feature="feature-b")
    finalize.finalize(r, message="feat(b): beta", feature="feature-b")

    return r


def test_finalize_lands_on_main_directly(repo_with_two_finalized_features: Path) -> None:
    """After finalize, both features' changes are on main."""
    r = repo_with_two_finalized_features
    _git(["checkout", "-q", "main"], r)
    subjects = _git(["log", "--pretty=%s"], r).splitlines()
    assert "feat(a): alpha" in subjects
    assert "feat(b): beta" in subjects
    assert "initial" in subjects
    assert (r / "a.txt").read_text() == "from feature a\n"
    assert (r / "b.txt").read_text() == "from feature b\n"


def test_feature_branches_deleted_after_finalize(repo_with_two_finalized_features: Path) -> None:
    """ga/<feature> branches are deleted after finalize (unless --keep-feature-branch)."""
    r = repo_with_two_finalized_features
    assert not gitwrap.branch_exists("ga/feature-a", cwd=r)
    assert not gitwrap.branch_exists("ga/feature-b", cwd=r)


def test_feature_dirs_cleaned_after_finalize(repo_with_two_finalized_features: Path) -> None:
    """.gitagent/features/<key>/ is removed after finalize."""
    r = repo_with_two_finalized_features
    items = session.features_summary(r)
    assert items == []


def test_audit_log_preserved_after_finalize(repo_with_two_finalized_features: Path) -> None:
    """The global audit log survives finalize teardown."""
    r = repo_with_two_finalized_features
    p = store.paths_for_feature(r, "nonexistent")
    log_events = [e["event"] for e in store.read_log(p)]
    assert "start" in log_events
    assert "finalize" in log_events


def test_start_on_main_without_feature_flag_fails(repo_with_two_finalized_features: Path) -> None:
    r = repo_with_two_finalized_features
    _git(["checkout", "-q", "main"], r)
    with pytest.raises(GitAgentError, match="Could not determine"):
        session.start(r)


def test_start_on_main_with_feature_flag_works(tmp_path: Path) -> None:
    """New model: start from main using --feature, no checkout needed."""
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

    _git(["checkout", "-q", "main"], r)
    s = session.start(r, feature_name="my-feature")
    assert s.feature == "my-feature"
    assert s.branch == "ga/my-feature"
    assert s.target_branch == "main"
    # Feature branch was created
    assert gitwrap.branch_exists("ga/my-feature", cwd=r)
    # But the working tree is still on main (start checked out ga/my-feature
    # internally for the session, but that's the internal branch)
    # Actually in our new model, start does checkout ga/<feature>
    # so we are now on ga/my-feature
    assert gitwrap.current_branch(r) == "ga/my-feature"
    # Clean up
    session.abort(r, feature_name="my-feature")


def test_crosstalk_isolation(tmp_path: Path) -> None:
    """Proposals/agents of one feature are invisible to another."""
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

    s1 = session.start(r, feature_name="feature-a")
    agents.spawn(r, agent_id="new_a", feature="feature-a")
    p1 = store.paths_for_feature(r, "feature-a")
    wt = Path(store.load_agent(p1, "new_a").worktree)
    _edit(wt / "extra.txt", "extra\n")
    proposals.propose(r, agent_id="new_a", title="extra", feature="feature-a")

    s2 = session.start(r, feature_name="feature-b")
    p2 = store.paths_for_feature(r, "feature-b")
    assert s2.id != s1.id
    assert store.agent_ids(p2) == []
    assert store.proposal_ids(p2) == []


def test_finalize_with_keep_feature_branch(tmp_path: Path) -> None:
    """--keep-feature-branch preserves the ga/<feature> branch after finalize."""
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

    s = session.start(r, feature_name="my-feat")
    agents.spawn(r, agent_id="agent1", feature="my-feat")
    p = store.paths_for_feature(r, "my-feat")
    wt = Path(store.load_agent(p, "agent1").worktree)
    _edit(wt / "data.txt", "data\n")
    proposals.propose(r, agent_id="agent1", title="add data", feature="my-feat")
    review.accept(r, proposal_id=store.proposal_ids(p)[0], feature="my-feat")
    finalize.finalize(
        r, message="feat: data", feature="my-feat", keep_feature_branch=True,
    )

    # Branch preserved
    assert gitwrap.branch_exists("ga/my-feat", cwd=r)
    # But .gitagent/features/<key>/ is still cleaned up
    items = session.features_summary(r)
    assert items == []
