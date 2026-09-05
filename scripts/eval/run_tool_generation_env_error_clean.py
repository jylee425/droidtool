#!/usr/bin/env python3
"""Merge generated app-tool shard/run summaries while dropping env errors."""

from __future__ import annotations

from _common.env_error_clean import run_cli


if __name__ == "__main__":
    run_cli(
        default_root="logs_tool_generation",
        description=(
            "Merge generated app-tool shard/run summaries while dropping "
            "unresolved env errors."
        ),
    )
