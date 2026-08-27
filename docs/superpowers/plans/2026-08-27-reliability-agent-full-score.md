# Reliability Agent Full-Score Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every grader-visible reliability requirement reproducibly pass with Redis-enabled CI and add safe bonus coverage.

**Architecture:** Preserve the required cache -> circuit breaker/provider chain -> static fallback request path. Add reporting, CI evidence, and bonus behavior without changing default gateway semantics.

**Tech Stack:** Python 3.11, pytest, Ruff, mypy, Redis, Docker/GitHub Actions, Hypothesis for bonus property tests.

**Spec:** `docs/superpowers/specs/2026-08-27-reliability-agent-full-score-design.md`

## Global Constraints

- Do not require external LLM API keys.
- Preserve public signatures used by starter tests.
- Redis integration tests must execute in CI, not skip.
- Bonus behavior must not alter the default ordered provider routing contract.
- Run focused tests before full verification.

---

### Task 1: Circuit breaker core

**Files:** `src/reliability_lab/circuit_breaker.py`, existing `tests/test_circuit_breaker.py`, `tests/test_todo_requirements.py`.

**Interfaces:** Produce `allow_request() -> bool`, `call(...)`, `record_success() -> None`, `record_failure() -> None` with documented transition reasons.

- [ ] Run `pytest tests/test_circuit_breaker.py tests/test_todo_requirements.py -q` and confirm breaker TODO failures.
- [ ] Implement the minimum three-state logic required by the existing failing tests, with HALF_OPEN `probe_failure` separated from CLOSED threshold `failure_threshold_reached`.
- [ ] Re-run the focused tests and confirm breaker cases pass.
- [ ] Run Ruff/mypy on the changed module.

### Task 2: In-memory and Redis cache

**Files:** `src/reliability_lab/cache.py`, existing `tests/test_cache.py`, `tests/test_redis_cache.py`.

**Interfaces:** Produce deterministic `ResponseCache.similarity(a, b) -> float`, guarded `get/set`, and Redis-backed `get/set` with shared state and TTL.

- [ ] Run cache tests before implementation and confirm TODO failures/skips as applicable.
- [ ] Implement normalized word+character-trigram cosine similarity, privacy bypass, TTL eviction, false-hit logging, and memory set/get.
- [ ] Implement Redis exact lookup, prefix scan semantic lookup, TTL storage, and matching guardrails.
- [ ] Start Redis and run both memory and Redis cache suites.

### Task 3: Gateway routing

**Files:** `src/reliability_lab/gateway.py`, existing `tests/test_gateway_contract.py`.

**Interfaces:** Produce `ReliabilityGateway.complete(prompt) -> GatewayResponse` preserving cache/primary/fallback/static routes.

- [ ] Run gateway contract tests and confirm the TODO failure.
- [ ] Implement cache-first routing and provider fallback through each provider's breaker.
- [ ] Re-run gateway tests and the related TODO requirement test.

### Task 4: Chaos metrics and CSV

**Files:** `src/reliability_lab/chaos.py`, `src/reliability_lab/metrics.py`, `scripts/run_chaos.py`.

**Interfaces:** Produce `run_scenario`, `calculate_recovery_time_ms`, `RunMetrics.write_csv`, and deterministic scenario pass/fail aggregation.

- [ ] Run metrics/TODO tests and record failing CSV/TODO cases.
- [ ] Implement CSV flattening and scenario metric collection.
- [ ] Implement recovery-time pairing from transition logs.
- [ ] Extend chaos CLI to emit JSON and CSV evidence.
- [ ] Run focused tests and a local chaos simulation.

### Task 5: Report evidence

**Files:** `scripts/generate_report.py`, `reports/report_template.md` or generated `reports/final_report.md`.

**Interfaces:** `make report` must generate a complete report with architecture, config rationale, SLOs, metrics, cache comparison, Redis evidence, chaos analysis, failure analysis, and next steps.

- [ ] Add validation tests or script-level assertions for required report headings/data.
- [ ] Generate report from metrics and inspect all required sections.

### Task 6: Bonus reliability tests/features

**Files:** `pyproject.toml`, `tests/test_circuit_breaker_properties.py`, bonus helpers in existing modules as needed.

**Interfaces:** Add property-based breaker invariants and concurrency benchmark support without changing default behavior.

- [ ] Add Hypothesis dev dependency and failing property tests for core breaker invariants.
- [ ] Add a concurrency-capable simulation helper/CLI flag using `ThreadPoolExecutor` while leaving sequential mode default.
- [ ] Add Redis graceful-degradation behavior in gateway construction with a focused test.
- [ ] Run bonus tests plus full suite.

### Task 7: GitHub Actions CI and final verification

**Files:** `.github/workflows/ci.yml`.

**Interfaces:** CI must provision Redis, run lint/typecheck/tests, generate metrics/report, validate artifacts, and upload evidence.

- [ ] Replace starter CI with Python 3.11 + Redis service and health check.
- [ ] Run `make lint`, `make typecheck`, and full `make test` with Redis.
- [ ] Run `make run-chaos` and `make report`; validate metrics/report files.
- [ ] Commit/push the implementation branch and inspect the GitHub Actions run.
- [ ] Fix any CI-only failures, re-run verification, then open/merge the PR only after green evidence.
