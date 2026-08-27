from __future__ import annotations

import json
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import redis as redis_lib
from redis.exceptions import RedisError

from reliability_lab.budget import CostBudget
from reliability_lab.cache import ResponseCache, SharedRedisCache
from reliability_lab.circuit_breaker import CircuitBreaker, CircuitBreakerLike
from reliability_lab.config import LabConfig, ScenarioConfig
from reliability_lab.gateway import ReliabilityGateway
from reliability_lab.metrics import RunMetrics
from reliability_lab.providers import FakeLLMProvider
from reliability_lab.redis_circuit_breaker import SharedRedisCircuitBreaker


def load_queries(path: str | Path = "data/sample_queries.jsonl") -> list[str]:
    queries: list[str] = []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        queries.append(json.loads(line)["query"])
    return queries


def _local_breakers(config: LabConfig) -> dict[str, CircuitBreakerLike]:
    return {
        provider_config.name: CircuitBreaker(
            name=provider_config.name,
            failure_threshold=config.circuit_breaker.failure_threshold,
            reset_timeout_seconds=config.circuit_breaker.reset_timeout_seconds,
            success_threshold=config.circuit_breaker.success_threshold,
        )
        for provider_config in config.providers
    }


def _build_breakers(config: LabConfig) -> dict[str, CircuitBreakerLike]:
    if config.circuit_breaker.backend != "redis":
        return _local_breakers(config)

    distributed: dict[str, CircuitBreakerLike] = {}
    opened_clients: list[SharedRedisCircuitBreaker] = []
    try:
        for provider_config in config.providers:
            breaker = SharedRedisCircuitBreaker(
                name=provider_config.name,
                failure_threshold=config.circuit_breaker.failure_threshold,
                reset_timeout_seconds=config.circuit_breaker.reset_timeout_seconds,
                success_threshold=config.circuit_breaker.success_threshold,
                redis_url=config.circuit_breaker.redis_url,
            )
            if not breaker.ping():
                breaker.close()
                raise RedisError("Redis circuit-breaker backend is unavailable")
            distributed[provider_config.name] = breaker
            opened_clients.append(breaker)
    except RedisError:
        for breaker in opened_clients:
            breaker.close()
        return _local_breakers(config)
    return distributed


def build_gateway(
    config: LabConfig, provider_overrides: dict[str, float] | None = None
) -> ReliabilityGateway:
    providers: list[FakeLLMProvider] = []
    for provider_config in config.providers:
        fail_rate = (
            provider_overrides.get(provider_config.name, provider_config.fail_rate)
            if provider_overrides
            else provider_config.fail_rate
        )
        providers.append(
            FakeLLMProvider(
                provider_config.name,
                fail_rate,
                provider_config.base_latency_ms,
                provider_config.cost_per_1k_tokens,
            )
        )

    breakers = _build_breakers(config)

    cache: ResponseCache | SharedRedisCache | None = None
    if config.cache.enabled:
        if config.cache.backend == "redis":
            redis_cache = SharedRedisCache(
                config.cache.redis_url,
                config.cache.ttl_seconds,
                config.cache.similarity_threshold,
            )
            cache = (
                redis_cache
                if redis_cache.ping()
                else ResponseCache(
                    config.cache.ttl_seconds,
                    config.cache.similarity_threshold,
                )
            )
        else:
            cache = ResponseCache(config.cache.ttl_seconds, config.cache.similarity_threshold)

    budget = (
        CostBudget(config.budget.limit, config.budget.switch_ratio)
        if config.budget.enabled
        else None
    )
    return ReliabilityGateway(providers, breakers, cache=cache, budget=budget)


def collect_redis_evidence(
    redis_url: str,
    ttl_seconds: int,
    similarity_threshold: float,
) -> dict[str, object]:
    """Capture reproducible proof that Redis cache state is shared across instances."""
    prefix = "rl:cache:evidence:"
    writer = SharedRedisCache(redis_url, ttl_seconds, similarity_threshold, prefix=prefix)
    reader = SharedRedisCache(redis_url, ttl_seconds, similarity_threshold, prefix=prefix)
    try:
        if not writer.ping():
            return {
                "available": False,
                "shared_response": None,
                "score": 0.0,
                "keys": [],
                "ttls_seconds": {},
            }

        writer.flush()
        query = "Day 25 Redis shared cache evidence"
        response = "shared cache evidence response"
        writer.set(query, response, {"purpose": "grading_evidence"})
        shared_response, score = reader.get(query)

        client: Any = redis_lib.Redis.from_url(redis_url, decode_responses=True)
        try:
            keys = sorted(str(key) for key in client.scan_iter(f"{prefix}*"))
            ttls = {key: int(client.ttl(key)) for key in keys}
        finally:
            client.close()

        return {
            "available": True,
            "shared_response": shared_response,
            "score": score,
            "keys": keys,
            "ttls_seconds": ttls,
        }
    finally:
        reader.close()
        writer.close()


