#!/usr/bin/env python3
"""Merge skill-guided eval summaries while dropping environment errors."""

from __future__ import annotations

from _common.env_error_clean import run_cli


if __name__ == "__main__":
    run_cli(
        default_root="logs_skill",
        description=(
            "Merge skill-guided shard/run summaries while dropping "
            "unresolved env errors."
        ),
    )
