#!/usr/bin/env python3
"""Merge shard/run summaries while dropping unresolved env errors."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def is_env_error(row: dict[str, Any]) -> bool:
    if row.get("env_error") is True:
        return True
    if row.get("error") in {"reset_failed", "env_error"}:
        return True
    if row.get("termination") == "env_error":
        return True
    if row.get("termination") == "error" and row.get("n_steps") == 0:
        return True
    # B-MoCA uses ``steps`` rather than ``n_steps`` and reports emulator,
    # Appium, and reset failures as a top-level error before any actor step.
    # Such episodes contain no task execution evidence and must be rerun.
    step_count = row.get("steps", row.get("n_steps"))
    if row.get("error") and step_count == 0:
        return True
    return False


def key_for(benchmark: str, row: dict[str, Any]) -> tuple[Any, ...]:
    if benchmark == "b_moca":
        return (row.get("task"), row.get("env_id"))
    if benchmark == "mobilesafetybench":
        return (row.get("task_category"), row.get("task_id"), row.get("instruction"))
    if benchmark == "android_world":
        return (row.get("task_name"), row.get("seed_index"))
    return tuple(sorted(row.items()))


def summary_paths(run_dir: Path) -> list[Path]:
    return sorted(run_dir.glob("episode_summary*.jsonl"))


def merge_shards(root: Path, group: str) -> None:
    # Current AW runs keep shard outputs under <group>/shardNN. Keep the
    # legacy sibling pattern for older partial runs.
    shard_dirs = sorted((root / group).glob("shard[0-9][0-9]"))
    if not shard_dirs:
        shard_dirs = sorted(root.glob(f"{group}_shard[0-9][0-9]"))
    rows: list[dict[str, Any]] = []
    for shard_dir in shard_dirs:
        rows.extend(read_jsonl(shard_dir / "episode_summary.jsonl"))
    out_dir = root / group
    write_jsonl(out_dir / "episode_summary.jsonl", rows)
    with (out_dir / "merge_meta.json").open("w", encoding="utf-8") as f:
        json.dump(
            {
                "source_shards": [str(p) for p in shard_dirs],
                "episodes": len(rows),
            },
            f,
            indent=2,
        )


def collect_env_error_clean(root: Path, benchmark: str, group: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    bench_root = root / benchmark
    base = bench_root / group
    candidates = [
        base,
        *sorted(bench_root.glob(f"{group}_run_[0-9][0-9]")),
        # Backward compatibility for logs produced before the run_XX naming.
        *sorted(bench_root.glob(f"{group}_rerun_[0-9][0-9]")),
    ]
    by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    unresolved: dict[tuple[Any, ...], dict[str, Any]] = {}

    for run_dir in candidates:
        for path in summary_paths(run_dir):
            for row in read_jsonl(path):
                key = key_for(benchmark, row)
                row = dict(row)
                row["_source_summary"] = str(path)
                if is_env_error(row):
                    if key in by_key:
                        continue
                    unresolved.setdefault(key, row)
                    continue
                # Reruns are intended to replace env errors, not to overwrite a
                # completed non-env episode with a later accidental duplicate.
                by_key.setdefault(key, row)
                unresolved.pop(key, None)

    rows = list(by_key.values())
    meta = {
        "benchmark": benchmark,
        "group": group,
        "source_runs": [str(p) for p in candidates if p.exists()],
        "episodes": len(rows),
        "unresolved_env_errors": len(unresolved),
        "unresolved_keys": [list(k) for k in unresolved],
    }
    return rows, meta


def merge_env_error_clean(root: Path, benchmark: str, group: str, *, write_output_dir: bool = True) -> dict[str, Any]:
    rows, meta = collect_env_error_clean(root, benchmark, group)
    if write_output_dir:
        out_dir = root / benchmark / "env_error_clean" / group
        write_jsonl(out_dir / "episode_summary.jsonl", rows)
        with (out_dir / "merge_meta.json").open("w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
    return meta


def discover_groups(root: Path, benchmark: str) -> list[str]:
    bench_root = root / benchmark
    groups: set[str] = set()
    generated_run_re = re.compile(r"^(?P<group>.+)_(?:run|rerun)_\d\d(?:_shard\d\d)?$")
    shard_suffix_re = re.compile(r"^(?P<group>.+)_shard\d\d$")
    skip_names = {"env_error_clean", "_tool_registry"}
    for path in bench_root.iterdir() if bench_root.exists() else []:
        if not path.is_dir():
            continue
        name = path.name
        if name in skip_names or name.startswith("."):
            continue
        if generated_run_re.match(name) or shard_suffix_re.match(name):
            continue
        groups.add(name)
    return sorted(groups)


def run_cli(default_root: str, description: str) -> None:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--root", default=default_root)
    parser.add_argument("--benchmark", choices=["b_moca", "mobilesafetybench", "android_world"], required=True)
    parser.add_argument("--group", default="")
    parser.add_argument("--merge-shards-only", action="store_true")
    parser.add_argument("--no-output-dir", action="store_true")
    args = parser.parse_args()

    root = Path(args.root)
    if args.merge_shards_only:
        if not args.group:
            raise SystemExit("--group is required with --merge-shards-only")
        merge_shards(root / args.benchmark, args.group)
        return

    groups = [args.group] if args.group else discover_groups(root, args.benchmark)
    metas = [
        merge_env_error_clean(root, args.benchmark, group, write_output_dir=not args.no_output_dir)
        for group in groups
    ]
    print(json.dumps(metas, indent=2, ensure_ascii=False))
