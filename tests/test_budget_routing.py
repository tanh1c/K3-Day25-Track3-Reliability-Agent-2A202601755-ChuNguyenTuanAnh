"""Behavior tests for opt-in cost-aware provider routing."""
from __future__ import annotations

import importlib
import importlib.util
from typing import Any

from reliability_lab.cache import ResponseCache
from reliability_lab.circuit_breaker import CircuitBreaker
from reliability_lab.gateway import ReliabilityGateway
from reliability_lab.providers import FakeLLMProvider, ProviderResponse


def _budget_type() -> type[Any]:
    spec = importlib.util.find_spec("reliability_lab.budget")
    assert spec is not None, "CostBudget module is missing"
    module = importlib.import_module("reliability_lab.budget")
    budget_type = getattr(module, "CostBudget", None)
    assert budget_type is not None, "CostBudget is missing"
    return budget_type


def _providers() -> list[FakeLLMProvider]:
    return [
        FakeLLMProvider("primary", 0.0, 1, 0.01),
        FakeLLMProvider("backup", 0.0, 1, 0.006),
    ]


def _breakers(providers: list[FakeLLMProvider]) -> dict[str, CircuitBreaker]:
    return {
        provider.name: CircuitBreaker(
            name=provider.name,
            failure_threshold=3,
            reset_timeout_seconds=1.0,
            success_threshold=1,
        )
        for provider in providers
    }


def _gateway(spent: float, cache: ResponseCache | None = None) -> ReliabilityGateway:
    budget_type = _budget_type()
    providers = _providers()
    budget = budget_type(limit=1.0, switch_ratio=0.8, spent=spent)
    return ReliabilityGateway(providers, _breakers(providers), cache=cache, budget=budget)


def test_below_80_percent_uses_primary() -> None:
    response = _gateway(0.79).complete("budget routing below threshold")
    assert response.provider == "primary"
    assert response.route == "primary"


def test_at_80_percent_uses_cheapest_provider_first() -> None:
    response = _gateway(0.80).complete("budget routing at threshold")
    assert response.provider == "backup"
    assert response.route == "fallback"


class NeverCalledProvider(FakeLLMProvider):
    def complete(self, prompt: str) -> ProviderResponse:
        raise AssertionError(f"provider must not be called for: {prompt}")


def test_at_100_percent_returns_static_fallback_without_provider_call() -> None:
    budget_type = _budget_type()
    provider = NeverCalledProvider("primary", 0.0, 1, 0.01)
    budget = budget_type(limit=1.0, switch_ratio=0.8, spent=1.0)
    gateway = ReliabilityGateway(
        [provider],
        _breakers([provider]),
        budget=budget,
    )

    response = gateway.complete("budget exhausted")

    assert response.route == "static_fallback"
    assert response.provider is None
    assert response.error == "budget_exhausted"
    assert response.estimated_cost == 0.0


def test_cache_hit_bypasses_exhausted_budget() -> None:
    cache = ResponseCache(ttl_seconds=60, similarity_threshold=0.9)
    cache.set("cached while budget exhausted", "cached answer")

    response = _gateway(1.0, cache=cache).complete("cached while budget exhausted")

    assert response.cache_hit is True
    assert response.text == "cached answer"
    assert response.provider is None
    assert response.estimated_cost == 0.0
