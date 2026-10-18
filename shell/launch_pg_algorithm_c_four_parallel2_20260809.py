#!/usr/bin/env python3
"""Prepare and launch four binary-plan lanes with Top-5 replayed 2+2+1.

The historical filename is retained for compatibility. The invoked tuner uses
binary interval corners, not Algorithm C's experience-table subset SGD.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path("/home/anonymous/miniconda3/envs/resource_adaptive/bin/python")
if not PYTHON.is_file():
    PYTHON = Path(sys.executable)
BASE = Path("/disk1/resource_adaptive/bruteforce_pg17_runs_20260718")
FRAMEWORK = Path("/disk1/resource_adaptive/bruteforce_pg17_framework_20260717")
BENCHBASE = Path("/home/anonymous/benchbase-postgres")
JOB_SQL = Path("/home/anonymous/dataset/pg_data/join-order-benchmark")
NATIVE_RELEASE = Path("/disk1/resource_adaptive/pg_algorithm_c_native_hybench_20260809_v2")
FULL_WORKLOAD_LAUNCHER = ROOT / "shell/run_pg_full_workload_isolated.sh"
RUN_BASE = Path("/disk1/resource_adaptive/pg_algorithm_c_four_parallel2_20260809")
OPERATOR_RULE_ARTIFACT = ROOT / "config/generated/operator_resource_reject_rules_20260809.json"
NESTED_POLICY = "nested_inner_resource_outer_semantic_v1"
COST_PARAMS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
CPUSETS = {
    "ra_fullgrid_hybench_cpu_20260718_w1": "96-99,352-355",
    "ra_fullgrid_hybench_cpu_20260718_w2": "100-103,356-359",
    "ra_fullgrid_hybench_none_20260718_w1": "104-111,360-367",
    "ra_fullgrid_hybench_none_20260718_w2": "112-119,368-375",
}
CONTROLLER_CLIENT_CPUSETS = {
    "xp_cpu": "128-143",
    "xp_io": "144-159",
    "job_memory": "160-179",
    "tpch_memory": "180-199",
}
EXTERNAL_RESERVED_CPUSETS = {
    "pg5v1_hyxp_compute00_59630_w1": "32-51",
    "ra_mysql_sgd_tpch_full3w_20260809t095500_diskpolicyv2_tpch": "200-207",
}
MAX_CONTROLLER_CLIENT_CPU = 255
DIRECTIONAL_CHANGE_THRESHOLD_PCT = 0.1
PLAN_QUERY_COUNTS = {
    "xp_cpu": 12,
    "xp_io": 12,
    "job_memory": 113,
    "tpch_memory": 22,
}


SCENES: Dict[str, Dict[str, Any]] = {
    "xp_cpu": {
        "template": BASE / "runs/E6-R1_hybench_xp_none/optimizer_config.json",
        "pool": BASE / "workers/hybench_cpu/workers.json",
        "target": "cpu",
        "workload": "hybench_xp",
        "dbname": "hybench_sf10x",
        "template_db": "hybench_sf10x_base",
        "operator_rule_contract": "hybench_xp_native_sf10x_xtp32_xap4_cpu_v1",
    },
    "xp_io": {
        "template": BASE / "runs/E6-R1_hybench_xp_none/optimizer_config.json",
        "pool": BASE / "workers/hybench_none/workers.json",
        "target": "io",
        "workload": "hybench_xp",
        "dbname": "hybench_sf10x",
        "template_db": "hybench_sf10x_base",
        "operator_rule_contract": "hybench_xp_native_sf10x_xtp32_xap4_io_v1",
    },
    "job_memory": {
        "template": BASE / "runs/A2-R1_job_memory/optimizer_config.json",
        "pool": BASE / "workers/generic_memory/workers.json",
        "target": "memory",
        "workload": "job",
        "dbname": "fullgrid_job",
        "template_db": "template_fullgrid_job",
        "operator_rule_contract": "job_canonical113_memory_v1",
    },
    "tpch_memory": {
        "template": BASE / "runs/A3-R1_tpch_memory/optimizer_config.json",
        "pool": BASE / "workers/generic/workers.json",
        "target": "memory",
        "workload": "tpch",
        "dbname": "fullgrid_tpch_sf5",
        "template_db": "template_fullgrid_tpch_sf5",
        "operator_rule_contract": "tpch_sf5_q1_q22_serial_memory_v1",
    },
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expand_cpuset(spec: str) -> set[int]:
    cpus: set[int] = set()
    for raw_part in str(spec).split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "-" in part:
            low_raw, high_raw = part.split("-", 1)
            low = int(low_raw)
            high = int(high_raw)
            if low < 0 or high < low:
                raise ValueError(f"invalid cpuset range: {part}")
            cpus.update(range(low, high + 1))
        else:
            cpu = int(part)
            if cpu < 0:
                raise ValueError(f"invalid cpuset CPU: {part}")
            cpus.add(cpu)
    if not cpus:
        raise ValueError(f"empty cpuset: {spec!r}")
    return cpus


def validate_controller_client_cpusets() -> Dict[str, Any]:
    expanded = {
        scene_name: expand_cpuset(spec)
        for scene_name, spec in CONTROLLER_CLIENT_CPUSETS.items()
    }
    if set(expanded) != set(SCENES):
        raise ValueError(
            "controller/client cpuset scene mismatch: "
            f"configured={sorted(expanded)} expected={sorted(SCENES)}"
        )
    for scene_name, cpus in expanded.items():
        if max(cpus) > MAX_CONTROLLER_CLIENT_CPU:
            raise ValueError(
                f"controller/client cpuset exceeds CPU {MAX_CONTROLLER_CLIENT_CPU}: "
                f"scene={scene_name} cpus={CONTROLLER_CLIENT_CPUSETS[scene_name]}"
            )
    scene_names = sorted(expanded)
    for index, left_name in enumerate(scene_names):
        for right_name in scene_names[index + 1 :]:
            overlap = expanded[left_name].intersection(expanded[right_name])
            if overlap:
                raise ValueError(
                    f"controller/client cpuset overlap: {left_name} vs {right_name}: "
                    f"{sorted(overlap)}"
                )
    reserved = {
        name: expand_cpuset(spec) for name, spec in EXTERNAL_RESERVED_CPUSETS.items()
    }
    for scene_name, cpus in expanded.items():
        for owner, reserved_cpus in reserved.items():
            overlap = cpus.intersection(reserved_cpus)
            if overlap:
                raise ValueError(
                    f"controller/client cpuset overlaps external owner={owner}: "
                    f"scene={scene_name} cpus={sorted(overlap)}"
                )
    return {
        "scope": "controller_and_all_inherited_workload_clients",
        "allowed_cpu_range": "0-255",
        "max_allowed_cpu": MAX_CONTROLLER_CLIENT_CPU,
        "scene_cpusets": dict(CONTROLLER_CLIENT_CPUSETS),
        "external_reserved_cpusets": dict(EXTERNAL_RESERVED_CPUSETS),
        "pairwise_disjoint": True,
        "external_overlap": False,
    }


def canonical_job_files() -> List[Path]:
    files = sorted(
        (path for path in JOB_SQL.glob("*.sql") if re.fullmatch(r"\d+[a-z]\.sql", path.name)),
        key=lambda path: (int(re.match(r"\d+", path.name).group()), path.name),
    )
    if len(files) != 113:
        raise RuntimeError(f"canonical JOB expected 113 SQL files, got {len(files)}")
    return files


def workers_for(scene: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = read_json(scene["pool"])
    if not isinstance(raw, list) or len(raw) != 2:
        raise RuntimeError(f"worker pool must contain exactly two workers: {scene['pool']}")
    workers: List[Dict[str, Any]] = []
    for item in raw:
        worker = copy.deepcopy(item)
        worker["dbname"] = scene["dbname"]
        worker["targets"] = [scene["target"]]
        if worker["container"] in CPUSETS:
            worker["cpuset_cpus"] = CPUSETS[worker["container"]]
        if worker.get("disk_root") != "/disk1" or not str(worker.get("data_dir", "")).startswith("/disk1/"):
            raise RuntimeError(f"worker is not on disk1: {worker}")
        workers.append(worker)
    return workers


def workload_plan_file(scene_name: str, cfg: Dict[str, Any], scene_dir: Path) -> Path:
    if scene_name == "job_memory":
        output = scene_dir / "canonical_job_113_plan.sql"
        chunks = [f"-- {path.name}\n{path.read_text(encoding='utf-8').strip()}" for path in canonical_job_files()]
        output.write_text("\n\n".join(chunks) + "\n", encoding="utf-8")
        return output
    configured = Path(cfg["binary_plan_tuning"]["workload"]["plan_sql_file"])
    if not configured.is_file():
        raise FileNotFoundError(configured)
    return configured


def configure_scene(
    scene_name: str,
    scene: Dict[str, Any],
    run_root: Path,
    restart_meta: Dict[str, str],
) -> Path:
    if not OPERATOR_RULE_ARTIFACT.is_file():
        raise FileNotFoundError(OPERATOR_RULE_ARTIFACT)
    if not FULL_WORKLOAD_LAUNCHER.is_file():
        raise FileNotFoundError(FULL_WORKLOAD_LAUNCHER)
    cfg = read_json(scene["template"])
    workers = workers_for(scene)
    scene_dir = run_root / scene_name
    scene_dir.mkdir(parents=True, exist_ok=False)
    bpt = cfg["binary_plan_tuning"]
    bpt["output_dir"] = str(scene_dir)
    bpt["workload_name"] = scene["workload"]
    bpt["workload_context"]["sql_count"] = 113 if scene_name == "job_memory" else (22 if scene_name == "tpch_memory" else 6)
    bpt["workload_context"].pop("job_query_files", None)
    plan_sql_file = workload_plan_file(scene_name, cfg, scene_dir)
    expected_plan_query_count = PLAN_QUERY_COUNTS[scene_name]
    bpt["workload"]["plan_sql_file"] = str(plan_sql_file)
    bpt["workload"]["plan_coverage_gate"] = {
        "enabled": scene_name not in {"xp_cpu", "xp_io"},
        "catalog_complete": scene_name == "job_memory",
        "coverage_scope": (
            "canonical_job_113"
            if scene_name == "job_memory"
            else (
                "tpch_q1_q22"
                if scene_name == "tpch_memory"
                else "hybench_xp_at1_at6_iq1_iq6"
            )
        ),
        "expected_query_count": expected_plan_query_count,
        "expected_query_ids": [
            f"file_{index}" for index in range(1, expected_plan_query_count + 1)
        ],
        "plan_sql_sha256": sha256_file(plan_sql_file),
        "block_physical_base_topk": False,
        "fail_closed_before_physical_workload": False,
    }
    endpoint_screening = bpt.get("endpoint_screening") or {}
    endpoint_screening["max_queries"] = max(
        int(endpoint_screening.get("max_queries") or 0),
        expected_plan_query_count,
    )
    bpt["endpoint_screening"] = endpoint_screening
    base_acceptance = bpt.get("acceptance_policy") or {}
    target_overrides = (
        base_acceptance.get("target_overrides")
        if isinstance(base_acceptance.get("target_overrides"), dict)
        else {}
    )
    target_acceptance = (
        target_overrides.get(scene["target"])
        if isinstance(target_overrides.get(scene["target"]), dict)
        else {}
    )
    effective_acceptance = {**base_acceptance, **target_acceptance}
    outer_min_qps_gain_pct = float(
        effective_acceptance.get(
            "min_db_qps_gain_pct", DIRECTIONAL_CHANGE_THRESHOLD_PCT
        )
    )
    outer_min_target_drop_pct = float(
        effective_acceptance.get(
            "min_target_drop_pct", DIRECTIONAL_CHANGE_THRESHOLD_PCT
        )
    )
    outer_min_other_up_pct = max(
        DIRECTIONAL_CHANGE_THRESHOLD_PCT,
        float(
            effective_acceptance.get(
                "min_other_up_pct", DIRECTIONAL_CHANGE_THRESHOLD_PCT
            )
        ),
    )
    if outer_min_qps_gain_pct <= 0.0 or outer_min_target_drop_pct <= 0.0:
        raise ValueError(
            "formal outer acceptance thresholds must be positive: "
            f"scene={scene_name} qps={outer_min_qps_gain_pct} "
            f"target={outer_min_target_drop_pct}"
        )
    search = bpt["binary_search"]
    search.update(
        {
            "max_rounds": 100,
            "max_candidates_per_round": 5,
            "accepted_shrink_target": 10,
            "plan_probe_workers": 4,
            "stop_only_on_epsilon": False,
            "full_bruteforce": False,
            "disable_candidate_reduction": False,
            "run_plan_probe_for_full_bruteforce": True,
            "no_improvement_round_limit": 0,
            "range_anchor_policy": NESTED_POLICY,
            "replay_candidate_pruning": {"enabled": False},
            "operator_resource_reject_rules": {
                "enabled": True,
                "artifact_path": str(OPERATOR_RULE_ARTIFACT),
                "artifact_sha256": sha256_file(OPERATOR_RULE_ARTIFACT),
                "contract_id": scene["operator_rule_contract"],
                "envelope_relative_tolerance": 0.05,
            },
            "nested_loop": {
                "outer_acceptance": {
                    "min_db_qps_gain_pct": outer_min_qps_gain_pct,
                    "min_target_drop_pct": outer_min_target_drop_pct,
                    "min_other_up_pct": outer_min_other_up_pct,
                    "threshold_source": "binary_plan_tuning.acceptance_policy",
                    "non_target_bottleneck_threshold_pct": 80.0,
                    "acceptance_tier_downgrade_after_rounds": 2,
                },
                "fallback_plan_change_replay_expansion": {
                    "enabled": scene_name not in {"xp_cpu", "xp_io"},
                    "trigger_after_fallback_miss_rounds": 1,
                    "max_extra_candidates_per_round": 5,
                },
            },
        }
    )
    validation = bpt["validation"]
    validation["parallel_baseline"] = {
        "enabled": True,
        "compare_mode": "same_worker",
        "parallelism": 2,
        "required": True,
    }
    validation["shared_disk_restore_barrier"] = True
    validation["docker_runtime"]["container"] = workers[0]["container"]
    if scene_name == "tpch_memory":
        # One native serial Q1-Q22 pass completes in roughly 20-30 seconds;
        # requiring 45 one-second samples would reject an otherwise complete
        # 22/22 pass as a technical failure.
        validation["docker_runtime"]["min_stable_samples"] = 15
    validation["docker_workers"] = {
        "enabled": True,
        "cpu_memory_parallelism": 2,
        "io_parallelism": 2,
        "none_parallelism": 2,
        "same_ssd_adaptive_parallelism": {
            "enabled": True,
            "initial": 2,
            "preferred": 8,
            "stable_cap": 12,
            "allow_stable_cap": True,
            "daily_mean_limit_pct": 75.0,
            "daily_peak_limit_pct": 90.0,
            "warning_mean_pct": 85.0,
            "warning_peak_pct": 95.0,
            "physical_device": "nvme1n1",
            "effective_worker_cap": 2,
        },
        "workers": workers,
    }
    hybench = validation["hybench_xp"]
    hybench["restore_from_template_before_validation"] = True
    hybench["template_db"] = scene["template_db"]
    hybench["props_output_dir"] = "workload_props"
    for forbidden in ("ap_query_ids", "ap_query_weights", "ap_query_sequence", "ap_query_sequence_limit"):
        hybench.pop(forbidden, None)
    extra = hybench.get("extra_props") if isinstance(hybench.get("extra_props"), dict) else {}
    for forbidden in ("ap_query_ids", "ap_query_weights", "ap_query_sequence", "ap_query_sequence_limit"):
        extra.pop(forbidden, None)
    hybench["extra_props"] = extra
    if scene["workload"] == "hybench_xp":
        hybench.pop("random_seed", None)
        hybench.update(
            {
                "enabled": True,
                "mode": "runxp",
                "hybench_dir": str(NATIVE_RELEASE),
                "launcher": str(ROOT / "shell/run_hybench_native_xp_isolated.sh"),
                "base_props_file": str(NATIVE_RELEASE / "conf/pg.props"),
                "base_props_sha256": sha256_file(NATIVE_RELEASE / "conf/pg.props"),
                "statement_file": str(NATIVE_RELEASE / "conf/stmt_postgres.toml"),
                "native_jar_file": str(NATIVE_RELEASE / "lib/HyBench-1.0-SNAPSHOT.jar"),
                "native_jar_sha256": sha256_file(
                    NATIVE_RELEASE / "lib/HyBench-1.0-SNAPSHOT.jar"
                ),
                "xtpclient": 32,
                "xapclient": 4,
                "at1_percent": 40,
                "at2_percent": 30,
                "at3_percent": 15,
                "at4_percent": 5,
                "at5_percent": 7,
                "at6_percent": 3,
                "xp_run_mins": 5,
                "run_mins": 5,
                "statement_file_sha256": sha256_file(
                    NATIVE_RELEASE / "conf/stmt_postgres.toml"
                ),
                "allowed_tunable_workload_fields": [
                    "xtpclient",
                    "xapclient",
                    "at1_percent",
                    "at2_percent",
                    "at3_percent",
                    "at4_percent",
                    "at5_percent",
                    "at6_percent",
                ],
            }
        )
    else:
        hybench.update(
            {
                "enabled": True,
                "mode": "generic",
                "hybench_dir": str(FRAMEWORK),
                "launcher": str(FULL_WORKLOAD_LAUNCHER),
                "base_props_file": str(FRAMEWORK / "config/generic_pg.props"),
                "statement_file": str(FRAMEWORK / "config/generic_statements.toml"),
                "run_mins": 0,
                "xp_run_mins": 0,
            }
        )
    cfg["postgres"].update(
        {
            "host": workers[0]["host"],
            "port": workers[0]["port"],
            "dbname": scene["dbname"],
        }
    )
    meta = bpt.setdefault("experiment_meta", {})
    meta.update(
        {
            "exp_id": f"PG-BINARY-PLAN-{scene_name}-20260809",
            "benchmark": scene["workload"],
            "method": "binary_interval_corner_nested_top5_parallel2_v2",
            "candidate_generator": "all_lr_interval_corners",
            "algorithm_c_contract_compliant": False,
            "algorithm_c_contract_gap": (
                "no_experience_table_T_or_gradient_g_j_eta_candidate_direction"
            ),
            "bottleneck": scene["target"],
            "accepted_shrink_target": 10,
            "workload_policy": "20260806-pg-experiment-workload-policy",
            "replay_top_k": 5,
            "replay_parallelism": 2,
            "replay_wave_shape": [2, 2, 1],
            "controller_client_cpuset": CONTROLLER_CLIENT_CPUSETS[scene_name],
            "controller_client_allowed_cpu_range": "0-255",
            **restart_meta,
        }
    )
    config_path = scene_dir / "optimizer_config.json"
    write_json(config_path, cfg)
    write_json(scene_dir / "worker_assignment.json", workers)
    return config_path


def command_env(scene_name: str) -> Dict[str, str]:
    env = os.environ.copy()
    env.update({"PYTHONUNBUFFERED": "1", "PYTHONPATH": str(ROOT)})
    if scene_name == "job_memory":
        env.update(
            {
                "GENERIC_WORKLOAD": "job",
                "GENERIC_RUN_SECONDS": "0",
                "JOB_SQL_DIR": str(JOB_SQL),
                "JOB_QUERY_FILES": "",
                "JOB_MAX_QUERIES": "0",
                "JOB_STATEMENT_TIMEOUT_MS": "1800000",
                "JOB_WORKERS": "8",
                "JOB_MAX_CONCURRENT_LONG_QUERIES": "8",
                "JOB_LONG_QUERY_FRACTION": "0.2",
                "JOB_TARGET_WALL_TIME_S": "900",
            }
        )
    elif scene_name == "tpch_memory":
        env.update(
            {
                "GENERIC_WORKLOAD": "tpch",
                "GENERIC_RUN_SECONDS": "0",
                "BENCHBASE_SOURCE_ROOT": str(BENCHBASE),
                "BENCHBASE_RUNTIME_ROOT": str(RUN_BASE / "benchbase_runtime"),
                "BENCHBASE_SCALEFACTOR": "5",
                "BENCHBASE_TERMINALS": "1",
                "BENCHBASE_SERIAL": "true",
                "BENCHBASE_JAVA_XMX": "-Xmx2g",
            }
        )
    return env


def launch(run_root: Path, configs: Dict[str, Path]) -> None:
    affinity_contract = validate_controller_client_cpusets()
    taskset = shutil.which("taskset")
    if not taskset:
        raise FileNotFoundError("taskset is required for controller/client affinity")
    pids: Dict[str, int] = {}
    workload_contracts: Dict[str, Dict[str, str]] = {}
    affinity_audit: Dict[str, Dict[str, Any]] = {}
    started_processes: List[subprocess.Popen[bytes]] = []
    for scene_name, config_path in configs.items():
        scene = SCENES[scene_name]
        log_path = run_root / scene_name / "controller.log"
        handle = log_path.open("ab", buffering=0)
        controller_command = [
            str(PYTHON),
            "-B",
            str(ROOT / "binary_plan_resource_tuner.py"),
            "--config",
            str(config_path),
            "--output-dir",
            str(run_root / scene_name),
            "binary-search",
            "--target",
            scene["target"],
            "--active-params",
            ",".join(COST_PARAMS),
            "--resume",
        ]
        expected_affinity = expand_cpuset(CONTROLLER_CLIENT_CPUSETS[scene_name])
        command = [taskset, "-c", CONTROLLER_CLIENT_CPUSETS[scene_name], *controller_command]
        effective_env = command_env(scene_name)
        audited_keys = (
            "GENERIC_WORKLOAD",
            "GENERIC_RUN_SECONDS",
            "JOB_SQL_DIR",
            "JOB_QUERY_FILES",
            "JOB_MAX_QUERIES",
            "JOB_STATEMENT_TIMEOUT_MS",
            "JOB_WORKERS",
            "JOB_MAX_CONCURRENT_LONG_QUERIES",
            "JOB_LONG_QUERY_FRACTION",
            "JOB_TARGET_WALL_TIME_S",
            "BENCHBASE_SOURCE_ROOT",
            "BENCHBASE_RUNTIME_ROOT",
            "BENCHBASE_SCALEFACTOR",
            "BENCHBASE_TERMINALS",
            "BENCHBASE_SERIAL",
        )
        workload_contracts[scene_name] = {
            key: effective_env[key] for key in audited_keys if key in effective_env
        }
        try:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=effective_env,
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            started_processes.append(process)
            deadline = time.monotonic() + 2.0
            observed_affinity: set[int] = set()
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(
                        f"controller exited during affinity setup: scene={scene_name} "
                        f"returncode={process.returncode}"
                    )
                observed_affinity = set(os.sched_getaffinity(process.pid))
                if observed_affinity == expected_affinity:
                    break
                time.sleep(0.02)
            if observed_affinity != expected_affinity:
                raise RuntimeError(
                    f"controller affinity mismatch: scene={scene_name} "
                    f"expected={sorted(expected_affinity)} observed={sorted(observed_affinity)}"
                )
        except Exception:
            for started in started_processes:
                try:
                    os.killpg(started.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            raise
        pids[scene_name] = process.pid
        affinity_audit[scene_name] = {
            "pid": process.pid,
            "expected_cpuset": CONTROLLER_CLIENT_CPUSETS[scene_name],
            "observed_cpus": sorted(observed_affinity),
            "verified": observed_affinity == expected_affinity,
            "inheritance": "all workload subprocesses inherit unless they explicitly repin",
        }
        (run_root / scene_name / "controller.pid").write_text(f"{process.pid}\n", encoding="utf-8")
        write_json(run_root / scene_name / "controller_client_affinity.json", affinity_audit[scene_name])
    write_json(run_root / "controllers.json", pids)
    write_json(run_root / "controller_client_affinity_audit.json", affinity_audit)
    manifest_path = run_root / "launch_manifest.json"
    manifest = read_json(manifest_path)
    manifest["effective_workload_environment"] = workload_contracts
    manifest["controllers"] = pids
    manifest["controller_client_affinity_contract"] = affinity_contract
    manifest["controller_client_affinity_audit"] = affinity_audit
    manifest["launched_at_epoch_s"] = time.time()
    write_json(manifest_path, manifest)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "launch"))
    parser.add_argument("--tag", default=time.strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--scenes", default=",".join(SCENES))
    parser.add_argument("--supersedes-run", default="")
    parser.add_argument("--restart-reason", default="")
    args = parser.parse_args()
    selected = [item.strip() for item in args.scenes.split(",") if item.strip()]
    unknown = sorted(set(selected).difference(SCENES))
    if unknown or not selected:
        raise ValueError(f"invalid --scenes: unknown={unknown}, selected={selected}")
    run_root = RUN_BASE / args.tag
    if args.command == "prepare":
        affinity_contract = validate_controller_client_cpusets()
        run_root.mkdir(parents=True, exist_ok=False)
        restart_meta = {
            key: value
            for key, value in {
                "supersedes_run": args.supersedes_run,
                "restart_reason": args.restart_reason,
            }.items()
            if value
        }
        configs = {
            name: configure_scene(name, scene, run_root, restart_meta)
            for name, scene in SCENES.items()
        }
        write_json(
            run_root / "launch_manifest.json",
            {
                "run_root": str(run_root),
                "scenes": {name: str(path) for name, path in configs.items()},
                "top_k": 5,
                "fallback_plan_change_replay_expansion": {
                    "trigger": "after_one_fallback_tier_top5_miss",
                    "plan_tree_change_required": True,
                    "unique_plan_fingerprint_required": True,
                    "max_extra_candidates_per_round": 5,
                    "max_real_replays_in_expanded_round": 10,
                },
                "operator_resource_reject_rules": {
                    "artifact": str(OPERATOR_RULE_ARTIFACT),
                    "artifact_sha256": sha256_file(OPERATOR_RULE_ARTIFACT),
                    "hard_reject_match": "exact_operator_direction_set_with_observed_magnitude_envelope",
                    "topk_backfill": True,
                    "qps_used": False,
                },
                "parallelism_per_scene": 2,
                "wave_shape": [2, 2, 1],
                "expanded_wave_shape": [2, 2, 1, 2, 2, 1],
                "accepted_shrink_target": 10,
                "method": "binary_interval_corner_nested_top5_parallel2_v2",
                "algorithm_c_contract_compliant": False,
                "algorithm_c_contract_gap": (
                    "no_experience_table_T_or_gradient_g_j_eta_candidate_direction"
                ),
                "controller_client_affinity_contract": affinity_contract,
                **restart_meta,
            },
        )
        print(run_root, flush=True)
        return 0
    if not run_root.is_dir():
        raise FileNotFoundError(f"prepare first: {run_root}")
    configs = {name: run_root / name / "optimizer_config.json" for name in selected}
    launch(run_root, configs)
    print(run_root, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
