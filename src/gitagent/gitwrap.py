from __future__ import annotations

import contextlib
import subprocess
from pathlib import Path

from .errors import GitAgentError


def resolve(repo: Path | str | None = None) -> Path:
    """Return an absolute repo path, defaulting to the current git top-level."""
    return Path(repo).resolve() if repo is not None else repo_root()


def repo_root(cwd: Path | str | None = None) -> Path:
    try:
        out = _run(["rev-parse", "--show-toplevel"], cwd=cwd, check=True)
    except GitAgentError as exc:
        raise GitAgentError("Not inside a git repository.") from exc
    return Path(out.strip())


def run(args: list[str], cwd: Path | str | None = None, *, check: bool = True) -> str:
    return _run(args, cwd=cwd, check=check)


def run_ok(args: list[str], cwd: Path | str | None = None) -> bool:
    cmd = ["git", *args]
    proc = subprocess.run(
        cmd, cwd=str(cwd) if cwd is not None else None, capture_output=True, text=True
    )
    return proc.returncode == 0


def _run(args: list[str], cwd: Path | str | None, *, check: bool) -> str:
    cmd = ["git", *args]
    proc = subprocess.run(
        cmd, cwd=str(cwd) if cwd is not None else None, capture_output=True, text=True
    )
    if proc.returncode != 0 and check:
        raise GitAgentError(_format_error(cmd, proc.stderr, proc.stdout))
    return proc.stdout


def _format_error(cmd: list[str], stderr: str, stdout: str) -> str:
    parts = [" ".join(cmd)]
    if stderr.strip():
        parts.append(stderr.strip())
    if stdout.strip():
        parts.append(stdout.strip())
    return "\n".join(parts)


def current_sha(cwd: Path | str | None = None) -> str:
    return run(["rev-parse", "HEAD"], cwd=cwd).strip()


def current_branch(cwd: Path | str | None = None) -> str | None:
    try:
        return run(["symbolic-ref", "--short", "HEAD"], cwd=cwd).strip()
    except GitAgentError:
        return None


def is_clean(cwd: Path | str | None = None) -> bool:
    return run(["status", "--porcelain"], cwd=cwd).strip() == ""


def worktree_add(
    path: Path | str,
    branch: str,
    base_ref: str,
    cwd: Path | str | None = None,
) -> None:
    run(["worktree", "add", "-b", branch, str(path), base_ref], cwd=cwd)


def worktree_add_detached(
    path: Path | str,
    ref: str,
    cwd: Path | str | None = None,
) -> None:
    """Create a detached worktree at *path* based on *ref* (branch or SHA)."""
    run(["worktree", "add", "--detach", str(path), ref], cwd=cwd)


def worktree_remove(
    path: Path | str, *, force: bool = False, cwd: Path | str | None = None
) -> None:
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(path))
    with contextlib.suppress(GitAgentError):
        run(args, cwd=cwd)


def worktree_prune(cwd: Path | str | None = None) -> None:
    with contextlib.suppress(GitAgentError):
        run(["worktree", "prune", "--expire=now"], cwd=cwd)


def branch_exists(branch: str, cwd: Path | str | None = None) -> bool:
    return run_ok(["rev-parse", "--verify", f"refs/heads/{branch}"], cwd=cwd)


def branch_delete(branch: str, *, force: bool = True, cwd: Path | str | None = None) -> None:
    args = ["branch", "-D" if force else "-d", branch]
    with contextlib.suppress(GitAgentError):
        run(args, cwd=cwd)


def commit(message: str, *, sign: bool = False, cwd: Path | str | None = None) -> str:
    args = ["commit", "-m", message]
    if sign:
        args.append("-S")
    run(args, cwd=cwd)
    return current_sha(cwd=cwd)


def merge_squash(branch: str, cwd: Path | str | None = None) -> None:
    run(["merge", "--squash", branch], cwd=cwd)


def abort_merge(cwd: Path | str | None = None) -> None:
    with contextlib.suppress(GitAgentError):
        run(["merge", "--abort"], cwd=cwd)


def reset_hard(sha: str, cwd: Path | str | None = None) -> None:
    """Hard-reset HEAD to *sha* in the given working directory."""
    run(["reset", "--hard", sha], cwd=cwd)


def update_ref(ref: str, sha: str, cwd: Path | str | None = None) -> None:
    """Update a symbolic ref (e.g. refs/heads/main) to point to *sha*."""
    run(["update-ref", ref, sha], cwd=cwd)


def unmerged_files(cwd: Path | str | None = None) -> list[str]:
    out = run(["diff", "--name-only", "--diff-filter=U"], cwd=cwd)
    return [line for line in out.splitlines() if line.strip()]
