from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from reliability_lab.circuit_breaker import CircuitBreaker, CircuitState


@given(st.integers(min_value=1, max_value=20))
def test_closed_breaker_opens_exactly_at_failure_threshold(threshold: int) -> None:
    breaker = CircuitBreaker("property", failure_threshold=threshold, reset_timeout_seconds=1)
    for _ in range(threshold - 1):
        breaker.record_failure()
        assert breaker.state == CircuitState.CLOSED
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN
    assert breaker.transition_log[-1]["reason"] == "failure_threshold_reached"


@given(
    st.integers(min_value=1, max_value=10),
    st.integers(min_value=1, max_value=10),
)
def test_success_resets_any_partial_failure_streak(threshold: int, partial: int) -> None:
    breaker = CircuitBreaker(
        "property",
        failure_threshold=threshold + partial + 1,
        reset_timeout_seconds=1,
    )
    for _ in range(partial):
        breaker.record_failure()
    breaker.record_success()
    assert breaker.failure_count == 0
    assert breaker.state == CircuitState.CLOSED


@given(st.integers(min_value=1, max_value=10))
def test_half_open_failure_always_uses_probe_failure(success_threshold: int) -> None:
    breaker = CircuitBreaker(
        "property",
        failure_threshold=100,
        reset_timeout_seconds=1,
        success_threshold=success_threshold,
        state=CircuitState.HALF_OPEN,
    )
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN
    assert breaker.transition_log[-1]["reason"] == "probe_failure"
