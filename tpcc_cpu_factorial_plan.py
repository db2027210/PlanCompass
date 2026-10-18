"""Generate (never execute) an 8-point BenchBase TPCC CPU-contention plan."""

from __future__ import annotations

import argparse
from itertools import product
import json
from pathlib import Path
import random
from typing import Any, Mapping, Sequence


DEFAULT_CONFIG = Path(__file__).resolve().parent / "config" / "tpcc_cpu_contention_factorial.json"


def load_factorial(path: Path | str = DEFAULT_CONFIG) -> dict[str, Any]:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    factors = cfg.get("factors", {})
    if set(factors) != {"innodb_spin_wait_delay", "innodb_sync_spin_loops", "innodb_thread_concurrency"}:
        raise ValueError("only the three reviewed dynamic InnoDB variables are permitted")
    if any(not isinstance(levels, list) or len(levels) != 2 or len(set(levels)) != 2 for levels in factors.values()):
        raise ValueError("each factor must have exactly two distinct levels")
    target = cfg.get("target", {})
    if target.get("container") != "ra-mysql-grid-tpcc-cpu" or int(target.get("port", 0)) != 13308:
        raise ValueError("target must remain the dedicated TPCC container on 13308")
    if target.get("required_label") != "resource-adaptive-mysql.owner=mysql-grid-20260718":
        raise ValueError("exact ownership label is required")
    guardrails = cfg.get("guardrails", {})
    for key in ("require_exact_container_label_and_port", "snapshot_runtime_globals_before_first_change",
                "restore_after_every_point", "verify_restore_by_select", "abort_on_restore_failure"):
        if guardrails.get(key) is not True:
            raise ValueError(f"mandatory guardrail disabled: {key}")
    return cfg


def build_plan(cfg: Mapping[str, Any], seed: int = 20260718) -> dict[str, Any]:
    names = list(cfg["factors"])
    rows = [dict(zip(names, values)) for values in product(*(cfg["factors"][name] for name in names))]
    if cfg["benchmark"].get("randomize_order", True):
        random.Random(seed).shuffle(rows)
    points = []
    for index, values in enumerate(rows, 1):
        points.append({
            "point_id": index,
            "values": values,
            "precondition": "exact label/port verified; original globals snapshot exists",
            "mutation_template": [f"SET GLOBAL {name} = {int(value)}" for name, value in values.items()],
            "workload": "BenchBase TPCC warmup then measured interval",
            "finally": "restore every variable from immutable runtime snapshot and SELECT-verify",
        })
    variables = ", ".join(f"'{name}'" for name in names)
    return {
        "mode": "plan_only_no_sql_or_workload_executed",
        "target": cfg["target"],
        "runtime_snapshot_sql": f"SHOW GLOBAL VARIABLES WHERE Variable_name IN ({variables})",
        "points": points,
        "restore_algorithm": "for each snapshotted (name,value): SET GLOBAL name=value; then re-read all; abort on mismatch",
        "run_count": len(points) * int(cfg["benchmark"].get("repeats_per_point", 1)),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--seed", type=int, default=20260718)
    args = parser.parse_args(argv)
    print(json.dumps(build_plan(load_factorial(args.config), args.seed), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
