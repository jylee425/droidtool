#!/usr/bin/env python
"""API key, model routing, and hosted-model policy helpers."""
from __future__ import annotations

import base64
import io
import os
import sys
import time
from pathlib import Path
from scripts.eval._common.api_loader import load_api_key, load_gemini_vertex_config

def normalize_model_name(model: str) -> str:
    normalized = model.strip()
    aliases = {
        "gpt55": "gpt-5.5",
        "gpt_55": "gpt-5.5",
        "gpt-5.5": "gpt-5.5",
        "claude_sonnet_46": "claude-sonnet-4-6",
        "claude-sonnet-4.6": "claude-sonnet-4-6",
        "claude-sonnet-4-6": "claude-sonnet-4-6",
        "gemini31pro": "gemini-3.1-pro-preview",
        "gemini_31_pro": "gemini-3.1-pro-preview",
        "gemini-3.1-pro": "gemini-3.1-pro-preview",
        "gemini-3.1-pro-preview": "gemini-3.1-pro-preview",
        "gemini35flash": "gemini-3.5-flash",
        "gemini-3.5-flash": "gemini-3.5-flash",
    }
    return aliases.get(normalized.lower(), normalized)


def provider_from_model(model: str) -> str:
    model_l = normalize_model_name(model).lower()
    if "qwen3-vl" in model_l or "qwen3_vl" in model_l:
        return "qwen_local"
    if model_l.startswith("gpt") or model_l.startswith("o"):
        return "openai"
    if model_l.startswith("claude"):
        return "anthropic"
    return "gemini"


def _png_data_url(screenshot_png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(screenshot_png).decode("ascii")


class BasePolicy:
    provider = "base"

    def __init__(self, model: str, max_output_tokens: int, temperature: float):
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature

    def generate(
        self,
        system_text: str,
        user_text: str,
        screenshot_png: bytes,
        max_retries: int = 3,
    ) -> str:
        raise NotImplementedError


class GeminiPolicy(BasePolicy):
    provider = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str,
        max_output_tokens: int = 4096,
        temperature: float = 0.0,
        use_vertex_ai: bool = False,
        vertex_project: str | None = None,
        vertex_location: str = "global",
    ):
        try:
            from google import genai  # type: ignore
            from google.genai import types  # type: ignore
        except ImportError:
            sys.exit(
                "[gemini_run] google-genai not installed.\n"
                "pip install google-genai"
            )
        super().__init__(model, max_output_tokens, temperature)
        self._genai = genai
        self._types = types
        self.use_vertex_ai = use_vertex_ai
        self.vertex_project = vertex_project or ""
        self.vertex_location = vertex_location or "global"
        if self.use_vertex_ai:
            if not self.vertex_project:
                sys.exit("[gemini_run] Vertex AI enabled but project is empty.")
            self._client = genai.Client(
                vertexai=True,
                project=self.vertex_project,
                location=self.vertex_location,
            )
        else:
            self._client = genai.Client(api_key=api_key)
        suffix = (
            f" vertex_project={self.vertex_project}"
            f" vertex_location={self.vertex_location}"
            if self.use_vertex_ai
            else ""
        )
        print(f"[+] GeminiPolicy ready: model={model}{suffix}", flush=True)

    def generate(
        self,
        system_text: str,
        user_text: str,
        screenshot_png: bytes,
        max_retries: int = 3,
    ) -> str:
        """Call Gemini with the system instruction + user text + screenshot.

        Returns the response text (empty string on failure after retries).
        """
        types = self._types

        user_parts = [
            types.Part.from_text(text=user_text),
            types.Part.from_bytes(data=screenshot_png, mime_type="image/png"),
        ]
        contents = [
            types.Content(role="user", parts=user_parts),
        ]
        base_config_kwargs = {
            "system_instruction": system_text,
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
        }
        config_kwargs = dict(base_config_kwargs)
        if "3.1-pro" not in self.model:
            try:
                config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
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
            except Exception as e:  # noqa: BLE001
                if (
                    "thinking" in str(e).lower()
                    and "thinking_config" in config_kwargs
                ):
                    config_kwargs = dict(base_config_kwargs)
                    try:
                        config = types.GenerateContentConfig(**config_kwargs)
                        resp = self._client.models.generate_content(
                            model=self.model,
                            contents=contents,
                            config=config,
                        )
                        return resp.text or ""
                    except Exception as retry_exc:  # noqa: BLE001
                        e = retry_exc
                last_exc = e
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    print(
                        f"[!] Gemini call failed (attempt {attempt+1}/{max_retries}): "
                        f"{str(e)[:120]} — retrying in {wait}s",
                        flush=True,
                    )
                    time.sleep(wait)
        print(f"[!] Gemini call failed after {max_retries} retries: {last_exc}", flush=True)
        return ""


