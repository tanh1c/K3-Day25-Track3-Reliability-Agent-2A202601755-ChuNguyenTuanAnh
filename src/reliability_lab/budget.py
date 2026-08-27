from __future__ import annotations

import threading

from reliability_lab.providers import FakeLLMProvider


class CostBudget:
    """Thread-safe cost budget that switches to cheaper providers near exhaustion."""

    def __init__(self, limit: float, switch_ratio: float = 0.8, spent: float = 0.0) -> None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if not 0 < switch_ratio < 1:
            raise ValueError("switch_ratio must be between 0 and 1")
        if spent < 0:
            raise ValueError("spent cannot be negative")
        self.limit = float(limit)
        self.switch_ratio = float(switch_ratio)
        self._spent = float(spent)
        self._lock = threading.Lock()

    @property
    def spent(self) -> float:
        with self._lock:
            return self._spent

    @property
    def usage_ratio(self) -> float:
        with self._lock:
            return self._spent / self.limit

    def is_exhausted(self) -> bool:
        return self.usage_ratio >= 1.0

    def record(self, cost: float) -> None:
        if cost < 0:
            raise ValueError("cost cannot be negative")
        with self._lock:
            self._spent += cost

    def ordered(self, providers: list[FakeLLMProvider]) -> list[FakeLLMProvider]:
        """Return configured order below threshold, cheapest-first after threshold."""
        if self.usage_ratio < self.switch_ratio:
            return list(providers)
        return sorted(providers, key=lambda provider: provider.cost_per_1k_tokens)
