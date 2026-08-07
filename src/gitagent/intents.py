"""Semantic intent tracking: start_intent, repurpose, get_current_intent.

Intents annotate the edit log with *why* an agent is making changes. Each
agent has a single "current intent" at any time.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .db import Database, get_db
from .errors import GitAgentError
from .agents import validate_agent


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def start_intent(agent_id: str, intent: str, db: Database | None = None) -> dict:
    """Record the start of a new intent for an agent.

    Returns ``{intent_id}``.
    """
    db = db or get_db()
    validate_agent(agent_id, db)

    now = _now()
    cur = db.execute(
        """INSERT INTO intents (agent_id, kind, intent, ts)
           VALUES (?, 'start', ?, ?)""",
        (agent_id, intent, now),
    )
    db.commit()

    return {"intent_id": cur.lastrowid}


def repurpose(agent_id: str, intent: str, db: Database | None = None) -> dict:
    """Record a intent shift (repurpose) for an agent.

    Returns ``{intent_id}``.
    """
    db = db or get_db()
    validate_agent(agent_id, db)

    now = _now()
    cur = db.execute(
        """INSERT INTO intents (agent_id, kind, intent, ts)
           VALUES (?, 'repurpose', ?, ?)""",
        (agent_id, intent, now),
    )
    db.commit()

    return {"intent_id": cur.lastrowid}


def get_current_intent(agent_id: str, db: Database | None = None) -> dict | None:
    """Return the active intent for an agent, or None if none set."""
    db = db or get_db()
    row = db.fetchone(
        """SELECT id AS intent_id, intent
           FROM intents
           WHERE agent_id = ?
           ORDER BY id DESC
           LIMIT 1""",
        (agent_id,),
    )
    return dict(row) if row else None
