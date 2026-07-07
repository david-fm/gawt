from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from . import gitwrap, store
from .errors import GitAgentError
from .models import ProposalState, Session, SessionState

GITIGNORE_ENTRY = ".gitagent/"


def init(repo: Path | None = None) -> None:
    repo = gitwrap.resolve(repo)
    if store.initialized(repo):
        raise GitAgentError("gitagent is already initialized in this repository.")
    gitwrap.repo_root(repo)
    store.ensure_dirs(repo)
    _ensure_gitignored(repo)
    store.log_event(repo, {"event": "init"})


def _ensure_gitignored(repo: Path) -> None:
    gi = repo / ".gitignore"
    if gi.exists():
        lines = gi.read_text(encoding="utf-8").splitlines()
        if GITIGNORE_ENTRY in lines:
            return
        gi.write_text(gi.read_text(encoding="utf-8").rstrip() + "\n" + GITIGNORE_ENTRY + "\n")
    else:
        gi.write_text(GITIGNORE_ENTRY + "\n", encoding="utf-8")


def start(repo: Path | None = None, *, feature: str) -> Session:
    repo = gitwrap.resolve(repo)
    store.require_init(repo)
    if store.load_session(repo) is not None:
        raise GitAgentError(
            "A session is already active. Run `gitagent abort` before starting a new one."
        )
    base_sha = gitwrap.current_sha(repo)
    base_branch = gitwrap.current_branch(repo)
    if base_branch is None:
        raise GitAgentError("HEAD is detached. Check out a branch before starting a session.")

    sid = "s_" + secrets.token_hex(4)
    integration_branch = f"gitagent/integration/{sid}"
    integration_worktree = repo / store.GITAGENT_DIR / "integration" / "worktree"

    gitwrap.worktree_add(integration_worktree, integration_branch, base_sha, cwd=repo)

    session = Session(
        id=sid,
        feature=feature,
        base_sha=base_sha,
        base_branch=base_branch,
        integration_branch=integration_branch,
        integration_worktree=str(integration_worktree),
        state=SessionState.OPEN,
        created_at=store.now(),
        updated_at=store.now(),
    )
    store.save_session(repo, session)
    store.log_event(
        repo,
        {
            "event": "start",
            "session": sid,
            "feature": feature,
            "base_sha": base_sha,
            "base_branch": base_branch,
        },
    )
    return session


def status_snapshot(repo: Path | None = None) -> dict[str, Any]:
    repo = gitwrap.resolve(repo)
    store.require_init(repo)
    session = store.load_session(repo)
    if session is None:
        return {"initialized": True, "session": None}

    agents: list[dict[str, Any]] = []
    for aid in store.agent_ids(repo):
        try:
            a = store.load_agent(repo, aid)
        except GitAgentError:
            continue
        agents.append(a.to_dict())

    proposals: list[dict[str, Any]] = []
    for pid in store.proposal_ids(repo):
        try:
            p = store.load_proposal(repo, pid)
            r = store.load_review(repo, pid)
        except GitAgentError:
            continue
        proposals.append({"manifest": p.to_dict(), "review": r.to_dict()})

    integrated = sum(
        1
        for pid in store.proposal_ids(repo)
        if store.load_review(repo, pid).state == ProposalState.INTEGRATED
    )
    return {
        "initialized": True,
        "session": session.to_dict(),
        "agents": agents,
        "proposals": proposals,
        "integration": {
            "branch": session.integration_branch,
            "worktree": session.integration_worktree,
            "base_sha": session.base_sha,
            "integrated_count": integrated,
        },
    }


def log_entries(repo: Path | None = None) -> list[dict[str, Any]]:
    repo = gitwrap.resolve(repo)
    store.require_init(repo)
    return store.read_log(repo)


def abort(repo: Path | None = None) -> None:
    repo = gitwrap.resolve(repo)
    store.require_init(repo)
    session = store.require_session(repo)
    store.log_event(repo, {"event": "abort", "session": session.id})
    store.teardown(repo, session, keep_log=True)
