from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from typing import Any, TypeVar

from redis.exceptions import RedisError

from reliability_lab.circuit_breaker import CircuitOpenError, CircuitState

T = TypeVar("T")
TransitionRecord = dict[str, str | float]


class SharedRedisCircuitBreaker:
    """Circuit breaker whose state is shared by independent Redis clients."""

    def __init__(
        self,
        name: str,
        failure_threshold: int,
        reset_timeout_seconds: float,
        success_threshold: int = 1,
        redis_url: str = "redis://localhost:6379/0",
        prefix: str = "rl:circuit:",
    ) -> None:
        import redis as redis_lib

        if failure_threshold <= 0:
            raise ValueError("failure_threshold must be positive")
        if reset_timeout_seconds <= 0:
            raise ValueError("reset_timeout_seconds must be positive")
        if success_threshold <= 0:
            raise ValueError("success_threshold must be positive")

        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout_seconds = reset_timeout_seconds
        self.success_threshold = success_threshold
        self.prefix = prefix
        self._redis: Any = redis_lib.Redis.from_url(redis_url, decode_responses=True)
        self._token = uuid.uuid4().hex
        self._base = f"{prefix}{name}:"
        self._counter_ttl_seconds = max(60, int(reset_timeout_seconds * 10) + 1)
        self._probe_lease_ms = max(1000, int(reset_timeout_seconds * 2000))
        self._redis.set(self._key("state"), CircuitState.CLOSED.value, nx=True)

    def _key(self, suffix: str) -> str:
        return f"{self._base}{suffix}"

    @property
    def state(self) -> CircuitState:
        raw = self._redis.get(self._key("state"))
        if raw is None:
            self._redis.set(self._key("state"), CircuitState.CLOSED.value, nx=True)
            raw = self._redis.get(self._key("state"))
        try:
            return CircuitState(str(raw))
        except ValueError:
            return CircuitState.CLOSED

    @property
    def transition_log(self) -> list[TransitionRecord]:
        records: list[TransitionRecord] = []
        for raw in self._redis.lrange(self._key("transitions"), 0, -1):
            parsed: object = json.loads(str(raw))
            if not isinstance(parsed, dict):
                continue
            from_state = parsed.get("from")
            to_state = parsed.get("to")
            reason = parsed.get("reason")
            timestamp = parsed.get("ts")
            if (
                isinstance(from_state, str)
                and isinstance(to_state, str)
                and isinstance(reason, str)
                and isinstance(timestamp, (int, float))
            ):
                records.append(
                    {
                        "from": from_state,
                        "to": to_state,
                        "reason": reason,
                        "ts": float(timestamp),
                    }
                )
        return records

    def ping(self) -> bool:
        try:
            return bool(self._redis.ping())
        except RedisError:
            return False

    def allow_request(self) -> bool:
        current = self.state
        if current == CircuitState.CLOSED:
            return True

        lease_key = self._key("probe_lease")
        if current == CircuitState.HALF_OPEN:
            owner = self._redis.get(lease_key)
            if owner == self._token:
                return True
            if owner is not None:
                return False
            return bool(
                self._redis.set(
                    lease_key,
                    self._token,
                    nx=True,
                    px=self._probe_lease_ms,
                )
            )

        opened_raw = self._redis.get(self._key("opened_at"))
        if opened_raw is None:
            return False
        try:
            opened_at = float(opened_raw)
        except (TypeError, ValueError):
            return False
        if time.time() - opened_at < self.reset_timeout_seconds:
            return False

        acquired = bool(
            self._redis.set(
                lease_key,
                self._token,
                nx=True,
                px=self._probe_lease_ms,
            )
        )
        if not acquired:
            return False

        if self.state != CircuitState.OPEN:
            self._release_probe_lease()
            return self.state == CircuitState.CLOSED

        self._transition(CircuitState.HALF_OPEN, "reset_timeout_elapsed")
        return True

    def call(self, fn: Callable[..., T], *args: object, **kwargs: object) -> T:
        if not self.allow_request():
            raise CircuitOpenError(f"circuit {self.name} is open")
        try:
            result = fn(*args, **kwargs)
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result

    def record_failure(self) -> None:
        current = self.state
        self._redis.delete(self._key("successes"))
        failure_count = int(self._redis.incr(self._key("failures")))
        self._redis.expire(self._key("failures"), self._counter_ttl_seconds)

        if current == CircuitState.HALF_OPEN:
            self._redis.set(self._key("opened_at"), str(time.time()))
            self._transition(CircuitState.OPEN, "probe_failure")
            self._release_probe_lease()
            return

        if current == CircuitState.CLOSED and failure_count >= self.failure_threshold:
            self._redis.set(self._key("opened_at"), str(time.time()))
            self._transition(CircuitState.OPEN, "failure_threshold_reached")

    def record_success(self) -> None:
        self._redis.delete(self._key("failures"))
        if self.state != CircuitState.HALF_OPEN:
            self._redis.delete(self._key("successes"))
            return

        success_count = int(self._redis.incr(self._key("successes")))
        self._redis.expire(self._key("successes"), self._counter_ttl_seconds)
        if success_count < self.success_threshold:
            return

        self._transition(CircuitState.CLOSED, "probe_success")
        self._redis.delete(self._key("successes"), self._key("opened_at"))
        self._release_probe_lease()

    def flush(self) -> None:
        for key in self._redis.scan_iter(f"{self._base}*"):
            self._redis.delete(key)
        self._redis.set(self._key("state"), CircuitState.CLOSED.value, nx=True)

    def close(self) -> None:
        self._redis.close()

    def _transition(self, new_state: CircuitState, reason: str) -> None:
        state_key = self._key("state")
        previous = self._redis.set(state_key, new_state.value, get=True)
        previous_state = str(previous) if previous is not None else CircuitState.CLOSED.value
        if previous_state == new_state.value:
            return
        record: TransitionRecord = {
            "from": previous_state,
            "to": new_state.value,
            "reason": reason,
            "ts": time.time(),
        }
        self._redis.rpush(self._key("transitions"), json.dumps(record, sort_keys=True))

    def _release_probe_lease(self) -> None:
        script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('del', KEYS[1])
        end
        return 0
        """
        self._redis.eval(script, 1, self._key("probe_lease"), self._token)
