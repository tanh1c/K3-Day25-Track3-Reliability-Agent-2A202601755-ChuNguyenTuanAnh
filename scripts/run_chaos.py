from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from reliability_lab.chaos import (
    load_queries,
    run_scenario,
    run_scenario_concurrent,
    run_simulation,
)
from reliability_lab.config import ScenarioConfig, load_config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--out", default="reports/metrics.json")
    parser.add_argument("--seed", type=int, default=25)
    args = parser.parse_args()

    random.seed(args.seed)
    config = load_config(args.config)
    queries = load_queries()
    metrics = run_simulation(config, queries)

    output = Path(args.out)
    metrics.write_json(output)
    metrics.write_csv(output.with_suffix(".csv"))

    healthy = ScenarioConfig(
        name="cache_comparison",
        description="Healthy providers used to isolate cache impact",
        provider_overrides={provider.name: 0.0 for provider in config.providers},
    )

    random.seed(args.seed)
    with_cache = run_scenario(config, queries, healthy)
    no_cache_config = config.model_copy(deep=True)
    no_cache_config.cache.enabled = False
    random.seed(args.seed)
    without_cache = run_scenario(no_cache_config, queries, healthy)
    comparison = {
        "with_cache": with_cache.to_report_dict(),
        "without_cache": without_cache.to_report_dict(),
        "cost_delta": round(without_cache.estimated_cost - with_cache.estimated_cost, 6),
        "p95_delta_ms": round(without_cache.percentile(95) - with_cache.percentile(95), 2),
    }
    comparison_path = output.with_name("cache_comparison.json")
    comparison_path.write_text(json.dumps(comparison, indent=2, ensure_ascii=False))

    random.seed(args.seed)
    concurrent = run_scenario_concurrent(no_cache_config, queries, healthy, workers=8)
    concurrent_path = output.with_name("concurrent_metrics.json")
    concurrent.write_json(concurrent_path)

    print(f"wrote {output}")
    print(f"wrote {output.with_suffix('.csv')}")
    print(f"wrote {comparison_path}")
    print(f"wrote {concurrent_path}")


if __name__ == "__main__":
    main()