class AnthropicPolicy(BasePolicy):
    provider = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str,
        max_output_tokens: int = 4096,
        temperature: float = 0.0,
    ):
        try:
            import anthropic  # type: ignore
        except ImportError:
            sys.exit("[anthropic_run] anthropic not installed.\npip install anthropic")
        super().__init__(model, max_output_tokens, temperature)
        self._client = anthropic.Anthropic(api_key=api_key)
        print(f"[+] AnthropicPolicy ready: model={model}", flush=True)

    def generate(
        self,
        system_text: str,
        user_text: str,
        screenshot_png: bytes,
        max_retries: int = 3,
    ) -> str:
        image_b64 = base64.b64encode(screenshot_png).decode("ascii")
        content = [
            {"type": "text", "text": user_text},
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": image_b64,
                },
            },
        ]
        last_exc: Exception | None = None
        for attempt in range(max_retries):
            try:
                kwargs = {
                    "model": self.model,
                    "system": system_text,
                    "messages": [{"role": "user", "content": content}],
                    "max_tokens": self.max_output_tokens,
                    "temperature": self.temperature,
                }
                try:
                    resp = self._client.messages.create(**kwargs)
                except Exception as e:  # noqa: BLE001
                    if (
                        "temperature" in str(e).lower()
                        and "temperature" in kwargs
                    ):
                        kwargs.pop("temperature", None)
                        resp = self._client.messages.create(**kwargs)
                    else:
                        raise
                parts = [
                    getattr(block, "text", "")
                    for block in getattr(resp, "content", [])
                    if getattr(block, "type", "") == "text"
                ]
                return "\n".join(p for p in parts if p)
            except Exception as e:  # noqa: BLE001
                last_exc = e
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    print(
                        f"[!] Anthropic call failed (attempt {attempt+1}/{max_retries}): "
                        f"{str(e)[:120]} - retrying in {wait}s",
                        flush=True,
                    )
                    time.sleep(wait)
        print(f"[!] Anthropic call failed after {max_retries} retries: {last_exc}", flush=True)
        return ""


class OpenAIPolicy(BasePolicy):
    provider = "openai"

    def __init__(
        self,
        api_key: str,
        model: str,
        max_output_tokens: int = 4096,
        temperature: float = 0.0,
    ):
        try:
            from openai import OpenAI  # type: ignore
        except ImportError:
            sys.exit("[openai_run] openai not installed.\npip install openai")
        super().__init__(model, max_output_tokens, temperature)
        self._client = OpenAI(api_key=api_key)
        print(f"[+] OpenAIPolicy ready: model={model}", flush=True)

    def generate(
        self,
        system_text: str,
        user_text: str,
        screenshot_png: bytes,
        max_retries: int = 3,
    ) -> str:
        messages = [
            {"role": "system", "content": system_text},
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": user_text},
                    {"type": "input_image", "image_url": _png_data_url(screenshot_png)},
                ],
            },
        ]
        last_exc: Exception | None = None
        for attempt in range(max_retries):
            try:
                kwargs = {
                    "model": self.model,
                    "input": messages,
                    "max_output_tokens": self.max_output_tokens,
                    "temperature": self.temperature,
                }
                try:
                    resp = self._client.responses.create(**kwargs)
                except Exception as e:  # noqa: BLE001
                    if (
                        "Unsupported parameter" in str(e)
                        and "temperature" in str(e)
                        and "temperature" in kwargs
                    ):
                        kwargs.pop("temperature", None)
                        resp = self._client.responses.create(**kwargs)
                    else:
                        raise
                text = getattr(resp, "output_text", "")
                if text:
                    return text
                chunks: list[str] = []
                for item in getattr(resp, "output", []) or []:
                    for content in getattr(item, "content", []) or []:
                        val = getattr(content, "text", "")
                        if val:
                            chunks.append(val)
                return "\n".join(chunks)
            except Exception as e:  # noqa: BLE001
                last_exc = e
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    print(
                        f"[!] OpenAI call failed (attempt {attempt+1}/{max_retries}): "
                        f"{str(e)[:120]} - retrying in {wait}s",
                        flush=True,
                    )
                    time.sleep(wait)
        print(f"[!] OpenAI call failed after {max_retries} retries: {last_exc}", flush=True)
        return ""


