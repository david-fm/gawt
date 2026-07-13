from __future__ import annotations

from pathlib import Path

import pytest

from gitagent import skill


def test_locate_returns_path() -> None:
    """locate() should find the skill in the repo (walk-up) or bundled resource."""
    p = skill.locate()
    assert p is not None
    assert (p / "SKILL.md").exists()


def test_install_copies_to_destination(tmp_path: Path) -> None:
    dst = tmp_path / "skills" / "gitagent"
    result = skill.install(destination=dst)
    assert result == dst
    assert (dst / "SKILL.md").exists()
    # content must be identical to the source
    src = skill.locate()
    assert src is not None
    assert (dst / "SKILL.md").read_bytes() == (src / "SKILL.md").read_bytes()


def test_install_creates_parent_directories(tmp_path: Path) -> None:
    dst = tmp_path / "deeply" / "nested" / "skills" / "gitagent"
    skill.install(destination=dst)
    assert dst.is_dir()
    assert (dst / "SKILL.md").exists()


def test_install_refuses_existing_without_force(tmp_path: Path) -> None:
    dst = tmp_path / "skills" / "gitagent"
    dst.mkdir(parents=True)
    marker = dst / "preexisting.txt"
    marker.write_text("do not touch", encoding="utf-8")

    with pytest.raises(FileExistsError):
        skill.install(destination=dst, force=False)

    # existing contents untouched
    assert marker.read_text(encoding="utf-8") == "do not touch"


def test_install_overwrites_when_force_true(tmp_path: Path) -> None:
    dst = tmp_path / "skills" / "gitagent"
    dst.mkdir(parents=True)
    (dst / "stale.txt").write_text("old", encoding="utf-8")

    result = skill.install(destination=dst, force=True)
    assert result == dst
    assert (dst / "SKILL.md").exists()
    assert not (dst / "stale.txt").exists()


def test_skill_is_a_single_file() -> None:
    """The skill must live entirely in SKILL.md (opencode loads only that file).

    AGENTS.md / README.md / examples / etc. are NOT loaded by opencode's skill
    system; bundling them would mislead users into thinking they're read.
    """
    src = skill.locate()
    assert src is not None
    files = sorted(p.name for p in src.iterdir() if p.is_file())
    assert files == ["SKILL.md"]
