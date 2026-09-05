"""Clock evaluators.

Legacy clock evaluator scripts call `adb shell` / `adb pull` without a device
serial. In multi-shard eval there can be multiple emulators, so target the
shard serial exported by the rollout environment before importing evaluators.
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

from .alarm1030am_midweek import check_alarm1030am_midweek
from .alarm1030am_weekend import check_alarm1030am_weekend
from .alarm1030am_weekday import check_alarm1030am_weekday
from .alarm1030am_and_volume import check_alarm1030am_and_volume
from .alarm1330pm_and_after_2 import check_alarm1330pm_and_after_2
from .alarm1330pm_and_before_2 import check_alarm1330pm_and_before_2
from .alarm1330pm_and_volume import check_alarm1330pm_and_volume
from .alarm1330pm_weekend_and_volume import check_alarm1330pm_weekend_and_volume
from .alarm1330pm_weekday_and_volume import check_alarm1330pm_weekday_and_volume
from .alarm1330pm_weekday import check_alarm1330pm_weekday
from .alarm1330pm_weekend import check_alarm1330pm_weekend
from .delete_9am import check_delete_9am
from .delete_9am_and_create_alarm1030am import check_delete_9am_and_create_alarm1030am
from .turn_alarm9am_and_volume import check_alarm9am_and_volume
