"""Integration tests for Redis-shared circuit-breaker state."""
from __future__ import annotations

import importlib
import importlib.util
import time
import uuid
from typing import Any

import pytest
from redis.exceptions import RedisError

from reliability_lab.circuit_breaker import CircuitState

REDIS_URL = "redis://localhost:6379/0"


def _redis_available() -> bool:
    try:
        import redis as redis_lib

        client = redis_lib.Redis.from_url(REDIS_URL)
        client.ping()
        client.close()
        return True
    except RedisError:
        return False


pytestmark = pytest.mark.skipif(
    not _redis_available(),
    reason="Redis not running — start with: make docker-up",
)


def _breaker_type() -> type[Any]:
    spec = importlib.util.find_spec("reliability_lab.redis_circuit_breaker")
    assert spec is not None, "Redis shared circuit breaker module is missing"
    module = importlib.import_module("reliability_lab.redis_circuit_breaker")
    breaker_type = getattr(module, "SharedRedisCircuitBreaker", None)
    assert breaker_type is not None, "SharedRedisCircuitBreaker is missing"
    return breaker_type


def _pair() -> tuple[Any, Any]:
    breaker_type = _breaker_type()
    prefix = f"rl:test:circuit:{uuid.uuid4().hex}:"
    first = breaker_type(
        name="primary",
        failure_threshold=2,
        reset_timeout_seconds=0.05,
        success_threshold=1,
        redis_url=REDIS_URL,
        prefix=prefix,
    )
    second = breaker_type(
        name="primary",
        failure_threshold=2,
        reset_timeout_seconds=0.05,
        success_threshold=1,
        redis_url=REDIS_URL,
        prefix=prefix,
    )
    first.flush()
    return first, second


def _close_pair(first: Any, second: Any) -> None:
    first.flush()
    first.close()
    second.close()


def test_open_and_close_state_is_shared_across_instances() -> None:
    first, second = _pair()
    try:
        first.record_failure()
        first.record_failure()
        assert first.state == CircuitState.OPEN
        assert second.state == CircuitState.OPEN
        assert second.allow_request() is False

        time.sleep(0.06)
        assert second.allow_request() is True
        assert second.state == CircuitState.HALF_OPEN
        second.record_success()

        assert second.state == CircuitState.CLOSED
        assert first.state == CircuitState.CLOSED
    finally:
        _close_pair(first, second)


def test_only_one_instance_can_own_half_open_probe() -> None:
    first, second = _pair()
    try:
        first.record_failure()
        first.record_failure()
        time.sleep(0.06)

        assert first.allow_request() is True
        assert first.state == CircuitState.HALF_OPEN
        assert second.allow_request() is False

        first.record_failure()
        assert second.state == CircuitState.OPEN
    finally:
        _close_pair(first, second)


def test_transition_log_is_shared_and_preserves_reasons() -> None:
    first, second = _pair()
    try:
        first.record_failure()
        first.record_failure()
        time.sleep(0.06)
        assert second.allow_request() is True
        second.record_success()

        reasons = [entry["reason"] for entry in first.transition_log]
        assert "failure_threshold_reached" in reasons
        assert "reset_timeout_elapsed" in reasons
        assert "probe_success" in reasons
    finally:
        _close_pair(first, second)
