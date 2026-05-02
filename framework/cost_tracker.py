"""
CostTracker: Intercepts Gemini API calls to accumulate token usage and estimate USD cost.

Usage
-----
tracker = CostTracker()
tracked_client = tracker.wrap_client(raw_gemini_client)

# Pass tracked_client to agents instead of the raw client.
# After the run:
print(tracker.summary())
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, List, Optional


# Gemini 2.5 Flash pricing (USD per 1 million tokens, May 2025)
_PRICE_INPUT_PER_1M  = 0.10   # conservative mid-range
_PRICE_OUTPUT_PER_1M = 0.40


@dataclass
class _CallRecord:
    model: str
    input_tokens: int
    output_tokens: int


class CostTracker:
    """
    Thread-safe accumulator for Gemini token usage.

    Attributes
    ----------
    input_tokens  : total prompt tokens across all calls
    output_tokens : total candidate tokens across all calls
    call_count    : number of generate_content calls recorded
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.call_count: int = 0
        self._calls: List[_CallRecord] = []

    # ------------------------------------------------------------------

    def record(self, response: Any, model: str = "unknown") -> None:
        """Extract usage_metadata from a Gemini response and accumulate."""
        meta = getattr(response, "usage_metadata", None)
        if meta is None:
            return

        inp = getattr(meta, "prompt_token_count", 0) or 0
        out = getattr(meta, "candidates_token_count", 0) or 0

        with self._lock:
            self.input_tokens  += inp
            self.output_tokens += out
            self.call_count    += 1
            self._calls.append(_CallRecord(model=model, input_tokens=inp, output_tokens=out))

    # ------------------------------------------------------------------

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def estimated_usd(self) -> float:
        return (
            self.input_tokens  / 1_000_000 * _PRICE_INPUT_PER_1M
            + self.output_tokens / 1_000_000 * _PRICE_OUTPUT_PER_1M
        )

    def summary(self) -> dict:
        """Return a serialisable snapshot of accumulated usage."""
        return {
            "input_tokens":   self.input_tokens,
            "output_tokens":  self.output_tokens,
            "total_tokens":   self.total_tokens,
            "call_count":     self.call_count,
            "estimated_usd":  round(self.estimated_usd, 6),
        }

    def reset(self) -> None:
        with self._lock:
            self.input_tokens = 0
            self.output_tokens = 0
            self.call_count = 0
            self._calls.clear()

    # ------------------------------------------------------------------

    def wrap_client(self, client: Any) -> "_TrackedClient":
        """Return a transparent proxy around *client* that records every call."""
        return _TrackedClient(client, self)


# ---------------------------------------------------------------------------
# Proxy helpers
# ---------------------------------------------------------------------------

class _TrackedModels:
    """Proxy for `client.models` that intercepts `generate_content`."""

    def __init__(self, models: Any, tracker: CostTracker) -> None:
        self._models  = models
        self._tracker = tracker

    def generate_content(self, *args: Any, **kwargs: Any) -> Any:
        # `model` may arrive as the first positional arg or as a kwarg
        model_name = args[0] if args else kwargs.get("model", "unknown")
        response = self._models.generate_content(*args, **kwargs)
        self._tracker.record(response, model=model_name)
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._models, name)


class _TrackedClient:
    """Transparent proxy around a genai.Client that accumulates token usage."""

    def __init__(self, client: Any, tracker: CostTracker) -> None:
        self._client  = client
        self._tracker = tracker
        # Expose a wrapped .models namespace
        self.models = _TrackedModels(client.models, tracker)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)
