#!/usr/bin/env python3
"""Run one fail-closed diagnostic TPC-H Top-5 batch.

The batch replays exactly three trusted exact-plan-regime candidates followed by
two uncertain probes.  No diagnostic result can update the formal incumbent,
accepted counter, parameter ranges, or gradient state.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Mapping, Optional, Tuple


RESOURCES: Tuple[str, ...] = ("cpu", "io", "memory")
ALLOWED_PARAMS = {
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
}
BASELINE_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}
MARKER = "RESOURCE_ADAPTIVE_BENCHMARK_JSON="


class TechnicalFailure(RuntimeError):
    """The physical workload call did not yield valid replay evidence."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent), text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def append_event(path: Path, value: Mapping[str, Any]) -> None:
    event = copy.deepcopy(dict(value))
    event.setdefault("timestamp_epoch", time.time())
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def finite(value: Any, name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def band_score(utilization: float, weight: float, target: bool) -> float:
    target_min, target_max, hard = (
        (0.85, 0.90, 0.95) if target else (0.30, 0.50, 0.90)
    )
    if target:
        if utilization < target_min:
            gap = (target_min - utilization) / target_min
            return weight * (1.0 - gap * gap)
        if utilization <= target_max:
            position = (utilization - target_min) / (target_max - target_min)
            return weight * (1.0 - 0.2 * position)
        span = max(hard - target_max, 0.05)
        gap = (utilization - target_max) / span
        hard_excess = max(0.0, utilization - hard) / span
        return weight * (0.8 - gap * gap - 4.0 * hard_excess)
    if utilization < target_min:
        gap = (target_min - utilization) / target_min
        return weight * 0.8 * (1.0 - gap * gap)
    if utilization <= target_max:
        position = (utilization - target_min) / (target_max - target_min)
        return weight * (0.8 + 0.2 * position)
    span = max(hard - target_max, 0.05)
    gap = (utilization - target_max) / span
    hard_excess = max(0.0, utilization - hard) / span
    return weight * (1.0 - gap * gap - 4.0 * hard_excess)


def score_resource(
    resource: Mapping[str, Any], capability: Mapping[str, float]
) -> Tuple[float, Dict[str, float]]:
    utilization = {
        name: finite(resource[name], f"resource.{name}") / capability[name]
        for name in RESOURCES
    }
    score = sum(
        band_score(utilization[name], weight, name == "memory")
        for name, weight in (("cpu", 1.0), ("io", 3.0), ("memory", 1.0))
    )
    return score, utilization


def extract_payload(output: str) -> Dict[str, Any]:
    lines = output.splitlines()
    for index in range(len(lines) - 1, -1, -1):
        if MARKER not in lines[index]:
            continue
        marker = json.loads(lines[index].split(MARKER, 1)[1].strip())
        if not isinstance(marker, dict):
            raise TechnicalFailure("benchmark marker is not an object")
        if marker.get("status") is not None:
            return marker
        if index + 1 >= len(lines):
            raise TechnicalFailure("summary is missing after benchmark marker")
        summary = json.loads(lines[index + 1].strip())
        if not isinstance(summary, dict):
            raise TechnicalFailure("benchmark summary is not an object")
        merged = copy.deepcopy(summary)
        for key in (
            "benchmark",
            "benchmark_latency",
            "formal_pg_resource",
            "candidate_measurement_protocol",
        ):
            if key in marker:
                merged[key] = copy.deepcopy(marker[key])
        return merged
    raise TechnicalFailure("benchmark marker is missing")


def classify_payload(
    payload: Mapping[str, Any],
    returncode: int,
    capability: Mapping[str, float],
) -> Tuple[Dict[str, float], Dict[str, float], float]:
    if returncode != 0:
        raise TechnicalFailure(f"workload exited with status {returncode}")
    if payload.get("status") != "complete":
        raise TechnicalFailure(f"workload status is {payload.get('status')!r}")
    if int(payload.get("failed_queries", -1)) != 0:
        raise TechnicalFailure("workload contains failed queries")
    if int(payload.get("successful_queries", 0)) <= 0:
        raise TechnicalFailure("workload contains no successful query")
    cleanup = payload.get("worker_cleanup") or {}
    if (
        cleanup.get("controller_thread_stopped") is not True
        or cleanup.get("database_backends_fully_released") is not True
        or int(cleanup.get("remaining_benchmark_backend_count", -1)) != 0
    ):
        raise TechnicalFailure("workload cleanup is incomplete")
    protocol = payload.get("candidate_measurement_protocol") or {}
    if protocol.get("stable") is not True:
        raise TechnicalFailure("candidate measurement is not stable")
    formal = payload.get("formal_pg_resource") or {}
    if formal.get("semantics") != "pg5_io_total_throughput_utilization_v1":
        raise TechnicalFailure("formal PostgreSQL resource semantics mismatch")
    caps = formal.get("caps") or {}
    expected_caps = {
        "cpu_quota_cores": capability["cpu"],
        "memory_bytes": capability["memory"] * 1024.0 ** 3,
        "io_total_cap_bps": capability["io"] * 1024.0 ** 2,
    }
    for name, expected in expected_caps.items():
        observed = finite(caps.get(name), f"caps.{name}")
        if not math.isclose(observed, expected, rel_tol=0.0, abs_tol=1e-5):
            raise TechnicalFailure(
                f"formal resource cap mismatch for {name}: {observed} != {expected}"
            )
    aggregate = formal.get("aggregate") or {}
    utilization = {
        name: finite(aggregate.get(f"{name}_utilization"), f"utilization.{name}")
        for name in RESOURCES
    }
    if any(value < 0.0 for value in utilization.values()):
        raise TechnicalFailure("negative utilization")
    resource = {
        name: utilization[name] * capability[name]
        for name in RESOURCES
    }
    score, _ = score_resource(resource, capability)
    return resource, utilization, score


def validate_selection(
    selection: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    supported_contracts = {
        "exact_plan_regime_trusted3_unknown_probe2_top5_v1",
        "exact_structural_plan_regime_trusted3_unknown_probe2_top5_v2",
        (
            "exact_structural_plan_regime_trusted3_unknown_probe2_top5_"
            "diagnostic_v1"
        ),
        "tpch_q1_completion_risk_probe5_discovery_v1",
        "exact_structural_plan_regime_stability_probe5_diagnostic_v1",
    }
    if selection.get("selection_contract") not in supported_contracts:
        raise ValueError("selection contract mismatch")
    candidates = selection.get("top5") or []
    if not isinstance(candidates, list) or len(candidates) != 5:
        raise ValueError("selection must contain exactly five candidates")
    roles = [candidate.get("selection_role") for candidate in candidates]
    probe5_contracts = {
        "tpch_q1_completion_risk_probe5_discovery_v1",
        "exact_structural_plan_regime_stability_probe5_diagnostic_v1",
    }
    expected_roles = (
        ["probe"] * 5
        if selection.get("selection_contract") in probe5_contracts
        else ["trusted", "trusted", "trusted", "probe", "probe"]
    )
    if roles != expected_roles:
        raise ValueError(f"selection roles mismatch: {roles}")
    required_false = (
        "accepted_update_allowed",
        "incumbent_update_allowed",
        "range_update_allowed",
        "gradient_update_allowed",
    )
    if (
        selection.get("diagnostic_only") is not True
        or selection.get("formal_continuation") is not False
        or selection.get("serial_real_replay_required") is not True
        or int(selection.get("maximum_real_replay_invocations", -1)) != 5
        or selection.get("qps_used_for_inner_scoring") is not False
        or selection.get("technical_failures_count_toward_physical_cap")
        is not True
        or selection.get("technical_failures_count_as_valid_or_accepted")
        is not False
        or selection.get("unknown_plan_regime_trusted_allowed") is not False
        or any(selection.get(field) is not False for field in required_false)
    ):
        raise ValueError("selection violates the diagnostic replay contract")
    identifiers = [
        (candidate.get("three_level_fingerprint") or {}).get("id")
        for candidate in candidates
    ]
    if (
        any(not isinstance(value, str) for value in identifiers)
        or len(set(identifiers)) != 5
    ):
        raise ValueError("selection fingerprints are missing or duplicated")
    required = (
        "feature_ratio",
        "predicted_resource",
        "utilization",
        "dScore/dU",
        "T",
        "confidence",
        "g_j",
        "eta",
        "delta_param",
        "candidate_score",
    )
    for rank, candidate in enumerate(candidates, 1):
        missing = [name for name in required if name not in candidate]
        if missing:
            raise ValueError(f"rank {rank} is missing fields: {missing}")
        if (
            candidate.get("diagnostic_only") is not True
            or candidate.get("qps_used_for_inner_scoring") is not False
            or candidate.get("incumbent_update_eligible") is not False
            or candidate.get("eligible_for_best_candidate") is not False
            or any(candidate.get(field) is not False for field in required_false)
        ):
            raise ValueError(
                f"rank {rank} violates the diagnostic no-update contract"
            )
        params = candidate.get("candidate_params") or candidate.get("optimized_params")
        if not isinstance(params, dict) or set(params) != ALLOWED_PARAMS:
            raise ValueError(f"rank {rank} parameter set mismatch")
    return copy.deepcopy(candidates)


def validate_gate_summary(summary: Mapping[str, Any]) -> None:
    if int(summary.get("successful_real_replay_count", -1)) != 5:
        raise ValueError("entry gate does not contain five valid replays")
    if int(summary.get("technical_failure_count", -1)) != 0:
        raise ValueError("entry gate contains a technical failure")
    if summary.get("baseline_params_restored") is not True:
        raise ValueError("entry gate baseline was not restored")


def baseline_resource(
    summary: Mapping[str, Any], capability: Mapping[str, float]
) -> Tuple[Dict[str, float], float]:
    decision = summary.get("r0_decision_window") or {}
    if (
        summary.get("status") != "complete"
        or int(summary.get("failed_queries", -1)) != 0
        or decision.get("available") is not True
        or decision.get("stable") is not True
        or (summary.get("r0_profile_construction_gate") or {}).get("passed") is not True
        or (summary.get("r0_resource_gate") or {}).get("passed") is not True
    ):
        raise ValueError("baseline R0 evidence is not valid")
    utilization_pct = decision.get("utilization_pct") or {}
    resource = {
        name: finite(utilization_pct[name], f"baseline.{name}") / 100.0
        * capability[name]
        for name in RESOURCES
    }
    score, _ = score_resource(resource, capability)
    return resource, score


def initial_range_state(config: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    raw_ranges = config.get("param_ranges") or {}
    if set(raw_ranges) != ALLOWED_PARAMS:
        raise ValueError("configured parameter range set mismatch")
    state: Dict[str, Dict[str, Any]] = {}
    for name in sorted(ALLOWED_PARAMS):
        raw = raw_ranges[name]
        minimum = finite(raw["min_value"], f"range.{name}.min")
        maximum = finite(raw["max_value"], f"range.{name}.max")
        if minimum > maximum:
            raise ValueError(f"invalid parameter range for {name}")
        transform = "linear" if name == "parallel_setup_cost" else "log"
        if transform == "log" and minimum <= 0.0:
            raise ValueError(f"log parameter range must be positive for {name}")
        state[name] = {
            "min_value": minimum,
            "max_value": maximum,
            "transform": transform,
        }
    return state


def shrink_ranges(
    ranges: Mapping[str, Mapping[str, Any]],
    accepted_params: Mapping[str, Any],
) -> Dict[str, Dict[str, Any]]:
    shrunk: Dict[str, Dict[str, Any]] = {}
    for name, raw in ranges.items():
        minimum = finite(raw["min_value"], f"range.{name}.min")
        maximum = finite(raw["max_value"], f"range.{name}.max")
        accepted = min(
            maximum,
            max(minimum, finite(accepted_params[name], f"accepted.{name}")),
        )
        transform = str(raw["transform"])
        if transform == "log":
            low = math.log(minimum)
            high = math.log(maximum)
            center = math.log(accepted)
            half_width = (high - low) / 4.0
            new_low = max(low, min(center - half_width, high - 2.0 * half_width))
            new_high = new_low + 2.0 * half_width
            new_minimum = math.exp(new_low)
            new_maximum = math.exp(new_high)
        elif transform == "linear":
            half_width = (maximum - minimum) / 4.0
            new_minimum = max(
                minimum,
                min(accepted - half_width, maximum - 2.0 * half_width),
            )
            new_maximum = new_minimum + 2.0 * half_width
        else:
            raise ValueError(f"unsupported transform for {name}: {transform}")
        shrunk[name] = {
            "min_value": new_minimum,
            "max_value": new_maximum,
            "transform": transform,
        }
    return shrunk


def apply_params(container: str, database: str, params: Mapping[str, Any]) -> str:
    normalized = {}
    for name, raw_value in params.items():
        if name not in ALLOWED_PARAMS:
            raise ValueError(f"unsupported parameter {name}")
        value = finite(raw_value, name)
        if value < 0.0:
            raise ValueError(f"negative parameter {name}")
        normalized[name] = value
    psql = [
        "docker",
        "exec",
        container,
        "psql",
        "-X",
        "-v",
        "ON_ERROR_STOP=1",
        "-p",
        "64304",
        "-U",
        "postgres",
        "-d",
        database,
    ]
    for name, value in sorted(normalized.items()):
        subprocess.run(
            psql + ["-c", f"ALTER SYSTEM SET {name} = '{value:.17g}';"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    subprocess.run(
        psql + ["-c", "SELECT pg_reload_conf();"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    names = ",".join(f"'{name}'" for name in sorted(normalized))
    verified = subprocess.run(
        psql
        + [
            "-Atc",
            "SELECT name || '=' || setting FROM pg_settings "
            f"WHERE name IN ({names}) ORDER BY name;",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return verified.stdout.strip()


def run_workload(workload: Path, point_root: Path, timeout_seconds: int) -> Tuple[int, str]:
    point_root.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        ["bash", str(workload)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout_seconds,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    (point_root / "workload.log").write_text(
        completed.stdout, encoding="utf-8"
    )
    return completed.returncode, completed.stdout


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--selection-sha256", required=True)
    parser.add_argument("--gate-summary", type=Path, required=True)
    parser.add_argument("--gate-summary-sha256", required=True)
    parser.add_argument("--baseline-summary", type=Path, required=True)
    parser.add_argument("--baseline-summary-sha256", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--workload", type=Path, required=True)
    parser.add_argument("--workload-sha256", required=True)
    parser.add_argument("--accepted-counter", type=Path, required=True)
    parser.add_argument("--resume-state", type=Path)
    parser.add_argument("--resume-state-sha256")
    parser.add_argument("--selection-start-rank", type=int, default=1)
    parser.add_argument(
        "--selection-exclude-rank",
        type=int,
        action="append",
        default=[],
    )
    parser.add_argument(
        "--maximum-real-replay-invocations",
        type=int,
        default=5,
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--diagnostic-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.diagnostic_only:
        raise SystemExit("--diagnostic-only is mandatory for this runner")
    expected = (
        (args.selection, args.selection_sha256),
        (args.gate_summary, args.gate_summary_sha256),
        (args.baseline_summary, args.baseline_summary_sha256),
        (args.config, args.config_sha256),
        (args.workload, args.workload_sha256),
    )
    for path, sha256 in expected:
        actual = file_sha256(path)
        if actual != sha256:
            raise SystemExit(f"SHA mismatch for {path}: {actual} != {sha256}")

    config = load_json(args.config)
    capability = {
        name: finite(config["capability"][name], f"capability.{name}")
        for name in RESOURCES
    }
    if capability != {
        "cpu": 22.0,
        "io": 36.0,
        "memory": 1_730_000_000 / 1024.0 ** 3,
    }:
        raise SystemExit(f"formal capability mismatch: {capability}")
    formal = config.get("formal_outer_protocol") or {}
    if (
        int(formal.get("accepted_shrink_target", -1)) != 10
        or int(formal.get("formal_search_accepted_target", -1)) != 10
        or int(formal.get("formal_search_max_replay_top_k", -1)) != 5
    ):
        raise SystemExit("formal target or replay cap mismatch")
    if not args.accepted_counter.is_file():
        raise SystemExit(f"accepted counter is missing: {args.accepted_counter}")
    accepted_count_start = int(
        args.accepted_counter.read_text(encoding="utf-8").strip()
    )
    if accepted_count_start != int(
        formal.get("formal_search_accepted_count_start", -1)
    ):
        raise SystemExit(
            "accepted counter does not match configured formal start: "
            f"{accepted_count_start}"
        )

    selection = load_json(args.selection)
    candidates = validate_selection(selection)
    if args.selection_start_rank < 1 or args.selection_start_rank > 5:
        raise SystemExit("selection start rank must be in [1, 5]")
    excluded_ranks = list(args.selection_exclude_rank)
    if (
        len(set(excluded_ranks)) != len(excluded_ranks)
        or any(rank < 1 or rank > 5 for rank in excluded_ranks)
    ):
        raise SystemExit("selection exclude ranks must be unique values in [1, 5]")
    selected_ranks = [
        rank
        for rank in range(1, 6)
        if rank >= args.selection_start_rank and rank not in set(excluded_ranks)
    ]
    if (
        args.maximum_real_replay_invocations < 1
        or args.maximum_real_replay_invocations > 5
        or args.maximum_real_replay_invocations != len(selected_ranks)
    ):
        raise SystemExit(
            "maximum real replay invocations must exactly equal the number "
            f"of selected ranks: {len(selected_ranks)}"
        )
    full_selection_replay = selected_ranks == [1, 2, 3, 4, 5]
    gate_summary = load_json(args.gate_summary)
    validate_gate_summary(gate_summary)
    baseline_summary = load_json(args.baseline_summary)
    incumbent_resource, incumbent_score = baseline_resource(
        baseline_summary, capability
    )
    incumbent_params = copy.deepcopy(BASELINE_PARAMS)
    range_state = initial_range_state(config)
    inherited_accepted_count = 0
    if args.resume_state is not None:
        if not args.resume_state_sha256:
            raise SystemExit("resume state SHA-256 is required")
        actual_resume_sha = file_sha256(args.resume_state)
        if actual_resume_sha != args.resume_state_sha256:
            raise SystemExit(
                "resume state SHA mismatch: "
                f"{actual_resume_sha} != {args.resume_state_sha256}"
            )
        resume_state = load_json(args.resume_state)
        if (
            resume_state.get("schema_version")
            != "tpch_exact_plan_regime_formal_state_v1"
            or resume_state.get("status") != "complete"
            or resume_state.get("baseline_restored") is not True
            or int(resume_state.get("technical_failure_count", -1)) != 0
            or int(resume_state.get("accepted_count", -1))
            != accepted_count_start
        ):
            raise SystemExit("resume state is not a clean formal continuation")
        resume_params = resume_state.get("incumbent_params")
        resume_resource = resume_state.get("incumbent_resource")
        resume_ranges = resume_state.get("range_state")
        if (
            not isinstance(resume_params, dict)
            or set(resume_params) != ALLOWED_PARAMS
            or not isinstance(resume_resource, dict)
            or not isinstance(resume_ranges, dict)
            or set(resume_ranges) != ALLOWED_PARAMS
        ):
            raise SystemExit("resume state incumbent/range payload is invalid")
        incumbent_params = {
            name: finite(value, f"resume.incumbent_params.{name}")
            for name, value in resume_params.items()
        }
        incumbent_resource = {
            name: finite(resume_resource[name], f"resume.incumbent_resource.{name}")
            for name in RESOURCES
        }
        resumed_score, _ = score_resource(incumbent_resource, capability)
        stored_score = finite(
            resume_state.get("incumbent_score"), "resume.incumbent_score"
        )
        if not math.isclose(
            resumed_score, stored_score, rel_tol=0.0, abs_tol=1e-9
        ):
            raise SystemExit("resume incumbent score is inconsistent with resources")
        incumbent_score = stored_score
        range_state = copy.deepcopy(resume_ranges)
        inherited_accepted_count = accepted_count_start

    args.output.mkdir(parents=True, exist_ok=True)
    state_path = args.output / "diagnostic_state.json"
    events_path = args.output / "events.jsonl"
    summary_path = args.output / "diagnostic_summary.json"
    lock_path = args.output / "diagnostic.lock"
    if state_path.exists() or summary_path.exists():
        raise SystemExit("formal output already contains state; refuse overwrite")

    state: Dict[str, Any] = {
        "schema_version": "tpch_exact_plan_regime_diagnostic_state_v1",
        "status": "starting",
        "diagnostic_only": True,
        "formal_state_mutation_allowed": False,
        "accepted_update_allowed": False,
        "incumbent_update_allowed": False,
        "range_update_allowed": False,
        "gradient_update_allowed": False,
        "accepted_shrink_target": 10,
        "accepted_count": accepted_count_start,
        "inherited_accepted_count": inherited_accepted_count,
        "batch_added_accepted_count": 0,
        "physical_call_count": 0,
        "valid_replay_count": 0,
        "technical_failure_count": 0,
        "incumbent_params": incumbent_params,
        "incumbent_resource": incumbent_resource,
        "incumbent_score": incumbent_score,
        "range_state": range_state,
        "resume_state_sha256": (
            args.resume_state_sha256 if args.resume_state is not None else None
        ),
        "selection_sha256": args.selection_sha256,
        "selection_start_rank": args.selection_start_rank,
        "selection_excluded_ranks": sorted(excluded_ranks),
        "selection_replayed_ranks": selected_ranks,
        "maximum_real_replay_invocations": (
            args.maximum_real_replay_invocations
        ),
        "full_selection_replay": full_selection_replay,
        "accepted_decision_deferred": True,
        "entry_gate_summary_sha256": args.gate_summary_sha256,
        "baseline_summary_sha256": args.baseline_summary_sha256,
        "qps_used_for_inner_scoring": False,
        "updated_at_epoch": time.time(),
    }
    atomic_json(state_path, state)

    termination_requested = False

    def request_termination(_signum: int, _frame: Any) -> None:
        nonlocal termination_requested
        termination_requested = True

    signal.signal(signal.SIGTERM, request_termination)
    signal.signal(signal.SIGINT, request_termination)

    valid_results: List[Dict[str, Any]] = []
    technical_failures: List[Dict[str, Any]] = []
    batch_aborted_after_technical_failure = False
    with lock_path.open("w", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        state["status"] = "running"
        state["pid"] = os.getpid()
        state["updated_at_epoch"] = time.time()
        atomic_json(state_path, state)
        append_event(
            events_path,
            {
                "event": "tpch_diagnostic_exact_plan_regime_batch_start",
                "pid": os.getpid(),
                "accepted_shrink_target": 10,
                "accepted_count_before": state["accepted_count"],
                "maximum_real_replay_invocations": (
                    args.maximum_real_replay_invocations
                ),
                "selection_start_rank": args.selection_start_rank,
                "selection_excluded_ranks": sorted(excluded_ranks),
                "selection_replayed_ranks": selected_ranks,
                "full_selection_replay": full_selection_replay,
                "accepted_decision_deferred": True,
                "diagnostic_only": True,
                "accepted_update_allowed": False,
                "incumbent_update_allowed": False,
                "range_update_allowed": False,
                "gradient_update_allowed": False,
                "trusted_slots": sum(
                    candidate["selection_role"] == "trusted"
                    for candidate in candidates
                ),
                "probe_slots": sum(
                    candidate["selection_role"] == "probe"
                    for candidate in candidates
                ),
                "serial_real_replay_required": True,
                "unknown_plan_regime_trusted_allowed": False,
                "technical_failures_count_toward_physical_cap": True,
                "technical_failures_count_as_valid_or_accepted": False,
                "selection_sha256": args.selection_sha256,
            },
        )
        try:
            for rank, candidate in enumerate(candidates, 1):
                if rank not in selected_ranks:
                    continue
                if (
                    termination_requested
                    or state["physical_call_count"]
                    >= args.maximum_real_replay_invocations
                ):
                    break
                role = str(candidate["selection_role"])
                params = candidate.get("candidate_params") or candidate["optimized_params"]
                point_root = args.output / f"rank_{rank:02d}" / "attempt_01"
                verification = apply_params(args.container, args.database, params)
                started = {
                    "rank": rank,
                    "role": role,
                    "fingerprint": candidate["three_level_fingerprint"]["id"],
                    "started_at_epoch": time.time(),
                }
                atomic_json(point_root / "started.json", started)
                state["physical_call_count"] += 1
                state["current_rank"] = rank
                state["updated_at_epoch"] = time.time()
                atomic_json(state_path, state)
                append_event(
                    events_path,
                    {
                        "event": "tpch_diagnostic_real_replay_started",
                        "rank": rank,
                        "selection_role": role,
                        "candidate_params": params,
                        "feature_ratio": candidate["feature_ratio"],
                        "predicted_resource": candidate["predicted_resource"],
                        "utilization": candidate["utilization"],
                        "dScore/dU": candidate["dScore/dU"],
                        "T": candidate["T"],
                        "confidence": candidate["confidence"],
                        "g_j": candidate["g_j"],
                        "eta": candidate["eta"],
                        "delta_param": candidate["delta_param"],
                        "candidate_score": candidate["candidate_score"],
                        "three_level_fingerprint": candidate[
                            "three_level_fingerprint"
                        ],
                        "verification": verification,
                    },
                )
                try:
                    returncode, output = run_workload(
                        args.workload, point_root, args.timeout_seconds
                    )
                    payload = extract_payload(output)
                    resource, utilization, actual_score = classify_payload(
                        payload, returncode, capability
                    )
                    result = {
                        "rank": rank,
                        "selection_role": role,
                        "candidate_params": params,
                        "feature_ratio": candidate["feature_ratio"],
                        "predicted_resource": candidate["predicted_resource"],
                        "predicted_utilization": candidate["utilization"],
                        "dScore/dU": candidate["dScore/dU"],
                        "T": candidate["T"],
                        "confidence": candidate["confidence"],
                        "g_j": candidate["g_j"],
                        "eta": candidate["eta"],
                        "delta_param": candidate["delta_param"],
                        "candidate_score": candidate["candidate_score"],
                        "three_level_fingerprint": candidate[
                            "three_level_fingerprint"
                        ],
                        "actual_resource": resource,
                        "actual_utilization": utilization,
                        "actual_score": actual_score,
                        "technical_failure": False,
                        "valid_replay": True,
                        "accepted_update_eligible": False,
                        "diagnostic_only": True,
                        "accepted_update_allowed": False,
                        "incumbent_update_allowed": False,
                        "range_update_allowed": False,
                        "gradient_update_allowed": False,
                        "probe_updates_forbidden": role == "probe",
                        "qps_used_for_inner_scoring": False,
                    }
                    atomic_json(point_root / "result.json", result)
                    valid_results.append(result)
                    state["valid_replay_count"] += 1
                    state["updated_at_epoch"] = time.time()
                    atomic_json(state_path, state)
                    append_event(
                        events_path,
                        {
                            "event": "tpch_diagnostic_real_replay_complete",
                            **result,
                        },
                    )
                except Exception as exc:
                    failure = {
                        "rank": rank,
                        "selection_role": role,
                        "error": f"{type(exc).__name__}: {exc}",
                        "technical_failure": True,
                        "valid_replay": False,
                        "accepted": False,
                    }
                    atomic_json(point_root / "technical_failure.json", failure)
                    technical_failures.append(failure)
                    state["technical_failure_count"] += 1
                    state["updated_at_epoch"] = time.time()
                    atomic_json(state_path, state)
                    append_event(
                        events_path,
                        {
                            "event": (
                                "tpch_diagnostic_real_replay_technical_failure"
                            ),
                            **failure,
                        },
                    )
                    # A technical failure can be caused by a shared batch
                    # dependency (missing workload driver, broken container,
                    # database connectivity, and so on).  Continuing after the
                    # first failure can consume all five physical-call slots
                    # without adding any valid evidence.  Fail closed and let a
                    # successor batch use new fingerprints after the shared
                    # cause has been repaired.
                    batch_aborted_after_technical_failure = True
                    append_event(
                        events_path,
                        {
                            "event": (
                                "tpch_diagnostic_batch_aborted_after_"
                                "technical_failure"
                            ),
                            "failed_rank": rank,
                            "remaining_ranks_not_started": [
                                selected_rank
                                for selected_rank in selected_ranks
                                if selected_rank > rank
                            ],
                        },
                    )
                    break
        finally:
            restored = apply_params(
                args.container, args.database, BASELINE_PARAMS
            )
            append_event(
                events_path,
                {
                    "event": "tpch_diagnostic_baseline_params_restored",
                    "verification": restored,
                },
            )

        append_event(
            events_path,
            {
                "event": "tpch_diagnostic_batch_complete_no_state_update",
                "accepted_count_after": state["accepted_count"],
                "batch_added_accepted_count": 0,
                "accepted_update_applied": False,
                "incumbent_update_applied": False,
                "range_update_applied": False,
                "gradient_update_applied": False,
                "accepted_decision_deferred": True,
                "selection_sha256": args.selection_sha256,
                "selection_replayed_ranks": selected_ranks,
                "maximum_real_replay_invocations": (
                    args.maximum_real_replay_invocations
                ),
                "technical_failure_count": len(technical_failures),
                "batch_aborted_after_technical_failure": (
                    batch_aborted_after_technical_failure
                ),
            },
        )

        state["status"] = (
            "technical_failure_abort"
            if batch_aborted_after_technical_failure
            else "complete"
        )
        state["current_rank"] = None
        state["baseline_restored"] = True
        state["updated_at_epoch"] = time.time()
        atomic_json(state_path, state)
        summary = {
            "schema_version": (
                "tpch_exact_plan_regime_diagnostic_batch_summary_v1"
            ),
            "diagnostic_only": True,
            "formal_state_mutation_allowed": False,
            "accepted_update_applied": False,
            "incumbent_update_applied": False,
            "range_update_applied": False,
            "gradient_update_applied": False,
            "accepted_shrink_target": 10,
            "accepted_count": state["accepted_count"],
            "batch_added_accepted_count": state["batch_added_accepted_count"],
            "physical_call_count": state["physical_call_count"],
            "valid_replay_count": state["valid_replay_count"],
            "technical_failure_count": state["technical_failure_count"],
            "batch_aborted_after_technical_failure": (
                batch_aborted_after_technical_failure
            ),
            "selection_sha256": args.selection_sha256,
            "selection_start_rank": args.selection_start_rank,
            "selection_excluded_ranks": sorted(excluded_ranks),
            "selection_replayed_ranks": selected_ranks,
            "maximum_real_replay_invocations": (
                args.maximum_real_replay_invocations
            ),
            "full_selection_replay": full_selection_replay,
            "accepted_decision_deferred": True,
            "baseline_restored": True,
            "successful": valid_results,
            "technical_failures": technical_failures,
        }
        atomic_json(summary_path, summary)


if __name__ == "__main__":
    main()
