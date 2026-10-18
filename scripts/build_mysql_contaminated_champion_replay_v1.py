#!/usr/bin/env python3
"""Freeze only final champions whose recorded I/O equals zero."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "config/formal/frozen_mysql_contaminated_champion_replay_20260829_v1"
CONTRACT = OUT / "champion_replay_contract.json"
AUTHORITY = ROOT / "history/mysql_four_scene_20260809/v960_to_c_tpch_fullparallel_live_authority_20260828"
FREEZE = ROOT / "config/formal/frozen_mysql_four_scene_cpu_a_slots_four_worker_20260828_v3/four_scene_effective_resource_successor.json"
SOURCES = {
    "a_job": "history/mysql_four_scene_20260809/v153_to_a_job4_round11_20260825_attempt001_job/algorithm_a_state.json",
    "a_tpch": "history/mysql_four_scene_20260809/v100_to_a_tpch_external_diagnostic_resume_20260823_attempt001_tpch/algorithm_a_state.json",
    "a_cpu": "history/mysql_four_scene_20260809/v843_to_a_cpu_round5_control2_resume_20260827_xp_cpu/algorithm_a_state.json",
    "a_io": "history/mysql_four_scene_20260809/v1060_to_a_io_round6_v2_resume_20260828_xp_io/algorithm_a_state.json",
    "b_job": "history/mysql_four_scene_20260809/v523_to_b_job_mixed_four_worker_boundary_resume_v73_20260827_job/run_state.json",
    "b_tpch": "history/mysql_four_scene_20260809/v517_to_b_tpch_mixed_four_worker_boundary_resume_v73_20260827_tpch/run_state.json",
    "b_cpu": "history/mysql_four_scene_20260809/v551_to_b_cpu_mixed_eight_worker_v3_20260827_xp_cpu/run_state.json",
    "b_io": "history/mysql_four_scene_20260809/v809_to_b_io_outer15_slow_docker_resume_20260827_xp_io/run_state.json",
    "c_job": "history/mysql_four_scene_20260809/v965_to_c_job_pg_coverage_mixed_resume_20260828_job/run_state.json",
    "c_tpch": "history/mysql_four_scene_20260809/v966_to_c_tpch_pg_coverage_mixed_resume_20260828_tpch/run_state.json",
    "c_cpu": "history/mysql_four_scene_20260809/v1040_c_cpu_effective_terminal_projection_20260828/run_state.json",
    "c_io": "history/mysql_four_scene_20260809/v1042_c_io_effective_terminal_projection_20260828/run_state.json",
}
A_DECISIONS = {
    "a_job": "history/mysql_four_scene_20260809/v153_to_a_job4_round11_20260825_attempt001_job/round_011/outer_decision.json",
    "a_tpch": "history/mysql_four_scene_20260809/v100_to_a_tpch_external_diagnostic_resume_20260823_attempt001_tpch/round_014/outer_decision.json",
    "a_cpu": "history/mysql_four_scene_20260809/v843_to_a_cpu_round5_control2_resume_20260827_xp_cpu/round_010/outer_decision.json",
    "a_io": "history/mysql_four_scene_20260809/v1060_to_a_io_round6_v2_resume_20260828_xp_io/round_010/outer_decision.json",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def binding(path: str | Path) -> dict[str, str]:
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = ROOT / resolved
    return {"path": str(resolved.relative_to(ROOT)), "sha256": sha(resolved)}


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=False)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(f"{sha(path)}  {path.name}\n")


def main() -> int:
    if OUT.exists() or OUT.is_symlink():
        raise RuntimeError(f"fresh contract required: {OUT}")
    audit: dict[str, Any] = {}
    for key, source in SOURCES.items():
        state = load(ROOT / source)
        if key.startswith("a_"):
            decision = load(ROOT / A_DECISIONS[key])
            row = decision["accepted"]["row"]
            audit[key] = {
                "algorithm": "A", "scene": state["async_scene"],
                "recorded_io": float(row["resource_means"]["io"]),
                "params": state["incumbent_params"], "source": source,
            }
        else:
            scene = state["async_scene"]
            audit[key] = {
                "algorithm": state["algorithm"], "scene": scene,
                "recorded_io": float(state["incumbents"][scene]["resource_means"]["io"]),
                "params": state["params_by_scene"][scene], "source": source,
            }
    selected = {key: row for key, row in audit.items() if row["recorded_io"] == 0.0}
    expected = {"b_job", "b_cpu", "b_io", "c_job", "c_tpch", "c_io"}
    if set(selected) != expected:
        raise RuntimeError(f"zero-I/O champion set differs: {sorted(selected)}")
    assignments = {
        "job_memory": {"job_w1": "b_job", "job_w2": "c_job"},
        "tpch_sf4_memory": {"tpch_w1": "c_tpch"},
        "hybench_xp_cpu": {"xp_cpu_w1": "b_cpu"},
        "hybench_xp_io": {"xp_io_w3": "b_io", "xp_io_w4": "c_io"},
    }
    points = {
        key: {
            "algorithm": row["algorithm"], "scene": row["scene"],
            "contaminated_recorded_io": row["recorded_io"],
            "params": row["params"],
        }
        for key, row in selected.items()
    }
    prepared = AUTHORITY / "prepared_binding.json"
    preflight = AUTHORITY / "transition_preflight.json"
    accounts = AUTHORITY / "benchmark_accounts_binding.json"
    adapter = ROOT / "config/formal/frozen_mysql_c49_contaminated_champion_adapter_20260829_v4/algorithm_c_measurement_adapter_contract.json"
    contract = {
        "schema_version": "mysql_contaminated_champion_replay_contract_v1",
        "created_at": datetime.now().astimezone().isoformat(),
        "status": "FROZEN_RUNTIME_VALIDATED", "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "purpose": "replay_only_final_champions_with_exactly_zero_recorded_io",
        "baseline_replayed": False, "algorithm_search_reexecuted": False,
        "accepted_state_credit_increment": 0, "historical_artifacts_mutated": False,
        "all_four_scenes_start_concurrently": True,
        "per_scene_progress_independent": True, "four_scene_barrier": False,
        "selected_point_count": len(points), "omitted_nonzero_point_count": len(audit) - len(points),
        "contamination_audit": audit, "points": points,
        "assignments_by_scene": assignments,
        "authority": {
            "prepared_path": str(prepared), "prepared_sha256": sha(prepared),
            "preflight_path": str(preflight), "preflight_sha256": sha(preflight),
            "accounts_path": str(accounts), "accounts_sha256": sha(accounts),
        },
        "resource_freeze": {"path": str(FREEZE), "sha256": sha(FREEZE)},
        "source_result_evidence": {key: binding(path) for key, path in SOURCES.items()},
        "source_bindings": {
            "runner": binding("scripts/run_mysql_contaminated_champion_replay_v1.py"),
            "shared_measurement": binding("scripts/run_mysql_four_scene_algorithm_c_v2.py"),
            "mixed_device_sampler": binding("scripts/run_mysql_four_scene_r0_v5.py"),
            "single_scene_measurement": binding("scripts/mysql_single_scene_measurement_v1.py"),
            "runtime_configurator": binding("scripts/run_mysql_algorithm_a_job_to_xp_resume_v1.py"),
            "c49_adapter": binding(adapter),
            "workload_adapter": binding("mysql_workload_adapter_v2.py"),
        },
    }
    atomic(CONTRACT, contract)
    print(json.dumps({
        "status": "PASS", "contract": str(CONTRACT), "sha256": sha(CONTRACT),
        "selected": sorted(selected),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
