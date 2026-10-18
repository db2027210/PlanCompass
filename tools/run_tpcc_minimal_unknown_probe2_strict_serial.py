#!/usr/bin/env python3
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any, Dict, List, Mapping, Optional, Tuple

from gradient_replay_metrics import build_top5_prediction_metrics
from resource_utility import ResourceUtilityCalculator
try:
    from replay_correctness_policy import classify_transaction_abort_gate
except ImportError:
    from tpcc_round11_current_correctness_policy import (
        classify_transaction_abort_gate,
    )
from adaptive_replay_policy import (
    adaptive_completion_certified,
    config_from_env,
    decide_after_success,
    load_history,
)
from tpcc_remaining_budget_selector import select_remaining_candidates


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
    "cpu_tuple_cost": 0.010616461941294047,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0026541154853235117,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}
PSQL = [
    "psql",
    "-X",
    "-v",
    "ON_ERROR_STOP=1",
    "-h",
    "127.0.0.1",
    "-p",
    "59640",
    "-U",
    "postgres",
    "-d",
    "fullgrid_tpcc",
]
WORKLOAD = [
    "/home/anonymous/miniconda3/bin/python3",
    "-u",
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "gradient_debug_round11_tpcc_calibrated_deadband_v1_20260728/"
    "code/run_tpcc_sensitive_stable_point.py",
    "--container",
    "pg5_tpcc_d2_20260722",
    "--host",
    "127.0.0.1",
    "--port",
    "59640",
    "--database",
    "fullgrid_tpcc",
    "--user",
    "postgres",
    "--password",
    "postgres",
    "--cpu-limit-cores",
    "3.8",
    "--memory-limit-bytes",
    "6497397651",
    "--io-total-limit-mib-s",
    "54",
    "--io-read-cap-mib-s",
    "27",
    "--io-write-cap-mib-s",
    "27",
    "--stability-target-resource",
    "cpu",
]
RESTORE = [
    "/home/anonymous/program/resource_adaptive/shell/restore_pg5_tpcc_from_template.sh"
]
CAPABILITY = {"cpu": 3.8, "memory": 6.0511731087, "io": 54.0}
SCORE_CALCULATOR = ResourceUtilityCalculator(
    score_mode="band_target",
    resource_weights={"cpu": 1.0, "memory": 1.0, "io": 3.0},
    resource_target_ranges={
        "cpu": {
            "target_min_pct": 85.0,
            "target_max_pct": 90.0,
            "hard_overload_pct": 95.0,
            "role": "target_bottleneck",
        },
        "memory": {
            "target_min_pct": 30.0,
            "target_max_pct": 50.0,
            "hard_overload_pct": 90.0,
            "role": "non_bottleneck",
        },
        "io": {
            "target_min_pct": 30.0,
            "target_max_pct": 50.0,
            "hard_overload_pct": 90.0,
            "role": "non_bottleneck",
        },
    },
)
EXPECTED_WORKLOAD_SHA256 = (
    "bf03a7b4e299798739f4d92393b01db2c89c7cd266b58d12dad6737d5b0643ee"
)
EXPECTED_RESTORE_SHA256 = (
    "1af8eca4b8fbd229f525b627293eb58a51aa3510472c97340b5ea317be5cde59"
)
EXPECTED_RUNNER_SHA256 = EXPECTED_WORKLOAD_SHA256
EXTRACTOR = Path(__file__).resolve().with_name(
    "extract_tpcc_round11_calibrated_top5.py"
)
MAX_REAL_WORKLOAD_INVOCATIONS = 2


class TerminationRequested(Exception):
    """Signal-driven stop that must bypass technical retry accounting."""


def load_json(path: Path) -> Dict[str, Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"expected JSON object: {path}")
    return parsed


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def write_event(path: Path, event: dict) -> None:
    event = dict(event)
    event.setdefault("timestamp_epoch", time.time())
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def count_real_workload_invocations(events_path: Path) -> int:
    """Count physical calls, including legacy calls with sealed sample evidence."""
    if not events_path.is_file():
        return 0
    started = set()
    reserved = set()
    for line_number, line in enumerate(
        events_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"invalid replay event at line {line_number}"
            ) from exc
        event_name = event.get("event")
        if event_name not in {
            "tpcc_gradient_real_replay_started",
            "tpcc_gradient_real_replay_workload_invocation_reserved",
        }:
            continue
        rank = event.get("rank")
        attempt = event.get("attempt")
        if rank is None or attempt is None:
            raise RuntimeError(
                "workload invocation event lacks rank/attempt binding"
            )
        binding = (int(rank), int(attempt))
        if event_name == "tpcc_gradient_real_replay_workload_invocation_reserved":
            reserved.add(binding)
        else:
            started.add(binding)
    legacy_physical = {
        binding
        for binding in started - reserved
        if _legacy_call_has_physical_evidence(events_path.parent, *binding)
    }
    return len(reserved | legacy_physical)


def load_invoked_ranks(events_path: Path) -> List[int]:
    """Return ranks with at least one evidenced physical invocation."""
    if not events_path.is_file():
        return []
    started = set()
    reserved = set()
    for line_number, line in enumerate(
        events_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"invalid replay event at line {line_number}"
            ) from exc
        event_name = event.get("event")
        if event_name not in {
            "tpcc_gradient_real_replay_started",
            "tpcc_gradient_real_replay_workload_invocation_reserved",
        }:
            continue
        rank = event.get("rank")
        attempt = event.get("attempt")
        if rank is None or attempt is None:
            raise RuntimeError("workload invocation event lacks rank/attempt binding")
        binding = (int(rank), int(attempt))
        if event_name == "tpcc_gradient_real_replay_workload_invocation_reserved":
            reserved.add(binding)
        else:
            started.add(binding)
    bindings = reserved | {
        binding
        for binding in started - reserved
        if _legacy_call_has_physical_evidence(events_path.parent, *binding)
    }
    return sorted({rank for rank, _ in bindings})


def _legacy_call_has_physical_evidence(
    replay_root: Path,
    rank: int,
    attempt: int,
) -> bool:
    attempt_root = replay_root / f"rank_{rank:02d}" / f"attempt_{attempt:02d}"
    summaries = list(attempt_root.glob("point_*/summary.json"))
    if summaries:
        return True
    sample_paths = [
        *attempt_root.glob("point_*/resource_samples.jsonl"),
        *attempt_root.glob("point_*/database_counter_samples.jsonl"),
    ]
    return any(path.is_file() and path.stat().st_size > 0 for path in sample_paths)


