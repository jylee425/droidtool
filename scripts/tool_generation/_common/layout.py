from __future__ import annotations

from pathlib import Path
from typing import Iterator


_STAGE_LEAVES = {"test_case", "results", "rollouts", "verdict"}


def _stage_root(path: Path) -> Path:
    path = path.resolve()
    return path.parent if path.name in _STAGE_LEAVES else path


def repair_root(path: Path) -> Path:
    return _stage_root(path).parent


def stage_name(path: Path) -> str:
    return _stage_root(path).name


def target_stage_dir(path: Path, app_slug: str, target_slug: str) -> Path:
    return repair_root(path) / app_slug / target_slug / stage_name(path)


def target_stage_leaf(
    path: Path,
    app_slug: str,
    target_slug: str,
    leaf: str,
) -> Path:
    return target_stage_dir(path, app_slug, target_slug) / leaf


def test_case_root(path: Path) -> Path:
    path = path.resolve()
    return path.parent if path.name == "test_case" else path


def target_test_case_dir(path: Path, app_slug: str, target_slug: str) -> Path:
    return test_case_root(path) / app_slug / target_slug / "test_case"


def iter_test_case_files(path: Path, filename: str = "test_case.json") -> Iterator[Path]:
    yield from sorted(test_case_root(path).glob(f"*/*/test_case/{filename}"))


def iter_stage_files(path: Path, leaf: str, filename: str) -> Iterator[Path]:
    root = repair_root(path)
    stage = stage_name(path)
    yield from sorted(root.glob(f"*/*/{stage}/{leaf}/{filename}"))


def iter_stage_artifacts(path: Path, *filenames: str) -> Iterator[tuple[str, str, Path]]:
    root = repair_root(path)
    stage = stage_name(path)
    for target_dir in sorted(root.glob(f"*/*/{stage}")):
        if all((target_dir / name).exists() for name in filenames):
            yield target_dir.parent.parent.name, target_dir.parent.name, target_dir


def iter_target_artifacts(path: Path, *filenames: str) -> Iterator[tuple[str, str, Path]]:
    root = path.resolve()
    for target_dir in sorted(root.glob("*/*")):
        if target_dir.is_dir() and all((target_dir / name).exists() for name in filenames):
            yield target_dir.parent.name, target_dir.name, target_dir


def resolve_target_artifact_dir(
    path: Path,
    app_slug: str,
    target_slug: str,
    *filenames: str,
) -> Path:
    direct = path.resolve() / app_slug / target_slug
    if all((direct / name).exists() for name in filenames):
        return direct
    distributed = target_stage_dir(path, app_slug, target_slug)
    if all((distributed / name).exists() for name in filenames):
        return distributed
    return direct
