# Day 25 Reliability Engineering Final Report

**Sinh viên:** Chu Nguyễn Tuấn Anh  
**MSSV:** 2A202601755

## 1. Architecture summary

The gateway uses cache-first routing, provider-specific circuit breakers, ordered fallback, and deterministic static degradation. Core grading keeps the local circuit breaker and budget routing disabled by default. Stretch mode can use Redis-shared breaker state across replicas and an opt-in cost budget that moves traffic to the cheaper provider at 80% usage and blocks new paid calls at 100%.

```text
User Request
    |
    v
[ReliabilityGateway] --> [Memory/Redis semantic cache] -- HIT --> response
    | MISS
    v
[CostBudget: optional] -- >=100% --> static fallback
    | <100%
    v
[Local/Redis CircuitBreaker: primary] --> Primary provider
    | failure/open or >=80% cheaper-first policy
    v
[Local/Redis CircuitBreaker: backup]  --> Backup provider
    | failure/open
    v
[Static degraded response]
```

## 2. Configuration

| Setting | Value | Reason |
|---|---:|---|
| failure_threshold | 3 | Stops repeated provider failures after a bounded consecutive-failure streak. |
| reset_timeout_seconds | 2 | Bounds fail-fast duration before a HALF_OPEN recovery probe. |
| success_threshold | 1 | Defines probe evidence required before closing. |
| circuit backend | memory | Memory remains grader-safe default; Redis is opt-in for distributed shared state. |
| cache backend | memory | Memory is the safe default; Redis provides cross-instance cache reuse. |
| cache TTL | 300 s | Limits staleness while preserving repeated-query hits. |
| similarity_threshold | 0.92 | Conservative semantic threshold plus year/ID false-hit protection. |
| budget enabled | False | Disabled by default so core grader behavior is unchanged. |
| budget limit | 1.0 | Maximum opt-in paid-provider spend before deterministic cutoff. |
| budget switch ratio | 0.8 | At 80% usage the cheaper provider is attempted first. |
| load_test requests | 100 | Exercises cache warming, failures, breaker transitions, and fallback. |

### Providers

| Provider | Fail rate | Base latency (ms) | Cost / 1K tokens |
|---|---:|---:|---:|
| primary | 0.25 | 180 | 0.01 |
| backup | 0.05 | 260 | 0.006 |

## 3. SLO definitions

| SLI | SLO target | Actual value | Met? |
|---|---|---:|---|
| Availability | >= 99% | 100.00% | PASS |
| Latency P95 | < 2500 ms | 313.63 ms | PASS |
| Fallback success rate | >= 95% | 100.00% | PASS |
| Cache hit rate | >= 10% | 62.00% | PASS |
| Recovery time | < 5000 ms | 2374.94 ms | PASS |

## 4. Metrics

| Metric | Value |
|---|---:|
| total_requests | 300 |
| availability | 1.0 |
| error_rate | 0.0 |
| latency_p50_ms | 237.18 |
| latency_p95_ms | 313.63 |
| latency_p99_ms | 318.08 |
| fallback_success_rate | 1.0 |
| cache_hit_rate | 0.62 |
| estimated_cost | 0.05672 |
| estimated_cost_saved | 0.186 |
| circuit_open_count | 7 |
| recovery_time_ms | 2374.93634223938 |

`recovery_time_ms` is expected to be close to 2000 ms because the core circuit breaker deliberately stays OPEN for `reset_timeout_seconds = 2` before a HALF_OPEN recovery probe. The small amount above 2 seconds comes from provider latency plus scheduling/measurement overhead.

## 5. Cache comparison

The comparison uses the same healthy-provider scenario and random seed with cache enabled and disabled.

| Metric | Without cache | With cache | Delta / evidence |
|---|---:|---:|---:|
| latency_p50_ms | 207.14 | 212.15 | cache hits return at zero provider latency |
| latency_p95_ms | 239.15 | 234.4 | 4.74 ms |
| estimated_cost | 0.06096 | 0.02181 | 0.03915 |
| cache_hit_rate | 0 | 0.64 | semantic cache reuse |

The starter metrics path records latency only when latency is greater than zero, so zero-latency cache hits are excluded from percentile samples. Cache value is therefore demonstrated most directly by hit rate, lower cost, cost saved, and preserved availability rather than P50 alone.

