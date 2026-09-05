#!/usr/bin/env python3
"""Merge GUI-only shard/run summaries while dropping unresolved env errors."""

from __future__ import annotations

from _common.env_error_clean import run_cli


if __name__ == "__main__":
    run_cli(
        default_root="logs_gui_only",
        description="Merge GUI-only shard/run summaries while dropping unresolved env errors.",
    )
