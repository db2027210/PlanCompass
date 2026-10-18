#!/usr/bin/env python3
"""Freeze the v72 TPC-H client-QPS technical-continuation contract."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SOURCE_ROOT = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v1178_to_b_client_primary_four_scene_20260903_tpch"
)
PARENT_ROOT = ROOT / (
    "config/formal/"
    "frozen_mysql_four_scene_algorithm_b_client_primary_20260903_v71"
)
OUTPUT_ROOT = ROOT / (
    "config/formal/"
    "frozen_mysql_four_scene_algorithm_b_client_primary_20260903_v72"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def binding(relative: str) -> dict[str, str]:
    path = ROOT / relative
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"v72 source binding absent: {path}")
    return {"path": relative, "sha256": sha(path)}


def main() -> int:
    if OUTPUT_ROOT.exists() or OUTPUT_ROOT.is_symlink():
        raise RuntimeError(f"fresh v72 contract root required: {OUTPUT_ROOT}")
    state_path = SOURCE_ROOT / "run_state.json"
    state = load(state_path)
    if (
        state.get("algorithm") != "B"
        or state.get("async_scene") != "tpch_sf4_memory"
        or state.get("status") != "RUNNING"
        or not str(state.get("stage") or "").startswith(
            "ALGORITHM_B_OUTER_001_"
        )
        or int(state.get("outer_attempt") or 0) != 0
        or int((state.get("accepted_rounds") or {}).get("tpch_sf4_memory") or 0) != 0
        or int((state.get("rejected_outer_attempts") or {}).get("tpch_sf4_memory") or 0) != 0
    ):
        raise RuntimeError("v72 source is not a pre-decision TPC-H boundary")
    baseline = SOURCE_ROOT / "formal_independent_baseline/batch_result.json"
    waves = {
        f"wave_{rank:02d}": sha(
            SOURCE_ROOT / f"fresh_calibration/wave_{rank:02d}/batch_result.json"
        )
        for rank in range(1, 16)
    }
    parent = load(PARENT_ROOT / "algorithm_b_top4_contract.json")
    from scripts import mysql_tpch_client_qps_measurement_v2 as client_qps

    parent.update({
        "created_at": datetime.now().astimezone().isoformat(),
        "contract_release": "v72_tpch_client_qps_predecision_continuation",
        "qps_metric_contract": client_qps.contract(),
        "tpch_outer_primary_metric": client_qps.METRIC,
        "tpch_server_questions_role": "audit_only",
        "tpch_same_worker_client_qps_normalization_required": True,
        "technical_continuation": {
            "kind": "same_cohort_metric_binding_correction_before_first_decision_v1",
            "source_run_root": str(SOURCE_ROOT.resolve()),
            "source_state_path": str(state_path.resolve()),
            "source_state_sha256": sha(state_path),
            "source_baseline_path": str(baseline.resolve()),
            "source_baseline_sha256": sha(baseline),
            "completed_calibration_waves_reused": 15,
            "calibration_batch_sha256": waves,
            "raw_full_suite_client_qps_present_in_every_reused_row": True,
            "model_rebuilt_from_client_qps": True,
            "partial_outer_measurements_reused": False,
            "accepted_or_rejected_outer_credit_reused": False,
            "source_controller_terminated_before_first_outer_decision": True,
            "initial_all_four_scene_launch_preserved": True,
            "sibling_scenes_stopped": False,
        },
    })
    parent["source_bindings"].update({
        "builder_v72": binding(
            "scripts/build_mysql_algorithm_b_tpch_client_primary_v72.py"
        ),
        "controller_v72": binding(
            "scripts/run_mysql_algorithm_b_tpch_client_primary_continuation_v1.py"
        ),
        "tpch_client_qps_adapter": binding(
            "scripts/mysql_tpch_client_qps_measurement_v2.py"
        ),
        "tpch_worker_normalization": binding(
            "scripts/mysql_tpch_worker_qps_normalization_v1.py"
        ),
    })
    atomic(OUTPUT_ROOT / "algorithm_b_top4_contract.json", parent)
    coordination = load(PARENT_ROOT / "coordination_contract.json")
    coordination.update({
        "created_at": datetime.now().astimezone().isoformat(),
        "successor_release": "v72_tpch_client_qps_predecision_continuation",
        "sibling_scene_wait": False,
        "four_scene_barrier": False,
    })
    atomic(OUTPUT_ROOT / "coordination_contract.json", coordination)
    print(json.dumps({
        "status": "PASS",
        "contract": str(OUTPUT_ROOT / "algorithm_b_top4_contract.json"),
        "contract_sha256": sha(OUTPUT_ROOT / "algorithm_b_top4_contract.json"),
        "coordination_sha256": sha(OUTPUT_ROOT / "coordination_contract.json"),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
