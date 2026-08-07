"""Tests for intents.py — start_intent, repurpose, get_current_intent."""
from __future__ import annotations

from gitagent import agents, intents, session


def test_start_intent(repo_with_gitagent):
    repo, db = repo_with_gitagent
    session.start_session("intent-test", db=db)
    result = agents.register_agent("dev", db=db)
    aid = result["agent_id"]

    r = intents.start_intent(aid, "implement auth middleware", db=db)
    assert "intent_id" in r


def test_get_current_intent(repo_with_gitagent):
    repo, db = repo_with_gitagent
    session.start_session("current", db=db)
    aid = agents.register_agent("dev", db=db)["agent_id"]

    intents.start_intent(aid, "first intent", db=db)
    current = intents.get_current_intent(aid, db=db)
    assert current["intent"] == "first intent"

    intents.repurpose(aid, "second intent", db=db)
    current = intents.get_current_intent(aid, db=db)
    assert current["intent"] == "second intent"


def test_no_intent(repo_with_gitagent):
    repo, db = repo_with_gitagent
    session.start_session("no-intent", db=db)
    aid = agents.register_agent("dev", db=db)["agent_id"]

    current = intents.get_current_intent(aid, db=db)
    assert current is None


def test_repurpose(repo_with_gitagent):
    repo, db = repo_with_gitagent
    session.start_session("repurpose", db=db)
    aid = agents.register_agent("dev", db=db)["agent_id"]

    intents.start_intent(aid, "build auth", db=db)
    r = intents.repurpose(aid, "build rate limiter", db=db)
    assert "intent_id" in r

    current = intents.get_current_intent(aid, db=db)
    assert current["intent"] == "build rate limiter"