def apply_params(params: dict) -> Dict[str, float]:
    if set(params) != ALLOWED_PARAMS:
        raise ValueError(
            "parameter set is incomplete or unsupported: "
            f"{sorted(params)}"
        )
    normalized = {}
    for name, raw_value in params.items():
        if name not in ALLOWED_PARAMS:
            raise ValueError(f"unsupported parameter: {name}")
        value = float(raw_value)
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(f"invalid parameter value: {name}={value}")
        normalized[name] = value
    for name, value in sorted(normalized.items()):
        completed = subprocess.run(
            PSQL + ["-c", f"ALTER SYSTEM SET {name} = '{value:.17g}';"],
            check=False,
            env={**os.environ, "PGPASSWORD": "postgres"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"ALTER SYSTEM failed for {name}={value}: {completed.stdout.strip()}"
            )
    subprocess.run(
        PSQL + ["-c", "SELECT pg_reload_conf();"],
        check=True,
        env={**os.environ, "PGPASSWORD": "postgres"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    names = ",".join(f"'{name}'" for name in sorted(normalized))
    verification = subprocess.run(
        PSQL
        + [
            "-Atc",
            "SELECT name || '=' || setting FROM pg_settings "
            f"WHERE name IN ({names}) ORDER BY name;",
        ],
        check=True,
        env={**os.environ, "PGPASSWORD": "postgres"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    readback: Dict[str, float] = {}
    for raw_line in verification.stdout.splitlines():
        name, separator, raw_value = raw_line.partition("=")
        if not separator or name not in normalized:
            continue
        readback[name] = float(raw_value)
    if set(readback) != set(normalized):
        raise RuntimeError(
            f"parameter readback is incomplete: expected={sorted(normalized)}, "
            f"actual={sorted(readback)}"
        )
    mismatches = {
        name: {"expected": normalized[name], "actual": readback[name]}
        for name in normalized
        if not math.isclose(
            readback[name],
            normalized[name],
            # PostgreSQL cost GUC SHOW output is rounded to roughly six
            # significant decimal digits for non-default fractional values.
            rel_tol=1e-5,
            abs_tol=1e-12,
        )
    }
    if mismatches:
        raise RuntimeError(f"parameter readback mismatch: {mismatches}")
    return readback


def actual_score(utilization: dict) -> float:
    resource = {
        name: float(utilization[name]) * CAPABILITY[name]
        for name in CAPABILITY
    }
    return float(
        SCORE_CALCULATOR.calculate_utility_score(resource, CAPABILITY)
    )


def validate_directional_score_contract() -> Dict[str, float]:
    probes = {
        "target_cpu_85": SCORE_CALCULATOR.calculate_resource_score(0.85, "cpu"),
        "target_cpu_90": SCORE_CALCULATOR.calculate_resource_score(0.90, "cpu"),
        "non_target_memory_50": SCORE_CALCULATOR.calculate_resource_score(
            0.50, "memory"
        ),
        "non_target_memory_30": SCORE_CALCULATOR.calculate_resource_score(
            0.30, "memory"
        ),
        "non_target_io_50": SCORE_CALCULATOR.calculate_resource_score(0.50, "io"),
        "non_target_io_30": SCORE_CALCULATOR.calculate_resource_score(0.30, "io"),
    }
    if not probes["target_cpu_85"] > probes["target_cpu_90"]:
        raise RuntimeError("target CPU directional score contract failed")
    if not probes["non_target_memory_50"] > probes["non_target_memory_30"]:
        raise RuntimeError("non-target memory directional score contract failed")
    if not probes["non_target_io_50"] > probes["non_target_io_30"]:
        raise RuntimeError("non-target IO directional score contract failed")
    return probes


def restore_database() -> str:
    completed = subprocess.run(
        RESTORE,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=180,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"TPC-C snapshot restore failed: {completed.stdout.strip()}"
        )
    return completed.stdout.strip()


def run_workload(
    output_root: Path, log_path: Path
) -> Tuple[subprocess.CompletedProcess, Optional[Dict]]:
    output_root.mkdir(parents=True, exist_ok=True)
    command = WORKLOAD + ["--output-root", str(output_root)]
    process = subprocess.Popen(
        command,
        env={
            **os.environ,
            "PYTHONUNBUFFERED": "1",
            "PYTHONPATH": "/home/anonymous/program/resource_adaptive",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, _ = process.communicate(timeout=1200)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            stdout, _ = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, _ = process.communicate()
        log_path.write_text(stdout or "", encoding="utf-8")
        raise RuntimeError("TPC-C workload timed out after 1200 seconds")
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            stdout, _ = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, _ = process.communicate()
        log_path.write_text(stdout or "", encoding="utf-8")
        raise
    completed = subprocess.CompletedProcess(
        command,
        process.returncode,
        stdout or "",
        None,
    )
    log_path.write_text(completed.stdout, encoding="utf-8")
    marker = "RESOURCE_ADAPTIVE_BENCHMARK_JSON="
    payload = None
    for line in reversed(completed.stdout.splitlines()):
        if marker not in line:
            continue
        try:
            payload = json.loads(line.split(marker, 1)[1].strip())
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        break
    return completed, payload


def normalize_measurement_payload(
    payload: Optional[Dict],
    output_root: Path,
) -> Tuple[Dict[str, float], Dict, Dict]:
    """Validate one stable point and return its sealed resource payload."""
    if not isinstance(payload, dict) or not payload:
        raise RuntimeError("workload did not emit a structured payload")
    protocol = payload.get("candidate_measurement_protocol") or {}
    if not isinstance(protocol, dict):
        raise RuntimeError("candidate measurement protocol is not an object")
    aggregate = (payload.get("formal_pg_resource") or {}).get("aggregate") or {}
    utilization = {
        "cpu": aggregate.get("cpu_utilization"),
        "memory": aggregate.get("memory_utilization"),
        "io": aggregate.get("io_utilization"),
    }

    raw_summary_path = protocol.get("summary_path")
    if not raw_summary_path:
        raise RuntimeError("measurement protocol lacks summary_path")
    summary_path = Path(str(raw_summary_path)).resolve()
    resolved_output_root = output_root.resolve()
    if resolved_output_root not in summary_path.parents:
        raise RuntimeError(f"measurement summary escapes output root: {summary_path}")
    if summary_path.name != "summary.json":
        raise RuntimeError("measurement summary filename is not summary.json")
    point_id = str(protocol.get("point_id") or "")
    if not point_id or summary_path.parent.name != point_id:
        raise RuntimeError("measurement point_id does not bind summary parent")
    if protocol.get("runner_sha256") != EXPECTED_RUNNER_SHA256:
        raise RuntimeError("measurement runner SHA is not the sealed runner")
    if protocol.get("qps_used_for_inner_scoring") is not False:
        raise RuntimeError("QPS was not explicitly excluded from inner scoring")
    declared_summary_sha = str(protocol.get("summary_sha256") or "")
    if declared_summary_sha != sha256_file(summary_path):
        raise RuntimeError("measurement summary SHA mismatch")
    summary = load_json(summary_path)
    continuity = summary.get("database_counter_continuity") or {}
    cleanup = summary.get("worker_cleanup") or {}
    measurement_integrity_passed = (
        protocol.get("stable") is True
        and (summary.get("window_mean_stability") or {}).get("stable") is True
        and continuity.get("passed") is True
        and continuity.get("counter_reset_count") == 0
        and cleanup.get("fully_released") is True
        and not (cleanup.get("remaining_pids") or [])
        and cleanup.get("counter_thread_stopped") is True
    )
    for field in ("native_log",):
        evidence_path = Path(str(summary.get(field) or "")).resolve()
        if resolved_output_root not in evidence_path.parents or not evidence_path.is_file():
            raise RuntimeError(f"{field} is not bound to the current point")
    if sha256_file(Path(str(summary["native_log"]))) != str(
        summary.get("native_log_sha256") or ""
    ):
        raise RuntimeError("native workload log SHA mismatch")
    native_log_text = Path(str(summary["native_log"])).read_text(
        encoding="utf-8",
        errors="replace",
    )
    correctness_gate = classify_transaction_abort_gate(
        summary,
        native_log_text,
    )
    correctness_passed = bool(
        measurement_integrity_passed
        and correctness_gate["valid_replay"]
    )
    algorithmic_gate_rejection = (
        protocol.get("algorithmic_gate_rejection") is True
    )
    valid_replay = correctness_passed
    sample_path = Path(
        str((payload.get("formal_pg_resource") or {}).get("samples_path") or "")
    ).resolve()
    if resolved_output_root not in sample_path.parents or not sample_path.is_file():
        raise RuntimeError("formal resource samples are not bound to current point")
    summary_utilization = summary.get("resource_utilization") or {}
    utilization = {
        name: summary_utilization.get(name)
        for name in ("cpu", "memory", "io")
    }
    for name, payload_field in (
        ("cpu", "cpu_utilization"),
        ("memory", "memory_utilization"),
        ("io", "io_utilization"),
    ):
        if not math.isclose(
            float(utilization[name]),
            float(aggregate[payload_field]),
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise RuntimeError(f"payload/summary resource mismatch for {name}")

    normalized_protocol = dict(protocol)
    normalized_protocol.update({
        "sealed_protocol_correctness_passed": protocol.get(
            "correctness_passed"
        ),
        "sealed_protocol_technical_failure": protocol.get(
            "technical_failure"
        ),
        "stable": measurement_integrity_passed,
        "correctness_passed": correctness_passed,
        "algorithmic_gate_rejection": algorithmic_gate_rejection,
        "technical_failure": not valid_replay,
        "correctness_gate": correctness_gate,
        "validated_summary_path": str(summary_path),
        "validated_summary_sha256": declared_summary_sha,
    })
    return utilization, normalized_protocol, summary


def validate_generation_and_top5(
    analysis_path: Path,
    replay_root: Path,
) -> Tuple[Dict[str, Any], Path, List[Dict[str, Any]], Dict[str, Any]]:
    if sha256_file(Path(WORKLOAD[2])) != EXPECTED_WORKLOAD_SHA256:
        raise RuntimeError("TPC-C workload runner SHA changed")
    if sha256_file(Path(RESTORE[0])) != EXPECTED_RESTORE_SHA256:
        raise RuntimeError("TPC-C snapshot restore script SHA changed")

    analysis = load_json(analysis_path)
    selection_contract = analysis.get("selection_contract")
    fresh_discovery = (
        selection_contract == "tpcc_round12_current_plan_discovery_probe5_v1"
    )
    minimal_probe2 = (
        selection_contract == "tpcc_minimal_unknown_acquisition_probe2_v1"
    )
    corrected_round13 = (
        selection_contract
        == "tpcc_round13_exact_plan_regime_corrected_top5_v1"
    )
    robust_round14 = (
        selection_contract
        == "tpcc_robust_exact_plan_regime_trusted3_probe2_v1"
    )
    fresh_serial = (
        fresh_discovery
        or minimal_probe2
        or corrected_round13
        or robust_round14
    )
    if selection_contract not in {
        "tpcc_round11_calibrated_deadband_maximin_top5_v1",
        "tpcc_round12_current_plan_discovery_probe5_v1",
        "tpcc_minimal_unknown_acquisition_probe2_v1",
        "tpcc_round13_exact_plan_regime_corrected_top5_v1",
        "tpcc_robust_exact_plan_regime_trusted3_probe2_v1",
    }:
        raise RuntimeError("Top-5 selection contract mismatch")
    if not fresh_serial and not EXTRACTOR.is_file():
        raise RuntimeError("round11 Top-5 extractor is absent")
    history_path = Path(str(analysis.get("source_history") or "")).resolve()
    if not history_path.is_file():
        raise RuntimeError("source history is absent")
    generation_root = analysis_path.parent if fresh_serial else history_path.parent
    config_path = (
        Path(str(analysis.get("source_config") or "")).resolve()
        if fresh_serial
        else generation_root / "config.json"
    )
    if sha256_file(config_path) != analysis.get("source_config_sha256"):
        raise RuntimeError("generation config SHA changed")
    if sha256_file(history_path) != analysis.get("source_history_sha256"):
        raise RuntimeError("source history SHA mismatch")
    source_generation_root = history_path.parent
    if (source_generation_root / "controller_exit_code.txt").read_text(
        encoding="utf-8"
    ).strip() != "0":
        raise RuntimeError("candidate generation did not exit successfully")
    restore_evidence = load_json(source_generation_root / "baseline_restore.json")
    if restore_evidence.get("baseline_params_restored") is not True:
        raise RuntimeError("candidate-generation baseline was not restored")

    history_lines = history_path.read_text(encoding="utf-8").splitlines()
    ranking_line = int(analysis.get("source_ranking_line_number") or 0)
    stop_line = int(analysis.get("source_generation_only_stop_line_number") or 0)
    if ranking_line <= 0:
        raise RuntimeError("ranking line provenance is invalid")
    ranking_event = json.loads(history_lines[ranking_line - 1])
    if ranking_event.get("event") != "experience_table_all_subset_ranking":
        raise RuntimeError("source ranking event mismatch")
    if corrected_round13 or robust_round14:
        discovery_path = Path(str(analysis.get("source_discovery") or "")).resolve()
        discovery_ledger_path = Path(
            str(analysis.get("source_discovery_ledger") or "")
        ).resolve()
        if (
            not discovery_path.is_file()
            or sha256_file(discovery_path)
            != analysis.get("source_discovery_sha256")
            or not discovery_ledger_path.is_file()
            or sha256_file(discovery_ledger_path)
            != analysis.get("source_discovery_ledger_sha256")
        ):
            raise RuntimeError("round13 discovery evidence SHA mismatch")
        mutation = analysis.get("state_mutation_contract") or {}
        if mutation != {
            "accepted_update": False,
            "incumbent_update": False,
            "range_update": False,
            "gradient_update": False,
        }:
            raise RuntimeError("round13 diagnostic state-mutation contract mismatch")
    if not fresh_serial:
        if stop_line != ranking_line + 1:
            raise RuntimeError("ranking/stop line provenance is invalid")
        stop_event = json.loads(history_lines[stop_line - 1])
        if (
            stop_event.get("event")
            != "experience_table_candidate_generation_only_stop"
        ):
            raise RuntimeError("generation-only stop event mismatch")

        replay_root.mkdir(parents=True, exist_ok=True)
        reextracted_path = replay_root / "preflight_reextracted_top5.json"
        completed = subprocess.run(
            [
                sys.executable,
                str(EXTRACTOR),
                str(generation_root),
                str(reextracted_path),
                "--score-deadband",
                str(float(analysis.get("score_deadband"))),
            ],
            check=False,
            env={
                **os.environ,
                "PYTHONPATH": "/home/anonymous/program/resource_adaptive",
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=180,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"fresh Top-5 extraction failed: {completed.stdout.strip()}"
            )
        if load_json(reextracted_path) != analysis:
            raise RuntimeError("fresh Top-5 extraction differs from sealed analysis")

    candidates = analysis.get("top5") or []
    if len(candidates) != MAX_REAL_WORKLOAD_INVOCATIONS:
        raise RuntimeError(
            "candidate count does not match physical cap: "
            f"{len(candidates)} != {MAX_REAL_WORKLOAD_INVOCATIONS}"
        )
    if [item.get("rank") for item in candidates] != list(
        range(1, MAX_REAL_WORKLOAD_INVOCATIONS + 1)
    ):
        raise RuntimeError("candidate ranks are not contiguous")
    scores: List[float] = []
    fingerprint_ids: List[str] = []
    for expected_rank, candidate in enumerate(candidates, 1):
        if fresh_serial:
            expected_role = (
                "uncertain_probe"
                if fresh_discovery or minimal_probe2 or expected_rank > 3
                else "trusted"
            )
            expected_trusted = expected_role == "trusted"
            if (
                candidate.get("selection_role") != expected_role
                or candidate.get("trusted") is not expected_trusted
                or candidate.get("diagnostic_only") is not True
                or candidate.get("incumbent_update_eligible") is not False
            ):
                raise RuntimeError(
                    f"rank {expected_rank} fresh serial role is invalid"
                )
            candidate["candidate_params"] = dict(
                candidate.get("optimized_params") or {}
            )
            candidate["candidate_score_recomputed"] = candidate.get(
                "candidate_score"
            )
        score = float(candidate.get("candidate_score"))
        recomputed = float(candidate.get("candidate_score_recomputed"))
        if not math.isfinite(score) or not math.isclose(
            score, recomputed, rel_tol=1e-12, abs_tol=1e-12
        ):
            raise RuntimeError(f"rank {expected_rank} score is invalid")
        params = candidate.get("candidate_params") or {}
        if set(params) != ALLOWED_PARAMS or any(
            not math.isfinite(float(value)) or float(value) < 0.0
            for value in params.values()
        ):
            raise RuntimeError(f"rank {expected_rank} params are invalid")
        predicted = candidate.get("predicted_resource") or {}
        feature_ratio = candidate.get("feature_ratio") or {}
        if (
            not set(CAPABILITY).issubset(predicted)
            or set(feature_ratio) != set(CAPABILITY)
            or any(
                not math.isfinite(float(predicted[name]))
                or float(predicted[name]) < 0.0
                or not math.isfinite(float(feature_ratio[name]))
                or float(feature_ratio[name]) < 0.0
                for name in CAPABILITY
            )
        ):
            raise RuntimeError(
                f"rank {expected_rank} prediction/feature ratio is invalid"
            )
        predicted_score = actual_score({
            name: float(predicted[name]) / CAPABILITY[name]
            for name in CAPABILITY
        })
        if not math.isclose(
            score, predicted_score, rel_tol=1e-12, abs_tol=1e-12
        ):
            raise RuntimeError(
                f"rank {expected_rank} score does not match predicted resource"
            )
        plan_fingerprint = str(
            candidate.get("workload_plan_fingerprint") or ""
        )
        fingerprint = candidate.get("three_level_fingerprint") or {}
        fingerprint_id = str(fingerprint.get("id") or "")
        if (
            not plan_fingerprint.startswith("ratio-plan-sha256:")
            or fingerprint.get("valid") is not True
            or fingerprint.get("parameter_fallback_used") is not False
            or fingerprint.get("plan_level") != plan_fingerprint
            or not fingerprint_id.startswith("three-level-sha256:")
        ):
            raise RuntimeError(f"rank {expected_rank} fingerprint is invalid")
        if not isinstance(candidate.get("boundary_plateau"), bool):
            raise RuntimeError(f"rank {expected_rank} plateau flag is not bool")
        if not isinstance(candidate.get("incumbent_update_eligible"), bool):
            raise RuntimeError(f"rank {expected_rank} eligibility is not bool")
        if (
            candidate["boundary_plateau"]
            and candidate["incumbent_update_eligible"]
        ):
            raise RuntimeError("plateau candidate is incumbent-update eligible")
        scores.append(score)
        fingerprint_ids.append(fingerprint_id)
    if len(set(fingerprint_ids)) != MAX_REAL_WORKLOAD_INVOCATIONS:
        raise RuntimeError("candidate fingerprints are not unique")
    provenance = {
        "analysis_sha256": sha256_file(analysis_path),
        "selection_contract": selection_contract,
        "extractor_sha256": (
            None if fresh_serial else sha256_file(EXTRACTOR)
        ),
        "generation_config_sha256": sha256_file(config_path),
        "source_history": str(history_path),
        "source_history_sha256": sha256_file(history_path),
        "source_ranking_line_number": ranking_line,
        "source_generation_only_stop_line_number": stop_line,
        "workload_sha256": sha256_file(Path(WORKLOAD[2])),
        "restore_script_sha256": sha256_file(Path(RESTORE[0])),
    }
    return analysis, generation_root, list(candidates), provenance


def validate_baseline(
    baseline_summary_path: Path,
    history_path: Path,
) -> Tuple[Dict[str, Any], Dict[str, float], Dict[str, Any]]:
    baseline_summary_path = baseline_summary_path.resolve()
    if not baseline_summary_path.is_file():
        raise RuntimeError("baseline summary is absent")
    baseline = load_json(baseline_summary_path)
    history_events = [
        json.loads(line)
        for line in history_path.read_text(encoding="utf-8").splitlines()
    ]
    protocol_events = [
        item
        for item in history_events
        if item.get("event") == "ratio_scale_baseline_measurement_protocol"
    ]
    if len(protocol_events) != 1:
        raise RuntimeError("baseline protocol event count is not one")
    protocol = protocol_events[0].get("measurement_protocol") or {}
    evidence = protocol.get("correctness_evidence") or {}
    checks = evidence.get("checks") or {}
    if (
        Path(str(protocol.get("summary_path") or "")).resolve()
        != baseline_summary_path
        or protocol.get("summary_sha256") != sha256_file(baseline_summary_path)
        or not protocol.get("point_id")
        or baseline_summary_path.parent.name != protocol.get("point_id")
        or protocol.get("runner_sha256") != EXPECTED_RUNNER_SHA256
        or protocol.get("qps_used_for_inner_scoring") is not False
        or protocol.get("stable") is not True
        or protocol.get("correctness_passed") is not True
        or evidence.get("passed") is not True
        or not checks
        or any(value is not True for value in checks.values())
    ):
        raise RuntimeError("baseline protocol/correctness binding failed")
    continuity = baseline.get("database_counter_continuity") or {}
    cleanup = baseline.get("worker_cleanup") or {}
    if (
        baseline.get("status") != "complete"
        or (baseline.get("window_mean_stability") or {}).get("stable") is not True
        or continuity.get("passed") is not True
        or continuity.get("counter_reset_count") != 0
        or cleanup.get("fully_released") is not True
        or cleanup.get("remaining_pids")
        or cleanup.get("counter_thread_stopped") is not True
    ):
        raise RuntimeError("baseline summary is incomplete or unclean")
    native_log = Path(str(baseline.get("native_log") or "")).resolve()
    if (
        baseline_summary_path.parent not in native_log.parents
        or not native_log.is_file()
        or sha256_file(native_log) != baseline.get("native_log_sha256")
    ):
        raise RuntimeError("baseline native-log binding failed")
    baseline_correctness_gate = classify_transaction_abort_gate(
        baseline,
        native_log.read_text(encoding="utf-8", errors="replace"),
    )
    if (
        baseline_correctness_gate["valid_replay"] is not True
        or baseline_correctness_gate["algorithmic_gate_rejection"] is not False
        or baseline_correctness_gate["technical_failure"] is not False
    ):
        raise RuntimeError(
            "baseline correctness gate failed: "
            f"{baseline_correctness_gate}"
        )
    baseline_utilization = {
        name: float((baseline.get("resource_utilization") or {})[name])
        for name in CAPABILITY
    }
    if any(
        not math.isfinite(value) or value < 0.0
        for value in baseline_utilization.values()
    ):
        raise RuntimeError("baseline resource utilization is invalid")
    return baseline, baseline_utilization, {
        "summary_path": str(baseline_summary_path),
        "summary_sha256": sha256_file(baseline_summary_path),
        "point_id": protocol.get("point_id"),
        "runner_sha256": protocol.get("runner_sha256"),
        "correctness_contract": evidence.get("contract"),
    }


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit(
            "usage: run_tpcc_round11_calibrated_top5_strict_serial.py "
            "TOP5_JSON REPLAY_ROOT BASELINE_SUMMARY"
        )
    analysis_path = Path(sys.argv[1]).resolve()
    replay_root = Path(sys.argv[2]).resolve()
    baseline_summary_path = Path(sys.argv[3]).resolve()
    replay_root.mkdir(parents=True, exist_ok=True)
    events_path = replay_root / "events.jsonl"
    summary_path = replay_root / "summary.json"

    analysis, generation_root, candidates, provenance = (
        validate_generation_and_top5(analysis_path, replay_root)
    )
    fresh_discovery = (
        analysis.get("selection_contract")
        == "tpcc_round12_current_plan_discovery_probe5_v1"
    )
    minimal_probe2 = (
        analysis.get("selection_contract")
        == "tpcc_minimal_unknown_acquisition_probe2_v1"
    )
    corrected_round13 = (
        analysis.get("selection_contract")
        == "tpcc_round13_exact_plan_regime_corrected_top5_v1"
    )
    robust_round14 = (
        analysis.get("selection_contract")
        == "tpcc_robust_exact_plan_regime_trusted3_probe2_v1"
    )
    fresh_serial = (
        fresh_discovery
        or minimal_probe2
        or corrected_round13
        or robust_round14
    )
    expected_replay_prefix = (
        "top2_real_replay_acquisition"
        if minimal_probe2
        else (
            "top5_real_replay_round12"
            if fresh_discovery
            else (
                "top5_real_replay_round13"
                if corrected_round13
                else (
                    "top5_real_replay_round14"
                    if robust_round14
                    else "top5_real_replay_round11"
                )
            )
        )
    )
    if replay_root.parent != generation_root or not replay_root.name.startswith(
        expected_replay_prefix
    ):
        raise RuntimeError(
            "replay root must be one current-generation direct child named "
            f"{expected_replay_prefix}*"
        )
    history_path = Path(str(analysis["source_history"])).resolve()
    _, baseline_utilization, baseline_provenance = validate_baseline(
        baseline_summary_path,
        history_path,
    )
    baseline_score = actual_score(baseline_utilization)
    scoring_contract = validate_directional_score_contract()

    round_lock_path = generation_root / "strict_top5_real_replay.lock"
    ledger_path = generation_root / (
        "strict_top2_real_replay_acquisition_ledger_v1.json"
        if minimal_probe2
        else (
            "strict_top5_real_replay_round12_ledger_v1.json"
            if fresh_discovery
            else (
                "strict_top5_real_replay_round13_ledger_v1.json"
                if corrected_round13
                else (
                    "strict_top5_real_replay_round14_ledger_v1.json"
                    if robust_round14
                    else "strict_top5_real_replay_round11_ledger_v1.json"
                )
            )
        )
    )
    ledger_contract = (
        "tpcc_minimal_unknown_acquisition_probe2_strict_serial_replay_v1"
        if minimal_probe2
        else (
            "tpcc_round12_current_plan_discovery_strict_serial_replay_v1"
            if fresh_discovery
            else (
                "tpcc_round13_exact_plan_regime_corrected_strict_serial_replay_v1"
                if corrected_round13
                else (
                    "tpcc_round14_robust_exact_plan_regime_strict_serial_replay_v1"
                    if robust_round14
                    else "tpcc_round11_calibrated_top5_strict_serial_replay_v1"
                )
            )
        )
    )
    successful: List[Dict[str, Any]] = []
    technical_failures: List[Dict[str, Any]] = []
    adaptive_enabled = (
        os.environ.get("ADAPTIVE_REAL_REPLAY_ENABLED", "0") == "1"
    )
    adaptive_history_paths = [
        value
        for value in os.environ.get(
            "ADAPTIVE_REPLAY_HISTORY_JSON", ""
        ).split(os.pathsep)
        if value
    ]
    adaptive_history = (
        load_history(adaptive_history_paths)
        if adaptive_history_paths
        else []
    )
    adaptive_config = config_from_env()
    adaptive_decisions: List[Dict[str, Any]] = []
    adaptive_stop_requested = False
    baseline_params_restored = False
    baseline_snapshot_restored = False
    baseline_params_restore_error: Optional[str] = None
    baseline_snapshot_restore_error: Optional[str] = None
    fatal_error: Optional[str] = None
    real_workload_invocation_count = 0
    invoked_ranks: List[int] = []
    selected_candidates: List[Dict[str, Any]] = []
    remaining_selection: Dict[str, Any] = {
        "selection_contract": "tpcc_remaining_budget_maximin_v1",
        "remaining_physical_budget": MAX_REAL_WORKLOAD_INVOCATIONS,
        "available_ranks": [],
        "selected_ranks": [],
        "selected_candidates": [],
    }

    def _termination_handler(signum: int, _frame: Any) -> None:
        raise TerminationRequested(f"termination signal received: {signum}")

    previous_sigterm_handler = signal.signal(
        signal.SIGTERM,
        _termination_handler,
    )
    previous_sigint_handler = signal.signal(
        signal.SIGINT,
        _termination_handler,
    )

    lock_handle = round_lock_path.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if ledger_path.exists():
            ledger = load_json(ledger_path)
            if (
                ledger.get("contract") != ledger_contract
                or ledger.get("source_history_sha256")
                != provenance["source_history_sha256"]
                or Path(str(ledger.get("replay_root") or "")).resolve()
                != replay_root
            ):
                raise RuntimeError("persistent replay ledger contract mismatch")
            successful = list(ledger.get("successful") or [])
            technical_failures = list(ledger.get("technical_failures") or [])
            adaptive_decisions = list(
                ledger.get("adaptive_decisions") or []
            )
            adaptive_stop_requested = bool(
                ledger.get("adaptive_stop_requested")
            )
        else:
            ledger = {
                "contract": ledger_contract,
                "source_history_sha256": provenance["source_history_sha256"],
                "replay_root": str(replay_root),
                "successful": [],
                "technical_failures": [],
                "audited_noncounting_summaries": [],
                "real_workload_invocation_count": 0,
                "maximum_real_workload_invocations": (
                    MAX_REAL_WORKLOAD_INVOCATIONS
                ),
                "adaptive_decisions": [],
                "adaptive_stop_requested": False,
            }
            atomic_json(ledger_path, ledger)
        observed_invocations = count_real_workload_invocations(events_path)
        invoked_ranks = load_invoked_ranks(events_path)
        ledger_invocations = int(
            ledger.get("real_workload_invocation_count", observed_invocations)
        )
        if ledger_invocations != observed_invocations:
            raise RuntimeError("replay ledger and invocation events disagree")
        if ledger_invocations > MAX_REAL_WORKLOAD_INVOCATIONS:
            raise RuntimeError("persistent ledger exceeds real workload hard cap")
        real_workload_invocation_count = ledger_invocations
        ledger["real_workload_invocation_count"] = (
            real_workload_invocation_count
        )
        ledger["maximum_real_workload_invocations"] = (
            MAX_REAL_WORKLOAD_INVOCATIONS
        )
        atomic_json(ledger_path, ledger)
        if len(successful) > MAX_REAL_WORKLOAD_INVOCATIONS or len({
            int(item["rank"]) for item in successful
        }) != len(successful):
            raise RuntimeError("persistent ledger exceeds or duplicates Top-5")
        represented_summaries = {
            str(
                (item.get("measurement_protocol") or {}).get(
                    "validated_summary_path"
                )
            )
            for item in successful
        }
        represented_summaries.update(
            str(Path(str(item["summary_path"])).resolve())
            for item in ledger.get("audited_noncounting_summaries") or []
        )
        replay_dirs = (
            [replay_root]
            if fresh_serial
            else list(generation_root.glob("top5_real_replay_round11*"))
        )
        unexpected_summaries = [
            str(path.resolve())
            for replay_dir in replay_dirs
            for path in replay_dir.glob("rank_*/attempt_*/point_*/summary.json")
            if str(path.resolve()) not in represented_summaries
        ]
        if unexpected_summaries:
            raise RuntimeError(
                "unledgered stable-point artifacts require manual audit: "
                f"{unexpected_summaries}"
            )

        successful_ranks = {int(item["rank"]) for item in successful}
        if fresh_serial:
            selected_candidates = [
                candidate
                for candidate in candidates
                if int(candidate["rank"]) not in invoked_ranks
            ][
                : MAX_REAL_WORKLOAD_INVOCATIONS - real_workload_invocation_count
            ]
            remaining_selection = {
                "selection_contract": (
                    "tpcc_presealed_probe2_acquisition_serial_order_v1"
                    if minimal_probe2
                    else (
                        "tpcc_round12_presealed_probe5_serial_order_v1"
                        if fresh_discovery
                        else (
                            "tpcc_round14_presealed_trusted3_probe2_serial_order_v1"
                            if robust_round14
                            else "tpcc_round13_presealed_trusted3_probe2_serial_order_v1"
                        )
                    )
                ),
                "remaining_physical_budget": (
                    MAX_REAL_WORKLOAD_INVOCATIONS
                    - real_workload_invocation_count
                ),
                "available_ranks": [
                    int(candidate["rank"])
                    for candidate in candidates
                    if int(candidate["rank"]) not in invoked_ranks
                ],
                "selected_ranks": [
                    int(candidate["rank"])
                    for candidate in selected_candidates
                ],
                "selected_candidates": selected_candidates,
            }
        else:
            if 1 not in successful_ranks or 1 not in invoked_ranks:
                raise RuntimeError(
                    "rank1 must be zero-call reclassified before remaining-budget replay"
                )
            remaining_selection = select_remaining_candidates(
                candidates,
                MAX_REAL_WORKLOAD_INVOCATIONS - real_workload_invocation_count,
                invoked_ranks,
            )
            selected_candidates = list(
                remaining_selection.get("selected_candidates") or []
            )

        try:
            start_snapshot = restore_database()
            start_params = apply_params(BASELINE_PARAMS)
            write_event(events_path, {
                "event": "tpcc_gradient_top5_start",
                "candidate_count": len(candidates),
                "maximum_real_workload_invocations": (
                    MAX_REAL_WORKLOAD_INVOCATIONS
                ),
                "real_workload_invocation_count": (
                    real_workload_invocation_count
                ),
                "already_successful_real_replays": len(successful),
                "baseline_verification": start_params,
                "baseline_snapshot_restore": start_snapshot,
                "baseline_provenance": baseline_provenance,
                "baseline_actual_utilization": baseline_utilization,
                "baseline_actual_score": baseline_score,
                "scoring_contract": scoring_contract,
                "top5_provenance": provenance,
                "adaptive_real_replay_enabled": adaptive_enabled,
                "adaptive_history_paths": adaptive_history_paths,
                "adaptive_history_point_count": len(adaptive_history),
                "invoked_ranks": invoked_ranks,
                "remaining_budget_selection": {
                    key: value
                    for key, value in remaining_selection.items()
                    if key != "selected_candidates"
                },
            })
            for candidate in selected_candidates:
                if adaptive_stop_requested:
                    break
                rank = int(candidate["rank"])
                if rank in successful_ranks:
                    continue
                if len(successful) >= MAX_REAL_WORKLOAD_INVOCATIONS:
                    raise RuntimeError(
                        "successful replay hard cap already reached"
                    )
                previous_attempts = sum(
                    int(item.get("rank") or 0) == rank
                    for item in technical_failures
                )
                for attempt in range(previous_attempts + 1, 3):
                    if (
                        real_workload_invocation_count
                        >= MAX_REAL_WORKLOAD_INVOCATIONS
                    ):
                        raise RuntimeError(
                            "real workload invocation hard cap reached before workload"
                        )
                    point_root = (
                        replay_root
                        / f"rank_{rank:02d}"
                        / f"attempt_{attempt:02d}"
                    )
                    log_path = point_root / "workload.log"
                    if point_root.exists():
                        raise RuntimeError(
                            f"replay attempt root already exists: {point_root}"
                        )
                    point_root.mkdir(parents=True)
                    validated_success = False
                    try:
                        restore_output = restore_database()
                        verification = apply_params(
                            candidate["candidate_params"]
                        )
                        write_event(events_path, {
                            "event": "tpcc_gradient_real_replay_started",
                            "rank": rank,
                            "attempt": attempt,
                            "candidate": candidate,
                            "snapshot_restore": restore_output,
                            "verification": verification,
                        })
                        real_workload_invocation_count += 1
                        ledger["real_workload_invocation_count"] = (
                            real_workload_invocation_count
                        )
                        atomic_json(ledger_path, ledger)
                        write_event(events_path, {
                            "event": (
                                "tpcc_gradient_real_replay_workload_invocation_reserved"
                            ),
                            "rank": rank,
                            "attempt": attempt,
                            "real_workload_invocation_count": (
                                real_workload_invocation_count
                            ),
                        })
                        completed, payload = run_workload(point_root, log_path)
                        utilization, protocol, measurement_summary = (
                            normalize_measurement_payload(payload, point_root)
                        )
                        if (
                            protocol.get("stable") is not True
                            or protocol.get("technical_failure") is True
                            or protocol.get("correctness_passed") is not True
                            or any(
                                not math.isfinite(float(utilization[name]))
                                or float(utilization[name]) < 0.0
                                for name in utilization
                            )
                        ):
                            raise RuntimeError(
                                "structured workload point failed technical gate"
                            )
                        measured_score = actual_score(utilization)
                        gate_rejection = bool(
                            protocol.get("algorithmic_gate_rejection") is True
                        )
                        eligible = bool(
                            candidate["incumbent_update_eligible"]
                            and not candidate["boundary_plateau"]
                            and not gate_rejection
                        )
                        result = {
                            "rank": rank,
                            "attempt": attempt,
                            "selection_role": candidate.get(
                                "selection_role", "legacy"
                            ),
                            "trusted": candidate.get("trusted"),
                            "diagnostic_only": True,
                            "eligible_for_incumbent_or_accepted": False,
                            "candidate_params": candidate["candidate_params"],
                            "predicted_score": candidate["candidate_score"],
                            "predicted_score_delta": candidate.get(
                                "candidate_score_delta"
                            ),
                            "feature_ratio": candidate["feature_ratio"],
                            "predicted_resource": candidate["predicted_resource"],
                            "workload_plan_fingerprint": candidate[
                                "workload_plan_fingerprint"
                            ],
                            "three_level_fingerprint": candidate[
                                "three_level_fingerprint"
                            ],
                            "actual_utilization": utilization,
                            "actual_resource": {
                                name: float(utilization[name])
                                * CAPABILITY[name]
                                for name in CAPABILITY
                            },
                            "actual_score": measured_score,
                            "actual_score_delta_vs_baseline": (
                                measured_score - baseline_score
                            ),
                            "workload_returncode": completed.returncode,
                            "algorithmic_gate_rejection": (
                                gate_rejection
                            ),
                            "offline_plateau": bool(
                                candidate["boundary_plateau"]
                            ),
                            "incumbent_update_eligible": eligible,
                            "consumes_successful_replay_budget": True,
                            "consumes_technical_failure_budget": False,
                            "benchmark": payload.get("benchmark") or {},
                            "benchmark_latency": (
                                payload.get("benchmark_latency") or {}
                            ),
                            "measurement_protocol": protocol,
                            "measurement_summary": measurement_summary,
                            "payload": payload,
                        }
                        validated_success = True
                        atomic_json(point_root / "point_result.json", result)
                        successful.append(result)
                        successful_ranks.add(rank)
                        ledger["successful"] = successful
                        ledger["technical_failures"] = technical_failures
                        atomic_json(ledger_path, ledger)
                        write_event(events_path, {
                            "event": "tpcc_gradient_real_replay_complete",
                            **result,
                        })
                        if adaptive_enabled:
                            remaining_candidates = [
                                item
                                for item in selected_candidates
                                if int(item["rank"])
                                not in successful_ranks
                            ]
                            incumbent_scores = [baseline_score]
                            incumbent_scores.extend(
                                float(item["actual_score"])
                                for item in successful
                                if item.get("actual_score") is not None
                                and item.get("incumbent_update_eligible")
                                is not False
                                and item.get("algorithmic_gate_rejection")
                                is not True
                            )
                            decision = decide_after_success(
                                history=adaptive_history,
                                current_successes=successful,
                                remaining_candidates=remaining_candidates,
                                incumbent_actual_score=max(
                                    incumbent_scores
                                ),
                                config=adaptive_config,
                            )
                            decision_event = {
                                **decision.as_event(),
                                "scenario": "tpcc",
                                "completed_rank": rank,
                                "remaining_ranks": [
                                    int(item["rank"])
                                    for item in remaining_candidates
                                ],
                            }
                            adaptive_decisions.append(decision_event)
                            adaptive_stop_requested = (
                                not decision.continue_replay
                            )
                            ledger["adaptive_decisions"] = (
                                adaptive_decisions
                            )
                            ledger["adaptive_stop_requested"] = (
                                adaptive_stop_requested
                            )
                            atomic_json(ledger_path, ledger)
                            write_event(events_path, decision_event)
                        break
                    except TerminationRequested:
                        raise
                    except Exception as exc:
                        if validated_success:
                            raise RuntimeError(
                                "validated successful replay persistence failed; "
                                "refusing a retry that could exceed the hard cap"
                            ) from exc
                        failure = {
                            "rank": rank,
                            "attempt": attempt,
                            "error": f"{type(exc).__name__}: {exc}",
                            "consumes_successful_replay_budget": False,
                        }
                        technical_failures.append(failure)
                        ledger["successful"] = successful
                        ledger["technical_failures"] = technical_failures
                        atomic_json(ledger_path, ledger)
                        write_event(events_path, {
                            "event": (
                                "tpcc_gradient_real_replay_technical_failure"
                            ),
                            **failure,
                        })
                else:
                    write_event(events_path, {
                        "event": (
                            "tpcc_gradient_candidate_abandoned_after_"
                            "technical_failures"
                        ),
                        "rank": rank,
                    })
                if adaptive_stop_requested:
                    break
        except BaseException as exc:
            fatal_error = f"{type(exc).__name__}: {exc}"
            write_event(events_path, {
                "event": "tpcc_gradient_replay_fatal_error",
                "error": fatal_error,
            })
        finally:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            try:
                final_snapshot_output = restore_database()
                baseline_snapshot_restored = True
            except Exception as exc:
                final_snapshot_output = ""
                baseline_snapshot_restore_error = (
                    f"{type(exc).__name__}: {exc}"
                )
            try:
                final_param_readback = apply_params(BASELINE_PARAMS)
                baseline_params_restored = True
            except Exception as exc:
                final_param_readback = {}
                baseline_params_restore_error = (
                    f"{type(exc).__name__}: {exc}"
                )
            write_event(events_path, {
                "event": "tpcc_gradient_baseline_restore_finally",
                "baseline_snapshot_restored": baseline_snapshot_restored,
                "baseline_snapshot_restore_output": final_snapshot_output,
                "baseline_snapshot_restore_error": (
                    baseline_snapshot_restore_error
                ),
                "baseline_params_restored": baseline_params_restored,
                "baseline_param_readback": final_param_readback,
                "baseline_params_restore_error": baseline_params_restore_error,
            })
    finally:
        lock_handle.close()
        signal.signal(signal.SIGTERM, previous_sigterm_handler)
        signal.signal(signal.SIGINT, previous_sigint_handler)

    successful = sorted(successful, key=lambda item: int(item["rank"]))
    prediction_error = build_top5_prediction_metrics(successful, CAPABILITY)
    full_top5_complete = bool(
        len(successful) == MAX_REAL_WORKLOAD_INVOCATIONS
        and [int(item["rank"]) for item in successful]
        == list(range(1, MAX_REAL_WORKLOAD_INVOCATIONS + 1))
        and (minimal_probe2 or prediction_error["complete_top5"])
    )
    adaptive_round_complete = adaptive_completion_certified(
        enabled=adaptive_enabled and adaptive_stop_requested,
        successful_count=len(successful),
        decisions=adaptive_decisions,
    )
    round_complete = full_top5_complete or adaptive_round_complete
    # This is a diagnostic replay runner. It can certify an entry gate, but it
    # must never mutate or claim a formal accepted/incumbent/range update.
    diagnostic_entry_gate_eligible = bool(
        round_complete
        and baseline_params_restored
        and baseline_snapshot_restored
        and fatal_error is None
    )
    state_commit_eligible = False
    eligible_successful = [
        item
        for item in successful
        if item.get("incumbent_update_eligible") is True
    ]
    best = (
        max(eligible_successful, key=lambda item: item["actual_score"])
        if state_commit_eligible and eligible_successful
        else None
    )
    selected = (
        best
        if best is not None and best["actual_score"] > baseline_score
        else None
    )
    summary = {
        "successful_real_replay_count": len(successful),
        "technical_failure_count": len(technical_failures),
        "real_workload_invocation_count": real_workload_invocation_count,
        "maximum_real_workload_invocations": MAX_REAL_WORKLOAD_INVOCATIONS,
        "maximum_successful_real_replays": MAX_REAL_WORKLOAD_INVOCATIONS,
        "round_complete": round_complete,
        "full_top5_complete": full_top5_complete,
        "adaptive_round_complete": adaptive_round_complete,
        "adaptive_real_replay_enabled": adaptive_enabled,
        "adaptive_history_paths": adaptive_history_paths,
        "adaptive_history_point_count": len(adaptive_history),
        "adaptive_stop_requested": adaptive_stop_requested,
        "adaptive_decisions": adaptive_decisions,
        "adaptive_skipped_ranks": [
            int(candidate["rank"])
            for candidate in candidates
            if int(candidate["rank"])
            not in {int(item["rank"]) for item in successful}
        ],
        "invoked_ranks": invoked_ranks,
        "remaining_physical_budget_at_selection": remaining_selection.get(
            "remaining_physical_budget"
        ),
        "remaining_budget_available_ranks": remaining_selection.get(
            "available_ranks", []
        ),
        "remaining_budget_selected_ranks": remaining_selection.get(
            "selected_ranks", []
        ),
        "remaining_budget_selection_contract": remaining_selection.get(
            "selection_contract"
        ),
        "diagnostic_entry_gate_eligible": diagnostic_entry_gate_eligible,
        "state_commit_eligible": False,
        "fatal_error": fatal_error,
        "successful": successful,
        "technical_failures": technical_failures,
        "prediction_error": prediction_error,
        "baseline_params_restored": baseline_params_restored,
        "baseline_params_restore_error": baseline_params_restore_error,
        "baseline_snapshot_restored": baseline_snapshot_restored,
        "baseline_snapshot_restore_error": (
            baseline_snapshot_restore_error
        ),
        "baseline_provenance": baseline_provenance,
        "top5_provenance": provenance,
        "baseline_actual_utilization": baseline_utilization,
        "baseline_actual_score": baseline_score,
        "selected_incumbent_rank": selected.get("rank") if selected else None,
        "selected_incumbent_params": (
            selected.get("candidate_params") if selected else None
        ),
        "selected_incumbent_actual_score": (
            selected.get("actual_score") if selected else baseline_score
        ),
        "incumbent_updated": False,
        "accepted_count_delta": 0,
        "range_updated": False,
        "gradient_update_applied": False,
        "rejected_candidates_update_state": False,
    }
    atomic_json(summary_path, summary)
    print(json.dumps({
        "summary_path": str(summary_path),
        "successful_real_replay_count": len(successful),
        "technical_failure_count": len(technical_failures),
        "round_complete": round_complete,
        "baseline_restored": (
            baseline_params_restored and baseline_snapshot_restored
        ),
    }, sort_keys=True))
    return 0 if diagnostic_entry_gate_eligible else 2


if __name__ == "__main__":
    raise SystemExit(main())
