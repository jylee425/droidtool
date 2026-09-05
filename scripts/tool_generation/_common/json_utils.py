from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
_HOST_PATH_RE = re.compile(r"(?<![\w.])/(?:home|Users)/[^\s\"'`,)}\]]+")
_EXPERIMENT_PATH_RE = re.compile(
    r"\boutput_tool(?:_v\d+(?:_[A-Za-z0-9]+)*)?/[^\s\"'`,)}\]]+"
)
_EXPERIMENT_ROUND_RE = re.compile(
    r"\b(?:repair|verification)_(?:gui|unit)_test_\d+\b"
)


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_") or "unknown"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def project_home_path(path: Path) -> str:
    """Serialize project-local paths without embedding a host-specific root."""
    project_home = Path(os.environ.get("PROJECT_PATH", _repo_root())).resolve()
    resolved_path = path.resolve()
    try:
        relative_path = resolved_path.relative_to(project_home)
    except ValueError:
        return str(path)
    if not relative_path.parts:
        return "$PROJECT_PATH"
    return f"$PROJECT_PATH/{relative_path.as_posix()}"


def sanitize_model_text(text: str) -> str:
    """Remove host- and experiment-specific identifiers from model-visible text."""
    repo = str(_repo_root())
    text = text.replace(repo + "/", "").replace(repo, ".")
    text = _HOST_PATH_RE.sub("<HOST_PATH>", text)
    text = _EXPERIMENT_PATH_RE.sub("<ARTIFACT_PATH>", text)
    text = _EXPERIMENT_ROUND_RE.sub("<EXPERIMENT_ROUND>", text)
    return text


def sanitize_model_value(value: Any) -> Any:
    """Recursively sanitize values before they are sent to a model."""
    if isinstance(value, str):
        return sanitize_model_text(value)
    if isinstance(value, list):
        return [sanitize_model_value(item) for item in value]
    if isinstance(value, dict):
        return {key: sanitize_model_value(item) for key, item in value.items()}
    return value


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def extract_json_object(text: str) -> Any:
    """Extract and parse a JSON object/array from a model response."""
    raw = text.strip()
    if not raw:
        raise ValueError("empty model response")

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    fence = _FENCE_RE.search(raw)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass

    starts = [idx for idx in (raw.find("{"), raw.find("[")) if idx >= 0]
    if not starts:
        raise ValueError("model response does not contain JSON")
    start = min(starts)
    end = max(raw.rfind("}"), raw.rfind("]"))
    if end <= start:
        raise ValueError("model response contains incomplete JSON")
    return json.loads(raw[start : end + 1])


def dump_json(path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def dump_text(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_raw_io_log(
    *,
    out_dir: Path,
    system_text: str,
    user_text: str,
    output_text: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Persist the exact model input/output text passed by the caller."""
    raw_dir = out_dir / "raw_io"
    full_input = system_text + "\n\n--- USER ---\n" + user_text
    paths = {
        "input_system": raw_dir / "input_system.txt",
        "input_user": raw_dir / "input_user.txt",
        "input_prompt": raw_dir / "input_prompt.txt",
        "output_response": raw_dir / "output_response.txt",
        "config": raw_dir / "config.json",
    }
    dump_text(paths["input_system"], system_text)
    dump_text(paths["input_user"], user_text)
    dump_text(paths["input_prompt"], full_input)
    dump_text(paths["output_response"], output_text)
    dump_json(paths["config"], metadata or {})
    return {key: str(path) for key, path in paths.items()}
