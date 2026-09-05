#!/usr/bin/env python
"""API key and config loading helpers for eval scripts."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]

def load_yaml_config(yaml_path: str | None = None) -> dict:
    candidates = []
    if yaml_path:
        candidates.append(Path(yaml_path))
    candidates.append(_REPO / "config" / "api_key.yaml")
    for p in candidates:
        if not p.exists():
            continue
        try:
            import yaml  # type: ignore
            cfg = yaml.safe_load(p.read_text()) or {}
            return cfg if isinstance(cfg, dict) else {}
        except Exception as e:
            print(f"[!] could not read {p}: {e}", flush=True)
    return {}


def load_api_key(env_name: str, yaml_path: str | None = None) -> str:
    key = os.environ.get(env_name, "")
    if key:
        return key
    cfg = load_yaml_config(yaml_path)
    key = str(cfg.get(env_name, "") or "")
    if key:
        return key
    sys.exit(
        f"[eval] {env_name} not found.\n"
        f"Set the env var or add {env_name} to config/api_key.yaml."
    )


def _as_bool(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def load_gemini_vertex_config(yaml_path: str | None = None) -> dict:
    cfg = load_yaml_config(yaml_path)
    return {
        "use_vertex_ai": _as_bool(
            os.environ.get("GEMINI_USE_VERTEX_AI")
            or os.environ.get("GOOGLE_GENAI_USE_VERTEXAI")
            or cfg.get("GEMINI_USE_VERTEX_AI")
        ),
        "project": (
            os.environ.get("GEMINI_VERTEX_PROJECT_ID")
            or os.environ.get("GOOGLE_CLOUD_PROJECT")
            or str(cfg.get("GEMINI_VERTEX_PROJECT_ID", "") or cfg.get("GEMINI_VERTEX_PROJECT_NUMBER", "") or "")
        ),
        "location": (
            os.environ.get("GEMINI_VERTEX_LOCATION")
            or str(cfg.get("GEMINI_VERTEX_LOCATION", "") or "")
            or "global"
        ),
    }


