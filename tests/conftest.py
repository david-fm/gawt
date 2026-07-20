from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(["init", "-q", "-b", "main"], r)
    _git(["config", "user.email", "t@t.t"], r)
    _git(["config", "user.name", "tester"], r)
    _git(["config", "commit.gpgsign", "false"], r)
    (r / "README.md").write_text("# repo\n", encoding="utf-8")
    _git(["add", "-A"], r)
    _git(["commit", "-qm", "initial"], r)
    return r


@pytest.fixture
def feature_branch(repo: Path) -> Path:
    """A repo ready for gitagent, staying on `main` (branchless model)."""
    return repo


@pytest.fixture
def started(feature_branch: Path) -> Path:
    from gitagent import session

    session.init(feature_branch)
    session.start(feature_branch, feature_name="test-feature")
    return feature_branch


def write_and_commit(path: Path, content: str, msg: str, cwd: Path) -> None:
    path.write_text(content, encoding="utf-8")
    _git(["add", "-A"], cwd)
    _git(["commit", "-qm", msg], cwd)
