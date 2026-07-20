from __future__ import annotations

import re

from .errors import GitAgentError

FEATURE_PREFIX = "ga/"


def slugify(branch: str) -> str:
    """Turn a feature key into a safe directory key.

    - any '/' are converted to '__' so the result is a single segment.
    - any other non-alphanumeric character is replaced with '-'.

    Accepts either a bare key ('auth-rl') or a legacy 'ga/auth-rl' form.
    """
    name = branch
    if name.startswith(FEATURE_PREFIX):
        name = name[len(FEATURE_PREFIX):]
    name = name.replace("/", "__")
    name = re.sub(r"[^A-Za-z0-9._-]", "-", name)
    if not name or name in (".", ".."):
        raise GitAgentError(f"Feature name '{branch}' yields an invalid feature key.")
    return name


def coerce(name: str) -> str:
    """Normalize a user-provided feature name into a safe key.

    Accepts 'ga/foo', 'foo', 'ga/foo/bar', 'foo__bar'.
    Always returns the bare key (no prefix): 'foo', 'foo__bar'.
    """
    if name.startswith(FEATURE_PREFIX):
        name = name[len(FEATURE_PREFIX):]
    name = name.replace("/", "__")
    name = re.sub(r"[^A-Za-z0-9._-]", "-", name)
    if not name or name in (".", ".."):
        raise GitAgentError(f"Feature name '{name}' is invalid.")
    return name


def is_feature_branch(branch: str | None) -> bool:
    return bool(branch) and branch.startswith(FEATURE_PREFIX)


def name_from_branch(branch: str) -> str:
    """Human-readable feature name derived from a feature branch or key.

    'ga/auth-rate-limiting' -> 'auth-rate-limiting'
    'auth-rate-limiting'    -> 'auth-rate-limiting'
    """
    if branch.startswith(FEATURE_PREFIX):
        return branch[len(FEATURE_PREFIX):]
    return branch


def branch_for_feature(name: str) -> str:
    """Canonical feature branch name for a user-provided feature name.

    Kept only for backward compatibility / display. gitagent no longer
    creates feature branches in the user's repository.

    'auth-rl'       -> 'ga/auth-rl'
    'ga/auth-rl'    -> 'ga/auth-rl'
    'foo/bar'       -> 'ga/foo__bar'
    """
    if name.startswith(FEATURE_PREFIX):
        return name
    return FEATURE_PREFIX + name.replace("__", "/")