class QwenPolicy(BasePolicy):
    """Local Hugging Face Qwen3-VL policy for screenshot-based evals."""

    provider = "qwen_local"

    def __init__(
        self,
        model: str,
        max_output_tokens: int = 4096,
        temperature: float = 0.0,
    ):
        try:
            import torch  # type: ignore
            from PIL import Image  # type: ignore
            from transformers import AutoProcessor, Qwen3VLForConditionalGeneration  # type: ignore
        except ImportError as exc:
            sys.exit(f"[qwen_local] Qwen3-VL dependencies missing: {exc}")

        model_path = Path(model).expanduser().resolve()
        if not (model_path / "config.json").is_file():
            sys.exit(f"[qwen_local] model not found: {model_path}")
        super().__init__(str(model_path), max_output_tokens, temperature)
        self._torch = torch
        self._image = Image
        self._processor = AutoProcessor.from_pretrained(
            model_path,
            local_files_only=True,
            trust_remote_code=True,
        )
        self._model = Qwen3VLForConditionalGeneration.from_pretrained(
            model_path,
            dtype=torch.bfloat16,
            device_map="auto",
            attn_implementation="sdpa",
            local_files_only=True,
            trust_remote_code=True,
        )
        self._model.eval()
        visible = os.environ.get("CUDA_VISIBLE_DEVICES", "<all>")
        print(
            f"[+] QwenPolicy ready: model={model_path} "
            f"CUDA_VISIBLE_DEVICES={visible}",
            flush=True,
        )

    def _input_device(self):
        device_map = getattr(self._model, "hf_device_map", None)
        if isinstance(device_map, dict):
            for device in device_map.values():
                if isinstance(device, str) and device.startswith("cuda"):
                    return device
                if isinstance(device, int):
                    return f"cuda:{device}"
        return next(self._model.parameters()).device

    def generate(
        self,
        system_text: str,
        user_text: str,
        screenshot_png: bytes,
        max_retries: int = 3,
    ) -> str:
        last_exc: Exception | None = None
        for attempt in range(max_retries):
            try:
                image = self._image.open(io.BytesIO(screenshot_png)).convert("RGB")
                messages = [
                    {"role": "system", "content": [{"type": "text", "text": system_text}]},
                    {
                        "role": "user",
                        "content": [
                            {"type": "image", "image": image},
                            {"type": "text", "text": user_text},
                        ],
                    },
                ]
                inputs = self._processor.apply_chat_template(
                    messages,
                    tokenize=True,
                    add_generation_prompt=True,
                    return_dict=True,
                    return_tensors="pt",
                ).to(self._input_device())
                kwargs = {
                    "max_new_tokens": self.max_output_tokens,
                    "do_sample": self.temperature > 0,
                }
                if self.temperature > 0:
                    kwargs["temperature"] = self.temperature
                with self._torch.inference_mode():
                    generated = self._model.generate(**inputs, **kwargs)
                trimmed = generated[:, inputs.input_ids.shape[-1]:]
                return self._processor.batch_decode(
                    trimmed,
                    skip_special_tokens=True,
                    clean_up_tokenization_spaces=False,
                )[0]
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    print(
                        f"[!] Qwen local generation failed "
                        f"(attempt {attempt + 1}/{max_retries}): {str(exc)[:160]} "
                        f"- retrying in {wait}s",
                        flush=True,
                    )
                    time.sleep(wait)
        print(f"[!] Qwen local generation failed: {last_exc}", flush=True)
        return ""


def make_policy(args) -> BasePolicy:
    provider = args.provider.lower()
    if provider == "gemini":
        api_key = load_api_key("GEMINI_API_KEY", args.api_key_yaml)
        vertex_cfg = load_gemini_vertex_config(args.api_key_yaml)
        return GeminiPolicy(
            api_key=api_key,
            model=args.model,
            max_output_tokens=args.max_output_tokens,
            temperature=args.temperature,
            use_vertex_ai=vertex_cfg["use_vertex_ai"],
            vertex_project=vertex_cfg["project"],
            vertex_location=vertex_cfg["location"],
        )
    if provider == "anthropic":
        api_key = load_api_key("ANTHROPIC_API_KEY", args.api_key_yaml)
        return AnthropicPolicy(
            api_key=api_key,
            model=args.model,
            max_output_tokens=args.max_output_tokens,
            temperature=args.temperature,
        )
    if provider == "openai":
        api_key = load_api_key("OPENAI_API_KEY", args.api_key_yaml)
        return OpenAIPolicy(
            api_key=api_key,
            model=args.model,
            max_output_tokens=args.max_output_tokens,
            temperature=args.temperature,
        )
    if provider == "qwen_local":
        return QwenPolicy(
            model=args.model,
            max_output_tokens=args.max_output_tokens,
            temperature=args.temperature,
        )
    sys.exit(f"[eval] unknown provider: {args.provider}")


def make_policy_for_model(
    model: str,
    *,
    max_output_tokens: int = 4096,
    temperature: float = 0.0,
    api_key_yaml: str | None = None,
) -> BasePolicy:
    class _Args:
        pass

    args = _Args()
    args.model = normalize_model_name(model)
    args.provider = provider_from_model(args.model)
    args.max_output_tokens = max_output_tokens
    args.temperature = temperature
    args.api_key_yaml = api_key_yaml
    return make_policy(args)


class ModelPolicyAdapter:
    """GeminiPolicy-compatible adapter used by benchmark-specific rollouts."""

    def __init__(
        self,
        api_key: str = "",
        model: str = "gemini-3.5-flash",
        max_output_tokens: int = 4096,
        temperature: float = 0.0,
        max_retries: int = 3,
        api_key_yaml: str | None = None,
        **_: object,
    ):
        del api_key
        self._backend = make_policy_for_model(
            model,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            api_key_yaml=api_key_yaml,
        )
        self.model = self._backend.model
        self.provider = self._backend.provider
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
        self.max_retries = max_retries

    def generate(
        self,
        system_text: str,
        user_text: str,
        screenshot_png: bytes,
        max_retries: int | None = None,
    ) -> str:
        return self._backend.generate(
            system_text=system_text,
            user_text=user_text,
            screenshot_png=screenshot_png,
            max_retries=max_retries or self.max_retries,
        )