def calculate_recovery_time_ms(gateway: ReliabilityGateway) -> float | None:
    recoveries_ms: list[float] = []
    for breaker in gateway.breakers.values():
        opened_ts: float | None = None
        for transition in breaker.transition_log:
            to_state = transition.get("to")
            timestamp = transition.get("ts")
            if not isinstance(timestamp, float):
                continue
            if to_state == "open":
                opened_ts = timestamp
            elif to_state == "closed" and opened_ts is not None:
                recoveries_ms.append((timestamp - opened_ts) * 1000.0)
                opened_ts = None
    if not recoveries_ms:
        return None
    return sum(recoveries_ms) / len(recoveries_ms)


def _record_result(
    metrics: RunMetrics, route: str, cache_hit: bool, latency_ms: float, cost: float
) -> None:
    metrics.total_requests += 1
    metrics.estimated_cost += cost
    if cache_hit:
        metrics.cache_hits += 1
        metrics.estimated_cost_saved += 0.001
    if route == "fallback":
        metrics.fallback_successes += 1
        metrics.successful_requests += 1
    elif route == "static_fallback":
        metrics.static_fallbacks += 1
        metrics.failed_requests += 1
    else:
        metrics.successful_requests += 1
    if latency_ms > 0:
        metrics.latencies_ms.append(latency_ms)


def run_scenario(config: LabConfig, queries: list[str], scenario: ScenarioConfig) -> RunMetrics:
    gateway = build_gateway(config, scenario.provider_overrides or None)
    metrics = RunMetrics()
    for _ in range(config.load_test.requests):
        prompt = random.choice(queries)
        result = gateway.complete(prompt)
        _record_result(
            metrics,
            result.route,
            result.cache_hit,
            result.latency_ms,
            result.estimated_cost,
        )

    metrics.circuit_open_count = sum(
        1
        for breaker in gateway.breakers.values()
        for transition in breaker.transition_log
        if transition.get("to") == "open"
    )
    metrics.recovery_time_ms = calculate_recovery_time_ms(gateway)
    return metrics


def run_scenario_concurrent(
    config: LabConfig,
    queries: list[str],
    scenario: ScenarioConfig,
    workers: int = 8,
) -> RunMetrics:
    """Bonus: run a scenario concurrently while sharing one gateway instance."""
    gateway = build_gateway(config, scenario.provider_overrides or None)
    prompts = [random.choice(queries) for _ in range(config.load_test.requests)]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(gateway.complete, prompts))

    metrics = RunMetrics()
    for result in results:
        _record_result(
            metrics,
            result.route,
            result.cache_hit,
            result.latency_ms,
            result.estimated_cost,
        )
    metrics.circuit_open_count = sum(
        1
        for breaker in gateway.breakers.values()
        for transition in breaker.transition_log
        if transition.get("to") == "open"
    )
    metrics.recovery_time_ms = calculate_recovery_time_ms(gateway)
    return metrics


def _scenario_passed(scenario: ScenarioConfig, metrics: RunMetrics) -> bool:
    if scenario.name == "primary_timeout_100":
        return metrics.availability >= 0.99 and metrics.fallback_success_rate >= 0.95
    if scenario.name == "all_healthy":
        return metrics.availability >= 0.99
    if scenario.name == "primary_flaky_50":
        return metrics.availability >= 0.95
    return metrics.successful_requests > 0


def run_simulation(config: LabConfig, queries: list[str]) -> RunMetrics:
    if not config.scenarios:
        default_scenario = ScenarioConfig(name="default", description="baseline run")
        metrics = run_scenario(config, queries, default_scenario)
        metrics.scenarios = {
            "default": "pass" if _scenario_passed(default_scenario, metrics) else "fail"
        }
        return metrics

    combined = RunMetrics()
    recovery_samples: list[float] = []
    for scenario in config.scenarios:
        result = run_scenario(config, queries, scenario)
        combined.scenarios[scenario.name] = (
            "pass" if _scenario_passed(scenario, result) else "fail"
        )
        combined.total_requests += result.total_requests
        combined.successful_requests += result.successful_requests
        combined.failed_requests += result.failed_requests
        combined.fallback_successes += result.fallback_successes
        combined.static_fallbacks += result.static_fallbacks
        combined.cache_hits += result.cache_hits
        combined.circuit_open_count += result.circuit_open_count
        combined.estimated_cost += result.estimated_cost
        combined.estimated_cost_saved += result.estimated_cost_saved
        combined.latencies_ms.extend(result.latencies_ms)
        if result.recovery_time_ms is not None:
            recovery_samples.append(result.recovery_time_ms)

    if recovery_samples:
        combined.recovery_time_ms = sum(recovery_samples) / len(recovery_samples)
    return combined
