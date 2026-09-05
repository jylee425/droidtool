#!/usr/bin/env python3
"""Merge isolation-test-tool summaries while dropping environment errors."""

from __future__ import annotations

from _common.env_error_clean import run_cli


if __name__ == "__main__":
    run_cli(
        default_root="logs_tool_isolation_test",
        description=(
            "Merge isolation-test-tool shard/run summaries while dropping "
            "unresolved env errors."
        ),
    )
