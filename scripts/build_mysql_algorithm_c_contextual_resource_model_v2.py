#!/usr/bin/env python3
"""Freeze v2 with planner-cost-independent physical plan identity."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "mysql_algorithm_c_resource_model.py"
V1 = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_contextual_plan_physics_20260809_v1"
    / "model_contract.json"
)
OUTPUT = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_contextual_plan_physics_20260809_v2"
    / "model_contract.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    predecessor = json.loads(V1.read_text(encoding="utf-8"))
    if (
        predecessor.get("schema_version")
        != "mysql_algorithm_c_contextual_plan_physics_contract_v1"
        or predecessor.get("status") != "FROZEN"
        or predecessor.get("empirical_replay_basis", {}).get("effective_replays") != 151
    ):
        raise SystemExit("v2 model-contract predecessor differs")
    document = dict(predecessor)
    document.update({
        "schema_version": "mysql_algorithm_c_contextual_plan_physics_contract_v2",
        "status": "FROZEN",
        "predecessor": {"path": str(V1.relative_to(ROOT)), "sha256": sha(V1)},
        "successor_reason": "exclude_planner_cost_numbers_from_physical_plan_identity",
        "model": {"path": str(MODEL.relative_to(ROOT)), "sha256": sha(MODEL)},
        "physical_plan_identity": {
            "planner_read_eval_prefix_query_costs_excluded": True,
            "data_read_per_join_retained_as_cardinality_times_width": True,
            "access_paths_cardinality_and_executor_structure_retained": True,
        },
    })
    if OUTPUT.exists():
        raise SystemExit(f"refusing to replace frozen output: {OUTPUT}")
    OUTPUT.parent.mkdir(parents=True, exist_ok=False)
    OUTPUT.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
