# Full Stretch Bonus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Redis-shared circuit-breaker state and 80%/100% cost-aware routing, then prove all six stretch goals without changing the verified core grader behavior.

**Architecture:** Keep both new behaviors additive and opt-in. A new Redis-backed breaker implements the gateway-facing breaker contract using shared Redis keys and a HALF_OPEN probe lease; a separate budget policy reorders providers only after cache lookup and only when enabled. Chaos/report generation writes explicit bonus evidence that CI validates against real Redis.

**Tech Stack:** Python 3.11, redis-py, Pydantic, pytest, Hypothesis, GitHub Actions, Docker Compose Redis 7.

**Spec:** `docs/superpowers/specs/2026-08-27-full-stretch-bonus-design.md`

## Global Constraints

- Preserve the default core grader behavior.
- Existing `CircuitBreaker` stays the default.
- Distributed circuit state is opt-in and falls back to local state if Redis is unavailable.
- Cost-aware routing is opt-in and disabled by default.
- Cache lookup happens before budget enforcement.
- Existing route labels stay compatible.
- No external API secrets are introduced.
- New Redis tests must execute, not skip, in GitHub Actions.
- Final generated report must identify Chu Nguyễn Tuấn Anh, MSSV 2A202601755, and show all six stretch goals PASS.

---

### Task 1: Define breaker contract and Redis-shared breaker with TDD

**Files:**
- Modify: `src/reliability_lab/circuit_breaker.py`
- Create: `src/reliability_lab/redis_circuit_breaker.py`
- Create: `tests/test_redis_circuit_breaker.py`

**Interfaces:**
- Produces: `CircuitBreakerLike` protocol with `name`, `state`, `transition_log`, `allow_request()`, and generic `call(...)`.
- Produces: `SharedRedisCircuitBreaker(name, failure_threshold, reset_timeout_seconds, success_threshold=1, redis_url="redis://localhost:6379/0", prefix="rl:circuit:")`.
- Produces: `ping() -> bool`, `flush() -> None`, `close() -> None` for integration/evidence lifecycle.

- [ ] **Step 1: Write failing Redis integration tests**

Create tests that use two independent instances sharing a unique prefix:

```python
def test_shared_open_and_close_state(redis_url: str) -> None:
    first = SharedRedisCircuitBreaker("primary", 2, 0.05, redis_url=redis_url, prefix=prefix)
    second = SharedRedisCircuitBreaker("primary", 2, 0.05, redis_url=redis_url, prefix=prefix)
    first.record_failure()
    first.record_failure()
    assert first.state == CircuitState.OPEN
    assert second.state == CircuitState.OPEN
    time.sleep(0.06)
    assert second.allow_request() is True
    second.record_success()
    assert first.state == CircuitState.CLOSED
```

Also verify only one instance can own a HALF_OPEN probe lease and that transition reasons include `failure_threshold_reached`, `reset_timeout_elapsed`, and `probe_success`.

- [ ] **Step 2: Run the new tests and verify the expected red state**

Run: `pytest tests/test_redis_circuit_breaker.py -q`

Expected: collection/import failure because `SharedRedisCircuitBreaker` does not exist yet.

- [ ] **Step 3: Add `CircuitBreakerLike` protocol**

Define the protocol in `circuit_breaker.py` so both local and Redis breakers satisfy one gateway type without changing local behavior.

- [ ] **Step 4: Implement Redis shared state**

Use provider-scoped keys for state, failure/success counters, opened timestamp, probe lease, and transition list. Initialize state with `SET NX`. Use Redis `INCR` followed by `EXPIRE` for failure counts. Use `SET probe_lease <instance-token> NX PX <lease-ms>` after reset timeout; only the token owner may probe in HALF_OPEN. Use wall-clock `time.time()` for `opened_at` so separate processes compare the same clock domain.

- [ ] **Step 5: Run focused Redis tests**

Run: `pytest tests/test_redis_circuit_breaker.py -q`

Expected: all tests pass against Redis; local environments without Redis may skip via fixture, but CI later asserts an exact pass count.

- [ ] **Step 6: Run existing circuit tests**

Run: `pytest tests/test_circuit_breaker.py tests/test_circuit_breaker_properties.py -q`

Expected: existing local-breaker tests remain green.

---

### Task 2: Add opt-in cost budget routing with TDD

**Files:**
- Create: `src/reliability_lab/budget.py`
- Modify: `src/reliability_lab/gateway.py`
- Modify: `src/reliability_lab/config.py`
- Modify: `src/reliability_lab/chaos.py`
- Modify: `configs/default.yaml`
- Create: `tests/test_budget_routing.py`

**Interfaces:**
- Produces: `CostBudget(limit: float, switch_ratio: float = 0.8, spent: float = 0.0)`.
- Produces: `CostBudget.usage_ratio -> float`, `is_exhausted() -> bool`, `record(cost: float) -> None`, and `ordered(providers: list[FakeLLMProvider]) -> list[FakeLLMProvider]`.
- Extends: `ReliabilityGateway(..., budget: CostBudget | None = None)`.
- Extends config with `BudgetConfig(enabled=False, limit, switch_ratio=0.8)` and breaker backend/Redis URL fields.

- [ ] **Step 1: Write failing budget-routing tests**

Cover these exact cases with healthy fake providers:

```python
def test_below_80_percent_uses_primary(): ...
def test_at_80_percent_uses_cheapest_provider_first(): ...
def test_at_100_percent_returns_static_fallback_without_provider_call(): ...
def test_cache_hit_bypasses_exhausted_budget(): ...
```

At 80%, the cheaper `backup` provider must be selected and response route must remain `fallback`. At 100%, response route is `static_fallback` and `error == "budget_exhausted"`.

