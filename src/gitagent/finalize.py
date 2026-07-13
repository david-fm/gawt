from __future__ import annotations

from pathlib import Path

from . import gitwrap, review, store
from .errors import GitAgentError
from .models import ProposalState


def finalize(
    repo: Path | None = None,
    *,
    message: str,
    sign: bool = False,
    no_reset: bool = False,
) -> str:
    """Integrate accepted proposals and produce a single commit on the current branch.

    The current branch must be a feature branch (`ga/...`). The commit lands on
    that branch, NOT on `main` — the user merges the feature branch into `main`
    with normal git (PR or `git merge --no-ff`) when ready.
    """
    repo = gitwrap.resolve(repo)
    store.require_init(repo)
    p = store.current_feature_paths(repo)
    session = store.require_session(p)
    if session.state.value not in ("open", "integrating"):
        raise GitAgentError(f"Session is {session.state.value}; cannot finalize.")

    review.integrate(repo)

    integrated = [
        pid
        for pid in store.proposal_ids(p)
        if store.load_review(p, pid).state == ProposalState.INTEGRATED
    ]
    if not integrated:
        raise GitAgentError(
            "No integrated proposals to finalize. "
            "Run `gitagent accept <pid>` and then `gitagent integrate` first."
        )

    cur_branch = gitwrap.current_branch(repo)
    if cur_branch is None:
        raise GitAgentError("HEAD is detached; check out a branch before finalize.")

    try:
        gitwrap.merge_squash(session.integration_branch, cwd=repo)
    except GitAgentError as exc:
        gitwrap.abort_merge(cwd=repo)
        raise GitAgentError(
            f"Squash merge of integration into '{cur_branch}' conflicted. "
            f"Resolve manually in your working tree, or `gitagent abort` to discard.\n"
            f"{exc}"
        ) from exc

    if gitwrap.is_clean(repo):
        raise GitAgentError(
            "Nothing to commit after squash (integration produces no net changes)."
        )

    sha = gitwrap.commit(message, sign=sign, cwd=repo)
    store.log_event(
        p,
        {
            "event": "finalize",
            "feature_key": p.feature.name,
            "session": session.id,
            "commit": sha,
            "message": message,
            "branch": cur_branch,
        },
    )

    if not no_reset:
        store.teardown(p, session, keep_log=True)

    return sha
