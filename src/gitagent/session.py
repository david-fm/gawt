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


def start(
    repo: Path | None = None,
    *,
    feature_name: str | None = None,
    target_branch: str = "main",
) -> Session:
    """Open a session for a feature.

    gitagent is fully decoupled from the user's branches: no ``ga/<feature>``
    branch is created and the user's current checkout is never changed.  The
    feature is a logical key (a directory under ``.gitagent/features/<key>/``).
    All work happens in detached worktrees derived from *target_branch*.  The
    target branch is only touched by ``finalize``, which writes one commit.
    """
    repo = gitwrap.resolve(repo)
    store.require_init(repo)

    if feature_name is None:
        raise GitAgentError(
            "A feature name is required.  Pass --feature <name> explicitly."
        )

    feature_key = feature.slugify(feature_name)
    p = store.paths(repo, feature_key)
    store.ensure_dirs(p)
    if store.load_session(p) is not None:
        raise GitAgentError(
            f"A session is already active for this feature ({feature_key}). "
            f"Run `gitagent abort` to discard, or `gitagent status` to inspect."
        )

    # Base the session on the live target branch HEAD.  We never create a
    # branch for the feature; the base SHA anchors every detached worktree.
    base_sha = gitwrap.run(["rev-parse", target_branch], cwd=repo).strip()

    sid = "s_" + secrets.token_hex(4)
    integration_branch = f"gitagent/integration/{feature_key}/{sid}"
    integration_worktree = p.integration / "worktree"

    # Detached worktree on the target branch — no branch is created for it.
    gitwrap.worktree_add_detached(integration_worktree, target_branch, cwd=repo)

    session = Session(
        id=sid,
        feature=feature.name_from_branch(feature_name),
        feature_key=feature_key,
        base_sha=base_sha,
        integration_branch=integration_branch,
        integration_worktree=str(integration_worktree),
        target_branch=target_branch,
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
            "base_sha": base_sha,
            "target_branch": target_branch,
        },
    )
    return session


def _resolve_paths_for(
    repo: Path,
    feature_name: str | None = None,
) -> store.Paths:
    """Resolve Paths, using *feature_name* or falling back to the current branch."""
    if feature_name is not None:
        return store.paths_for_feature(repo, feature_name)
    return store.current_feature_paths(repo)


def status_snapshot(
    repo: Path | None = None,
    *,
    feature_name: str | None = None,
) -> dict[str, Any]:
    repo = gitwrap.resolve(repo)
    store.require_init(repo)

    if feature_name is not None:
        p = store.paths_for_feature(repo, feature_name)
        session = store.load_session(p)
        return _snapshot_for_paths(p, session)

    # No feature specified: show a summary of all features.
    return {
        "initialized": True,
        "session": None,
        "branch": gitwrap.current_branch(repo),
        "features": _features_summary(repo),
    }


def _snapshot_for_paths(p: store.Paths, session: Session | None) -> dict[str, Any]:
    if session is None:
        return {
            "initialized": True,
            "session": None,
            "branch": gitwrap.current_branch(p.root.parent),
            "features": _features_summary(p.root.parent),
        }

    agents: list[dict[str, Any]] = []
    for aid in store.agent_ids(p):
        try:
            a = store.load_agent(p, aid)
            agents.append(a.to_dict())
        except GitAgentError:
            continue

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
        "branch": gitwrap.current_branch(p.root.parent),
        "session": session.to_dict(),
        "agents": agents,
        "proposals": proposals,
        "integration": {
            "branch": session.integration_branch,
            "worktree": session.integration_worktree,
            "base_sha": session.base_sha,
            "integrated_count": integrated,
        },
        "features": _features_summary(p.root.parent),
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
            "target": s.target_branch,
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


def abort(repo: Path | None = None, *, feature_name: str | None = None) -> None:
    repo = gitwrap.resolve(repo)
    store.require_init(repo)
    p = _resolve_paths_for(repo, feature_name)
    session = store.require_session(p)
    store.log_event(p, {"event": "abort", "feature_key": p.feature.name, "session": session.id})
    store.teardown(p, session, keep_log=True)
