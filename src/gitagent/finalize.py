from __future__ import annotations

import shutil
from pathlib import Path

from . import gitwrap, review, store
from .errors import GitAgentError
from .models import ProposalState, Session


def finalize(
    repo: Path | None = None,
    *,
    message: str,
    feature: str | None = None,
    target: str | None = None,
    sign: bool = False,
) -> str:
    """Integrate accepted proposals and produce a single commit on the target branch.

    Uses a detached temp worktree based on the target branch so the user's
    local checkout is never disturbed.  The target branch ref is updated via
    ``git update-ref`` after the commit.  The user's own checkout is never
    switched or modified.
    """
    repo = gitwrap.resolve(repo)
    store.require_init(repo)

    if feature is not None:
        p = store.paths_for_feature(repo, feature)
    else:
        p = store.current_feature_paths(repo)

    session = store.require_session(p)
    if session.state.value not in ("open", "integrating"):
        raise GitAgentError(f"Session is {session.state.value}; cannot finalize.")

    target_branch = target or session.target_branch

    # Serialize finalize for this feature. Two concurrent finalizes (or a
    # finalize racing an integrate) on the same feature must not both issue
    # `update-ref refs/heads/<target>` and overwrite each other's commit.
    with store.lock(p, "finalize"):
        return _finalize_locked(
            repo, p, session, message=message,
            target_branch=target_branch, sign=sign,
        )

def _finalize_locked(
    repo: Path,
    p: store.Paths,
    session: Session,
    *,
    message: str,
    target_branch: str,
    sign: bool,
) -> str:
    """Body of `finalize`, executed while the feature's `finalize` lock is held.

    The lock guarantees that only one finalize (and thus one `update-ref` to
    the target branch) runs at a time for a given feature, so concurrent
    superagents cannot clobber each other's commit on `main`.
    """
    # --- Phase 4: integrate against live target state --------------------
    review.integrate(repo, feature=session.feature_key)

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

    # --- Phase 5: land via detached temp worktree -----------------------
    temp_wt = p.root / "_finalize" / session.feature_key

    # The integration worktree is detached (no branch), so its accumulated
    # state lives at its HEAD.  Merge that SHA rather than a (now-nonexistent)
    # integration branch.  Resolve the SHA from inside the integration worktree.
    integration_tip = gitwrap.current_sha(Path(session.integration_worktree))

    try:
        gitwrap.worktree_add_detached(temp_wt, target_branch, cwd=repo)

        try:
            gitwrap.run(
                ["merge", "--squash", integration_tip],
                cwd=temp_wt,
            )
        except GitAgentError as exc:
            gitwrap.abort_merge(cwd=temp_wt)
            raise GitAgentError(
                f"Squash merge of integration into '{target_branch}' conflicted. "
                f"Resolve manually, or `gitagent abort` to discard.\n{exc}"
            ) from exc

        if gitwrap.is_clean(temp_wt):
            raise GitAgentError(
                "Nothing to commit after squash (integration produces no net changes)."
            )

        sha = gitwrap.commit(message, sign=sign, cwd=temp_wt)

        # Update the target branch ref to point to the new commit.
        gitwrap.run(["update-ref", f"refs/heads/{target_branch}", sha], cwd=repo)

    finally:
        # Always clean up the temp worktree, even if commit failed.
        if temp_wt.exists():
            gitwrap.worktree_remove(temp_wt, force=True, cwd=repo)
            gitwrap.worktree_prune(cwd=repo)

    # The target branch ref has been advanced to `sha` via update-ref.  We do
    # NOT touch the user's working tree or checkout — the user stays wherever
    # they were (e.g. on `main`).  If they happen to already be on the target
    # branch, refresh their index/working tree to the new commit.
    cur_branch = gitwrap.current_branch(repo)
    if cur_branch == target_branch:
        gitwrap.run(["reset", "--hard", sha], cwd=repo)

    store.log_event(
        p,
        {
            "event": "finalize",
            "feature_key": p.feature.name,
            "session": session.id,
            "commit": sha,
            "message": message,
            "target_branch": target_branch,
        },
    )

    # Remove .gitagent/features/<key>/ (keep global log).
    shutil.rmtree(p.feature, ignore_errors=True)

    return sha
