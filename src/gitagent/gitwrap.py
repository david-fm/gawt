from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
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


def git_common_dir(cwd: Path | str | None = None) -> Path:
    """Return the shared ``.git`` directory of the main repository.

    From a linked worktree this points to the **main** repo's ``.git``,
    not the per-worktree metadata directory.
    """
    try:
        out = _run(["rev-parse", "--git-common-dir"], cwd=cwd, check=True)
    except GitAgentError as exc:
        raise GitAgentError("Not inside a git repository.") from exc
    p = Path(out.strip())
    if not p.is_absolute():
        p = Path(cwd).resolve() / p if cwd is not None else Path.cwd().resolve() / p
    return p


def main_repo_root(cwd: Path | str | None = None) -> Path:
    """Return the main repository's working tree root, regardless of cwd.

    Walks up from ``--git-common-dir`` to find the repo root (the directory
    that contains ``.git`` or is the root of a bare repo's parent). Works
    identically from main, a linked worktree, or any subdir of either.
    """
    common = git_common_dir(cwd)
    cur = common.resolve()
    while cur != cur.parent:
        if (cur / ".git").exists() or cur.name == ".git":
            # Either <repo>/.git (resolved by going up one) or a bare repo root.
            if cur.name == ".git":
                return cur.parent
            return cur
        cur = cur.parent
    # Bare repo: common dir IS the repo root.
    return common


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


def file_exists_at(ref: str, file: str, cwd: Path | str | None = None) -> bool:
    """True if *file* exists in the tree at *ref*."""
    try:
        run(["cat-file", "-e", f"{ref}:{file}"], cwd=cwd)
        return True
    except GitAgentError:
        return False


def file_content_at(
    ref: str, file: str, cwd: Path | str | None = None
) -> str | None:
    """Return the blob content of *file* at *ref*, or None if absent."""
    raw = blob_bytes(ref, file, cwd=cwd)
    if raw is None:
        return None
    return raw.decode("utf-8", errors="replace")


def blob_bytes(
    ref: str, file: str, cwd: Path | str | None = None
) -> bytes | None:
    """Return the raw blob content of *file* at *ref*, or None if absent."""
    proc = subprocess.run(
        ["git", "cat-file", "blob", f"{ref}:{file}"],
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def list_files_vs_ref(
    ref: str, cwd: Path | str | None = None
) -> dict[str, str]:
    """Map of {file: status} for files that differ from *ref* in the worktree.

    Status is one of 'added', 'modified', 'deleted'. Comparison is blob-vs-disk
    (index-independent): the live worktree index never tracks agent writes, so
    relying on ``git diff`` would miscount files already committed to *ref*.
    """
    wt = Path(cwd).resolve()

    tracked = set(
        line
        for line in _run(
            ["ls-tree", "-r", "--name-only", ref], cwd=cwd, check=True
        ).splitlines()
        if line.strip() and not line.strip().startswith(".git/")
    )

    disk: set[str] = set()
    for root, dirs, files in os.walk(wt, followlinks=False):
        root_rel = Path(root).resolve().relative_to(wt)
        # Skip any nested repo internals (e.g. a nested .git or worktree hooks).
        dirs[:] = [d for d in dirs if d != ".git"]
        for fname in files:
            if fname == ".git":
                continue
            rel = root_rel / fname
            disk.add(rel.as_posix() if str(rel) != "." else fname)

    result: dict[str, str] = {}
    for f in sorted(tracked - disk):
        result[f] = "deleted"
    for f in sorted(tracked & disk):
        target = blob_bytes(ref, f, cwd=cwd)
        if target is not None and target != (wt / f).read_bytes():
            result[f] = "modified"
    for f in sorted(disk - tracked):
        result[f] = "added"
    return result


def diff_vs_ref(
    ref: str, file: str, cwd: Path | str | None = None
) -> tuple[str, str]:
    """Return ``(status, diff_text)`` for *file* vs *ref* using blob-vs-disk.

    Diff text is produced with ``git diff --no-index`` between the target
    blob and the current disk content, so index state does not matter.
    """
    wt = Path(cwd).resolve()
    disk_path = wt / file
    target = blob_bytes(ref, file, cwd=cwd)

    if target is None and not disk_path.exists():
        return "clean", ""
    if target is None:
        out = _run(
            ["diff", "--no-index", "--no-color", "/dev/null", str(disk_path)],
            cwd=cwd,
            check=False,
        )
        return "added", out
    if not disk_path.exists():
        fd, tmp = tempfile.mkstemp(suffix=f".{Path(file).name}")
        try:
            os.close(fd)
            with open(tmp, "wb") as fh:
                fh.write(target)
            out = _run(
                ["diff", "--no-index", "--no-color", tmp, "/dev/null"],
                cwd=cwd,
                check=False,
            )
        finally:
            os.unlink(tmp)
        return "deleted", out
    if target == disk_path.read_bytes():
        return "clean", ""
    fd, tmp = tempfile.mkstemp(suffix=f".{Path(file).name}")
    try:
        os.close(fd)
        with open(tmp, "wb") as fh:
            fh.write(target)
        out = _run(
            ["diff", "--no-index", "--no-color", tmp, str(disk_path)],
            cwd=cwd,
            check=False,
        )
    finally:
        os.unlink(tmp)
    return "modified", out
