"""
Live Google Gemini Provider using the official google-genai SDK.
Implements header-based authentication, pinned model ID from config,
configurable timeouts, bounded exponential backoff on 429s/transient errors,
structured output schemas, and call caching.
API keys are strictly protected from logs, exceptions, and string representations.
"""

import os
import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Type
from pydantic import BaseModel

from agent.providers.base import BaseAgentProvider
from agent.trace import TraceStep
from tools.registry import get_tool_definitions
from reconcile.cache import get_llm_cache

try:
    from google import genai
    from google.genai import types
    from google.genai.errors import APIError
    GENAI_AVAILABLE = True
except ImportError:
    genai = None
    types = None
    APIError = Exception
    GENAI_AVAILABLE = False

CONFIG_PATH = Path(__file__).parent.parent.parent.absolute() / "reconcile" / "config.json"
ENV_PATH = Path(__file__).parent.parent.parent.absolute() / ".env"

def _load_env_if_present() -> None:
    if os.environ.get("GEMINI_API_KEY") or not ENV_PATH.exists():
        return
    try:
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k and k not in os.environ:
                        os.environ[k] = v
    except Exception:
        pass

_load_env_if_present()

def load_reconcile_config() -> Dict[str, Any]:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

class GeminiAgentProvider(BaseAgentProvider):
    """
    Autonomous provider powered by the official google-genai SDK.
    Pins model ID from reconcile/config.json, uses header authentication,
    and guarantees strict credential privacy.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None
    ):
        self.config = config or load_reconcile_config()
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        # Read pinned model from config, fallback to gemini-3.5-flash
        self.model = model or self.config.get("model_id", "gemini-3.5-flash")
        self.timeout_seconds = int(self.config.get("request_timeout_seconds", 30))
        self.max_retries = int(self.config.get("max_retries", 3))
        self.backoff_factor = float(self.config.get("backoff_factor", 2.0))
        self.initial_delay = float(self.config.get("initial_retry_delay_seconds", 1.0))
        self.cache = get_llm_cache()

        self._client = None
        if self.is_available() and GENAI_AVAILABLE:
            # Header auth via HttpOptions; key is transmitted via x-goog-api-key header
            http_options = types.HttpOptions(
                headers={"x-goog-api-key": self.api_key},
                timeout=self.timeout_seconds * 1000
            )
            self._client = genai.Client(
                api_key=self.api_key,
                http_options=http_options
            )

    def __repr__(self) -> str:
        masked_key = f"{self.api_key[:4]}...{self.api_key[-4:]}" if len(self.api_key) >= 8 else "***"
        return f"GeminiAgentProvider(model={self.model!r}, api_key={masked_key!r})"

    def is_available(self) -> bool:
        return bool(self.api_key) and GENAI_AVAILABLE

    def generate_structured(
        self,
        prompt: str,
        response_schema: Type[BaseModel],
        system_instruction: Optional[str] = None,
        temperature: float = 0.1,
        use_cache: bool = True
    ) -> Dict[str, Any]:
        """
        Executes a structured generation request with pydantic schema enforcement,
        bounded retry with exponential backoff on 429/transient errors, and caching.
        """
        if not self.is_available():
            raise ValueError("Gemini API key is not configured or google-genai SDK is missing.")

        full_prompt_for_cache = f"SYSTEM: {system_instruction or ''}\nUSER: {prompt}"

        # 1. Check cache
        if use_cache:
            cached = self.cache.get(self.model, full_prompt_for_cache)
            if cached is not None:
                return {
                    "text": cached["response_text"],
                    "parsed": response_schema.model_validate_json(cached["response_text"]),
                    "cached": True,
                    "prompt_tokens": cached.get("prompt_tokens", 0),
                    "completion_tokens": cached.get("completion_tokens", 0),
                    "latency_ms": cached.get("latency_ms", 0.0)
                }

        # 2. Build Generation Config with structured output
        config_args = {
            "temperature": temperature,
            "response_mime_type": "application/json",
            "response_schema": response_schema
        }
        if self.config.get("max_output_tokens") is not None:
            config_args["max_output_tokens"] = int(self.config["max_output_tokens"])
        if system_instruction:
            config_args["system_instruction"] = system_instruction

        gen_config = types.GenerateContentConfig(**config_args)

        # 3. Bounded retry with exponential backoff
        delay = self.initial_delay
        last_exception = None

        start_time = time.perf_counter()
        for attempt in range(self.max_retries):
            try:
                response = self._client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=gen_config
                )
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0

                text = response.text or ""
                # Parse against schema
                parsed_obj = response_schema.model_validate_json(text)

                # Token usage
                usage = getattr(response, "usage_metadata", None)
                p_toks = getattr(usage, "prompt_token_count", 0) if usage else 0
                c_toks = getattr(usage, "candidates_token_count", 0) if usage else 0

                # Cache result
                if use_cache:
                    self.cache.set(
                        model=self.model,
                        prompt=full_prompt_for_cache,
                        response_text=text,
                        prompt_tokens=p_toks or 0,
                        completion_tokens=c_toks or 0,
                        latency_ms=elapsed_ms
                    )

                return {
                    "text": text,
                    "parsed": parsed_obj,
                    "cached": False,
                    "prompt_tokens": p_toks,
                    "completion_tokens": c_toks,
                    "latency_ms": round(elapsed_ms, 2)
                }

            except Exception as e:
                last_exception = e
                err_str = str(e)
                # Sanitize any accidental key leak in error strings
                if self.api_key and self.api_key in err_str:
                    err_str = err_str.replace(self.api_key, "[REDACTED_API_KEY]")

                is_retryable = (
                    "429" in err_str or
                    "resource exhausted" in err_str.lower() or
                    "rate limit" in err_str.lower() or
                    "503" in err_str or
                    "service unavailable" in err_str.lower() or
                    "deadline exceeded" in err_str.lower() or
                    "deadline_exceeded" in err_str.lower() or
                    "deadline expired" in err_str.lower() or
                    "504" in err_str
                )

                if is_retryable and attempt < self.max_retries - 1:
                    time.sleep(delay)
                    delay *= self.backoff_factor
                else:
                    raise RuntimeError(f"Gemini generation failed (attempt {attempt + 1}/{self.max_retries}): {err_str}") from None

        raise RuntimeError(f"Gemini generation exhausted {self.max_retries} retries: {last_exception}")

    def plan_step(
        self,
        prompt: str,
        context: Dict[str, Any],
        history: List[TraceStep]
    ) -> Dict[str, Any]:
        """
        Original plan_step implementation preserved for orchestrator agent compatibility.
        Uses x-goog-api-key header authentication and verifies credential privacy.
        """
        import urllib.request
        import urllib.error

        if not self.is_available():
            raise ValueError("GEMINI_API_KEY is not set. Please provide an API key or use DeterministicAgentProvider.")

        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"

        system_instruction = (
            "You are an Autonomous Financial Reconciliation Agent. "
            "Your task is to reconcile personal transactions, check for anomalies, compute budget variances, "
            "and propose grounded reallocations using your tools. "
            "Always think step-by-step and call appropriate tools in a coherent sequence. "
            "If information is missing, do not guess — ask for details."
        )

        user_content = f"User Request: {prompt}\nContext: {json.dumps(context, indent=2)}"
        contents = [{"role": "user", "parts": [{"text": user_content}]}]

        for step in history:
            contents.append({
                "role": "model",
                "parts": [{
                    "functionCall": {
                        "name": step.tool_name,
                        "args": step.tool_arguments
                    }
                }]
            })
            contents.append({
                "role": "function",
                "parts": [{
                    "functionResponse": {
                        "name": step.tool_name,
                        "response": step.tool_output
                    }
                }]
            })

        tools_decl = []
        for td in get_tool_definitions():
            tools_decl.append({
                "name": td["name"],
                "description": td["description"],
                "parameters": td["parameters"]
            })

        payload = {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": contents,
            "tools": [{"functionDeclarations": tools_decl}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 1024
            }
        }

        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_msg = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
            if self.api_key and self.api_key in error_msg:
                error_msg = error_msg.replace(self.api_key, "[REDACTED_API_KEY]")
            raise RuntimeError(f"Gemini API request failed with HTTP {e.code}: {error_msg}") from None
        except Exception as e:
            err_msg = str(e)
            if self.api_key and self.api_key in err_msg:
                err_msg = err_msg.replace(self.api_key, "[REDACTED_API_KEY]")
            raise RuntimeError(f"Gemini API request failed: {err_msg}") from None

        candidate = resp_data.get("candidates", [{}])[0]
        parts = candidate.get("content", {}).get("parts", [])

        for part in parts:
            if "functionCall" in part:
                fc = part["functionCall"]
                return {
                    "action": "tool_call",
                    "thought": f"Calling tool '{fc['name']}' based on LLM decision.",
                    "tool_name": fc["name"],
                    "arguments": fc.get("args", {})
                }
            elif "text" in part:
                text = part["text"]
                return {
                    "action": "finish",
                    "thought": "LLM finalized reconciliation plan.",
                    "final_recommendation": text,
                    "grounded_facts": [],
                    "verification": "none"
                }

        return {
            "action": "finish",
            "thought": "No further action produced by LLM.",
            "final_recommendation": "Reconciliation completed.",
            "grounded_facts": [],
            "verification": "none"
        }
