"""Replay: reconstruct a file's content at a snapshot boundary.

The base is ``git show <target_branch>:<file>`` and all ``edits`` rows with
``id <= boundary_edit_id`` are applied in id order:

- ``write``: content becomes ``full_content``
- ``edit``: apply old_string -> new_string (honoring ``replace_all``)
- ``delete``: file becomes absent (None)
- ``adjusted``: attribution marker from crash reconciliation — no-op

If an edit's old_string cannot be found against the reconstructed content,
the log and the disk disagree (out-of-band change) and we raise
``REPLAY_MISMATCH`` so the snapshot aborts instead of committing a lie.
"""
from __future__ import annotations

from pathlib import Path

from . import gitwrap
from .db import Database
from .errors import GitAgentError


def reconstruct(
    file: str,
    boundary_edit_id: int,
    *,
    db: Database,
    target_ref: str,
    repo: Path,
) -> str | None:
    """Return the content of *file* at *boundary_edit_id*, or None if deleted.

    None means the file is absent at the boundary (either never created or
    deleted by then).
    """
    rows = db.fetchall(
        """SELECT * FROM edits
           WHERE file = ? AND id <= ?
           ORDER BY id""",
        (file, boundary_edit_id),
    )

    content = gitwrap.file_content_at(target_ref, file, cwd=repo)
    if content is None:
        content = ""

    for r in rows:
        op = r["op"]
        if op == "adjusted":
            continue
        if op == "write":
            content = r["full_content"] or ""
        elif op == "edit":
            old = r["old_string"]
            new = r["new_string"]
            if old is None or old not in content:
                raise GitAgentError(
                    f"REPLAY_MISMATCH: edit #{r['id']} for '{file}' cannot be "
                    f"applied — old_string not found in reconstructed content "
                    f"(out-of-band change?)."
                )
            if r["replace_all"]:
                content = content.replace(old, new)
            else:
                content = content.replace(old, new, 1)
        elif op == "delete":
            content = None

    return content