# -*- coding: utf-8 -*-
"""Minimal OpenAI-compatible client for GPT-5.5 evaluation."""

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

import requests


def _load_env_file(env_path: str = ".env") -> Dict[str, str]:
    """Load a simple KEY=VALUE .env file without an extra dependency."""
    possible_paths = [
        env_path,
        os.path.join(os.getcwd(), env_path),
        os.path.join(Path(__file__).parent, env_path),
    ]
    env_file = next((path for path in possible_paths if os.path.exists(path)), None)
    if not env_file:
        return {}

    values = {}
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    except Exception as exc:
        print(f"[warn] failed to read .env file: {exc}")
    return values


_ENV_CONFIG = _load_env_file()


def get_config(key: str, default: Optional[str] = None) -> Optional[str]:
    """Read configuration from the environment, then .env, then a default."""
    return os.environ.get(key) or _ENV_CONFIG.get(key) or default


@dataclass
class GPTInput:
    text: str
    stream: bool = False


@dataclass
class GPTOutput:
    text: str
    usage_metadata: Dict = field(default_factory=dict)
    raw_response: Dict = field(default_factory=dict)


class GatewayRetryExhausted(requests.HTTPError):
    """This request spent its 502 allowance; callers must not replay it."""


class GPTClient:
    """LLM judge client using an OpenAI-compatible Chat Completions schema."""

    def __init__(
        self,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        verbose: bool = True,
    ):
        self.api_url = api_url or get_config(
            "OPENAI_API_URL", "https://api.openai.com/v1/chat/completions"
        )
        self.api_key = api_key or get_config("OPENAI_API_KEY")
        self.model = model or get_config("OPENAI_MODEL", "gpt-5.5")
        self.reasoning_effort = reasoning_effort or get_config(
            "OPENAI_REASONING_EFFORT", "medium"
        )
        self.max_output_tokens = int(get_config("OPENAI_MAX_OUTPUT_TOKENS", "32768"))
        self.max_tokens_param = get_config("OPENAI_MAX_TOKENS_PARAM", "max_completion_tokens")
        if self.max_tokens_param not in {"max_completion_tokens", "max_tokens"}:
            raise ValueError(
                "OPENAI_MAX_TOKENS_PARAM must be 'max_completion_tokens' or 'max_tokens'"
            )
        self.thinking = get_config("OPENAI_THINKING")
        self.timeout = int(get_config("OPENAI_TIMEOUT", "600"))
        self.verbose = verbose

        if not self.api_key:
            raise ValueError("Missing configuration: OPENAI_API_KEY")

    def query(self, input_data: GPTInput) -> GPTOutput:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": input_data.text}],
            "stream": input_data.stream,
        }
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        payload[self.max_tokens_param] = self.max_output_tokens
        if self.thinking:
            payload["thinking"] = {"type": self.thinking}

        if self.verbose:
            print(f"[request] model={self.model}, text_length={len(input_data.text)}")

        for retry in range(6):
            response = requests.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
                stream=input_data.stream,
            )
            if response.status_code != 502:
                break
            response.close()
            if retry == 5:
                raise GatewayRetryExhausted("HTTP 502 exhausted 5 retries", response=response)
            if self.verbose:
                print(f"[retry] HTTP 502; retry {retry + 1}/5 in 10 seconds")
            time.sleep(10)
        if response.status_code >= 400:
            print(f"[err] HTTP {response.status_code} body: {response.text[:1000]}")
            response.raise_for_status()

        if input_data.stream:
            raise ValueError("Streaming responses are not supported by this evaluator")

        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"Invalid Chat Completions response: {json.dumps(data)[:1000]}") from exc

        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )

        usage = data.get("usage", {})
        completion_details = usage.get("completion_tokens_details") or {}
        usage_metadata = {
            "promptTokenCount": usage.get("prompt_tokens", 0),
            "candidatesTokenCount": usage.get("completion_tokens", 0),
            "totalTokenCount": usage.get("total_tokens", 0),
            "thoughtsTokenCount": completion_details.get("reasoning_tokens", 0),
        }
        return GPTOutput(
            text=content or "",
            usage_metadata=usage_metadata,
            raw_response=data,
        )
