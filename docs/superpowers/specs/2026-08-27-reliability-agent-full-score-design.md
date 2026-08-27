# Reliability Agent Full-Score Design

## Goal

Complete the Day 25 reliability lab so the grader-visible core requirements pass reproducibly, while adding extra-credit reliability features behind safe defaults that do not change the required gateway contract.

## Architecture

The request path remains intentionally compatible with the starter contract:

```text
User
  |
  v
ReliabilityGateway
  |
  +--> Cache (memory or Redis) ---- HIT ---> return cached response
  |
  +--> CircuitBreaker(primary) ---> primary provider
  |          |
  |          +-- unavailable/error --> continue
  |
  +--> CircuitBreaker(backup) ----> backup provider
  |          |
  |          +-- unavailable/error --> continue
  |
  +--> Static fallback
```

Core routing semantics remain cache -> ordered provider chain -> static fallback. Stretch features must be opt-in or isolated to chaos/test tooling so hidden grader tests see the documented default behavior.

## Core requirements

### Circuit breaker

Implement CLOSED, OPEN, and HALF_OPEN with these invariants:

- CLOSED allows calls.
- OPEN denies calls until `reset_timeout_seconds` has elapsed.
- After the timeout, OPEN transitions to HALF_OPEN and permits a probe.
- Successful calls reset `failure_count` and increment `success_count`.
- HALF_OPEN closes only after `success_threshold` successful probes.
- HALF_OPEN failure immediately re-opens with reason `probe_failure`.
- CLOSED threshold failure opens with reason `failure_threshold_reached`.
- Duplicate transitions to the current state are not logged.

### In-memory semantic cache

- Privacy-sensitive queries are never stored or returned.
- TTL expiration is enforced before matching.
- Similarity is deterministic cosine similarity over normalized word tokens plus character trigrams.
- Exact string equality returns `1.0`.
- The highest-scoring entry is returned only when it meets the threshold.
- A 4-digit date/identifier mismatch is treated as a false hit, logged with reason `date_or_number_mismatch`, and rejected.

### Redis shared cache

- Use deterministic hashed keys under a prefix.
- Store query/response as a Redis hash and set Redis TTL.
- Exact lookup is O(1) by hash.
- Semantic lookup scans only the configured prefix and reuses `ResponseCache.similarity`.
- Apply the same privacy and false-hit safeguards as the in-memory cache.
- Two cache instances using the same Redis database/prefix must observe shared state.

### Gateway

- Cache hits return `route=cache_hit:{score:.2f}`, zero provider latency/cost, and `cache_hit=True`.
- Provider calls are wrapped by provider-specific circuit breakers.
- First provider success uses route `primary`; later provider success uses `fallback`.
- Provider results are cached with provider metadata.
- `ProviderError` and `CircuitOpenError` continue to the next provider.
- Exhaustion returns the documented static degraded response and preserves the last error.

### Chaos and metrics

- `run_scenario` records request counts, successes/failures, cache hits, fallback/static fallback counts, provider latency samples, cost, circuit-open count, and recovery time.
- Recovery time is the mean duration of OPEN -> CLOSED cycles across breaker transition logs.
- CSV output flattens scenario statuses into `scenario_<name>` columns.
- Scenario pass/fail rules must be explicit and reproducible.
- Cache-enabled and cache-disabled comparisons must be generated as report evidence.

### Report

`make report` must generate a complete report, not a template stub. It must include architecture, configuration with rationale, SLO targets and results, metrics, cache comparison, Redis evidence, chaos results, failure analysis, and next steps.

## CI design

GitHub Actions must run on pushes and pull requests. A single CI workflow will:

1. Check out the repository.
2. Start Redis as a GitHub Actions service with a health check.
3. Install Python 3.11 and the package with dev dependencies.
4. Run Ruff.
5. Run mypy.
6. Run the complete pytest suite so Redis integration tests execute instead of skip.
7. Run chaos simulation and generate JSON plus CSV metrics.
8. Generate the final report.
9. Validate that generated artifacts exist and contain required sections/fields.
10. Upload reports and test evidence as workflow artifacts.

No external API key is required. `REDIS_URL=redis://localhost:6379/0` may be set as a non-secret workflow environment variable.

## Stretch goals

The following are included only when they can be added without changing core defaults:

- Property-based tests using Hypothesis for breaker state invariants.
- Concurrent simulation support using `ThreadPoolExecutor`, exposed through an explicit configuration/CLI option or separate benchmark helper.
- Redis graceful degradation: when Redis is configured but unavailable, `build_gateway` may safely fall back to an in-memory cache while preserving the normal cache interface.
- SLO evaluation as deterministic report data.
- Cost-aware routing only as an opt-in feature with a disabled-by-default budget setting; hidden core tests must keep the documented provider order.
- Shared Redis breaker state only if it can be isolated behind an opt-in implementation and does not alter the default `CircuitBreaker` class contract.

## Testing strategy

Use the existing grader tests as the primary contract. Add focused tests for bonus behavior rather than modifying grader tests. Every implementation task follows red-green-refactor where practical: first add/identify a failing test, then implement minimal behavior, then run the focused suite and the full suite.

CI is the final source of truth because it verifies a fresh Linux environment with Redis enabled.

## Non-goals

- No real LLM API calls or API keys.
- No breaking public signatures used by starter tests.
- No large framework additions beyond small test/runtime dependencies justified by stretch goals.
- No retry loop that can create a retry storm.
