"""Tests for agents.py — register (with session routing), unregister, validate."""
from __future__ import annotations

import pytest

from gitagent import agents, session
from gitagent.errors import GitAgentError


def test_register_with_single_open_session(repo_with_gitagent):
    repo, db = repo_with_gitagent
    session.start_session("agent-test", db=db)
    # session_id optional with exactly one open session.
    result = agents.register_agent("implementer", db=db)
    assert result["agent_id"].startswith("a_")
    assert result["session_id"].startswith("s_")


def test_register_with_session_id(repo_with_gitagent):
    repo, db = repo_with_gitagent
    a = session.start_session("target", db=db)
    result = agents.register_agent("dev", a["session_id"], db=db)
    assert result["session_id"] == a["session_id"]


def test_register_requires_session_id_when_multiple_open(repo_with_gitagent):
    repo, db = repo_with_gitagent
    session.start_session("one", db=db)
    session.start_session("two", db=db)
    with pytest.raises(GitAgentError, match="Multiple open sessions"):
        agents.register_agent("ambig", db=db)


def test_register_with_session_id_routes(repo_with_gitagent):
    repo, db = repo_with_gitagent
    a = session.start_session("one", db=db)
    b = session.start_session("two", db=db)

    ra = agents.register_agent("a", a["session_id"], db=db)
    rb = agents.register_agent("b", b["session_id"], db=db)

    assert ra["session_id"] == a["session_id"]
    assert rb["session_id"] == b["session_id"]
    assert len(agents.list_agents(a["session_id"], db)) == 1
    assert len(agents.list_agents(b["session_id"], db)) == 1


def test_list_agents(repo_with_gitagent):
    repo, db = repo_with_gitagent
    session.start_session("list-test", db=db)
    agents.register_agent("one", db=db)
    agents.register_agent("two", db=db)
    lst = agents.list_agents(db=db)
    assert len(lst) == 2
    assert lst[0]["role"] == "one"


def test_unregister_agent(repo_with_gitagent):
    repo, db = repo_with_gitagent
    session.start_session("unreg", db=db)
    aid = agents.register_agent("temp", db=db)["agent_id"]
    agents.unregister_agent(aid, db=db)
    assert agents.list_agents(db=db)[0]["ended_at"] is not None


def test_validate_agent_active(repo_with_gitagent):
    repo, db = repo_with_gitagent
    session.start_session("validate", db=db)
    aid = agents.register_agent("active", db=db)["agent_id"]
    assert agents.validate_agent(aid, db=db)["id"] == aid


def test_validate_agent_ended(repo_with_gitagent):
    repo, db = repo_with_gitagent
    session.start_session("ended", db=db)
    aid = agents.register_agent("done", db=db)["agent_id"]
    agents.unregister_agent(aid, db=db)
    with pytest.raises(GitAgentError, match="ended"):
        agents.validate_agent(aid, db=db)


def test_validate_agent_not_found(repo_with_gitagent):
    repo, db = repo_with_gitagent
    session.start_session("nope", db=db)
    with pytest.raises(GitAgentError, match="not found"):
        agents.validate_agent("a_deadbeef", db=db)


def test_register_without_session(repo_with_gitagent):
    repo, db = repo_with_gitagent
    with pytest.raises(GitAgentError, match="No open session"):
        agents.register_agent("orphan", db=db)


@pytest.fixture()
def repo_with_db(repo_with_gitagent):
    return repo_with_gitagent