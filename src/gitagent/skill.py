from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path


def locate() -> Path | None:
    """Return the path to the bundled gitagent skill, or None if not found.

    Tries (in order):
      1. The package resource `gitagent._skills.gitagent` (wheel installs).
      2. `<repo>/skills/gitagent` found by walking up from this package file
         (source checkouts and editable installs).
    """
    try:
        traversable = resources.files("gitagent").joinpath("_skills", "gitagent")
        if traversable.is_dir():
            return Path(str(traversable))
    except (ModuleNotFoundError, FileNotFoundError):
        pass

    package_dir = Path(__file__).resolve().parent
    for ancestor in (package_dir, *package_dir.parents):
        candidate = ancestor / "skills" / "gitagent"
        if candidate.is_dir():
            return candidate

    return None


def install(destination: Path | None = None, *, force: bool = True) -> Path:
    """Copy the bundled skill into `~/.agents/skills/gitagent` and return the path.

    Raises FileNotFoundError if the skill cannot be located.
    """
    src = locate()
    if src is None:
        raise FileNotFoundError(
            "gitagent skill source not found. "
            "If you installed via pipx/pip, this is a packaging bug. "
            "Otherwise, clone the gitagent repo and run `make install-skill`."
        )

    dst = destination or (Path.home() / ".agents" / "skills" / "gitagent")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if force and dst.exists():
        shutil.rmtree(dst)
    elif dst.exists():
        raise FileExistsError(
            f"Skill already installed at {dst}. Re-run with force=True to overwrite."
        )
    shutil.copytree(src, dst)
    return dst
