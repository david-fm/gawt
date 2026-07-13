from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from . import feature, gitwrap, store
from .errors import GitAgentError
from .models import ProposalState, Session, SessionState

GITIGNORE_ENTRY = ".gitagent/"
GITIGNORE_FEATURES = ".gitagent/features/"


def init(repo: Path | None = None) -> None:
    repo = gitwrap.resolve(repo)
    if store.initialized(repo):
        raise GitAgentError("gitagent is already initialized in this repository.")
    gitwrap.repo_root(repo)
    root = repo / store.GITAGENT_DIR
    root.mkdir(parents=True, exist_ok=True)
    (root / store.FEATURES_DIR).mkdir(parents=True, exist_ok=True)
    _ensure_gitignored(repo)
    log_path = store.global_log_path(repo)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    store.log_event_at(log_path, {"event": "init"})


def _ensure_gitignored(repo: Path) -> None:
    gi = repo / ".gitignore"
    needed = [GITIGNORE_ENTRY]
    if gi.exists():
        lines = gi.read_text(encoding="utf-8").splitlines()
        with gi.open("a", encoding="utf-8") as fh:
            for entry in needed:
                if entry not in lines:
                    fh.write(entry + "\n")
    else:
        gi.write_text("\n".join(needed) + "\n", encoding="utf-8")


def start(repo: Path | None = None) -> Session:
    """Open a session anchored at HEAD on the current feature branch.

    The feature name is derived from the branch (`ga/<name>` -> `<name>`). The
    branch must already exist; the user creates it with
    `git checkout -b ga/<name>`. Two features in two branches run in parallel.
    """
    repo = gitwrap.resolve(repo)
    store.require_init(repo)

    branch = gitwrap.current_branch(repo)
    if branch is None:
        raise GitAgentError("HEAD is detached. Check out a feature branch first.")
    if not feature.is_feature_branch(branch):
        raise GitAgentError(
            f"Current branch '{branch}' is not a feature branch. "
            f"Create one with `git checkout -b {feature.FEATURE_PREFIX}<name>` first. "
            f"`gitagent` refuses to start on 'main' / 'master' / detached HEAD."
        )

    feature_key = feature.slugify(branch)
    p = store.paths(repo, feature_key)
    store.ensure_dirs(p)
    if store.load_session(p) is not None:
        raise GitAgentError(
            f"A session is already active for this feature ({branch}). "
            f"Run `gitagent abort` to discard, or `gitagent status` to inspect."
        )

    base_sha = gitwrap.current_sha(repo)
    base_branch = branch
    sid = "s_" + secrets.token_hex(4)
    integration_branch = f"gitagent/integration/{feature_key}/{sid}"
    integration_worktree = p.integration / "worktree"

    gitwrap.worktree_add(integration_worktree, integration_branch, base_sha, cwd=repo)

    session = Session(
        id=sid,
        feature=feature.name_from_branch(branch),
        feature_key=feature_key,
        branch=branch,
        base_sha=base_sha,
        base_branch=base_branch,
        integration_branch=integration_branch,
        integration_worktree=str(integration_worktree),
        state=SessionState.OPEN,
        created_at=store.now(),
        updated_at=store.now(),
    )
    store.save_session(p, session)
    store.log_event(
        p,
        {
            "event": "start",
            "feature_key": feature_key,
            "feature": session.feature,
            "session": sid,
            "branch": branch,
            "base_sha": base_sha,
        },
    )
    return session


def status_snapshot(repo: Path | None = None) -> dict[str, Any]:
    repo = gitwrap.resolve(repo)
    store.require_init(repo)
    try:
        p = store.current_feature_paths(repo)
    except GitAgentError:
        return {
            "initialized": True,
            "session": None,
            "branch": gitwrap.current_branch(repo),
            "features": _features_summary(repo),
        }

    session = store.load_session(p)
    if session is None:
        return {
            "initialized": True,
            "session": None,
            "branch": gitwrap.current_branch(repo),
            "features": _features_summary(repo),
        }

    agents: list[dict[str, Any]] = []
    for aid in store.agent_ids(p):
        try:
            a = store.load_agent(p, aid)
        except GitAgentError:
            continue
        agents.append(a.to_dict())

    proposals: list[dict[str, Any]] = []
    for pid in store.proposal_ids(p):
        try:
            prop = store.load_proposal(p, pid)
            rev = store.load_review(p, pid)
        except GitAgentError:
            continue
        proposals.append({"manifest": prop.to_dict(), "review": rev.to_dict()})

    integrated = sum(
        1
        for pid in store.proposal_ids(p)
        if store.load_review(p, pid).state == ProposalState.INTEGRATED
    )
    return {
        "initialized": True,
        "branch": gitwrap.current_branch(repo),
        "session": session.to_dict(),
        "agents": agents,
        "proposals": proposals,
        "integration": {
            "branch": session.integration_branch,
            "worktree": session.integration_worktree,
            "base_sha": session.base_sha,
            "integrated_count": integrated,
        },
        "features": _features_summary(repo),
    }


def _features_summary(repo: Path) -> list[dict[str, Any]]:
    return features_summary(repo)


def features_summary(repo: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in store.list_features(repo):
        p = store.paths(repo, key)
        s = store.load_session(p)
        if s is None:
            out.append({"key": key, "session": None, "proposals": 0, "agents": 0})
            continue
        out.append(
            {
                "key": key,
                "session": s.id,
                "feature": s.feature,
                "state": s.state.value,
                "branch": s.branch,
                "integration_branch": s.integration_branch,
                "proposals": len(store.proposal_ids(p)),
                "agents": len(store.agent_ids(p)),
            }
        )
    return out


def log_entries(repo: Path | None = None) -> list[dict[str, Any]]:
    repo = gitwrap.resolve(repo)
    store.require_init(repo)
    return store.read_log_at(store.global_log_path(repo))


def abort(repo: Path | None = None) -> None:
    repo = gitwrap.resolve(repo)
    store.require_init(repo)
    p = store.current_feature_paths(repo)
    session = store.require_session(p)
    store.log_event(p, {"event": "abort", "feature_key": p.feature.name, "session": session.id})
    store.teardown(p, session, keep_log=True)