## 6. Redis shared cache

`SharedRedisCache` stores the original query and response in Redis hashes with TTL so independent gateway processes can share cached responses.

### Evidence of shared state

| Check | Observed value |
|---|---|
| Redis available | True |
| Read from second instance | shared cache evidence response |
| Exact-match score | 1.0 |

### Redis CLI output

```text
$ docker compose exec redis redis-cli KEYS "rl:cache:*"
1) "rl:cache:evidence:6ddec03bbc60"
```

| Redis key | TTL seconds at capture |
|---|---:|
| `rl:cache:evidence:6ddec03bbc60` | 300 |

### Privacy and false-hit evidence

Memory and Redis cache backends both bypass privacy-sensitive queries and reject high-similarity matches when 4-digit dates/IDs differ. Redis integration tests run in CI against a real Redis container.

## 7. Chaos scenarios

| Scenario | Expected behavior | Observed status | Pass/Fail |
|---|---|---|---|
| primary_timeout_100 | Primary provider fails 100% — all provider traffic should fallback successfully | pass | PASS |
| primary_flaky_50 | Primary provider fails 50% — circuit should oscillate while healthy backup preserves availability | pass | PASS |
| all_healthy | Baseline — both providers are forced healthy | pass | PASS |

## 8. Stretch goals — complete bonus evidence

| Stretch goal | Status | Reproducible evidence |
|---|---|---|
| ThreadPoolExecutor concurrent load | PASS | 100 requests, availability=1.0 |
| Redis shared circuit-breaker state | PASS | Two independent breakers share OPEN/CLOSED state and one HALF_OPEN probe lease |
| Redis cache graceful degradation | PASS | Redis cache backend falls back to ResponseCache when ping fails; covered by tests |
| Cost-aware routing at 80%/100% | PASS | primary below 80%; cheaper backup at 80%; paid calls blocked at 100% |
| Hypothesis circuit-breaker fuzzing | PASS | Property tests exercise threshold, reset, and HALF_OPEN transition invariants |
| SLO table and validation | PASS | Measured availability, latency, fallback, cache-hit, and recovery SLOs |

### Concurrent load

| Metric | Concurrent value |
|---|---:|
| total_requests | 100 |
| availability | 1.0 |
| latency_p95_ms | 238.23 |
| estimated_cost | 0.0579 |

### Distributed Redis circuit breaker

Two separately constructed `SharedRedisCircuitBreaker` objects use the same Redis namespace. Failure counters use Redis `INCR` + expiry, and HALF_OPEN recovery is protected by a Redis `SET ... NX` lease so only one replica probes.

| Distributed check | Observed value |
|---|---|
| Redis available | True |
| Instance A opened circuit | True |
| Instance B observed OPEN | True |
| First HALF_OPEN probe acquired | True |
| Second concurrent probe blocked | True |
| Instance B observed recovery to CLOSED | True |
| Shared transition reasons | ['failure_threshold_reached', 'reset_timeout_elapsed', 'probe_success'] |

### Cost-aware 80%/100% routing

Cache lookup remains first because a cache hit costs no model budget. On a cache miss, configured order is preserved below 80%; from 80% to below 100% providers are attempted cheapest-first; at 100% no paid provider is called.

| Budget state | Provider | Route | Error |
|---|---|---|---|
| 79% used | primary | primary | n/a |
| 80% used | backup | fallback | n/a |
| 100% used | n/a | static_fallback | budget_exhausted |

## 9. Failure analysis

The stretch implementation deliberately remains opt-in so Redis failure cannot remove the core local breaker path. If the distributed breaker backend is configured but Redis is unavailable during gateway construction, the gateway falls back to local `CircuitBreaker` instances. The cost budget is thread-safe inside one process; a production multi-replica deployment would additionally centralize the aggregate spend counter if a globally strict budget is required.

## 10. Next steps

1. Add Redis Sentinel/Cluster or a managed Redis service for high availability of shared resilience state.
2. Centralize the optional cost-budget spend counter for globally strict multi-replica enforcement.
3. Add per-provider quality SLOs alongside availability, latency, recovery, and cost SLOs.

## Reproducibility

```bash
pip install -e ".[dev]"
make docker-up
make lint
make typecheck
make test
make run-chaos
make report
make docker-down
```
