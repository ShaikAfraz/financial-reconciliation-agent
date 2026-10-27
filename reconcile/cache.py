"""
LLM Call Cache for Deterministic Two-Sided Financial Reconciliation.
Keys cache entries by (model, prompt_hash) to ensure benchmark reruns are reproducible,
deterministic, and fast. Tracks token usage and call latency per execution.
"""

import json
import hashlib
import time
from pathlib import Path
from typing import Dict, Any, Optional

PROJECT_ROOT = Path(__file__).parent.parent.absolute()
DEFAULT_CACHE_FILE = PROJECT_ROOT / "data" / "cache" / "llm_cache.json"

class LLMCache:
    """
    Persistent key-value cache for LLM completions.
    Key: SHA-256(f"{model}:{prompt}")
    """

    def __init__(self, cache_file: Optional[Path] = None):
        self.cache_file = cache_file or DEFAULT_CACHE_FILE
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._hits = 0
        self._misses = 0
        self._load()

    def _load(self):
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    self._entries = json.load(f)
            except Exception:
                self._entries = {}
        else:
            self._entries = {}

    def _save(self):
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self._entries, f, indent=2)
        except Exception:
            pass

    @staticmethod
    def compute_key(model: str, prompt: str) -> str:
        content = f"{model}:{prompt}".encode("utf-8")
        return hashlib.sha256(content).hexdigest()

    def get(self, model: str, prompt: str) -> Optional[Dict[str, Any]]:
        key = self.compute_key(model, prompt)
        entry = self._entries.get(key)
        if entry is not None:
            self._hits += 1
            return entry
        self._misses += 1
        return None

    def set(
        self,
        model: str,
        prompt: str,
        response_text: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: float = 0.0
    ) -> Dict[str, Any]:
        key = self.compute_key(model, prompt)
        total_tokens = prompt_tokens + completion_tokens
        entry = {
            "model": model,
            "prompt_hash": key,
            "response_text": response_text,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "latency_ms": round(latency_ms, 2),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
        self._entries[key] = entry
        self._save()
        return entry

    def clear(self):
        self._entries.clear()
        if self.cache_file.exists():
            try:
                self.cache_file.unlink()
            except Exception:
                pass

    def stats(self) -> Dict[str, Any]:
        return {
            "hits": self._hits,
            "misses": self._misses,
            "total_queries": self._hits + self._misses,
            "cached_entries": len(self._entries)
        }

# Global cache singleton
_default_cache: Optional[LLMCache] = None

def get_llm_cache() -> LLMCache:
    global _default_cache
    if _default_cache is None:
        _default_cache = LLMCache()
    return _default_cache