- [ ] **Step 2: Run focused tests and verify red state**

Run: `pytest tests/test_budget_routing.py -q`

Expected: import/constructor failures because budget support does not exist.

- [ ] **Step 3: Implement `CostBudget`**

Use a `threading.Lock` for `spent` reads/writes. Validate `limit > 0` and `0 < switch_ratio < 1`. `ordered()` returns configured order below the threshold and a stable `cost_per_1k_tokens` sort at/above the threshold but below exhaustion.

- [ ] **Step 4: Integrate budget after cache lookup**

In `ReliabilityGateway.complete`, keep current cache lookup first. If budget is exhausted after a miss, return deterministic static fallback with `budget_exhausted`. Otherwise iterate over `budget.ordered(self.providers)` when enabled; label route based on provider role in original configured order, then record actual response cost.

- [ ] **Step 5: Extend configuration additively**

Add breaker backend/Redis URL and budget config fields. Keep YAML defaults `backend: memory` and `budget.enabled: false`. `build_gateway` selects Redis breaker only when configured and `ping()` succeeds; otherwise it builds the existing local breaker. Build `CostBudget` only when enabled.

- [ ] **Step 6: Run budget and gateway tests**

Run: `pytest tests/test_budget_routing.py tests/test_gateway_contract.py -q`

Expected: all pass.

---

### Task 3: Generate deterministic evidence for both new stretch goals

**Files:**
- Modify: `src/reliability_lab/chaos.py`
- Modify: `scripts/run_chaos.py`
- Create: `tests/test_bonus_evidence.py`

**Interfaces:**
- Produces: `collect_distributed_breaker_evidence(redis_url: str) -> dict[str, object]`.
- Produces: `collect_cost_routing_evidence() -> dict[str, object]`.
- Produces artifact: `reports/bonus_evidence.json`.

- [ ] **Step 1: Write failing evidence tests**

Require distributed evidence keys for Redis availability, OPEN visibility across instances, single HALF_OPEN owner, second probe denied, and CLOSED visibility after recovery. Require budget evidence to show provider `primary` below threshold, provider `backup` at 80%, and `static_fallback` with `budget_exhausted` at 100%.

- [ ] **Step 2: Run focused test and verify red state**

Run: `pytest tests/test_bonus_evidence.py -q`

Expected: failure because collection helpers do not exist.

- [ ] **Step 3: Implement deterministic collectors**

Use short reset timeout only in evidence to keep CI fast. Clean the evidence Redis prefix before/after the proof. Construct budget evidence with deterministic healthy providers and preset budget spend rather than relying on random chaos cost accumulation.

- [ ] **Step 4: Write `bonus_evidence.json` from `run_chaos.py`**

Keep existing metrics artifacts unchanged and add the new bonus artifact.

- [ ] **Step 5: Run evidence tests with Redis**

Run: `pytest tests/test_bonus_evidence.py -q`

Expected: all pass with Redis running.

---

### Task 4: Upgrade report to prove all six stretch goals

**Files:**
- Modify: `scripts/generate_report.py`
- Modify generated: `reports/final_report.md`
- Add generated: `reports/bonus_evidence.json`

**Interfaces:**
- Consumes: `reports/bonus_evidence.json`.
- Produces: a six-row stretch-goal table, all rows `PASS`, plus distributed breaker and budget-routing evidence tables.

- [ ] **Step 1: Update report generator**

Read bonus evidence and replace obsolete statements that distributed circuit state and budget routing are future work. Add explicit explanations of Redis state sharing, HALF_OPEN lease behavior, 80% cheaper-provider switch, and 100% budget cutoff.

- [ ] **Step 2: Regenerate report**

Run: `make run-chaos && make report`

Expected: `reports/bonus_evidence.json` and `reports/final_report.md` are produced.

- [ ] **Step 3: Validate report content**

Check that the generated report contains student identity, six stretch-goal PASS rows, distributed Redis breaker evidence, cost-aware routing evidence, and no placeholder text.

---

### Task 5: Strengthen CI and run the full clean grader sequence

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- CI must explicitly prove new Redis tests executed rather than skipped.
- CI must validate `reports/bonus_evidence.json` and the six-row report evidence.

- [ ] **Step 1: Add explicit bonus test gate**

With `make docker-up` already running, execute:

```bash
pytest tests/test_redis_circuit_breaker.py -q | tee reports/redis-circuit-test-output.txt
pytest tests/test_budget_routing.py tests/test_bonus_evidence.py -q | tee reports/bonus-test-output.txt
```

Assert the Redis breaker output contains `passed` and not `skipped`.

- [ ] **Step 2: Extend artifact validator**

Require `reports/bonus_evidence.json`; assert distributed and budget evidence fields exactly match the design; assert the final report contains all six stretch labels and PASS status.

- [ ] **Step 3: Upload new evidence artifacts**

Include bonus JSON and focused bonus test logs in the Actions artifact.

- [ ] **Step 4: Run full CI on the feature head**

Required sequence on clean runner: install package, `make docker-up`, lint, mypy, exact core rubric tests, bonus tests, `make test`, `make run-chaos`, `make report`, validator, artifact upload, `make docker-down`.

Expected: every step success.

- [ ] **Step 5: Review branch diff against `main`**

Confirm core grader files were changed only additively, defaults preserve existing behavior, no student placeholders remain, and required grading artifacts are tracked.

- [ ] **Step 6: Open PR for integration**

Create a PR from `feat/full-stretch-bonus` to `main` summarizing all six stretch goals and attach fresh CI evidence. Merge only after exact-head CI is successful.
