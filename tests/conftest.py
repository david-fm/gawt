"""Shared fixtures for gitagent v0.5.0 tests."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gitagent.db import Database, reset_db


@pytest.fixture()
def tmp_repo(tmp_path: Path):
    """Create a temporary git repo with an initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(repo), check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(repo), check=True, capture_output=True,
    )
    # Initial commit so HEAD exists
    (repo / "README.md").write_text("# test\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=str(repo), check=True, capture_output=True,
    )
    return repo


@pytest.fixture()
def tmp_db(tmp_path: Path):
    """Create a fresh in-memory-like temp DB (on disk for testability)."""
    reset_db()
    db_path = tmp_path / "test_state.db"
    db = Database(db_path)
    yield db
    db.close()
    reset_db()


@pytest.fixture()
def repo_with_gitagent(tmp_repo: Path, tmp_db: Database):
    """A temp repo + .gitagent dir + the tmp_db wired in.

    Patches get_db to return the test db, and sets up the repo structure
    that session.py expects.
    """
    import gitagent.agents as agents_mod
    import gitagent.db as db_mod
    import gitagent.edits as edits_mod
    import gitagent.inbox as inbox_mod
    import gitagent.intents as intents_mod
    import gitagent.session as session_mod

    # Patch get_db to return our test db
    db_mod.get_db = lambda path=None: tmp_db
    session_mod.get_db = lambda path=None: tmp_db
    agents_mod.get_db = lambda path=None: tmp_db
    edits_mod.get_db = lambda path=None: tmp_db
    inbox_mod.get_db = lambda path=None: tmp_db
    intents_mod.get_db = lambda path=None: tmp_db

    # Patch repo_root to return our tmp_repo
    import gitagent.gitwrap as gw
    _orig_repo_root = gw.repo_root
    gw.repo_root = lambda cwd=None: tmp_repo

    # Create .gitagent dir
    (tmp_repo / ".gitagent").mkdir(exist_ok=True)

    yield tmp_repo, tmp_db

    # Restore
    gw.repo_root = _orig_repo_root
    db_mod.get_db = db_mod.get_db  # restore original
    session_mod.get_db = session_mod.get_db
    agents_mod.get_db = agents_mod.get_db
    edits_mod.get_db = edits_mod.get_db
    inbox_mod.get_db = inbox_mod.get_db
    intents_mod.get_db = intents_mod.get_db
