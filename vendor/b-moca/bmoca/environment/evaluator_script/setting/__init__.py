"""Android Settings evaluators.

Like the other evaluator packages, each Settings surface lives in its own
module. This package only selects the module associated with a task.
"""
from . import airplane, brightness, dark_theme, wifi

_EVALUATORS = {}
for _module in (airplane, brightness, dark_theme, wifi):
    for _task in _module.TASKS:
        _EVALUATORS[_task] = _module


def supports_state_transition(task_name):
    return task_name in _EVALUATORS


def capture_task_state(serial, task_name):
    evaluator = _EVALUATORS.get(task_name)
    if evaluator is None:
        return {"supported": False, "task": task_name, "serial": serial, "ok": False}
    return evaluator.capture(serial, task_name)


def evaluate_task_transition(task_name, before, after):
    evaluator = _EVALUATORS.get(task_name)
    if evaluator is None:
        return {"supported": False, "task": task_name, "success": False,
                "before": before, "after": after, "reason": "no Settings evaluator for task"}
    if not before or not after:
        return {"supported": True, "task": task_name, "success": False,
                "before": before, "after": after, "reason": "before/after state unavailable"}
    return evaluator.evaluate(task_name, before, after)


__all__ = ("capture_task_state", "evaluate_task_transition", "supports_state_transition")
