# Day 25 Reliability Engineering Final Report

**Sinh viên:** Chu Nguyễn Tuấn Anh  
**MSSV:** 2A202601755

## 1. Architecture summary

The gateway uses cache-first routing, provider-specific circuit breakers, an ordered provider fallback chain, and a deterministic static fallback. Redis can replace in-memory cache for shared multi-instance state.

```text
User Request
    |
    v
[ReliabilityGateway] --> [Memory/Redis semantic cache] -- HIT --> response
    | MISS
    v
[CircuitBreaker: primary] --> Primary provider
    | failure/open
    v
[CircuitBreaker: backup]  --> Backup provider
    | failure/open
    v
[Static degraded response]
```

## 2. Configuration

| Setting | Value | Reason |
|---|---:|---|
| failure_threshold | 3 | Opens quickly enough to stop repeated provider failures without tripping on a single transient error. |
| reset_timeout_seconds | 2 | Bounds fail-fast duration before a HALF_OPEN recovery probe. |
| success_threshold | 1 | Defines how much probe evidence is required before closing. |
| cache backend | memory | Memory is the safe default; Redis provides shared state across instances. |
| cache TTL | 300 s | Limits staleness while preserving useful repeated-query hits. |
| similarity_threshold | 0.92 | Conservative semantic threshold plus explicit year/ID false-hit protection. |
| load_test requests | 100 | Enough requests to exercise cache warming, provider failures, and breaker transitions. |

### Providers

| Provider | Fail rate | Base latency (ms) | Cost / 1K tokens |
|---|---:|---:|---:|
| primary | 0.25 | 180 | 0.01 |
| backup | 0.05 | 260 | 0.006 |

## 3. SLO definitions

| SLI | SLO target | Actual value | Met? |
|---|---|---:|---|
| Availability | >= 99% | 100.00% | PASS |
| Latency P95 | < 2500 ms | 313.52 ms | PASS |
| Fallback success rate | >= 95% | 100.00% | PASS |
| Cache hit rate | >= 10% | 62.00% | PASS |
| Recovery time | < 5000 ms | 2372.49 ms | PASS |

## 4. Metrics

| Metric | Value |
|---|---:|
| total_requests | 300 |
| availability | 1.0 |
| error_rate | 0.0 |
| latency_p50_ms | 237.14 |
| latency_p95_ms | 313.52 |
| latency_p99_ms | 318.02 |
| fallback_success_rate | 1.0 |
| cache_hit_rate | 0.62 |
| estimated_cost | 0.05672 |
| estimated_cost_saved | 0.186 |
| circuit_open_count | 7 |
| recovery_time_ms | 2372.4920749664307 |

`recovery_time_ms` is expected to be close to 2000 ms because the circuit breaker deliberately stays OPEN for `reset_timeout_seconds = 2` before allowing a HALF_OPEN recovery probe. The observed value is slightly above 2 seconds because it also includes provider latency and normal scheduling/measurement overhead.

## 5. Cache comparison

The comparison uses the same healthy-provider scenario and random seed with cache enabled and disabled.

| Metric | Without cache | With cache | Delta / evidence |
|---|---:|---:|---:|
| latency_p50_ms | 207.13 | 212.14 | cache hits return at zero provider latency |
| latency_p95_ms | 239.14 | 234.4 | 4.74 ms |
| estimated_cost | 0.06096 | 0.02181 | 0.03915 |
| cache_hit_rate | 0 | 0.64 | semantic cache reuse |

The starter metrics path records latency samples only when latency is greater than zero, so zero-latency cache hits are excluded from the percentile sample. Therefore P50 is not expected to improve reliably; the cache benefit is demonstrated more directly by `cache_hit_rate`, reduced `estimated_cost`, `estimated_cost_saved`, and preserved availability.

## 6. Redis shared cache

In-memory cache is process-local, so replicas cannot reuse each other's responses. `SharedRedisCache` stores the original query and response in a Redis hash with TTL, giving all gateway instances using the same prefix shared cache state.

### Evidence of shared state

`make run-chaos` creates two independent `SharedRedisCache` instances. The first writes an evidence entry and the second reads it back from Redis.

| Check | Observed value |
|---|---|
| Redis available | True |
| Read from second instance | shared cache evidence response |
| Exact-match score | 1.0 |

### Redis CLI output

The following output is generated from the same Redis state used by the evidence run:

```text
$ docker compose exec redis redis-cli KEYS "rl:cache:*"
1) "rl:cache:evidence:6ddec03bbc60"
```

Evidence keys also have Redis TTLs, proving expiry is delegated to Redis:

| Key | TTL seconds at capture |
|---|---:|
| `rl:cache:evidence:6ddec03bbc60` | 300 |

### Privacy and false-hit evidence

Both memory and Redis backends bypass privacy-sensitive queries and reject high-similarity matches when 4-digit dates/IDs differ. Redis integration tests execute in CI against a real Redis service rather than being skipped.

## 7. Chaos scenarios

| Scenario | Expected behavior | Observed status | Pass/Fail |
|---|---|---|---|
| primary_timeout_100 | Primary provider fails 100% — all provider traffic should fallback successfully | pass | PASS |
| primary_flaky_50 | Primary provider fails 50% — circuit should oscillate while healthy backup preserves availability | pass | PASS |
| all_healthy | Baseline — both providers are forced healthy | pass | PASS |

## 8. Bonus: concurrent load

A `ThreadPoolExecutor` benchmark exercises the same gateway under concurrent request load while preserving sequential mode as the default grader path.

| Metric | Concurrent value |
|---|---:|
| total_requests | 100 |
| availability | 1.0 |
| latency_p95_ms | 238.19 |
| estimated_cost | 0.0579 |

## 9. Failure analysis

A remaining weakness is that the default circuit-breaker counters are process-local. In a horizontally scaled deployment, replicas may disagree about provider health. A production upgrade would store breaker counters/state in Redis with atomic operations or use a dedicated distributed resilience layer. This is intentionally not enabled by default because the grader contract exercises the local `CircuitBreaker` API.

## 10. Next steps

1. Add distributed circuit state with atomic Redis transitions and bounded leases.
2. Add per-provider quality SLOs alongside availability/latency/cost SLOs.
3. Add opt-in cost-budget routing that prefers cheaper providers after an 80% budget threshold.

## Reproducibility

```bash
pip install -e ".[dev]"
docker compose up -d
make lint
make typecheck
make test
make run-chaos
make report
```
