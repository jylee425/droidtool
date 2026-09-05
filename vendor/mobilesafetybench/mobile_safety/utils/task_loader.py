import copy
import json
from pathlib import Path


def task_filename(task_category, task_id):
    return f"task_{task_category}_{task_id}.json"


def load_task(tasks_dir, task_category, task_id):
    tasks_dir = Path(tasks_dir)
    path = tasks_dir / task_filename(task_category, task_id)
    if not path.is_file():
        raise FileNotFoundError(f"Task file not found: {path}")

    with open(path, "r", encoding="utf-8") as task_json:
        task = json.load(task_json)

    if task.get("task_category") != task_category or task.get("task_id") != task_id:
        raise ValueError(
            f"Task file {path} contains "
            f"{task.get('task_category')}/{task.get('task_id')}, "
            f"expected {task_category}/{task_id}"
        )
    return copy.deepcopy(task)


def load_all_tasks(tasks_dir):
    tasks_dir = Path(tasks_dir)
    tasks = []
    for path in sorted(tasks_dir.glob("task_*.json")):
        with open(path, "r", encoding="utf-8") as task_json:
            task = json.load(task_json)
        if isinstance(task, list):
            tasks.extend(copy.deepcopy(task))
        else:
            tasks.append(copy.deepcopy(task))
    return tasks


def _expand_task_ref(task, tasks_dir):
    if (
        tasks_dir is not None
        and isinstance(task, dict)
        and "task_category" in task
        and "task_id" in task
        and "instruction" not in task
    ):
        return load_task(tasks_dir, task["task_category"], task["task_id"])
    return copy.deepcopy(task)


def load_task_file(path, tasks_dir=None):
    with open(path, "r", encoding="utf-8") as task_json:
        tasks = json.load(task_json)
    if isinstance(tasks, dict):
        return [_expand_task_ref(tasks, tasks_dir)]
    return [_expand_task_ref(task, tasks_dir) for task in tasks]
