#!/usr/bin/env python3
"""Freeze final baseline/A/B/C replay points after staged v94 C completes."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HISTORY = ROOT / "history/mysql_four_scene_20260809"
OUTPUT_ROOT = ROOT / (
    "config/formal/frozen_mysql_abc_final_counterbalanced_20260904_v1"
)
OUTPUT = OUTPUT_ROOT / "replay_contract.json"
FREEZE = ROOT / (
    "config/formal/frozen_mysql_four_scene_two_worker_20260903_v20/"
    "four_scene_effective_resource_successor.json"
)
SCENES = {
    "job_memory": "job",
    "tpch_sf4_memory": "tpch",
    "hybench_xp_cpu": "xp_cpu",
    "hybench_xp_io": "xp_io",
}
A_RESULTS = {
    "job_memory": HISTORY / (
        "v153_to_a_job4_round11_20260825_attempt001_job/final_result.json"
    ),
    "tpch_sf4_memory": HISTORY / (
        "v100_to_a_tpch_external_diagnostic_resume_20260823_attempt001_tpch/"
        "final_result.json"
    ),
    "hybench_xp_cpu": HISTORY / (
        "v843_to_a_cpu_round5_control2_resume_20260827_xp_cpu/final_result.json"
    ),
    "hybench_xp_io": HISTORY / (
        "v1060_to_a_io_round6_v2_resume_20260828_xp_io/final_result.json"
    ),
}
B_STATES = {
    "job_memory": HISTORY / (
        "v1178_to_b_client_primary_four_scene_20260903_job/run_state.json"
    ),
    "tpch_sf4_memory": HISTORY / (
        "v1191_to_b_tpch_client_primary_predecision_continue_20260903_tpch/"
        "run_state.json"
    ),
    "hybench_xp_cpu": HISTORY / (
        "v1184_to_b_client_primary_xp_cleanup_recovery_20260903_xp_cpu/"
        "run_state.json"
    ),
    "hybench_xp_io": HISTORY / (
        "v1184_to_b_client_primary_xp_cleanup_recovery_20260903_xp_io/"
        "run_state.json"
    ),
}
C_CAMPAIGN = "v1194_to_c_client_primary_four_scene_20260903"


def now() -> str:
    return datetime.now().astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
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
    return {"path": relative, "sha256": sha(path)}


def complete_state(path: Path, *, scene: str, algorithm: str) -> dict[str, Any]:
    state = load(path)
    if (
        state.get("status") != "COMPLETE"
        or state.get("algorithm") != algorithm
        or (state.get("converged") or {}).get(scene) is not True
        or not isinstance((state.get("params_by_scene") or {}).get(scene), Mapping)
    ):
        raise RuntimeError(f"{algorithm} complete state differs: {path}")
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authority-root", type=Path, required=True)
    args = parser.parse_args()
    if OUTPUT_ROOT.exists() or OUTPUT_ROOT.is_symlink():
        raise RuntimeError(f"fresh final replay contract root required: {OUTPUT_ROOT}")
    authority = args.authority_root.resolve()
    authority_files = {
        name: authority / filename
        for name, filename in {
            "preflight": "transition_preflight.json",
            "prepared": "prepared_binding.json",
            "accounts": "benchmark_accounts_binding.json",
        }.items()
    }
    if any(not path.is_file() for path in authority_files.values()):
        raise RuntimeError("final replay authority artifacts absent")
    from scripts import run_mysql_four_scene_algorithm_c_v2 as alg

    scenes: dict[str, Any] = {}
    result_bindings: dict[str, Any] = {}
    for scene, suffix in SCENES.items():
        a_path = A_RESULTS[scene]
        a = load(a_path)
        if (
            a.get("status") != "PASS"
            or a.get("algorithm") != "A"
            or a.get("scene") != scene
            or not isinstance(a.get("incumbent_params"), Mapping)
        ):
            raise RuntimeError(f"Algorithm A result differs: {a_path}")
        b_path = B_STATES[scene]
        b = complete_state(b_path, scene=scene, algorithm="B")
        c_path = HISTORY / f"{C_CAMPAIGN}_{suffix}" / "run_state.json"
        c = complete_state(c_path, scene=scene, algorithm="C")
        scenes[scene] = {
            "worker_ids": [f"{suffix}_w1", f"{suffix}_w2"],
            "points": {
                "baseline": dict(alg.DEFAULTS),
                "algorithm_a": copy_dict(a["incumbent_params"]),
                "algorithm_b": copy_dict(b["params_by_scene"][scene]),
                "algorithm_c": copy_dict(c["params_by_scene"][scene]),
            },
            "source_paths": {
                "algorithm_a": str(a_path),
                "algorithm_b": str(b_path),
                "algorithm_c": str(c_path),
            },
            "primary_client_metric": (
                "native_hybench_xp_client_xap_qps"
                if scene.startswith("hybench_xp")
                else "full_suite_client_qps"
            ),
        }
        for label, path in (("a", a_path), ("b", b_path), ("c", c_path)):
            result_bindings[f"{suffix}_{label}"] = {
                "path": str(path), "sha256": sha(path),
            }
    contract = {
        "schema_version": "mysql_abc_final_counterbalanced_replay_contract_v1",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "active_scenes": list(SCENES),
        "retired_workloads_included": False,
        "resource_freeze": {"path": str(FREEZE), "sha256": sha(FREEZE)},
        "authority_root": str(authority),
        "authority": {
            name: {"path": str(path), "sha256": sha(path)}
            for name, path in authority_files.items()
        },
        "points": ["baseline", "algorithm_a", "algorithm_b", "algorithm_c"],
        "minimum_valid_repeats_per_point": 5,
        "target_valid_repeats_per_point": 6,
        "target_valid_repeats_per_point_per_worker": 3,
        "initial_counterbalanced_waves": 12,
        "maximum_waves_with_recovery": 24,
        "workers_per_scene": 2,
        "all_four_scenes_parallel": True,
        "four_scene_barrier": False,
        "lease_mode": "none",
        "server_questions_qps_role": "audit_only_for_tpch_and_xp",
        "algorithm_search_reexecuted": False,
        "accepted_state_credit_increment": 0,
        "scenes": scenes,
        "source_result_evidence": result_bindings,
        "source_bindings": {
            "builder": binding(
                "scripts/build_mysql_abc_final_counterbalanced_contract_v1.py"
            ),
            "runner": binding(
                "scripts/run_mysql_abc_final_counterbalanced_scene_v1.py"
            ),
            "launcher": binding(
                "scripts/launch_mysql_abc_final_counterbalanced_replay_v1.py"
            ),
            "report_builder": binding(
                "scripts/build_mysql_abc_final_report_v1.py"
            ),
            "convergence_plotter": binding(
                "scripts/plot_mysql_abc_final_results_v1.py"
            ),
            "completion_auditor": binding(
                "scripts/audit_mysql_abc_final_completion_v1.py"
            ),
            "finalizer": binding(
                "scripts/finalize_mysql_abc_after_replay_v1.py"
            ),
            "client_primary_measurement": binding(
                "scripts/mysql_single_scene_measurement_client_primary_v1.py"
            ),
            "tpch_client_qps": binding(
                "scripts/mysql_tpch_client_qps_measurement_v2.py"
            ),
        },
    }
    atomic(OUTPUT, contract)
    print(json.dumps({
        "status": "PASS", "contract": str(OUTPUT), "sha256": sha(OUTPUT),
    }, sort_keys=True))
    return 0


def copy_dict(value: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(dict(value)))


if __name__ == "__main__":
    raise SystemExit(main())
