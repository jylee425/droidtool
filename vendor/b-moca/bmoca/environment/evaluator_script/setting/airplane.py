"""Airplane-mode Settings task evaluator."""
from .common import evaluation, service_boolean, setting, snapshot, unavailable, value

TASKS = {"turn_off_airplane_mode": 0, "turn_on_airplane_mode": 1}

def capture(serial, task):
    return snapshot(task, serial, airplane_mode_on=setting(serial, "global", "airplane_mode_on"),
                    airplane_mode_service=service_boolean(serial,
                    ("cmd", "connectivity", "airplane-mode"), r"^\s*enabled\s*$", r"^\s*disabled\s*$"))

def evaluate(task, before, after):
    if not before.get("ok") or not after.get("ok"): return unavailable(task, before, after)
    old, new = value(before, "airplane_mode_service"), value(after, "airplane_mode_service")
    provider_new, desired = value(after, "airplane_mode_on"), TASKS[task]
    success = new == desired and provider_new == desired
    return evaluation(task, before, after, success,
                      f"airplane mode final state is {desired}: {old!r} -> {new!r}; provider={provider_new!r}")
