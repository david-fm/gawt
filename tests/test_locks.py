"""Tests for locks.py — acquire/release, TTL reclaim, idempotency, rejection."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from gitagent import locks


def test_acquire_and_release(tmp_db):
    r = locks.acquire("a.py", "agent-1", "s_x", db=tmp_db)
    assert r["status"] == "held"
    assert r["token"]

    r2 = locks.acquire("a.py", "agent-1", "s_x", db=tmp_db)
    assert r2["status"] == "held"
    assert r2["token"] == r["token"]

    locks.release("a.py", r["token"], db=tmp_db)
    r3 = locks.acquire("a.py", "agent-2", "s_y", db=tmp_db)
    assert r3["status"] == "held"


def test_foreign_lock_rejected(tmp_db):
    locks.acquire("a.py", "agent-1", "s_x", db=tmp_db)
    r = locks.acquire("a.py", "agent-2", "s_y", db=tmp_db)
    assert r["status"] == "held_elsewhere"
    assert r["blocked_by"]["holder_agent"] == "agent-1"


def test_ttl_reclaims_stale(tmp_db):
    locks.acquire("a.py", "agent-1", "s_x", db=tmp_db)
    old = (datetime.now(UTC) - timedelta(seconds=20)).isoformat()
    tmp_db.execute(
        "UPDATE locks SET acquired_at = ? WHERE file = 'a.py'", (old,)
    )
    tmp_db.commit()
    r = locks.acquire("a.py", "agent-2", "s_y", ttl_seconds=5, db=tmp_db)
    assert r["status"] == "held"
    assert r["token"]


def test_release_conditional_token(tmp_db):
    locks.acquire("a.py", "agent-1", "s_x", db=tmp_db)
    locks.release("a.py", "wrong-token", db=tmp_db)
    r = locks.acquire("a.py", "agent-2", "s_y", db=tmp_db)
    assert r["status"] == "held_elsewhere"