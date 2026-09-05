from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def load_yaml_config(yaml_path: str | None = None) -> dict[str, Any]:
    candidates: list[Path] = []
    if yaml_path:
        candidates.append(Path(yaml_path))
    candidates.append(repo_root() / "config" / "api_key.yaml")
    for path in candidates:
        if not path.exists():
            continue
        if yaml is None:
            return {}
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            return loaded if isinstance(loaded, dict) else {}
        except Exception as exc:  # noqa: BLE001
            print(f"[tool_creation] could not read {path}: {exc}", flush=True)
    return {}


def load_gemini_config(yaml_path: str | None = None) -> dict[str, Any]:
    cfg = load_yaml_config(yaml_path)
    return {
        "api_key": os.environ.get("GEMINI_API_KEY")
        or str(cfg.get("GEMINI_API_KEY", "") or ""),
        "use_vertex_ai": _truthy(
            os.environ.get("GEMINI_USE_VERTEX_AI")
            or os.environ.get("GOOGLE_GENAI_USE_VERTEXAI")
            or cfg.get("GEMINI_USE_VERTEX_AI")
        ),
        "vertex_project": (
            os.environ.get("GEMINI_VERTEX_PROJECT_ID")
            or os.environ.get("GOOGLE_CLOUD_PROJECT")
            or str(
                cfg.get("GEMINI_VERTEX_PROJECT_ID", "")
                or cfg.get("GEMINI_VERTEX_PROJECT_NUMBER", "")
                or ""
            )
        ),
        "vertex_location": (
            os.environ.get("GEMINI_VERTEX_LOCATION")
            or str(cfg.get("GEMINI_VERTEX_LOCATION", "") or "")
            or "global"
        ),
    }


def normalize_gemini_model(model: str) -> str:
    aliases = {
        "gemini31pro": "gemini-3.1-pro-preview",
        "gemini_31_pro": "gemini-3.1-pro-preview",
        "gemini-3.1-pro": "gemini-3.1-pro-preview",
        "gemini-3.1-pro-preview": "gemini-3.1-pro-preview",
        "gemini35flash": "gemini-3.5-flash",
        "gemini-3.5-flash": "gemini-3.5-flash",
    }
    return aliases.get(model.strip().lower(), model.strip())


class GeminiTextClient:
    def __init__(
        self,
        *,
        model: str = "gemini-3.5-flash",
        api_key_yaml: str | None = None,
        temperature: float = 0.0,
        max_output_tokens: int = 8192,
    ) -> None:
        try:
            from google import genai  # type: ignore
            from google.genai import types  # type: ignore
        except ImportError:
            sys.exit("[tool_creation] google-genai not installed. pip install google-genai")

        cfg = load_gemini_config(api_key_yaml)
        self.model = normalize_gemini_model(model)
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self._types = types
        if cfg["use_vertex_ai"]:
            if not cfg["vertex_project"]:
                sys.exit("[tool_creation] Vertex AI enabled but project id is empty.")
            self._client = genai.Client(
                vertexai=True,
                project=str(cfg["vertex_project"]),
                location=str(cfg["vertex_location"]),
            )
            print(
                "[tool_creation] Gemini ready"
                f" model={self.model} vertex_project={cfg['vertex_project']}"
                f" location={cfg['vertex_location']}",
                flush=True,
            )
        else:
            if not cfg["api_key"]:
                sys.exit(
                    "[tool_creation] GEMINI_API_KEY not found and Vertex AI is disabled."
                )
            self._client = genai.Client(api_key=str(cfg["api_key"]))
            print(f"[tool_creation] Gemini ready model={self.model}", flush=True)

    def generate(
        self,
        *,
        system_text: str,
        user_text: str,
        max_retries: int = 4,
    ) -> str:
        types = self._types
        contents = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=user_text)],
            )
        ]
        base_kwargs = {
            "system_instruction": system_text,
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
        }
        config_kwargs = dict(base_kwargs)
        if "3.1-pro" not in self.model:
            try:
                config_kwargs["thinking_config"] = types.ThinkingConfig(
                    thinking_budget=0
                )
            except Exception:
                pass

        last_exc: Exception | None = None
        for attempt in range(max_retries):
            try:
                config = types.GenerateContentConfig(**config_kwargs)
                resp = self._client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=config,
                )
                return resp.text or ""
            except Exception as exc:  # noqa: BLE001
                if "thinking_config" in config_kwargs and "thinking" in str(exc).lower():
                    config_kwargs = dict(base_kwargs)
                    continue
                last_exc = exc
                if attempt < max_retries - 1:
                    wait = 2**attempt
                    print(
                        "[tool_creation] Gemini call failed"
                        f" ({attempt + 1}/{max_retries}): {str(exc)[:160]}"
                        f"; retrying in {wait}s",
                        flush=True,
                    )
                    time.sleep(wait)
        raise RuntimeError(f"Gemini call failed after {max_retries} retries: {last_exc}")


