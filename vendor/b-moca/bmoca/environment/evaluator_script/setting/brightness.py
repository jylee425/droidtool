"""Screen-brightness Settings task evaluator."""
from .common import evaluation, setting, snapshot, unavailable, value

TASKS = {"decrease_the_screen_brightness_in_setting"}

def capture(serial, task):
    return snapshot(task, serial,
                    screen_brightness=setting(serial, "system", "screen_brightness"),
                    screen_brightness_float=setting(serial, "system", "screen_brightness_float"))

def evaluate(task, before, after):
    if not before.get("ok") or not after.get("ok"): return unavailable(task, before, after)
    field = "screen_brightness_float"
    old, new = value(before, field), value(after, field)
    if old is None or new is None:
        field = "screen_brightness"
        old, new = value(before, field), value(after, field)
    return evaluation(task, before, after, old is not None and new is not None and new < old,
                      f"{field} decreased: {old!r} -> {new!r}")
