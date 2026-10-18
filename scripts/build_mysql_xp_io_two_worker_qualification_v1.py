#!/usr/bin/env python3
"""Freeze the two-worker counterbalanced XP/I/O qualification replay."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / (
    "config/formal/frozen_mysql_xp_io_two_worker_qualification_20260903_v1/"
    "qualification_contract.json"
)
SOURCE_POINTS = ROOT / (
    "config/formal/frozen_mysql_xp_io_device_fix_point_replay_20260829_v2/"
    "point_replay_contract.json"
)
FREEZE = ROOT / (
    "config/formal/frozen_mysql_xp_eight_worker_20260827_v1/"
    "xp_effective_resource_successor.json"
)
AUTHORITY = ROOT / (
    "history/mysql_four_scene_20260809/"
    "v1062_xp_io_device_fix_point_replay_authority_20260829"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def bind(path: Path) -> dict[str, str]:
    return {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh contract required: {OUTPUT}")
    source = load(SOURCE_POINTS)
    points = {
        name: dict(source["points"][name])
        for name in (
            "baseline", "algorithm_a_champion", "algorithm_b_champion",
            "algorithm_c_champion",
        )
    }
    waves = [
        {"xp_io_w1": "baseline", "xp_io_w2": "algorithm_a_champion"},
        {"xp_io_w1": "algorithm_b_champion", "xp_io_w2": "algorithm_c_champion"},
        {"xp_io_w1": "algorithm_a_champion", "xp_io_w2": "baseline"},
        {"xp_io_w1": "algorithm_c_champion", "xp_io_w2": "algorithm_b_champion"},
    ]
    authority_files = {
        name: AUTHORITY / filename for name, filename in {
            "manifest": "live_authority.json",
            "prepared": "prepared_binding.json",
            "preflight": "transition_preflight.json",
            "accounts": "benchmark_accounts_binding.json",
        }.items()
    }
    contract = {
        "schema_version": "mysql_xp_io_two_worker_qualification_contract_v1",
        "created_at": datetime.now().astimezone().isoformat(),
        "status": "FROZEN",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": "hybench_xp_io",
        "purpose": "environment_qualification_only",
        "algorithm_search_reexecuted": False,
        "accepted_state_credit_increment": 0,
        "historical_artifacts_mutated": False,
        "worker_ids": ["xp_io_w1", "xp_io_w2"],
        "physical_worker_count": 2,
        "point_labels": list(points),
        "replicas_per_point": 2,
        "counterbalanced_wave_assignments": waves,
        "points": points,
        "required_device_major_minor": "259:6",
        "measurement_gates": {
            "native_xp_iq_uniform_random_iq1_iq7": True,
            "native_at_mix": "35/25/15/15/7/3",
            "frozen_xap_xtp_ratio_unchanged": True,
            "coverage_complete": True,
            "client_xap_qps_positive": True,
            "server_questions_qps_audit_only": True,
            "io_fraction_of_cap_strictly_positive": True,
            "measurement_seconds_maximum": 900,
            "resource_stability_required": True,
        },
        "successor_search_contract": {
            "primary_acceptance_metric": "client_xap_qps",
            "server_questions_qps_role": "audit_only",
            "accepted_qps_must_strictly_exceed_previous_incumbent": True,
            "after_two_no_gain_outer_rounds": [
                "predicted_point_expansion", "plan_tree_expansion",
                "shrink_around_current_incumbent",
            ],
            "terminal_normalized_width": 0.01,
            "xp_candidate_set": "scene_specific_frozen_top4",
            "algorithm_a_prior_prohibited_for_b_and_c": True,
        },
        "resource_freeze": bind(FREEZE),
        "authority": {
            f"{name}_{field}": value
            for name, path in authority_files.items()
            for field, value in (("path", str(path)), ("sha256", sha(path)))
        },
        "source_result_evidence": {
            "algorithm_a": bind(ROOT / (
                "history/mysql_four_scene_20260809/"
                "v1060_to_a_io_round6_v2_resume_20260828_xp_io/algorithm_a_state.json"
            )),
            "algorithm_b": bind(ROOT / (
                "history/mysql_four_scene_20260809/"
                "v809_to_b_io_outer15_slow_docker_resume_20260827_xp_io/run_state.json"
            )),
            "algorithm_c": bind(ROOT / (
                "history/mysql_four_scene_20260809/"
                "v1042_c_io_effective_terminal_projection_20260828/run_state.json"
            )),
        },
        "source_bindings": {
            "builder": bind(Path(__file__).resolve()),
            "runner": bind(ROOT / "scripts/run_mysql_xp_io_two_worker_qualification_v1.py"),
            "single_scene_measurement": bind(ROOT / "scripts/mysql_single_scene_measurement_v2.py"),
            "reset": bind(ROOT / "scripts/reset_mysql_xp_data_planes_mixed_eight_worker_scene_v6.py"),
            "workload_adapter": bind(ROOT / "mysql_workload_adapter_v2.py"),
            "mixed_device_sampler": bind(ROOT / "scripts/run_mysql_four_scene_r0_v5.py"),
        },
    }
    OUTPUT.parent.mkdir(parents=True, mode=0o700)
    temporary = OUTPUT.with_name(f".{OUTPUT.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(contract, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, OUTPUT)
    print(json.dumps({"status": "PASS", "contract": str(OUTPUT), "sha256": sha(OUTPUT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
