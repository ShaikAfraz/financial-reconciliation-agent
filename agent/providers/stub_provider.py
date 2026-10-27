"""
Deterministic Stub Provider for CI and Testing.
Used exclusively in unit/integration tests to supply scripted LLM outputs,
simulate edge cases (malformed JSON, hallucinations, rate limits), and verify backoff.
Never used in benchmarks or production runtime.
"""

import json
import time
from typing import Dict, Any, List, Optional, Type, Union
from pydantic import BaseModel
from agent.providers.base import BaseAgentProvider
from agent.trace import TraceStep

class StubProvider(BaseAgentProvider):
    """
    Deterministic stub provider that replays scripted responses for testing.
    Can simulate transient 429 rate limit errors to verify retry and backoff.
    """

    def __init__(
        self,
        scripted_responses: Optional[List[Union[Dict[str, Any], str, Exception]]] = None,
        model: str = "stub-model"
    ):
        self.model = model
        self.responses: List[Union[Dict[str, Any], str, Exception]] = list(scripted_responses or [])
        self.call_history: List[Dict[str, Any]] = []
        self.rate_limit_failures_remaining: int = 0
        self.retry_delay_log: List[float] = []

    def set_responses(self, responses: List[Union[Dict[str, Any], str, Exception]]):
        self.responses = list(responses)

    def set_rate_limit_failures(self, count: int):
        """Simulate HTTP 429 failures before succeeding."""
        self.rate_limit_failures_remaining = count

    def generate_structured(
        self,
        prompt: str,
        response_schema: Type[BaseModel],
        system_instruction: Optional[str] = None,
        temperature: float = 0.1,
        use_cache: bool = True
    ) -> Dict[str, Any]:
        """
        Simulates structured output generation.
        Handles scripted exceptions (e.g. 429 rate limits), JSON strings, or dicts.
        """
        start_time = time.perf_counter()
        self.call_history.append({
            "prompt": prompt,
            "system_instruction": system_instruction,
            "schema": response_schema.__name__
        })

        # Check if rate limit failure should be simulated
        if self.rate_limit_failures_remaining > 0:
            self.rate_limit_failures_remaining -= 1
            raise RuntimeError("HTTP 429: Resource has been exhausted (rate limit exceeded).")

        if not self.responses:
            # Fallback default empty response matching schema
            fallback = {
                "decision": "none",
                "selected_candidate_ids": [],
                "exception_type": "none",
                "rationale": "Stub fallback response.",
                "evidence_refs": []
            }
            return {
                "text": json.dumps(fallback),
                "parsed": response_schema.model_validate(fallback),
                "cached": False,
                "prompt_tokens": 50,
                "completion_tokens": 20,
                "latency_ms": 1.0
            }

        next_resp = self.responses.pop(0)

        if isinstance(next_resp, Exception):
            raise next_resp

        if isinstance(next_resp, str):
            text = next_resp
            # Could be malformed JSON or valid JSON
            try:
                parsed = response_schema.model_validate_json(text)
            except Exception:
                parsed = None
        elif isinstance(next_resp, dict):
            text = json.dumps(next_resp)
            parsed = response_schema.model_validate(next_resp)
        else:
            text = str(next_resp)
            parsed = None

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        return {
            "text": text,
            "parsed": parsed,
            "cached": False,
            "prompt_tokens": 100,
            "completion_tokens": 40,
            "latency_ms": round(elapsed_ms, 2)
        }

    def plan_step(
        self,
        prompt: str,
        context: Dict[str, Any],
        history: List[TraceStep]
    ) -> Dict[str, Any]:
        return {
            "action": "finish",
            "thought": "Stub finished.",
            "final_recommendation": "Completed via StubProvider.",
            "grounded_facts": [],
            "verification": "none"
        }
