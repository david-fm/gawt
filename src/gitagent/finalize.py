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
    repo = gitwrap.resolve(repo)
    store.require_init(repo)
    session = store.require_session(repo)
    if session.state.value not in ("open", "integrating"):
        raise GitAgentError(f"Session is {session.state.value}; cannot finalize.")

    review.integrate(repo)

    integrated = [
        pid
        for pid in store.proposal_ids(repo)
        if store.load_review(repo, pid).state == ProposalState.INTEGRATED
    ]
    if not integrated:
        raise GitAgentError(
            "No integrated proposals to finalize. "
            "Run `gitagent accept <pid>` and then `gitagent integrate` first."
        )

    cur_branch = gitwrap.current_branch(repo)
    if cur_branch is None:
        raise GitAgentError("HEAD is detached; check out a branch before finalize.")
    if cur_branch != session.base_branch:
        # Proceed but warn: the squash will be a real 3-way merge and may conflict.
        pass

    try:
        gitwrap.merge_squash(session.integration_branch, cwd=repo)
    except GitAgentError as exc:
        gitwrap.abort_merge(cwd=repo)
        raise GitAgentError(
            f"Squash merge of integration into '{cur_branch}' conflicted. "
            "Resolve manually in your working tree, or `gitagent abort` to discard.\n"
            f"{exc}"
        ) from exc

    if gitwrap.is_clean(repo):
        raise GitAgentError(
            "Nothing to commit after squash (integration produces no net changes)."
        )

    sha = gitwrap.commit(message, sign=sign, cwd=repo)
    store.log_event(
        repo,
        {
            "event": "finalize",
            "session": session.id,
            "commit": sha,
            "message": message,
            "branch": cur_branch,
        },
    )

    if not no_reset:
        store.teardown(repo, session, keep_log=True)

    return sha