class Qwen3VLClient:
    def __init__(
        self,
        *,
        model_path: str | Path | None = None,
        temperature: float = 0.0,
        max_output_tokens: int = 8192,
        min_pixels: int = 256,
        max_pixels: int = 1_270_180,
        dtype: str = "bfloat16",
        device_map: str = "auto",
    ) -> None:
        try:
            import torch  # type: ignore
            from qwen_vl_utils import process_vision_info  # type: ignore
            from transformers import AutoProcessor, Qwen3VLForConditionalGeneration  # type: ignore
        except ImportError as exc:
            sys.exit(f"[tool_creation] Qwen3-VL dependencies missing: {exc}")

        default_path = repo_root() / "models" / "Qwen3-VL-4B-Instruct"
        self.model_path = Path(model_path or os.environ.get("QWEN3_VL_MODEL_PATH") or default_path).expanduser().resolve()
        if not (self.model_path / "config.json").exists():
            sys.exit(
                "[tool_creation] Qwen3-VL model not found: "
                f"{self.model_path}\n"
                "Download with: huggingface-cli download Qwen/Qwen3-VL-4B-Instruct "
                f"--local-dir {self.model_path}"
            )

        dtype_map = {
            "auto": "auto",
            "float32": torch.float32,
            "fp32": torch.float32,
            "float16": torch.float16,
            "fp16": torch.float16,
            "bfloat16": torch.bfloat16,
            "bf16": torch.bfloat16,
        }
        torch_dtype = dtype_map.get(str(dtype).lower(), torch.bfloat16)
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self._process_vision_info = process_vision_info
        self._torch = torch
        self.processor = AutoProcessor.from_pretrained(
            self.model_path,
            min_pixels=min_pixels,
            max_pixels=max_pixels,
            trust_remote_code=True,
        )
        self.model = Qwen3VLForConditionalGeneration.from_pretrained(
            self.model_path,
            torch_dtype=torch_dtype,
            device_map=device_map,
            attn_implementation="sdpa",
            trust_remote_code=True,
        )
        self.model.eval()
        print(
            "[tool_creation] Qwen3-VL ready"
            f" model_path={self.model_path} device_map={device_map}",
            flush=True,
        )

    def _input_device(self) -> str:
        device_map = getattr(self.model, "hf_device_map", None)
        if isinstance(device_map, dict):
            for value in device_map.values():
                if isinstance(value, str) and value.startswith("cuda"):
                    return value
        try:
            return str(next(self.model.parameters()).device)
        except Exception:
            return "cuda" if self._torch.cuda.is_available() else "cpu"

    def generate_with_image(
        self,
        *,
        system_text: str,
        user_text: str,
        image_path: str | Path | None = None,
    ) -> str:
        content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        if image_path:
            content.insert(0, {"type": "image", "image": str(Path(image_path).resolve())})
        messages = [
            {"role": "system", "content": [{"type": "text", "text": system_text}]},
            {"role": "user", "content": content},
        ]
        text = self.processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        image_inputs, video_inputs = self._process_vision_info(messages)
        inputs = self.processor(
            text=[text],
            images=image_inputs,
            videos=video_inputs,
            padding=True,
            return_tensors="pt",
        )
        inputs = inputs.to(self._input_device())
        generation_kwargs = {
            "max_new_tokens": self.max_output_tokens,
            "do_sample": self.temperature > 0,
        }
        if self.temperature > 0:
            generation_kwargs["temperature"] = self.temperature
        with self._torch.inference_mode():
            generated_ids = self.model.generate(**inputs, **generation_kwargs)
        generated_ids_trimmed = [
            out_ids[len(in_ids) :]
            for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
        ]
        decoded = self.processor.batch_decode(
            generated_ids_trimmed,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )
        return decoded[0] if decoded else ""

    def generate(self, *, system_text: str, user_text: str, max_retries: int = 1) -> str:
        return self.generate_with_image(system_text=system_text, user_text=user_text)
