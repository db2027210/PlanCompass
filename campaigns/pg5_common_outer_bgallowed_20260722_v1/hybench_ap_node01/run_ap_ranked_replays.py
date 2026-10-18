#!/usr/bin/env python3
"""Replay the sealed AP six-parameter exact-plan representatives."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time
import traceback
from typing import Any, Dict, List, Optional

import sys

LANE_DIR = Path(__file__).resolve().parent
BASE_ROOT = LANE_DIR.parents[2]
WORKSPACE_CORE = BASE_ROOT / "workspace/pg"
REPO_ROOT = (
    WORKSPACE_CORE
    if (WORKSPACE_CORE / "common_outer_loop.py").is_file()
    else BASE_ROOT
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common_outer_loop import binary_shrink_parameter_ranges, decide_outer_acceptance


CONTAINER = "p24_postgresql_recost_gradient_descent_ap_pg5hya201v1"
ACTIVE_PARAMS = (
    "seq_page_cost", "random_page_cost", "cpu_tuple_cost",
    "cpu_operator_cost", "parallel_setup_cost", "parallel_tuple_cost",
)
BASELINE_REL = "results/shared_baseline/P0_repeat_1_attempt_34_retry3"


@dataclass(frozen=True)
class ReplayRange:
    min_value: float
    max_value: float


INITIAL_RANGES = {
    "seq_page_cost": ReplayRange(0.1, 100.0),
    "random_page_cost": ReplayRange(0.1, 100.0),
    "cpu_tuple_cost": ReplayRange(0.001, 0.1),
    "cpu_operator_cost": ReplayRange(0.00025, 0.025),
    "parallel_setup_cost": ReplayRange(1.0, 10000.0),
    "parallel_tuple_cost": ReplayRange(0.001, 1.0),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def run(command: List[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, check=check, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def container_running() -> bool:
    result = run(
        ["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER],
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def inject_resident_helper(lane: Path, evidence_path: Path) -> Dict[str, Any]:
    helper = lane / "resident_memory_helper"
    helper_sha = sha256(helper)
    if helper_sha != "2fc5f4bf0e36a1d9f6abc773d6a719d7baa6c7596ed34d346eee56a8de209ecb":
        raise RuntimeError("resident memory helper hash mismatch")
    run(["docker", "cp", str(helper), f"{CONTAINER}:/tmp/resident_memory_helper"])
    run(["docker", "exec", CONTAINER, "chmod", "0555", "/tmp/resident_memory_helper"])
    launch = run([
        "docker", "exec", CONTAINER, "sh", "-c",
        "/tmp/resident_memory_helper 31457280 >/tmp/resident_memory_helper.log 2>&1 & echo $!",
    ])
    helper_pid = int(launch.stdout.strip())
    ready = False
    for _ in range(80):
        check = run(
            ["docker", "exec", CONTAINER, "grep", "-q", "^resident_memory_ready ",
             "/tmp/resident_memory_helper.log"],
            check=False,
        )
        if check.returncode == 0:
            ready = True
            break
        time.sleep(0.25)
    if not ready:
        raise RuntimeError("resident memory helper did not become ready")
    helper_cgroup = run(
        ["docker", "exec", CONTAINER, "cat", f"/proc/{helper_pid}/cgroup"]
    ).stdout
    init_cgroup = run(
        ["docker", "exec", CONTAINER, "cat", "/proc/1/cgroup"]
    ).stdout
    if helper_cgroup != init_cgroup:
        raise RuntimeError("resident helper does not share the container cgroup")
    evidence = {
        "schema_version": 1,
        "container": CONTAINER,
        "container_id": run(
            ["docker", "inspect", "-f", "{{.Id}}", CONTAINER]
        ).stdout.strip(),
        "helper_pid": helper_pid,
        "requested_bytes": 31457280,
        "helper_sha256": helper_sha,
        "same_cgroup_verified": True,
        "injected_at_epoch": time.time(),
        "cleanup_pending": True,
    }
    write_json(evidence_path, evidence)
    return evidence


def candidate_validation(
    point_dir: Path,
    helper_evidence: Dict[str, Any],
    *,
    process_returncode: int,
    expected_params: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    summary = json.loads((point_dir / "measurement_summary.json").read_text(encoding="utf-8"))
    db_qps = json.loads((point_dir / "measurement_db_qps.json").read_text(encoding="utf-8"))
    correctness = json.loads(
        (point_dir / "measurement_ap13_correctness_gate.json").read_text(encoding="utf-8")
    )
    point = json.loads((point_dir / "point_result.json").read_text(encoding="utf-8"))
    stability = summary.get("resource_stability") or {}
    memory = float(summary["resource_mean_utilization"]["memory"])
    qps = float(db_qps["db_qps"])
    finite = math.isfinite(memory) and math.isfinite(qps)
    expected_params = expected_params or {}
    point_costs = point.get("cost_parameters") or {}
    applied_params_match_point = set(expected_params).issubset(point_costs) and all(
        float(point_costs[name]) == float(value)
        for name, value in expected_params.items()
    )
    readback = point.get("readback") or {}
    hard_checks = {
        "candidate_process_exit_zero": process_returncode == 0,
        "point_passed": point.get("status") in {"complete", "passed"},
        "db_qps_passed": db_qps.get("passed") is True,
        "db_qps_source_exact": db_qps.get("source")
        == "pg_stat_statements_sum_calls_delta_per_second",
        "ap_qps_substitute_false": db_qps.get("ap_qps_used_as_substitute") is False,
        "ap13_correctness_passed": correctness.get("status") == "passed",
        "stability_metric_fields_memory_only": stability.get(
            "stability_metric_fields"
        ) == ["memory_utilization"],
        "resource_stability_passed": stability.get("passed") is True
        and stability.get("stable") is True,
        "resident_memory_same_cgroup": helper_evidence.get("same_cgroup_verified") is True,
        "resident_memory_cleanup_complete": helper_evidence.get("cleanup_complete") is True,
        "postgresql_settings_readback_passed": readback.get(
            "fixed_settings_passed"
        ) is True,
        "applied_params_match_point": applied_params_match_point,
        "finite_metrics": finite,
    }
    return {
        "hard_gate_passed": all(hard_checks.values()),
        "hard_checks": hard_checks,
        "candidate_process_returncode": process_returncode,
        "db_qps": qps,
        "utilization_pct": {
            name: 100.0 * float(summary["resource_mean_utilization"][name])
            for name in ("cpu", "memory", "io")
        },
        "point_dir": str(point_dir),
        "measurement_summary_sha256": sha256(point_dir / "measurement_summary.json"),
        "db_qps_evidence_sha256": sha256(point_dir / "measurement_db_qps.json"),
        "correctness_evidence_sha256": sha256(
            point_dir / "measurement_ap13_correctness_gate.json"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lane-root", type=Path, required=True)
    parser.add_argument("--ranking-json", type=Path, required=True)
    parser.add_argument("--selection-plan", type=Path, required=True)
    parser.add_argument("--state-json", type=Path)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--python-bin", type=Path, required=True)
    parser.add_argument("--start-rank", type=int, default=1)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--replicate-policy-v1", action="store_true")
    parser.add_argument("--stop-after-one", action="store_true")
    parser.add_argument("--attempt-run-id", default="outer_ap6_a1_r01")
    args = parser.parse_args()
    lane = args.lane_root.resolve()
    output = args.output_root.resolve()
    if not 1 <= args.start_rank <= 10:
        raise ValueError("--start-rank must be in [1, 10]")
    output.mkdir(parents=True, exist_ok=args.resume)
    if args.resume and not output.is_dir():
        raise RuntimeError("resume output root is unavailable")
    progress_path = output / "replay_progress.json"
    if container_running():
        raise RuntimeError(f"owned container already exists: {CONTAINER}")
    ranking_sha256 = sha256(args.ranking_json)
    ranking = json.loads(args.ranking_json.read_text(encoding="utf-8"))
    all_candidates = ranking["candidate_rankings"]
    if len(all_candidates) != 63:
        raise RuntimeError("sealed AP6 ranking must contain exactly 63 candidates")
    for candidate in all_candidates:
        optimized_params = candidate.get("optimized_params") or {}
        if set(optimized_params) != set(ACTIVE_PARAMS):
            raise RuntimeError(
                f"rank {candidate.get('rank')} is not an exact six-parameter AP candidate"
            )
    selection = json.loads(args.selection_plan.read_text(encoding="utf-8"))
    source_artifacts = selection.get("source_artifacts") or {}
    if source_artifacts.get("annotated_ranking_sha256") != ranking_sha256:
        raise RuntimeError("selection plan and annotated ranking hash mismatch")
    representative_rows = selection.get("representative_order")
    if not isinstance(representative_rows, list) or not representative_rows:
        raise RuntimeError("selection plan has no exact-plan representatives")
    candidate_by_rank = {
        int(candidate["rank"]): candidate for candidate in all_candidates
    }
    candidates: List[Dict[str, Any]] = []
    for replay_order, row in enumerate(representative_rows, start=1):
        if int(row.get("replay_order") or 0) != replay_order:
            raise RuntimeError("selection replay order is not contiguous")
        candidate = candidate_by_rank.get(int(row.get("rank") or 0))
        if candidate is None:
            raise RuntimeError(f"selection rank is absent: {row.get('rank')}")
        if row.get("exact_plan_cluster_id") != candidate.get(
            "plan_similarity_cluster_id"
        ):
            raise RuntimeError(f"rank {candidate['rank']}: plan cluster mismatch")
        candidates.append(candidate)
    applied_by_rank: Dict[int, Dict[str, Any]] = {}
    for candidate in candidates:
        rank = int(candidate["rank"])
        roundtrip = candidate.get("postgresql_guc_roundtrip")
        continuous = (candidate.get("continuous_model_only") or {}).get(
            "optimized_params"
        )
        if not isinstance(roundtrip, dict) or set(roundtrip) != set(ACTIVE_PARAMS):
            raise RuntimeError(f"rank {rank}: missing PostgreSQL GUC round-trip")
        if not isinstance(continuous, dict) or set(continuous) != set(ACTIVE_PARAMS):
            raise RuntimeError(f"rank {rank}: missing continuous audit point")
        if not all(
            row.get("idempotent") is True
            and float(row.get("applied_value")) == float(candidate["optimized_params"][name])
            for name, row in roundtrip.items()
        ):
            raise RuntimeError(f"rank {rank}: non-idempotent or mismatched applied GUC")
        applied_by_rank[rank] = {
            "source_continuous_params": continuous,
            "applied_params": candidate["optimized_params"],
            "applied_signature_sha256": candidate.get(
                "applied_parameter_signature_sha256"
            ),
            "exact_plan_cluster_id": candidate.get("plan_similarity_cluster_id"),
        }

    def resolved_params(candidate: Dict[str, Any]) -> Dict[str, float]:
        rank = int(candidate["rank"])
        record = applied_by_rank.get(rank)
        if record is None:
            raise RuntimeError(f"rank {rank} is not a sealed plan representative")
        continuous = {
            name: float(candidate["continuous_model_only"]["optimized_params"][name])
            for name in ACTIVE_PARAMS
        }
        if record.get("source_continuous_params") != continuous:
            raise RuntimeError(f"rank {rank}: continuous model point mismatch")
        if record.get("exact_plan_cluster_id") != candidate.get(
            "plan_similarity_cluster_id"
        ):
            raise RuntimeError(f"rank {rank}: exact plan cluster mismatch")
        params = {
            name: float(record["applied_params"][name])
            for name in ACTIVE_PARAMS
        }
        if set(params) != set(ACTIVE_PARAMS):
            raise RuntimeError(f"rank {rank}: incomplete applied params")
        return params
    state_path = args.state_json.resolve() if args.state_json else None
    if state_path is None:
        baseline_dir = lane / BASELINE_REL
        baseline_summary = json.loads(
            (baseline_dir / "measurement_summary.json").read_text(encoding="utf-8")
        )
        incumbent = {
            "db_qps": float(baseline_summary["db_qps"]),
            "utilization_pct": {
                name: 100.0 * float(baseline_summary["resource_mean_utilization"][name])
                for name in ("cpu", "memory", "io")
            },
            "params": json.loads(
                (baseline_dir / "point_result.json").read_text(encoding="utf-8")
            )["cost_parameters"],
            "source": str(baseline_dir),
        }
        active_ranges = dict(INITIAL_RANGES)
        accepted_shrinks_before = 0
        source_state_sha256 = None
    else:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("status") != "accepted_incumbent_state":
            raise RuntimeError("state-json is not an accepted incumbent state")
        incumbent = dict(state.get("incumbent") or {})
        if not set(incumbent.get("params") or {}).issuperset(ACTIVE_PARAMS):
            raise RuntimeError("state incumbent params are incomplete")
        raw_ranges = state.get("ranges") or {}
        if set(raw_ranges) != set(ACTIVE_PARAMS):
            raise RuntimeError("state ranges are incomplete")
        active_ranges = {
            name: ReplayRange(
                float(raw_ranges[name]["min_value"]),
                float(raw_ranges[name]["max_value"]),
            )
            for name in ACTIVE_PARAMS
        }
        accepted_shrinks_before = int(state.get("accepted_shrinks") or 0)
        source_state_sha256 = sha256(state_path)
    records: List[Dict[str, Any]] = []
    accepted: List[Dict[str, Any]] = []
    if args.replicate_policy_v1:
        write_json(
            output / "replicate_controller_v1_authorized.json",
            {
                "schema_version": 1,
                "policy_id": "outer_candidate_first_unstable_three_total_replicates_v1",
                "controller_pid": os.getpid(),
                "applies_from_rank_inclusive": 1,
                "first_replicate_stable_action": "use_first_directly",
                "first_replicate_unstable_action": "require_three_total_valid_replicates_then_mean_all_three",
                "second_replicate_stable_after_first_unstable_action": "do_not_select; continue_to_third_and_average",
                "technical_failures_enter_average": False,
                "authorized_at_epoch": time.time(),
            },
        )
    if args.start_rank > 1:
        if not args.resume:
            raise RuntimeError("--start-rank > 1 requires --resume")
        for prior_position in range(1, args.start_rank):
            prior_candidate = candidates[prior_position - 1]
            prior_params_path = output / f"rank_{prior_position:02d}_params.json"
            prior_helper_path = output / f"rank_{prior_position:02d}_resident_memory.json"
            prior_log_path = output / f"rank_{prior_position:02d}_candidate.log"
            prior_point_dir = (
                lane / "results/experience_outer"
                / f"candidate_outer_a1_r01_rank_{prior_position:04d}"
            )
            prior_validation_path = output / f"rank_{prior_position:02d}_validation.json"
            if prior_validation_path.is_file():
                prior_record = json.loads(prior_validation_path.read_text(encoding="utf-8"))
            else:
                prior_params = json.loads(prior_params_path.read_text(encoding="utf-8"))
                prior_helper = json.loads(prior_helper_path.read_text(encoding="utf-8"))
                prior_returncode = 1 if prior_position == 1 else 0
                prior_validation = candidate_validation(
                    prior_point_dir,
                    prior_helper,
                    process_returncode=prior_returncode,
                    expected_params=prior_params,
                )
                prior_decision = decide_outer_acceptance(
                    incumbent_db_qps=incumbent["db_qps"],
                    candidate_db_qps=prior_validation["db_qps"],
                    incumbent_target_utilization=incumbent["utilization_pct"]["memory"],
                    candidate_target_utilization=prior_validation["utilization_pct"]["memory"],
                    target_resource="memory",
                    incumbent_resource_utilization=incumbent["utilization_pct"],
                    candidate_resource_utilization=prior_validation["utilization_pct"],
                    non_target_bottleneck_threshold=80.0,
                    hard_gate_passed=prior_validation["hard_gate_passed"],
                ).to_dict()
                prior_record = {
                    "rank": prior_position,
                    "subset_id": prior_candidate.get("subset_id"),
                    "params": prior_params,
                    "validation": prior_validation,
                    "acceptance": prior_decision,
                    "candidate_log": str(prior_log_path),
                    "resident_memory_evidence": str(prior_helper_path),
                    "reconstructed_during_resume": True,
                }
                write_json(prior_validation_path, prior_record)
            records.append(prior_record)
            if prior_record.get("acceptance", {}).get("accepted") is True:
                accepted.append(prior_record)
    write_json(progress_path, {
        "schema_version": 1, "status": "running", "pid": os.getpid(),
        "initial_top_k": 5, "maximum_top_k": 10, "replayed_count": len(records),
        "current_rank": None, "incumbent": incumbent,
        "ranking_json": str(args.ranking_json), "started_at_epoch": time.time(),
        "resumed": args.resume, "start_rank": args.start_rank,
        "state_json": str(state_path) if state_path else None,
        "state_sha256": source_state_sha256,
        "accepted_shrinks_before": accepted_shrinks_before,
    })
    candidate_env = os.environ.copy()
    candidate_env["PYTHONPATH"] = str(REPO_ROOT)
    candidate_env["PYTHONNOUSERSITE"] = "1"
    candidate_env.update(
        {
            "PATH": (
                f"{BASE_ROOT}/jdk/jdk-23.0.2-oracle-x64/bin:"
                f"{BASE_ROOT}/python311_c7/bin:/usr/local/sbin:/usr/local/bin:"
                "/usr/sbin:/usr/bin:/sbin:/bin"
            ),
            "RA_PG_CAMPAIGN_ID": "pg5_common_outer_bgallowed_20260722_v1",
            "RA_PG_FROZEN_ROOT": str(BASE_ROOT / "frozen_config_v9"),
            "RA_PG_TEMPLATE_ROOT": str(lane / "package/templates/postgresql"),
            "RA_PG_WORK_ROOT": str(lane / "runtime/postgresql"),
            "RA_PG_OUTPUT_ROOT": str(lane / "results/method_runtime"),
            "RA_PG_MOUNT_ROOT": "/mnt/nvme1n1",
            "RA_PG_DISABLE_IO_CAP": "1",
            "RA_PG_PRESERVE_RESTORE_CACHE": "1",
            "RA_PG_CPUSET": "16-23",
            "RA_PG_CPUSET_MEMS": "0",
            "RA_PG_CONTAINER_SUFFIX": "pg5hya201v1",
            "RA_PG_PORT_OFFSET": "7000",
            "RA_PG_IMAGE": (
                "sha256:6c2f8e0ad2949b4e100456f138b1184fe58cb2010cd7a9721008abb87f501458"
            ),
            "RA_PG_JDBC_JAR": str(BASE_ROOT / "assets/postgresql-42.7.4.jar"),
            "RA_PG_HYBENCH_ROOT": str(lane / "package/assets/hybench"),
            "RA_PG_HYBENCH_LAUNCHER": str(
                lane / "package/assets/hybench/hybench"
            ),
            "RA_PG_HYBENCH_STATEMENTS": str(
                lane / "package/assets/hybench/stmt_postgres.toml"
            ),
            "RA_PG_HYBENCH_JAR": str(
                lane
                / "package/assets/hybench/HyBench-1.0-SNAPSHOT-jar-with-dependencies.jar"
            ),
        }
    )

    def run_one_replicate(
        position: int,
        candidate: Dict[str, Any],
        params: Dict[str, float],
        replicate_index: int,
    ) -> Dict[str, Any]:
        stem = f"rank_{position:02d}_rep_{replicate_index:02d}"
        attempt_id = (
            f"{args.attempt_run_id}_rank_{position:04d}_rep_{replicate_index:02d}"
        )
        point_dir = lane / "results/experience_outer" / f"candidate_{attempt_id}"
        log_path = output / f"{stem}_candidate.log"
        helper_path = output / f"{stem}_resident_memory.json"
        write_json(progress_path, {
            "schema_version": 1,
            "status": "replaying_replicate",
            "pid": os.getpid(),
            "initial_top_k": 5,
            "maximum_top_k": 10,
            "replayed_count": len(records),
            "current_rank": position,
            "current_subset_id": candidate.get("subset_id"),
            "current_replicate": replicate_index,
            "replicate_total_if_first_unstable": 3,
            "incumbent": incumbent,
            "updated_at_epoch": time.time(),
        })
        command = [
            str(args.python_bin), "-u", str(lane / "candidate_once.py"),
            "--lane-root", str(lane), "--attempt-id", attempt_id,
            "--candidate-params-json", str(output / f"rank_{position:02d}_params.json"),
            "--cpu-quota-cores", "5.5", "--memory-bytes", "223155000",
            "--io-read-bps", "67108864", "--io-write-bps", "36700160",
        ]
        helper_evidence: Optional[Dict[str, Any]] = None
        with log_path.open("x", encoding="utf-8") as log_handle:
            process = subprocess.Popen(
                command,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
                env=candidate_env,
            )
            for _ in range(720):
                if container_running():
                    helper_evidence = inject_resident_helper(lane, helper_path)
                    break
                if process.poll() is not None:
                    break
                time.sleep(0.25)
            if helper_evidence is None:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=30)
                returncode = int(process.returncode or 1)
            else:
                returncode = int(process.wait())

        if helper_evidence is None:
            validation = {
                "hard_gate_passed": False,
                "hard_checks": {
                    "candidate_process_exit_zero": returncode == 0,
                    "docker_container_created": False,
                    "complete_validation_evidence_available": False,
                },
                "candidate_process_returncode": returncode,
                "validation_error": "candidate did not create its owned Docker container",
                "db_qps": None,
                "utilization_pct": {},
                "point_dir": str(point_dir),
            }
        else:
            helper_evidence["cleanup_complete"] = not container_running()
            helper_evidence["cleanup_pending"] = not helper_evidence["cleanup_complete"]
            write_json(helper_path, helper_evidence)
            try:
                validation = candidate_validation(
                    point_dir,
                    helper_evidence,
                    process_returncode=returncode,
                    expected_params=params,
                )
            except Exception as exc:
                validation = {
                    "hard_gate_passed": False,
                    "hard_checks": {
                        "candidate_process_exit_zero": returncode == 0,
                        "complete_validation_evidence_available": False,
                    },
                    "candidate_process_returncode": returncode,
                    "validation_error": f"{type(exc).__name__}: {exc}",
                    "db_qps": None,
                    "utilization_pct": {},
                    "point_dir": str(point_dir),
                }
        hard_checks = validation.get("hard_checks") or {}
        technical_check_names = (
            "candidate_process_exit_zero",
            "point_passed",
            "db_qps_passed",
            "db_qps_source_exact",
            "ap_qps_substitute_false",
            "ap13_correctness_passed",
            "stability_metric_fields_memory_only",
            "resident_memory_same_cgroup",
            "resident_memory_cleanup_complete",
            "postgresql_settings_readback_passed",
            "applied_params_match_point",
            "finite_metrics",
        )
        technical_success = all(
            hard_checks.get(name) is True for name in technical_check_names
        )
        stable = bool(
            technical_success
            and hard_checks.get("resource_stability_passed") is True
        )
        replicate = {
            "schema_version": 1,
            "rank": int(candidate["rank"]),
            "replay_position": position,
            "subset_id": candidate.get("subset_id"),
            "replicate_index": replicate_index,
            "attempt_id": attempt_id,
            "params": params,
            "optimized_params": params,
            "source_continuous_model_params": candidate["continuous_model_only"][
                "optimized_params"
            ],
            "applied_signature_sha256": applied_by_rank[int(candidate["rank"])][
                "applied_signature_sha256"
            ],
            "point_dir": str(point_dir),
            "candidate_log": str(log_path),
            "resident_memory_evidence": str(helper_path) if helper_evidence else None,
            "candidate_process_returncode": returncode,
            "technical_success": technical_success,
            "technical_check_names": list(technical_check_names),
            "stable": stable,
            "validation": validation,
            "completed_at_epoch": time.time(),
        }
        write_json(output / f"{stem}_validation.json", replicate)
        return replicate

    def run_rank_with_replicates(
        position: int,
        candidate: Dict[str, Any],
    ) -> Dict[str, Any]:
        params = resolved_params(candidate)
        params_path = output / f"rank_{position:02d}_params.json"
        write_json(params_path, params)
        replicates: List[Dict[str, Any]] = []
        valid_replicates: List[Dict[str, Any]] = []
        first_stable_direct = False
        # Technical failures are not candidate evidence and must not consume
        # the three-valid-replicate stability policy.  Bound retries so an
        # unhealthy host still fails closed instead of looping forever.
        for replicate_index in range(1, 7):
            replicate = run_one_replicate(
                position,
                candidate,
                params,
                replicate_index,
            )
            replicates.append(replicate)
            if not replicate["technical_success"]:
                continue
            valid_replicates.append(replicate)
            if len(valid_replicates) == 1 and replicate["stable"]:
                first_stable_direct = True
                break
            if len(valid_replicates) >= 3:
                break

        composite_ledger_path: Optional[Path] = None
        if first_stable_direct:
            validation = valid_replicates[0]["validation"]
            selection_strategy = "first_replicate_stable_use_directly"
        elif len(valid_replicates) == 3:
            mean_db_qps = sum(
                float(replicate["validation"]["db_qps"])
                for replicate in valid_replicates
            ) / 3.0
            mean_utilization = {
                resource: sum(
                    float(replicate["validation"]["utilization_pct"][resource])
                    for replicate in valid_replicates
                ) / 3.0
                for resource in ("cpu", "memory", "io")
            }
            validation = {
                "hard_gate_passed": True,
                "hard_checks": {
                    "replicate_count_exactly_three": True,
                    "all_three_replicates_technical_success": True,
                    "all_three_db_qps_authoritative": True,
                    "all_three_ap13_correctness_passed": True,
                    "all_three_owned_docker_cleanup_complete": True,
                    "composite_arithmetic_means_finite": all(
                        math.isfinite(value)
                        for value in [mean_db_qps, *mean_utilization.values()]
                    ),
                },
                "db_qps": mean_db_qps,
                "utilization_pct": mean_utilization,
                "source": "arithmetic_mean_of_three_valid_authoritative_docker_replicates_v1",
                "composite": True,
            }
            composite_ledger_path = output / f"rank_{position:02d}_composite_ledger.json"
            ledger = {
                "schema_version": 1,
                "policy_id": "outer_candidate_first_unstable_three_total_replicates_v1",
                "rank": int(candidate["rank"]),
                "replay_position": position,
                "subset_id": candidate.get("subset_id"),
                "params": params,
                "trigger": "first_valid_replicate_unstable",
                "replicate_count": len(replicates),
                "valid_replicate_count": 3,
                "later_stable_replicate_cherry_picking_allowed": False,
                "technical_failures_enter_average": False,
                "replicates": [
                    {
                        "replicate_index": replicate["replicate_index"],
                        "attempt_id": replicate["attempt_id"],
                        "stable": replicate["stable"],
                        "db_qps": replicate["validation"]["db_qps"],
                        "utilization_pct": replicate["validation"]["utilization_pct"],
                        "point_dir": replicate["point_dir"],
                        "validation_path": str(
                            output
                            / f"rank_{position:02d}_rep_{replicate['replicate_index']:02d}_validation.json"
                        ),
                    }
                    for replicate in valid_replicates
                ],
                "arithmetic_mean": {
                    "db_qps": mean_db_qps,
                    "utilization_pct": mean_utilization,
                },
                "completed_at_epoch": time.time(),
            }
            write_json(composite_ledger_path, ledger)
            selection_strategy = "three_valid_replicate_arithmetic_mean_composite"
        else:
            validation = {
                "hard_gate_passed": False,
                "hard_checks": {
                    "first_replicate_stable": False,
                    "three_valid_replicates_available": False,
                    "technical_failure_excluded_from_average": True,
                    "technical_attempt_count": len(replicates),
                    "valid_replicate_count": len(valid_replicates),
                },
                "db_qps": None,
                "utilization_pct": {},
                "source": "replicate_policy_fail_closed",
                "composite": False,
            }
            selection_strategy = "technical_failure_no_composite_fail_closed"

        decision = decide_outer_acceptance(
            incumbent_db_qps=incumbent["db_qps"],
            candidate_db_qps=validation.get("db_qps"),
            incumbent_target_utilization=incumbent["utilization_pct"]["memory"],
            candidate_target_utilization=(validation.get("utilization_pct") or {}).get("memory"),
            target_resource="memory",
            incumbent_resource_utilization=incumbent["utilization_pct"],
            candidate_resource_utilization=validation.get("utilization_pct") or {},
            non_target_bottleneck_threshold=80.0,
            hard_gate_passed=validation.get("hard_gate_passed") is True,
        ).to_dict()
        record = {
            "schema_version": 1,
            "rank": int(candidate["rank"]),
            "replay_position": position,
            "subset_id": candidate.get("subset_id"),
            "params": params,
            "optimized_params": params,
            "source_continuous_model_params": candidate["continuous_model_only"][
                "optimized_params"
            ],
            "applied_signature_sha256": applied_by_rank[int(candidate["rank"])][
                "applied_signature_sha256"
            ],
            "replicate_policy": (
                "first_valid_stable_direct_else_three_valid_mean_"
                "technical_failures_retry_up_to_six_v2"
            ),
            "replicates": replicates,
            "selection_strategy": selection_strategy,
            "composite_ledger": (
                str(composite_ledger_path) if composite_ledger_path else None
            ),
            "validation": validation,
            "acceptance": decision,
            "candidate_failure_closed": validation.get("hard_gate_passed") is not True,
        }
        write_json(output / f"rank_{position:02d}_validation.json", record)
        return record

    for position, candidate in enumerate(candidates[:10], start=1):
        if position < args.start_rank:
            continue
        if position == 6 and accepted:
            break
        if args.replicate_policy_v1:
            record = run_rank_with_replicates(position, candidate)
            records.append(record)
            if record["acceptance"]["accepted"]:
                accepted.append(record)
            write_json(progress_path, {
                "schema_version": 1,
                "status": "batch_complete" if position == 10 else "replaying",
                "pid": os.getpid(),
                "initial_top_k": 5,
                "maximum_top_k": 10,
                "replayed_count": len(records),
                "current_rank": None,
                "accepted_candidate_count": len(accepted),
                "accepted_shrinks": accepted_shrinks_before,
                "replicate_policy_v1": True,
                "incumbent": incumbent,
                "updated_at_epoch": time.time(),
            })
            if args.stop_after_one:
                break
            continue
        params = resolved_params(candidate)
        params_path = output / f"rank_{position:02d}_params.json"
        write_json(params_path, params)
        attempt_id = f"{args.attempt_run_id}_rank_{position:04d}"
        point_dir = lane / "results/experience_outer" / f"candidate_{attempt_id}"
        log_path = output / f"rank_{position:02d}_candidate.log"
        helper_path = output / f"rank_{position:02d}_resident_memory.json"
        write_json(progress_path, {
            "schema_version": 1, "status": "replaying", "pid": os.getpid(),
            "initial_top_k": 5, "maximum_top_k": 10,
            "replayed_count": len(records), "current_rank": position,
            "current_subset_id": candidate.get("subset_id"),
            "incumbent": incumbent, "updated_at_epoch": time.time(),
        })
        command = [
            str(args.python_bin), "-u", str(lane / "candidate_once.py"),
            "--lane-root", str(lane), "--attempt-id", attempt_id,
            "--candidate-params-json", str(params_path),
            "--cpu-quota-cores", "5.5", "--memory-bytes", "223155000",
            "--io-read-bps", "67108864", "--io-write-bps", "36700160",
        ]
        with log_path.open("x", encoding="utf-8") as log_handle:
            process = subprocess.Popen(
                command, stdout=log_handle, stderr=subprocess.STDOUT,
                text=True, start_new_session=True, env=candidate_env,
            )
            helper_evidence = None
            for _ in range(720):
                if container_running():
                    helper_evidence = inject_resident_helper(lane, helper_path)
                    break
                if process.poll() is not None:
                    break
                time.sleep(0.25)
            if helper_evidence is None:
                process.terminate()
                process.wait(timeout=30)
                raise RuntimeError(f"candidate rank {position} never created its Docker container")
            returncode = process.wait()
        helper_evidence["cleanup_complete"] = not container_running()
        helper_evidence["cleanup_pending"] = not helper_evidence["cleanup_complete"]
        write_json(helper_path, helper_evidence)
        validation_error = None
        try:
            validation = candidate_validation(
                point_dir,
                helper_evidence,
                process_returncode=returncode,
                expected_params=params,
            )
        except Exception as exc:
            validation_error = f"{type(exc).__name__}: {exc}"
            validation = {
                "hard_gate_passed": False,
                "hard_checks": {
                    "candidate_process_exit_zero": returncode == 0,
                    "complete_validation_evidence_available": False,
                },
                "candidate_process_returncode": returncode,
                "validation_error": validation_error,
                "db_qps": None,
                "utilization_pct": {},
                "point_dir": str(point_dir),
            }
        decision = decide_outer_acceptance(
            incumbent_db_qps=incumbent["db_qps"],
            candidate_db_qps=validation["db_qps"],
            incumbent_target_utilization=incumbent["utilization_pct"]["memory"],
            candidate_target_utilization=validation["utilization_pct"].get("memory"),
            target_resource="memory",
            incumbent_resource_utilization=incumbent["utilization_pct"],
            candidate_resource_utilization=validation["utilization_pct"],
            non_target_bottleneck_threshold=80.0,
            hard_gate_passed=validation["hard_gate_passed"],
        ).to_dict()
        record = {
            "rank": int(candidate["rank"]),
            "replay_position": position,
            "subset_id": candidate.get("subset_id"),
            "params": params, "optimized_params": params,
            "source_continuous_model_params": candidate["continuous_model_only"][
                "optimized_params"
            ],
            "validation": validation,
            "acceptance": decision, "candidate_log": str(log_path),
            "resident_memory_evidence": str(helper_path),
            "candidate_process_returncode": returncode,
            "candidate_failure_closed": returncode != 0 or validation_error is not None,
        }
        records.append(record)
        write_json(output / f"rank_{position:02d}_validation.json", record)
        if decision["accepted"]:
            accepted.append(record)
        write_json(progress_path, {
            "schema_version": 1, "status": "batch_complete" if position in {5, 10} else "replaying",
            "pid": os.getpid(), "initial_top_k": 5, "maximum_top_k": 10,
            "replayed_count": len(records), "current_rank": None,
            "accepted_candidate_count": len(accepted), "incumbent": incumbent,
            "updated_at_epoch": time.time(),
        })
        if args.stop_after_one:
            break

    selected = None
    range_updates = None
    updated_ranges = None
    if accepted:
        selected = max(
            accepted,
            key=lambda item: (
                float(item["validation"]["db_qps"]),
                -float(item["validation"]["utilization_pct"]["memory"]),
                -int(item["rank"]),
            ),
        )
        updated_ranges, range_updates = binary_shrink_parameter_ranges(
            active_ranges, selected["params"], ACTIVE_PARAMS,
        )
    next_state = None
    if selected and updated_ranges is not None:
        next_state = {
            "schema_version": 1,
            "status": "accepted_incumbent_state",
            "accepted_shrinks": accepted_shrinks_before + 1,
            "incumbent": {
                "db_qps": float(selected["validation"]["db_qps"]),
                "utilization_pct": {
                    name: float(selected["validation"]["utilization_pct"][name])
                    for name in ("cpu", "memory", "io")
                },
                "params": {
                    **{name: float(selected["params"][name]) for name in ACTIVE_PARAMS},
                    "cpu_index_tuple_cost": 0.005,
                },
                "source": selected["validation"].get("point_dir"),
                "selected_rank": int(selected["rank"]),
                "selected_replay_position": int(selected["replay_position"]),
            },
            "ranges": {
                name: {
                    "min_value": float(updated_ranges[name].min_value),
                    "max_value": float(updated_ranges[name].max_value),
                }
                for name in ACTIVE_PARAMS
            },
            "source_state_path": str(state_path) if state_path else None,
            "source_state_sha256": source_state_sha256,
            "source_replay_result": str(output / "replay_result.json"),
            "created_at_epoch": time.time(),
        }
    valid_replay_count = sum(
        1
        for record in records
        if record.get("selection_strategy")
        != "technical_failure_no_composite_fail_closed"
    )
    expected_replay_count = min(10, len(candidates))
    if selected:
        final_status = "accepted_and_binary_shrunk"
    elif valid_replay_count < expected_replay_count:
        final_status = "infrastructure_failed_before_recall_complete"
    else:
        final_status = "rejected_top10_exhausted"
    final = {
        "schema_version": 1,
        "status": final_status,
        "pid": os.getpid(),
        "initial_top_k": 5, "maximum_top_k": 10,
        "replayed_count": len(records),
        "valid_replay_count": valid_replay_count,
        "expected_replay_count": expected_replay_count,
        "accepted_candidate_count": len(accepted),
        "accepted_shrinks_before": accepted_shrinks_before,
        "accepted_shrinks_after": accepted_shrinks_before + (1 if selected else 0),
        "incumbent": incumbent,
        "selected_candidate": selected,
        "range_update_strategy": "transform_space_binary_interval" if selected else None,
        "range_updates": range_updates,
        "records": records,
        "completed_at_epoch": time.time(),
    }
    write_json(output / "replay_result.json", final)
    if next_state is not None:
        write_json(output / "next_incumbent_state.json", next_state)
    write_json(progress_path, {
        key: value for key, value in final.items()
        if key not in {"records", "range_updates"}
    })
    print(json.dumps({
        "status": final["status"], "replayed_count": len(records),
        "accepted_candidate_count": len(accepted),
        "selected_rank": selected["rank"] if selected else None,
    }, sort_keys=True), flush=True)
    return 4 if final_status == "infrastructure_failed_before_recall_complete" else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as exc:
        try:
            output_index = os.sys.argv.index("--output-root") + 1
            fatal_output = Path(os.sys.argv[output_index]).resolve()
            fatal_progress_path = fatal_output / "replay_progress.json"
            prior = (
                json.loads(fatal_progress_path.read_text(encoding="utf-8"))
                if fatal_progress_path.is_file()
                else {}
            )
            prior.update({
                "schema_version": 1,
                "status": "fatal_error_fail_closed",
                "pid": os.getpid(),
                "fatal_error": f"{type(exc).__name__}: {exc}",
                "fatal_traceback": traceback.format_exc(),
                "failed_at_epoch": time.time(),
            })
            write_json(fatal_progress_path, prior)
        except Exception:
            pass
        raise
