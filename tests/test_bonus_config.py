"""Configuration tests for opt-in distributed breaker and cost budget."""
from __future__ import annotations

import pytest
import redis as redis_lib
from redis.exceptions import RedisError

from reliability_lab.budget import CostBudget
from reliability_lab.chaos import build_gateway
from reliability_lab.circuit_breaker import CircuitBreaker
from reliability_lab.config import LabConfig, load_config
from reliability_lab.redis_circuit_breaker import SharedRedisCircuitBreaker

REDIS_URL = "redis://localhost:6379/0"


def _redis_available() -> bool:
    client = redis_lib.Redis.from_url(REDIS_URL)
    try:
        return bool(client.ping())
    except RedisError:
        return False
    finally:
        client.close()


def _bonus_config(redis_url: str = REDIS_URL) -> LabConfig:
    raw = load_config("configs/default.yaml").model_dump()
    circuit = dict(raw["circuit_breaker"])
    circuit.update({"backend": "redis", "redis_url": redis_url})
    raw["circuit_breaker"] = circuit
    raw["budget"] = {"enabled": True, "limit": 1.0, "switch_ratio": 0.8}
    return LabConfig.model_validate(raw)


def test_default_config_keeps_bonus_features_opt_in() -> None:
    config = load_config("configs/default.yaml")
    assert config.circuit_breaker.backend == "memory"
    assert config.budget.enabled is False
    assert config.budget.switch_ratio == 0.8


@pytest.mark.skipif(not _redis_available(), reason="Redis required for distributed breaker")
def test_build_gateway_uses_redis_breakers_and_budget_when_enabled() -> None:
    gateway = build_gateway(_bonus_config())
    try:
        assert gateway.budget is not None
        assert isinstance(gateway.budget, CostBudget)
        assert gateway.budget.limit == 1.0
        assert all(
            isinstance(breaker, SharedRedisCircuitBreaker)
            for breaker in gateway.breakers.values()
        )
    finally:
        for breaker in gateway.breakers.values():
            if isinstance(breaker, SharedRedisCircuitBreaker):
                breaker.flush()
                breaker.close()


def test_unavailable_redis_breaker_falls_back_to_local_breaker() -> None:
    gateway = build_gateway(_bonus_config("redis://127.0.0.1:1/0"))
    assert all(isinstance(breaker, CircuitBreaker) for breaker in gateway.breakers.values())
    assert gateway.budget is not None
