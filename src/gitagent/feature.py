from __future__ import annotations

import re

from .errors import GitAgentError

FEATURE_PREFIX = "ga/"


def slugify(branch: str) -> str:
    """Turn a feature branch name (e.g. 'ga/auth-rl') into a safe directory key.

    - 'ga/' prefix is stripped.
    - '/' are converted to '__' so the result is a single segment.
    - any other non-alphanumeric character is replaced with '-'.
    """
    if not branch.startswith(FEATURE_PREFIX):
        raise GitAgentError(
            f"Branch '{branch}' is not a feature branch. "
            f"Feature branches must start with '{FEATURE_PREFIX}'. "
            f"Create one with `git checkout -b {FEATURE_PREFIX}<name>` first."
        )
    rest = branch[len(FEATURE_PREFIX):]
    rest = rest.replace("/", "__")
    rest = re.sub(r"[^A-Za-z0-9._-]", "-", rest)
    if not rest or rest in (".", ".."):
        raise GitAgentError(f"Branch '{branch}' yields an invalid feature key.")
    return rest


def is_feature_branch(branch: str | None) -> bool:
    return bool(branch) and branch.startswith(FEATURE_PREFIX)


def name_from_branch(branch: str) -> str:
    """Human-readable feature name derived from a feature branch.

    'ga/auth-rate-limiting' -> 'auth-rate-limiting'
    """
    return branch[len(FEATURE_PREFIX):]
