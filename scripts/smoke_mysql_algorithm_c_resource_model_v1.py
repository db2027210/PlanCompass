#!/usr/bin/env python3
"""Run one live XP candidate through the contextual soft resource model."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mysql_algorithm_c_resource_model import enhance_resource_prediction
from scripts import run_mysql_four_scene_algorithm_c_v1 as controller


FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v11"
    / "four_scene_effective_resource_successor.json"
)
PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_live_rebind_v11_20260809T225000p0800/prepared_binding.json"
)
ACCOUNTS = (
    ROOT / "history/mysql_four_scene_20260809"
    / "benchmark_accounts_native_iq1_iq7_v11_20260809T225100p0800"
    / "benchmark_accounts_binding.json"
)
OUTPUT = (
    ROOT / "preflight/mysql_four_scene_20260809"
    / "resource_model_live_smoke_v2.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def main() -> int:
    freeze = load(FREEZE)
    prepared = load(PREPARED)
    accounts = load(ACCOUNTS)
    if (
        freeze.get("schema_version")
        != "mysql_disk2_four_scene_two_worker_effective_resource_successor_v11"
        or prepared.get("status") != "PREPARED"
        or prepared.get("freeze_sha256") != sha(FREEZE)
        or accounts.get("status") != "PASS"
        or accounts.get("prepared_binding_sha256") != sha(PREPARED)
    ):
        raise RuntimeError("live model-smoke authority differs")
    frozen = {row["worker_id"]: row for row in freeze["workers"]}
    live = {row["worker_id"]: row for row in prepared["workers"]}
    users = {row["worker_id"]: row for row in accounts["workers"]}
    worker = {**live["xp_cpu_w1"], **frozen["xp_cpu_w1"], **users["xp_cpu_w1"]}
    worker["formal_client_log"] = str(OUTPUT.with_suffix(".clients.jsonl"))
    incumbent = dict(controller.DEFAULTS)
    candidate = dict(incumbent)
    candidate["row_evaluate_cost"] = 0.12
    try:
        controller.apply_costs(worker, incumbent)
        baseline_plans = controller.capture_scene_plans(
            freeze, "hybench_xp_cpu", worker
        )
        controller.apply_costs(worker, candidate)
        candidate_plans = controller.capture_scene_plans(
            freeze, "hybench_xp_cpu", worker
        )
        result = enhance_resource_prediction(
            incumbent_resources={"cpu": 0.82, "memory": 0.55, "io": 0.01},
            experience_prediction={"cpu": 0.81, "memory": 0.56, "io": 0.011},
            baseline_plans=baseline_plans,
            candidate_plans=candidate_plans,
            relation_stats=controller.relation_stats(worker),
            executor_parameters=controller.executor_parameters(worker),
            concurrency=controller.SCENE_CONCURRENCY["hybench_xp_cpu"],
            cache_profile=controller.PLAN_CACHE_PROFILE,
        )
    finally:
        controller.apply_costs(worker, incumbent)
    if (
        result.get("hard_prune_allowed") is not False
        or not all(
            math.isfinite(float(result["predicted_resources"][name]))
            for name in ("cpu", "memory", "io")
        )
    ):
        raise RuntimeError("live contextual model smoke failed")
    document = {
        "schema_version": "mysql_algorithm_c_contextual_model_live_smoke_v2",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_sha256": sha(FREEZE),
        "prepared_binding_sha256": sha(PREPARED),
        "accounts_binding_sha256": sha(ACCOUNTS),
        "worker_id": "xp_cpu_w1",
        "incumbent_params": incumbent,
        "candidate_params": candidate,
        "result": result,
        "optimizer_costs_restored": True,
        "workload_started": False,
    }
    if OUTPUT.exists():
        raise RuntimeError(f"fresh smoke output exists: {OUTPUT}")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "status": "PASS", "plan_changed": result["plan_changed"],
        "confidence": result["confidence"], "output": str(OUTPUT),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
