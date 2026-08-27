"""Evidence contract for every newly implemented stretch goal."""
from __future__ import annotations

import pytest
import redis as redis_lib
from redis.exceptions import RedisError

from reliability_lab.chaos import (
    collect_cost_routing_evidence,
    collect_distributed_breaker_evidence,
)

REDIS_URL = "redis://localhost:6379/0"


def _redis_available() -> bool:
    client = redis_lib.Redis.from_url(REDIS_URL)
    try:
        return bool(client.ping())
    except RedisError:
        return False
    finally:
        client.close()


@pytest.mark.skipif(not _redis_available(), reason="Redis required for distributed evidence")
def test_distributed_breaker_evidence_proves_cross_instance_recovery() -> None:
    evidence = collect_distributed_breaker_evidence(REDIS_URL)

    assert evidence["available"] is True
    assert evidence["opened_by_instance_a"] is True
    assert evidence["observed_open_by_instance_b"] is True
    assert evidence["first_probe_acquired"] is True
    assert evidence["second_probe_blocked"] is True
    assert evidence["observed_closed_by_instance_b"] is True
    assert evidence["instance_a_final_state"] == "closed"
    assert evidence["instance_b_final_state"] == "closed"
    reasons = evidence["transition_reasons"]
    assert isinstance(reasons, list)
    assert "failure_threshold_reached" in reasons
    assert "reset_timeout_elapsed" in reasons
    assert "probe_success" in reasons


def test_cost_routing_evidence_proves_80_and_100_percent_policy() -> None:
    evidence = collect_cost_routing_evidence()

    assert evidence["switch_ratio"] == 0.8
    below = evidence["below_80_percent"]
    at_switch = evidence["at_80_percent"]
    exhausted = evidence["at_100_percent"]
    assert isinstance(below, dict)
    assert isinstance(at_switch, dict)
    assert isinstance(exhausted, dict)
    assert below["provider"] == "primary"
    assert below["route"] == "primary"
    assert at_switch["provider"] == "backup"
    assert at_switch["route"] == "fallback"
    assert exhausted["provider"] is None
    assert exhausted["route"] == "static_fallback"
    assert exhausted["error"] == "budget_exhausted"
    assert exhausted["estimated_cost"] == 0.0
