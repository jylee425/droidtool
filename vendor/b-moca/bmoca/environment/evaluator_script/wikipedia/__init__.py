"""Wikipedia evaluators.

The legacy evaluator scripts call `adb shell` / `adb pull` without a device
serial. In multi-shard eval there are multiple emulators, so those commands
fail with "more than one device/emulator". The rollout scripts set
BMOCA_ADB_SERIAL per shard; patch subprocess calls before importing the
individual evaluators so their existing ADB snippets target that shard.
"""

from __future__ import annotations

import os
import shlex
import subprocess


_ORIG_RUN = subprocess.run
_ORIG_POPEN = subprocess.Popen


def _serialise_adb_command(cmd):
    serial = os.environ.get("BMOCA_ADB_SERIAL")
    if not serial:
        return cmd

    if isinstance(cmd, str):
        parts = shlex.split(cmd)
        if parts and parts[0] == "adb" and "-s" not in parts:
            return " ".join(shlex.quote(p) for p in ["adb", "-s", serial, *parts[1:]])
        return cmd

    if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "adb" and "-s" not in cmd:
        return [cmd[0], "-s", serial, *cmd[1:]]

    return cmd


def _run_with_serial(cmd, *args, **kwargs):
    return _ORIG_RUN(_serialise_adb_command(cmd), *args, **kwargs)


class _PopenWithSerial(subprocess.Popen):
    def __init__(self, cmd, *args, **kwargs):
        super().__init__(_serialise_adb_command(cmd), *args, **kwargs)


subprocess.run = _run_with_serial
subprocess.Popen = _PopenWithSerial

from .go_to_explore_tab import check_go_to_explore_tab
from .go_to_saved_tab import check_go_to_saved_tab
from .go_to_search_tab import check_go_to_search_tab
from .text_size_50 import check_text_size_50
from .text_size_180 import check_text_size_180
from .disable_and_text_size_50 import check_disable_and_text_size_50
from .disable_and_text_size_180 import check_disable_and_text_size_180
from .disable_top1_random_go_feed import check_disable_top1_random
from .disable_top2_go_feed import check_disable_top2
from .disable_top2_random_go_feed import check_disable_top2_random
from .disable_preview_and_feed import check_disable_preview_and_feed
from .disable_history_topics import check_disable_history_topics
from .disable_day_topics import check_disable_day_topics
from .disable_odd import check_disable_odd
from .disable_even import check_disable_even
from .disable_prime import check_disable_prime

from ._feed_utils import (
    feed_indices_disabled,
    feed_indices_disabled_and_text_size,
    preview_and_feed_indices_disabled,
)


def check_disable_top2(driver):
    return feed_indices_disabled(driver, {0, 1})


def check_disable_top1_random(driver):
    return feed_indices_disabled(driver, {0, 6})


def check_disable_top2_random(driver):
    return feed_indices_disabled(driver, {0, 1, 6})


def check_disable_preview_and_feed(driver):
    return preview_and_feed_indices_disabled(driver, {1})


def check_disable_history_topics(driver):
    return feed_indices_disabled(driver, {3, 5})


def check_disable_day_topics(driver):
    return feed_indices_disabled(driver, {2, 5})


def check_disable_odd(driver):
    return feed_indices_disabled(driver, {0, 2, 4, 6})


def check_disable_even(driver):
    return feed_indices_disabled(driver, {1, 3, 5, 7})


def check_disable_prime(driver):
    return feed_indices_disabled(driver, {1, 2, 4, 6})


def check_disable_and_text_size_50(driver):
    return feed_indices_disabled_and_text_size(driver, {0}, "-5")


def check_disable_and_text_size_180(driver):
    return feed_indices_disabled_and_text_size(driver, {0}, "8")
