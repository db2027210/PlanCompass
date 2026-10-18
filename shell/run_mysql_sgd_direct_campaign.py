#!/usr/bin/env python3
"""Run one direct, T-guided, all-nonempty-subset MySQL SGD campaign."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import itertools
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SEARCH_GUIDANCE_CONTRACT = {
    "schema_version": "mysql_direct_search_guidance_v1",
    "candidate_generation_signals": ["target_resource_experience_effect"],
    "candidate_ranking_signals": ["cpu", "memory", "io"],
    "qps_role": "outer_acceptance_only",
    "qps_used_for_direction": False,
    "qps_used_for_step": False,
    "qps_used_for_ranking": False,
}
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common_outer_loop import decide_validation_acceptance
from resource_utility import ResourceUtilityCalculator


HISTORY = ROOT / "history/mysql_sgd_env_agents_20260723"
# Cross-scenario host02 serialization is disabled for the parallel campaign.
# Per-scenario controller locks remain the duplicate-run guard.
HOST02_SCENARIOS: set[str] = set()
HOST02_CAMPAIGN_LOCK = HISTORY / "host02_job_xp_campaign.lock"
POINT_RUNNER = ROOT / "shell/manual_mysql_sgd_r0_campaign.py"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
DEFAULTS = {
    "io_block_read_cost": 1.0,
    "memory_block_read_cost": 0.25,
    "row_evaluate_cost": 0.1,
    "key_compare_cost": 0.05,
    "disk_temptable_create_cost": 20.0,
    "disk_temptable_row_cost": 0.5,
    "memory_temptable_create_cost": 1.0,
    "memory_temptable_row_cost": 0.1,
}
SCENARIO_KEYS = {
    "job_memory": "job",
    "hybench_xp_io": "xp",
    "tpch_memory": "tpch",
    "tpcc_cpu": "tpcc",
}
STOP = False
CHILD: subprocess.Popen[str] | None = None


def stop(_signum: int, _frame: object) -> None:
    global STOP
    STOP = True
    if CHILD is not None and CHILD.poll() is None:
        CHILD.send_signal(signal.SIGTERM)


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def acquire_direct_host02_lease(scenario: str) -> int | None:
    if scenario not in HOST02_SCENARIOS:
        return None
    if os.environ.get("MYSQL_HOST02_CAMPAIGN_LEASE_HELD") == "1":
        return None
    HOST02_CAMPAIGN_LOCK.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        HOST02_CAMPAIGN_LOCK, os.O_RDWR | os.O_CREAT, 0o600
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        raise RuntimeError("host02 JOB/XP shared campaign lease is held")
    return descriptor


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def candidate(
    incumbent: Mapping[str, float],
    subset: tuple[str, ...],
    table: Mapping[str, Mapping[str, float]],
    ranges: Mapping[str, Mapping[str, float]],
    target: str,
    step: float,
    direction_multiplier: float = 1.0,
) -> dict[str, float]:
    result = dict(incumbent)
    for name in subset:
        low = float(ranges[name]["lower"])
        high = float(ranges[name]["upper"])
        current = float(result[name])
        direction = (1.0 if float(table[name][target]) < 0 else -1.0)
        direction *= float(direction_multiplier)
        log_value = math.log(current) + direction * step * (math.log(high) - math.log(low))
        result[name] = float(f"{min(high, max(low, math.exp(log_value))):.6g}")
    return result


def metrics_from_result(result: Mapping[str, Any]) -> dict[str, Any]:
    selected = result["rolling_measurement_control"]["selected_observation"]
    return {
        "db_qps": float(result["database_throughput"]["db_qps"]),
        "resource_means": {
            name: float(value)
            for name, value in dict(selected["resource_means"]).items()
        },
    }


def utility_score(
    calculator: ResourceUtilityCalculator,
    metrics: Mapping[str, Any],
) -> float:
    resources = {
        name: float(value)
        for name, value in dict(metrics["resource_means"]).items()
        if name in {"cpu", "memory", "io"}
    }
    return calculator.calculate_utility_score(
        resources,
        {name: 1.0 for name in resources},
    )


def resume_checkpoint(
    root: Path,
    output: Path,
    default_costs: Mapping[str, float],
    baseline_metrics: Mapping[str, Any],
    target_resource: str,
) -> tuple[dict[str, float], dict[str, Any], int, dict[str, Any] | None]:
    """Resume only the valid prefix accepted under the current strict gate."""
    accepted_prefix: list[
        tuple[Path, Path, dict[str, Any], dict[str, float]]
    ] = []
    prefix_ended = False
    for campaign in sorted(root.glob("direct_*")):
        if campaign == output or not campaign.is_dir():
            continue
        for decision_path in sorted(campaign.glob("round_[0-9][0-9][0-9].decision.json")):
            try:
                decision = load(decision_path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if decision.get("accepted") is not True:
                continue
            candidate_metrics = decision.get("candidate")
            incumbent_before = decision.get("incumbent_before")
            if not isinstance(candidate_metrics, Mapping) or not isinstance(
                incumbent_before, Mapping
            ):
                prefix_ended = True
                break
            revalidated = decide_validation_acceptance(
                incumbent_before,
                candidate_metrics,
                target_resource=target_resource,
                hard_gate_passed=True,
                utilization_key="resource_means",
                non_target_bottleneck_threshold=0.8,
            )
            if not revalidated.accepted:
                prefix_ended = True
                break
            match = re.fullmatch(r"(round_\d{3})\.decision\.json", decision_path.name)
            if match is None:
                prefix_ended = True
                break
            cost_path = campaign / f"{match.group(1)}.costs.json"
            if not cost_path.is_file():
                prefix_ended = True
                break
            try:
                costs = {
                    name: float(value)
                    for name, value in load(cost_path).items()
                    if name in default_costs
                }
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                prefix_ended = True
                break
            if set(costs) != set(default_costs):
                prefix_ended = True
                break
            accepted_prefix.append((campaign, decision_path, decision, costs))
        if prefix_ended:
            break

    if accepted_prefix:
        campaign, decision_path, decision, costs = accepted_prefix[-1]
        accepted_count = len(accepted_prefix)
        return (
            costs,
            dict(decision["candidate"]),
            accepted_count,
            {
                "campaign": str(campaign),
                "decision": str(decision_path),
                "accepted_shrinkage": accepted_count,
                "revalidated_non_target_threshold": 0.8,
                "checkpoint_counter_trusted_without_decisions": False,
                "resume_policy": "chronological_contiguous_strict_acceptance_prefix",
                "prefix_ended_on_invalid_historical_acceptance": prefix_ended,
            },
        )
    return dict(default_costs), dict(baseline_metrics), 0, None


def cleanup_failed_point(point_dir: Path) -> dict[str, Any]:
    """Remove only the exact failed point's containers, with bounded retries."""
    runtime_path = point_dir / "campaign_runtime.json"
    if not runtime_path.is_file():
        return {"verified": False, "reason": "campaign_runtime_missing"}
    runtime = load(runtime_path)
    host = str(runtime.get("host") or "")
    container = str(runtime.get("container") or "")
    if not host or re.fullmatch(r"ra_mysql_sgd_[a-z0-9_-]+", container) is None:
        return {"verified": False, "reason": "unsafe_or_missing_cleanup_identity"}
    script = (
        "set -e\n"
        f"docker rm -f {container} {container}_bootstrap >/dev/null 2>&1 || true\n"
        f"test -z \"$(docker ps -aq --filter name=^{container}$)\"\n"
        f"test -z \"$(docker ps -aq --filter name=^{container}_bootstrap$)\"\n"
    )
    errors: list[str] = []
    for cleanup_attempt in range(1, 4):
        try:
            completed = subprocess.run(
                [
                    "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                    host, "bash", "-s",
                ],
                input=script,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=90,
                check=False,
            )
            if completed.returncode == 0:
                return {"verified": True, "attempt": cleanup_attempt}
            errors.append(
                f"attempt {cleanup_attempt}: rc={completed.returncode}; "
                f"stderr={completed.stderr[-1000:]}"
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            errors.append(f"attempt {cleanup_attempt}: {type(error).__name__}: {error}")
        if cleanup_attempt < 3:
            time.sleep(3)
    return {"verified": False, "errors": errors}


def classify_candidate_failure(point_dir: Path) -> dict[str, Any] | None:
    """Return a valid-candidate rejection for an explicit correctness failure.

    Infrastructure failures remain technical retries.  A workload that ran to
    its formal correctness gate and failed it is evidence about the candidate,
    so retrying the same costs cannot make that point valid.
    """
    failure_path = point_dir / "failure.txt"
    try:
        failure = failure_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    if "correctness failed:" not in failure:
        return None
    return {
        "category": "formal_correctness_gate_failed",
        "failure_path": str(failure_path),
        "message": failure,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = load(args.config)
    if config.get("formal_scope") != "mysql_only":
        parser.error("direct campaign requires formal_scope=mysql_only")
    if config.get("cross_engine_comparison_prohibited") is not True:
        parser.error("mysql_only campaign must prohibit cross-engine comparison")

    scenario = str(config["scenario"])
    if scenario in HOST02_SCENARIOS and config.get("host_mutex") is not True:
        raise ValueError("host02 JOB/XP production config requires host_mutex=true")
    host02_lease_descriptor = acquire_direct_host02_lease(scenario)
    lane = str(config["lane"])
    target = str(config["target_resource"])
    table_doc = load(ROOT / str(config["experience_table"]))
    r0 = load(ROOT / str(config["r0_evidence"]))
    baseline_result = load(ROOT / str(r0["source"]["r0_result"]["path"]))
    baseline_metrics = metrics_from_result(baseline_result)
    active = [str(name) for name in table_doc["parameter_space"]["active_params"]]
    ranges = table_doc["parameter_space"]["ranges"]
    table = table_doc["table"]
    default_costs = {name: DEFAULTS[name] for name in active}
    subsets = [
        subset
        for size in range(1, len(active) + 1)
        for subset in itertools.combinations(active, size)
    ]
    subsets.sort(
        key=lambda subset: (
            sum(abs(float(table[name][target])) for name in subset),
            -len(subset),
            subset,
        ),
        reverse=True,
    )

    key = SCENARIO_KEYS[scenario]
    root = HISTORY / key / "formal_sgd_direct"
    run_id = datetime.now().strftime("direct_%Y%m%dT%H%M%S")
    output = root / run_id
    output.mkdir(parents=True, exist_ok=False)
    state_path = HISTORY / "manual_controllers" / scenario / "direct_campaign_state.json"
    incumbent, incumbent_metrics, accepted, resumed_from = resume_checkpoint(
        root,
        output,
        default_costs,
        baseline_metrics,
        target,
    )

    rejected = 0
    valid_attempt = 0
    technical_failures = 0
    target_accepted = int(config.get("target_accepted_shrinks", 10))
    max_attempts = max(30, int(config.get("maximum_valid_candidates", target_accepted * 10)))
    max_technical_retries = int(config.get("maximum_technical_retries", 3))
    measurement_seconds = int(config.get("measurement_seconds", 600))
    minimum_measurement_seconds = int(
        config.get("minimum_measurement_seconds", 180)
    )
    minimum_client_completions = int(
        config.get("minimum_client_completions", 0)
    )
    qps_tolerance = float(config.get("qps_noninferiority_tolerance", 0.05))
    target_tolerance = float(config.get("target_utilization_noise_tolerance", 0.005))
    utility_epsilon = float(config.get("utility_improvement_epsilon", 0.0001))
    calculator = ResourceUtilityCalculator()
    if target not in {"cpu", "memory", "io"}:
        raise ValueError("target resource must be cpu, memory or io")
    profile = ROOT / str(config["profile"])
    blocked_reason: str | None = None

    atomic_json(
        output / "checkpoint.json",
        {
            "scenario": scenario,
            "incumbent_costs": incumbent,
            "incumbent_metrics": incumbent_metrics,
            "accepted_shrinkage": accepted,
            "resumed_from": resumed_from,
            "search_guidance_contract": SEARCH_GUIDANCE_CONTRACT,
        },
    )

    while accepted < target_accepted and valid_attempt < max_attempts:
        if STOP:
            break
        point_number = valid_attempt + 1
        # The physics table is a prior, not a guarantee.  Probe its direction
        # and the local reverse direction before spending the next subset.  A
        # half-step after both failures prevents a single coarse jump from
        # skipping a narrow feasible basin.
        subset_phase = valid_attempt % 4
        subset = subsets[(valid_attempt // 4) % len(subsets)]
        step = float(config.get("log_space_step", 0.08))
        if subset_phase >= 2:
            step *= 0.5
        direction_multiplier = -1.0 if subset_phase in {1, 3} else 1.0
        costs = candidate(
            incumbent,
            subset,
            table,
            ranges,
            target,
            step=step,
            direction_multiplier=direction_multiplier,
        )
        point_id = f"round_{point_number:03d}"
        cost_path = output / f"{point_id}.costs.json"
        atomic_json(cost_path, costs)
        valid_result: dict[str, Any] | None = None
        candidate_failure: dict[str, Any] | None = None

        for retry in range(1, max_technical_retries + 1):
            if STOP:
                break
            retry_suffix = "" if retry == 1 else f"_retry_{retry:02d}"
            point_run_id = f"{run_id}_{point_id}{retry_suffix}"
            atomic_json(
                state_path,
                {
                    "phase": "SGD_RUNNING",
                    "scenario": scenario,
                    "round": point_number,
                    "stage": "MEASURING_CANDIDATE",
                    "subset": list(subset),
                    "direction_multiplier": direction_multiplier,
                    "log_space_step": step,
                    "accepted_shrinkage": accepted,
                    "rejected_count": rejected,
                    "technical_failure_count": technical_failures,
                    "technical_retry": retry,
                    "run_id": run_id,
                    "measurement_seconds": measurement_seconds,
                    "resumed_from": resumed_from,
                },
            )
            command = [
                str(PYTHON),
                str(POINT_RUNNER),
                "--scenario",
                lane,
                "--run-id",
                point_run_id,
                "--profile-json",
                str(profile),
                "--cost-json",
                str(cost_path),
                "--measurement-seconds",
                str(measurement_seconds),
                "--minimum-measurement-seconds",
                str(minimum_measurement_seconds),
                "--minimum-client-completions",
                str(minimum_client_completions),
            ]
            global CHILD
            CHILD = subprocess.Popen(command, cwd=ROOT, text=True)
            returncode = CHILD.wait()
            CHILD = None
            point_dir = HISTORY / key / "formal_sgd_campaigns" / point_run_id
            result_path = point_dir / "r0_result.json"
            if returncode == 0 and result_path.is_file():
                valid_result = load(result_path)
                break

            candidate_failure = classify_candidate_failure(point_dir)
            if candidate_failure is not None:
                cleanup = cleanup_failed_point(point_dir)
                candidate_failure["cleanup"] = cleanup
                candidate_failure["point_run_id"] = point_run_id
                candidate_failure["returncode"] = returncode
                if not cleanup.get("verified"):
                    blocked_reason = (
                        f"candidate cleanup barrier failed for {point_run_id}; "
                        "refusing to launch another point"
                    )
                    break
                atomic_json(
                    output / f"{point_id}.decision.json",
                    {
                        "schema_version": "mysql_sgd_strict_outer_decision_v3",
                        "accepted": False,
                        "incumbent_before": incumbent_metrics,
                        "candidate": None,
                        "incumbent_utility": utility_score(calculator, incumbent_metrics),
                        "candidate_utility": None,
                        "utility_delta": None,
                        "checks": {
                            "correctness_passed": False,
                            "qps_noninferiority_passed": None,
                            "target_noise_guard_passed": None,
                            "qps_strictly_higher_passed": None,
                            "target_strictly_lower_passed": None,
                            "non_target_below_0_8_passed": None,
                            "utility_improvement_passed": None,
                        },
                        "tolerances": {
                            "qps_relative_noninferiority": qps_tolerance,
                            "target_utilization_absolute": target_tolerance,
                            "utility_improvement_epsilon": utility_epsilon,
                            "legacy_tolerances_used_for_acceptance": False,
                        },
                        "rule": (
                            "correctness_AND_db_qps_strictly_higher_AND_"
                            "target_strictly_lower_AND_all_non_targets_"
                            "strictly_below_0.8"
                        ),
                        "measurement_seconds": measurement_seconds,
                        "search_probe": {
                            "subset": list(subset),
                            "direction_multiplier": direction_multiplier,
                            "log_space_step": step,
                        },
                        "failure": candidate_failure,
                    },
                )
                break

            technical_failures += 1
            cleanup = cleanup_failed_point(point_dir)
            technical_record = {
                "schema_version": "mysql_sgd_technical_retry_v2",
                "scenario": scenario,
                "point": point_number,
                "retry": retry,
                "returncode": returncode,
                "point_run_id": point_run_id,
                "point_dir": str(point_dir),
                "cleanup": cleanup,
                "consumes_valid_candidate_budget": False,
                "consumes_rejection_count": False,
                "recorded_utc": datetime.now(timezone.utc).isoformat(),
            }
            atomic_json(
                output / f"{point_id}.retry_{retry:02d}.technical_failure.json",
                technical_record,
            )
            if not cleanup.get("verified"):
                blocked_reason = (
                    f"technical cleanup barrier failed for {point_run_id}; "
                    "refusing to launch another point"
                )
                break

        if STOP or blocked_reason:
            break
        if candidate_failure is not None:
            valid_attempt += 1
            rejected += 1
            atomic_json(
                output / "checkpoint.json",
                {
                    "scenario": scenario,
                    "valid_attempts": valid_attempt,
                    "accepted_shrinkage": accepted,
                    "rejected_count": rejected,
                    "technical_failure_count": technical_failures,
                    "incumbent_costs": incumbent,
                    "incumbent_metrics": incumbent_metrics,
                    "resumed_from": resumed_from,
                    "updated_utc": datetime.now(timezone.utc).isoformat(),
                },
            )
            continue
        if valid_result is None:
            blocked_reason = (
                f"point {point_number} exhausted {max_technical_retries} "
                "technical retries without consuming exploration budget"
            )
            break

        metrics = metrics_from_result(valid_result)
        incumbent_utility = utility_score(calculator, incumbent_metrics)
        candidate_utility = utility_score(calculator, metrics)
        correctness_passed = valid_result.get("correctness", {}).get("passed") is True
        outer_decision = decide_validation_acceptance(
            incumbent_metrics,
            metrics,
            target_resource=target,
            hard_gate_passed=correctness_passed,
            utilization_key="resource_means",
            non_target_bottleneck_threshold=0.8,
        )
        qps_passed = (
            float(metrics["db_qps"]) > float(incumbent_metrics["db_qps"])
        )
        target_passed = (
            float(metrics["resource_means"][target])
            < float(incumbent_metrics["resource_means"][target])
        )
        non_target_passed = (
            outer_decision.incumbent_non_target_utilization is not None
            and outer_decision.candidate_non_target_utilization is not None
            and not outer_decision.incumbent_non_target_bottlenecks
            and not outer_decision.candidate_non_target_bottlenecks
        )
        utility_passed = candidate_utility > incumbent_utility + utility_epsilon
        accepted_now = outer_decision.accepted
        decision = {
            "schema_version": "mysql_sgd_strict_outer_decision_v3",
            "accepted": accepted_now,
            "incumbent_before": incumbent_metrics,
            "candidate": metrics,
            "incumbent_utility": incumbent_utility,
            "candidate_utility": candidate_utility,
            "utility_delta": candidate_utility - incumbent_utility,
            "checks": {
                "correctness_passed": correctness_passed,
                "qps_noninferiority_passed": qps_passed,
                "target_noise_guard_passed": target_passed,
                "qps_strictly_higher_passed": qps_passed,
                "target_strictly_lower_passed": target_passed,
                "non_target_below_0_8_passed": non_target_passed,
                "utility_improvement_passed": utility_passed,
            },
            "outer_gate": outer_decision.to_dict(),
            "tolerances": {
                "qps_relative_noninferiority": qps_tolerance,
                "target_utilization_absolute": target_tolerance,
                "utility_improvement_epsilon": utility_epsilon,
                "legacy_tolerances_used_for_acceptance": False,
            },
            "rule": (
                "correctness_AND_db_qps_strictly_higher_AND_target_strictly_lower_"
                "AND_all_non_targets_strictly_below_0.8"
            ),
            "measurement_seconds": measurement_seconds,
            "search_probe": {
                "subset": list(subset),
                "direction_multiplier": direction_multiplier,
                "log_space_step": step,
            },
            "search_guidance_contract": SEARCH_GUIDANCE_CONTRACT,
        }
        atomic_json(output / f"{point_id}.decision.json", decision)
        valid_attempt += 1
        if accepted_now:
            accepted += 1
            incumbent = costs
            incumbent_metrics = metrics
        else:
            rejected += 1
        atomic_json(
            output / "checkpoint.json",
            {
                "scenario": scenario,
                "valid_attempts": valid_attempt,
                "accepted_shrinkage": accepted,
                "rejected_count": rejected,
                "technical_failure_count": technical_failures,
                "incumbent_costs": incumbent,
                "incumbent_metrics": incumbent_metrics,
                "resumed_from": resumed_from,
                "updated_utc": datetime.now(timezone.utc).isoformat(),
            },
        )

    complete = accepted >= target_accepted
    phase = "COMPLETE" if complete else ("BLOCKED" if blocked_reason else "INCOMPLETE")
    final = {
        "phase": phase,
        "scenario": scenario,
        "round": valid_attempt,
        "stage": "FINISHED",
        "accepted_shrinkage": accepted,
        "rejected_count": rejected,
        "technical_failure_count": technical_failures,
        "run_id": run_id,
        "campaign_complete": complete,
        "blocked_reason": blocked_reason,
        "resumed_from": resumed_from,
        "host_mutex": scenario in HOST02_SCENARIOS,
        "host02_campaign_lease": {
            "required": scenario in HOST02_SCENARIOS,
            "controller_inherited": os.environ.get(
                "MYSQL_HOST02_CAMPAIGN_LEASE_HELD"
            ) == "1",
            "direct_descriptor_acquired": host02_lease_descriptor is not None,
            "path": str(HOST02_CAMPAIGN_LOCK),
        },
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
    }
    atomic_json(state_path, final)
    atomic_json(output / "campaign_result.json", final)
    return 0 if complete else 2


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    raise SystemExit(main())
