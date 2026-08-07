"""Tests for db.py — schema, migrations, indexes."""
from __future__ import annotations

from pathlib import Path

from gitagent.db import Database, CURRENT_VERSION


def test_schema_creation(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    # Check tables exist
    rows = db.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = {r["name"] for r in rows}
    assert "session" in tables
    assert "agents" in tables
    assert "intents" in tables
    assert "edits" in tables
    assert "inbox" in tables
    db.close()


def test_user_version(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    version = db.conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == CURRENT_VERSION
    db.close()


def test_idempotent_migration(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    db.close()
    # Reopen — should not fail
    db2 = Database(tmp_path / "test.db")
    version = db2.conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == CURRENT_VERSION
    db2.close()


def test_indexes_exist(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    rows = db.fetchall(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
    )
    names = {r["name"] for r in rows}
    assert "idx_edits_file" in names
    assert "idx_inbox_to" in names
    db.close()
