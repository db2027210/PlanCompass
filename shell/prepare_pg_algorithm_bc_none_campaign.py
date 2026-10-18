#!/usr/bin/env python3
"""Prepare isolated Algorithm B/C lanes for native XP no-bottleneck tuning."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shell.build_pg_none_experience_artifact import (
    build_none_artifact,
    read_json,
    verify_native_xp_x1x1_config,
)
from shell.run_pg_algorithm_c_scene import canonical_sha256


IO_CAPACITY_MIB_S = 29.508261709902946
NATIVE_XP_AT_MIX = {
    "at1_percent": 40,
    "at2_percent": 30,
    "at3_percent": 15,
    "at4_percent": 5,
    "at5_percent": 7,
    "at6_percent": 3,
}
NONE_ALGORITHM_LANES = ["algorithm_b_none", "algorithm_c_none"]
PLAN_SQL_FILE = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260810_algorithm_c_xp_cpu_behavior_cluster_clean_h1/xp_cpu/"
    "native_xp_full_plan_representatives.sql"
)
PLAN_SQL_SHA256 = "7e34e9da0aa5e94e03c585a66167a2917769187b8228dfd684bbbbfbbe872288"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def worker(
    *,
    name: str,
    container: str,
    port: int,
    cpuset: str,
    data_dir: str,
) -> Dict[str, Any]:
    return {
        "name": name,
        "container": container,
        "host": "127.0.0.1",
        "port": port,
        "user": "postgres",
        "password": "postgres",
        "dbname": "hybench_sf10x",
        "targets": ["none"],
        "cpus": "4",
        "cpuset_cpus": cpuset,
        "memory": "12g",
        "disk_root": "/disk1",
        "data_dir": data_dir,
        "container_data_dir": "/var/lib/postgresql/data",
    }


def configure_lane(
    source: Mapping[str, Any],
    *,
    algorithm: str,
    scene: str,
    output_dir: Path,
    artifact_path: Path,
    workers: Sequence[Mapping[str, Any]],
    native_contract: Mapping[str, Any],
) -> Dict[str, Any]:
    cfg = copy.deepcopy(dict(source))
    algorithm_cfg = cfg["algorithm_c"]
    algorithm_cfg["scene"] = scene
    algorithm_cfg["benchmark"] = "hybench_xp"
    algorithm_cfg["experience_table_path"] = str(artifact_path)
    algorithm_cfg["fresh_t_calibration_required"] = False
    algorithm_cfg["prediction_recalibration_required"] = False
    algorithm_cfg["prediction_calibration_reused"] = True
    algorithm_cfg["target_support_abstention_enabled"] = False
    algorithm_cfg["historical_valid_replay_seed_path"] = None
    algorithm_cfg["inner_scoring_contract"] = (
        "algorithm_bc_all_non_bottleneck_band_30_50_hard80_v1"
    )
    algorithm_cfg["accepted_target"] = 7
    algorithm_cfg["normalized_width_threshold"] = 0.01
    # Stability replay is an engineering check and is no longer part of the
    # formal A/B/C convergence contract.  Keep the legacy fields explicit so
    # an older base configuration cannot silently schedule extra workloads.
    algorithm_cfg["stability_confirmation_rounds"] = 0
    algorithm_cfg["stability_confirmation_max_attempts"] = 0
    if algorithm == "b":
        algorithm_cfg["algorithm_variant"] = "B"
        algorithm_cfg["method"] = "coordinate_grid_algorithm_b_v1"
        algorithm_cfg["candidate_search_contract"] = (
            "coordinate_grid15_plus_exhaustive_interactions_2_to_4_v1"
        )
    else:
        algorithm_cfg["algorithm_variant"] = "C"
        algorithm_cfg["method"] = (
            "experience_table_all_subset_sgd_algorithm_c_v1"
        )

    contract = copy.deepcopy(dict(algorithm_cfg.get("workload_contract") or {}))
    contract["contract_version"] = 3
    contract["scene"] = scene
    contract["target_resource"] = "none"
    contract["benchmark"] = "hybench_xp"
    contract["physical_replay"] = "native_HyBench_XP_default_full_workload"
    contract["top5_replay_parallelism"] = 2
    contract["wave_shape"] = [2, 2, 1]
    contract["database"] = {
        "dbname": "hybench_sf10x",
        "template_db": "hybench_sf10x_base",
        "workers": [
            {
                key: value
                for key, value in row.items()
                if key in {"name", "container", "host", "port", "cpuset_cpus"}
            }
            for row in workers
        ],
    }
    contract["native_xp"] = copy.deepcopy(dict(native_contract["native_xp"]))
    contract["native_xp"]["xtpclient"] = 1
    contract["native_xp"]["xapclient"] = 1
    contract["native_xp"]["xp_run_mins"] = 5
    contract["native_xp"]["at_percentages"] = copy.deepcopy(NATIVE_XP_AT_MIX)
    contract["inner_candidate_scoring"] = {
        "contract": "algorithm_bc_all_non_bottleneck_band_v1",
        "all_resources_role": "non_bottleneck",
        "all_resource_band_pct": [30, 50],
        "all_resource_hard_pct": 80,
        "qps_feature_forbidden": True,
        "resource_weights": {"cpu": 1, "memory": 1, "io": 3},
    }
    contract["resource_capacity"] = {
        "cpu_cores": 4,
        "memory_gib": 12,
        "io_mib_s": IO_CAPACITY_MIB_S,
    }
    contract["baseline_cohort"] = {
        "contract": "algorithm_c_four_lane_no_mutex_baseline_cohort_v1",
        "lane_count": len(NONE_ALGORITHM_LANES),
        "lanes": copy.deepcopy(NONE_ALGORITHM_LANES),
        "synchronized_repetitions": 2,
    }
    contract["candidate_cohort"] = {
        "contract": "algorithm_c_four_lane_no_mutex_candidate_wave_cohort_v1",
        "lane_count": 1,
        "lanes": [scene],
        "synchronization": "independent_lane_physical_waves",
    }
    contract["shared_ssd_coordination"] = {
        "contract": "pg_algorithm_c_user_authorized_no_mutex_v1",
        "enabled": False,
        "physical_workload_mode": "none",
        "database_restore_mode": "none",
        "covers_cache_eviction_and_physical_workload": False,
        "device": "nvme1n1",
        "reason": "isolated_container_metrics_and_user_authorized_disk1_parallelism",
        "technical_failure_or_acceptance_state_mutation": False,
    }
    plan_evidence = copy.deepcopy(dict(contract.get("plan_evidence") or {}))
    plan_evidence["path"] = str(PLAN_SQL_FILE)
    plan_evidence["sha256"] = PLAN_SQL_SHA256
    plan_evidence["physical_workload_sql_modified"] = False
    contract["plan_evidence"] = plan_evidence
    contract["resource_estimation"]["qps_feature_forbidden"] = True
    contract["qps_inner_scoring"] = False
    contract["operator_resource_pruning"] = False
    algorithm_cfg["workload_contract"] = contract
    algorithm_cfg["workload_contract_path"] = str(
        output_dir / "workload_contract.json"
    )

    validation = cfg["binary_plan_tuning"]["validation"]
    hybench_xp = validation["hybench_xp"]
    hybench_xp["xtpclient"] = 1
    hybench_xp["xapclient"] = 1
    hybench_xp["xp_run_mins"] = 5
    for key, value in NATIVE_XP_AT_MIX.items():
        hybench_xp[key] = value
    docker_workers = validation["docker_workers"]
    docker_workers["enabled"] = True
    docker_workers["none_parallelism"] = 2
    docker_workers["workers"] = [copy.deepcopy(dict(row)) for row in workers]
    cfg["postgres"] = {
        "host": "127.0.0.1",
        "port": workers[0]["port"],
        "user": "postgres",
        "password": "postgres",
        "dbname": "hybench_sf10x",
    }

    acceptance = cfg["binary_plan_tuning"]["acceptance_policy"]
    capacity = {
        "cpu_cores": 4,
        "memory_gib": 12,
        "io_mib_s": IO_CAPACITY_MIB_S,
    }
    acceptance["resource_capacity"] = copy.deepcopy(capacity)
    target_overrides = acceptance.setdefault("target_overrides", {})
    target_overrides["none"] = {
        "resource_capacity": copy.deepcopy(capacity),
        "none_require_resource_up": False,
        "none_required_up_resources": [],
        "min_db_qps_gain_pct": 0.1,
    }
    acceptance["none_require_resource_up"] = False
    acceptance["none_required_up_resources"] = []
    outer_acceptance = cfg["binary_plan_tuning"]["binary_search"][
        "nested_loop"
    ]["outer_acceptance"]
    if str(outer_acceptance.get("qps_reference_mode") or "") != "last_accepted":
        raise RuntimeError(
            "None B/C base must compare QPS with the previous accepted incumbent"
        )
    cfg["binary_plan_tuning"]["output_dir"] = str(output_dir)
    cfg["binary_plan_tuning"]["workload_name"] = scene
    cfg["binary_plan_tuning"]["workload"]["plan_sql_file"] = str(
        PLAN_SQL_FILE
    )
    cfg["binary_plan_tuning"]["workload"]["plan_coverage_gate"][
        "plan_sql_sha256"
    ] = PLAN_SQL_SHA256
    cfg["binary_plan_tuning"]["experiment_meta"] = {
        **copy.deepcopy(
            dict(cfg["binary_plan_tuning"].get("experiment_meta") or {})
        ),
        "exp_id": scene,
        "method": algorithm_cfg["method"],
        "target_resource": "none",
        "workload": "native_hybench_xp_x1x1",
    }
    ssd = algorithm_cfg["ssd_replay_qualification"]
    ssd["enabled"] = True
    ssd["non_target_maximum_container_io_util_pct"] = 80.0
    ssd["non_target_shared_ssd_policy"] = "container_io_capacity"
    ssd["out_of_band_is_technical_failure"] = True
    return cfg


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-root", required=True, type=Path)
    parser.add_argument("--algorithm-b-base", required=True, type=Path)
    parser.add_argument("--algorithm-c-base", required=True, type=Path)
    parser.add_argument("--artifact-source", required=True, type=Path)
    parser.add_argument("--artifact-source-config", required=True, type=Path)
    args = parser.parse_args()

    if not PLAN_SQL_FILE.is_file() or sha256_file(PLAN_SQL_FILE) != PLAN_SQL_SHA256:
        raise RuntimeError("native XP complete plan universe identity mismatch")
    campaign_root = args.campaign_root.resolve()
    if campaign_root.exists() and any(campaign_root.iterdir()):
        raise RuntimeError(f"campaign root is not empty: {campaign_root}")
    campaign_root.mkdir(parents=True, exist_ok=True)

    source_config = read_json(args.artifact_source_config.resolve())
    native_contract = verify_native_xp_x1x1_config(source_config)
    artifact_path = campaign_root / "artifact" / "experience_table_T_hybench_none_x1x1.json"
    artifact = build_none_artifact(
        read_json(args.artifact_source.resolve()),
        source_path=args.artifact_source.resolve(),
        source_config_path=args.artifact_source_config.resolve(),
        workload_contract=native_contract,
    )
    write_json(artifact_path, artifact)

    b_workers = [
        worker(
            name="w1",
            container="ra_fullgrid_hybench_cpu_20260718_w1",
            port=59513,
            cpuset="80-83",
            data_dir=(
                "/disk1/resource_adaptive/bruteforce_pg17_runs_20260718/"
                "workers/hybench_cpu/w1/pgdata"
            ),
        ),
        worker(
            name="w2",
            container="ra_fullgrid_hybench_cpu_20260718_w2",
            port=59514,
            cpuset="84-87",
            data_dir=(
                "/disk1/resource_adaptive/bruteforce_pg17_runs_20260718/"
                "workers/hybench_cpu/w2/pgdata"
            ),
        ),
    ]
    c_workers = [
        worker(
            name="w1",
            container="ra_fullgrid_hybench_none_20260718_w1",
            port=59515,
            cpuset="88-91",
            data_dir=(
                "/disk1/resource_adaptive/bruteforce_pg17_runs_20260718/"
                "workers/hybench_none/w1/pgdata"
            ),
        ),
        worker(
            name="w2",
            container="ra_fullgrid_hybench_none_20260718_w2",
            port=59516,
            cpuset="96-99",
            data_dir=(
                "/disk1/resource_adaptive/bruteforce_pg17_runs_20260718/"
                "workers/hybench_none/w2/pgdata"
            ),
        ),
    ]
    lanes = {
        "algorithm_b_none": configure_lane(
            read_json(args.algorithm_b_base.resolve()),
            algorithm="b",
            scene="algorithm_b_none",
            output_dir=campaign_root / "algorithm_b_none",
            artifact_path=artifact_path,
            workers=b_workers,
            native_contract=native_contract,
        ),
        "algorithm_c_none": configure_lane(
            read_json(args.algorithm_c_base.resolve()),
            algorithm="c",
            scene="algorithm_c_none",
            output_dir=campaign_root / "algorithm_c_none",
            artifact_path=artifact_path,
            workers=c_workers,
            native_contract=native_contract,
        ),
    }
    for lane, cfg in lanes.items():
        lane_dir = campaign_root / lane
        write_json(lane_dir / "optimizer_config.json", cfg)
        write_json(
            lane_dir / "workload_contract.json",
            cfg["algorithm_c"]["workload_contract"],
        )
    manifest = {
        "contract": "pg_algorithm_bc_native_xp_x1x1_none_campaign_v1",
        "campaign_root": str(campaign_root),
        "workload": "native HyBench XP SF10x x1/x1 full TP+AT+IQ",
        "target_resource": "none",
        "accepted_target": 7,
        "normalized_width_threshold": 0.01,
        "stability_confirmation_rounds": 0,
        "post_convergence_stability_confirmation": False,
        "qps_reference_mode": "last_accepted",
        "resource_capacity": {
            "cpu_cores": 4,
            "memory_gib": 12,
            "io_mib_s": IO_CAPACITY_MIB_S,
        },
        "non_bottleneck_threshold_pct": 80,
        "artifact_path": str(artifact_path),
        "artifact_sha256": sha256_file(artifact_path),
        "lanes": {
            lane: {
                "config": str(campaign_root / lane / "optimizer_config.json"),
                "workers": cfg["binary_plan_tuning"]["validation"][
                    "docker_workers"
                ]["workers"],
            }
            for lane, cfg in lanes.items()
        },
        "qps_used_for_inner_scoring": False,
        "operator_resource_pruning": False,
        "technical_failures_mutate_formal_state": False,
        "historical_valid_replay_seed_imported": False,
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    write_json(campaign_root / "campaign_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
