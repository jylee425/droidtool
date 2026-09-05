from __future__ import annotations

import re
from pathlib import Path


_VERSIONED_RE = re.compile(r"^(?P<stem>.+)_(?P<index>\d{2,})$")


def parse_versioned_name(name: str) -> tuple[str, int] | None:
    match = _VERSIONED_RE.match(name)
    if not match:
        return None
    return match.group("stem"), int(match.group("index"))


def is_versioned_name(name: str, stem: str | None = None) -> bool:
    parsed = parse_versioned_name(name)
    if parsed is None:
        return False
    return stem is None or parsed[0] == stem


def next_versioned_dir(parent: Path, stem: str, *, width: int = 2) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    max_index = -1
    for child in parent.iterdir():
        if not child.is_dir():
            continue
        parsed = parse_versioned_name(child.name)
        if parsed is None:
            continue
        child_stem, child_index = parsed
        if child_stem == stem:
            max_index = max(max_index, child_index)
    return parent / f"{stem}_{max_index + 1:0{width}d}"


def latest_versioned_dir(parent: Path, stem: str) -> Path | None:
    if not parent.exists():
        return None
    latest: tuple[int, Path] | None = None
    for child in parent.iterdir():
        if not child.is_dir():
            continue
        parsed = parse_versioned_name(child.name)
        if parsed is None:
            continue
        child_stem, child_index = parsed
        if child_stem != stem:
            continue
        if latest is None or child_index > latest[0]:
            latest = (child_index, child)
    return latest[1] if latest is not None else None


def resolve_versioned_creation_dir(out_dir: Path) -> Path:
    """Resolve an abstract .../creation stem to the next .../creation_NN dir."""
    if is_versioned_name(out_dir.name, "creation"):
        return out_dir
    if out_dir.name == "creation":
        return next_versioned_dir(out_dir.parent, "creation")
    return out_dir


def resolve_existing_creation_dir(path: Path) -> Path:
    """Resolve an abstract .../creation stem to the latest existing .../creation_NN."""
    if is_versioned_name(path.name, "creation"):
        return path
    if path.name == "creation":
        if path.exists():
            return path
        latest = latest_versioned_dir(path.parent, "creation")
        if latest is not None:
            return latest
        raise FileNotFoundError(f"no creation directories under {path.parent}")
    return path


def verification_dir_for_creation(creation_dir: Path, kind: str) -> Path:
    """Return sibling .../creation_NN_<kind> for a versioned creation dir."""
    parsed = parse_versioned_name(creation_dir.name)
    if parsed is None or parsed[0] != "creation":
        raise ValueError(
            f"creation_dir must be named creation_NN, got: {creation_dir}"
        )
    return creation_dir.parent / f"{creation_dir.name}_{kind}"


def next_verification_dir(parent: Path, kind: str) -> Path:
    """Return next sibling verification_<kind>_NN directory."""
    return next_versioned_dir(parent, f"verification_{kind}")
