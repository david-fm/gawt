from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from . import gitwrap, store
from .errors import GitAgentError
from .models import Agent, AgentState


def _resolve(repo: Path | None, feature: str | None = None) -> tuple[Path, store.Paths]:
    repo = gitwrap.resolve(repo)
    if feature is not None:
        p = store.paths_for_feature(repo, feature)
    else:
        p = store.current_feature_paths(repo)
    return repo, p


def spawn(
    repo: Path | None = None,
    *,
    agent_id: str,
    base: str | None = None,
    role: str = "",
    feature: str | None = None,
) -> Agent:
    repo, p = _resolve(repo, feature)
    session = store.require_session(p)
    if session.state.value not in ("open", "integrating"):
        raise GitAgentError(f"Session is {session.state.value}; cannot spawn agents.")
    if not agent_id:
        raise GitAgentError("Agent id cannot be empty.")
    if agent_id in store.agent_ids(p):
        raise GitAgentError(f"Agent '{agent_id}' already exists.")

    base_ref = base or session.base_sha
    if not gitwrap.run_ok(["rev-parse", "--verify", f"{base_ref}^{{commit}}"], cwd=repo):
        raise GitAgentError(f"Base ref '{base_ref}' does not resolve to a commit.")
    base_sha = gitwrap.run(["rev-parse", base_ref], cwd=repo).strip()

    # Each agent gets its own DETACHED worktree derived from the session base.
    # No branch is created for the agent — gitagent never adds branches to the
    # user's repository, so agents can never pollute or switch the user's refs.
    worktree = p.agents / agent_id / "worktree"

    try:
        gitwrap.worktree_add_detached(worktree, base_ref, cwd=repo)
    except GitAgentError as exc:
        raise GitAgentError(f"Failed to create worktree for '{agent_id}'.\n{exc}") from exc

    agent = Agent(
        id=agent_id,
        role=role,
        base_sha=base_sha,
        base_ref=base_ref,
        worktree=str(worktree),
        state=AgentState.ACTIVE,
        created_at=store.now(),
    )
    store.save_agent(p, agent)
    store.log_event(
        p,
        {"event": "spawn", "agent": agent_id, "base": base_ref, "worktree": str(worktree)},
    )
    return agent


def kill(repo: Path | None = None, *, agent_id: str, feature: str | None = None) -> None:
    repo, p = _resolve(repo, feature)
    store.require_session(p)
    agent = store.load_agent(p, agent_id)
    with contextlib.suppress(GitAgentError):
        gitwrap.worktree_remove(agent.worktree, force=True, cwd=repo)
    gitwrap.worktree_prune(cwd=repo)
    agent.state = AgentState.KILLED
    store.save_agent(p, agent)
    store.log_event(p, {"event": "kill", "agent": agent_id})


def list_agents(repo: Path | None = None, *, feature: str | None = None) -> list[dict[str, Any]]:
    repo, p = _resolve(repo, feature)
    store.require_session(p)
    out: list[dict[str, Any]] = []
    for aid in store.agent_ids(p):
        try:
            out.append(store.load_agent(p, aid).to_dict())
        except GitAgentError:
            continue
    return out
