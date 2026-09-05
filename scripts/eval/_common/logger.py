"""Shared logging utilities for actor rollout agents."""

from __future__ import annotations

import json
from pathlib import Path


_STEP_KEY_ORDER = (
    "episode_id",
    "task_name",
    "goal",
    "timestamp",
    "step_index",
    "screenshot_file",
    "screen_wh",
    "_run_config",
    "actor_input",
    "actor_output",
    "actor_inference_time_s",
    "step_duration_s",
    "description",
    "context",
    "action_text",
    "tool_call",
    "tool_result",
    "answer",
    "exec_error",
    "reward",
    "success",
    "done",
)

_LEGACY_KEYS = (
    "run_config",
    "actor_duration_s",
    "actor_inference_duration_s",
    "inference_time_s",
    "inference_duration_s",
    "input_messages",
    "input",
    "system_prompt",
    "raw_input",
    "raw_output",
    "raw_response",
    "model_input",
    "model_output",
    "step",
)


def _content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(part for part in parts if part)
    return ""


def _extract_prompts_from_messages(messages):
    if not isinstance(messages, list):
        return None, None
    system_prompt = None
    user_prompt = None
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        text = _content_text(msg.get("content"))
        if role == "system" and system_prompt is None and text:
            system_prompt = text
        elif role == "user" and user_prompt is None and text:
            user_prompt = text
    return system_prompt, user_prompt


def _normalize_actor_input(value, raw_fallback=None):
    if value is None:
        if raw_fallback is None:
            return None
        value = {"raw_input": raw_fallback}
    elif isinstance(value, dict):
        value = dict(value)
    elif isinstance(value, list):
        value = {"raw_input": value, "messages": value}
    else:
        value = {"raw_input": value}

    if "raw_input" not in value and raw_fallback is not None:
        value["raw_input"] = raw_fallback

    system_prompt, user_prompt = _extract_prompts_from_messages(value.get("messages"))
    if system_prompt is not None and "system_prompt" not in value:
        value["system_prompt"] = system_prompt
    if user_prompt is not None and "user_prompt" not in value:
        value["user_prompt"] = user_prompt

    key_order = (
        "raw_input",
        "system_prompt",
        "user_prompt",
        "messages",
        "history",
        "screenshot",
        "model",
    )
    return _reorder_mapping(value, key_order)


def _normalize_actor_output(value, raw_fallback=None):
    if value is None:
        return raw_fallback
    if isinstance(value, dict):
        normalized = dict(value)
        if "raw_output" not in normalized and raw_fallback is not None:
            normalized["raw_output"] = raw_fallback
        return _reorder_mapping(normalized, ("raw_output", "description", "context", "action"))
    return value


def _normalize_env_feedback(record, env_feedback=None):
    feedback = dict(env_feedback or record.get("env_feedback") or {})
    if not feedback:
        record.pop("env_feedback", None)
        return
    for key in ("success", "success_score", "reward", "proficiency", "safety"):
        if key not in feedback and key in record:
            feedback[key] = record.get(key)
    record["env_feedback"] = feedback


def _reorder_mapping(value, key_order):
    if not isinstance(value, dict):
        return value
    reordered = {}
    for key in key_order:
        if key in value:
            reordered[key] = value[key]
    for key, item in value.items():
        if key not in reordered:
            reordered[key] = item
    return reordered


def _reorder_step_record(record):
    reordered = {}
    for key in _STEP_KEY_ORDER:
        if key in record:
            reordered[key] = record[key]
    for key, value in record.items():
        if key not in reordered:
            reordered[key] = value
    record.clear()
    record.update(reordered)


def normalize_step_record(
    record,
    actor_input=None,
    actor_output=None,
    env_feedback=None,
):
    """Normalize a step record to the actor-only eval schema."""
    if "step_index" not in record and "step" in record:
        record["step_index"] = record.get("step")
    if "episode_id" not in record:
        task_name = record.get("task_name")
        env_id = record.get("env_id")
        if task_name and env_id:
            record["episode_id"] = f"{task_name}__{env_id}"

    if actor_input is None:
        actor_input = record.get("actor_input", record.get("input_messages"))
    if actor_output is None:
        actor_output = record.get(
            "actor_output",
            record.get("raw_response", record.get("model_output")),
        )

    raw_fallback = None
    model_input = record.get("model_input")
    if isinstance(model_input, dict):
        raw_fallback = model_input.get("messages")
    elif model_input is not None:
        raw_fallback = model_input
    if raw_fallback is None:
        raw_fallback = record.get("input_messages")

    record["actor_input"] = _normalize_actor_input(actor_input, raw_fallback=raw_fallback)
    record["actor_output"] = _normalize_actor_output(actor_output)
    if "_run_config" not in record:
        run_config = record.get("run_config")
        if isinstance(run_config, dict):
            record["_run_config"] = run_config
    if "actor_inference_time_s" not in record:
        record["actor_inference_time_s"] = (
            record.get("inference_time_s")
            or record.get("inference_duration_s")
            or record.get("actor_duration_s")
        )
    if "step_duration_s" not in record:
        record["step_duration_s"] = None
    _normalize_env_feedback(record, env_feedback=env_feedback)

    for key in _LEGACY_KEYS:
        record.pop(key, None)
    _reorder_step_record(record)
    return record


def backfill_step_env_feedback(task_dir, env_feedback):
    """Update already-written step_NNN.json files with final env feedback."""
    task_dir = Path(task_dir)
    if not task_dir.exists():
        return
    for path in sorted(task_dir.glob("step_*.json")):
        if path.name.endswith("_screen.json"):
            continue
        try:
            record = json.loads(path.read_text())
        except Exception:
            continue
        if record.get("type") == "final_observation":
            continue
        normalize_step_record(record, env_feedback=env_feedback)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2))


class JsonlWriter:
    """Append-mode JSONL file writer."""

    def __init__(self, path):
        self._fh = open(path, "a")

    def write(self, obj):
        self._fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self):
        self._fh.close()
