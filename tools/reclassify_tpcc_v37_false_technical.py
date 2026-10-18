#!/usr/bin/env python3
"""Zero-call repair for v37 rank-1/rank-3 correctness-policy misclassifications.

The script is deliberately not tied to mutable implicit paths.  Every input is
passed explicitly and SHA-pinned by the caller.  It refuses to run while the
diagnostic lock is held or while any reserved workload call lacks a terminal
event.  Physical-call accounting is immutable: attempt 1 for each rank becomes
the canonical result and attempt 2 becomes a valid, non-counting duplicate.
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
import sys
import time
from typing import Any, Dict, List, Mapping, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from resource_utility import ResourceUtilityCalculator
try:
    from tpcc_v38_replay_policy import (
        CORRECTNESS_POLICY_SHA256,
        classify_transaction_abort_gate,
    )
except ModuleNotFoundError:
    from tools.tpcc_v38_replay_policy import (
        CORRECTNESS_POLICY_SHA256,
        classify_transaction_abort_gate,
    )


FALSE_TECHNICAL_BINDINGS = {(1, 1), (1, 2), (3, 1), (3, 2)}
CANONICAL_BINDINGS = {(1, 1), (3, 1)}
DUPLICATE_BINDINGS = {(1, 2), (3, 2)}
CORRECTNESS_POLICY_PATH = Path(__file__).with_name(
    "tpcc_round11_current_correctness_policy.py"
)
CAPABILITY = {"cpu": 3.8, "memory": 6.0511731087, "io": 54.0}
EXPECTED_WORKLOAD_SHA256 = (
    "bf03a7b4e299798739f4d92393b01db2c89c7cd266b58d12dad6737d5b0643ee"
)
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


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_object(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def load_events(path: Path) -> Tuple[List[Dict[str, Any]], bytes]:
    raw = path.read_bytes()
    events: List[Dict[str, Any]] = []
    for line_number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        value = json.loads(line)
        if not isinstance(value, dict):
            raise RuntimeError(f"event line {line_number} is not an object")
        events.append(value)
    return events, raw


def event_bindings(
    events: Sequence[Mapping[str, Any]],
    event_names: set[str],
) -> set[Tuple[int, int]]:
    bindings: set[Tuple[int, int]] = set()
    for event in events:
        if event.get("event") not in event_names:
            continue
        bindings.add((int(event["rank"]), int(event["attempt"])))
    return bindings


def validate_terminal_events(events: Sequence[Mapping[str, Any]]) -> set[Tuple[int, int]]:
    reserved = event_bindings(
        events,
        {"tpcc_gradient_real_replay_workload_invocation_reserved"},
    )
    terminal = event_bindings(
        events,
        {
            "tpcc_gradient_real_replay_complete",
            "tpcc_gradient_real_replay_technical_failure",
        },
    )
    if not reserved or reserved != terminal:
        raise RuntimeError(
            "diagnostic replay has unsettled calls: "
            f"reserved={sorted(reserved)} terminal={sorted(terminal)}"
        )
    return reserved


def find_start_events(
    events: Sequence[Mapping[str, Any]],
) -> Dict[Tuple[int, int], Dict[str, Any]]:
    starts: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for event in events:
        if event.get("event") != "tpcc_gradient_real_replay_started":
            continue
        binding = (int(event["rank"]), int(event["attempt"]))
        if binding in starts:
            raise RuntimeError(f"duplicate start event: {binding}")
        starts[binding] = dict(event)
    return starts


def load_workload_payload(log_path: Path) -> Dict[str, Any]:
    marker = "RESOURCE_ADAPTIVE_BENCHMARK_JSON="
    for line in reversed(log_path.read_text(encoding="utf-8", errors="replace").splitlines()):
        if marker not in line:
            continue
        value = json.loads(line.split(marker, 1)[1].strip())
        if isinstance(value, dict):
            return value
    raise RuntimeError(f"structured workload payload is missing: {log_path}")


def candidate_for_rank(selection: Mapping[str, Any], rank: int) -> Dict[str, Any]:
    top5 = selection.get("top5")
    if not isinstance(top5, list) or len(top5) != 5:
        raise RuntimeError("selection is not an exact Top-5")
    candidates = {int(item["rank"]): dict(item) for item in top5}
    if sorted(candidates) != [1, 2, 3, 4, 5]:
        raise RuntimeError("selection ranks are not exactly 1..5")
    return candidates[rank]


def validate_start_candidate(
    start: Mapping[str, Any],
    selected: Mapping[str, Any],
    rank: int,
) -> Dict[str, Any]:
    invoked = start.get("candidate")
    if not isinstance(invoked, dict):
        raise RuntimeError(f"rank{rank} start event lacks its sealed candidate")
    checks = {
        "rank": int(invoked.get("rank", -1)) == rank,
        "params": invoked.get("candidate_params") == selected.get("params"),
        "plan": invoked.get("workload_plan_fingerprint")
        == selected.get("workload_plan_fingerprint"),
        "fingerprint": invoked.get("three_level_fingerprint")
        == selected.get("three_level_fingerprint"),
        "role": invoked.get("selection_role") == selected.get("selection_role"),
    }
    if not all(checks.values()):
        raise RuntimeError(f"rank{rank} candidate binding mismatch: {checks}")
    return dict(invoked)


def point_root_for_attempt(replay_root: Path, rank: int, attempt: int) -> Path:
    attempt_root = replay_root / f"rank_{rank:02d}" / f"attempt_{attempt:02d}"
    points = sorted(attempt_root.glob("point_*"))
    if len(points) != 1 or not (points[0] / "summary.json").is_file():
        raise RuntimeError(f"rank{rank} attempt {attempt} lacks one sealed summary")
    return points[0]


def actual_score(utilization: Mapping[str, Any]) -> float:
    resources = {
        name: float(utilization[name]) * CAPABILITY[name]
        for name in CAPABILITY
    }
    return float(SCORE_CALCULATOR.calculate_utility_score(resources, CAPABILITY))


def normalize_measurement_payload(
    payload: Mapping[str, Any],
    output_root: Path,
) -> Tuple[Dict[str, float], Dict[str, Any], Dict[str, Any]]:
    protocol = payload.get("candidate_measurement_protocol") or {}
    aggregate = (payload.get("formal_pg_resource") or {}).get("aggregate") or {}
    if not isinstance(protocol, dict) or not isinstance(aggregate, dict):
        raise RuntimeError("invalid structured measurement payload")
    summary_path = Path(str(protocol.get("summary_path") or "")).resolve()
    output_root = output_root.resolve()
    if (
        output_root not in summary_path.parents
        or summary_path.name != "summary.json"
        or summary_path.parent.name != str(protocol.get("point_id") or "")
    ):
        raise RuntimeError("measurement summary is not bound to the attempt")
    if protocol.get("runner_sha256") != EXPECTED_WORKLOAD_SHA256:
        raise RuntimeError("measurement runner SHA changed")
    if protocol.get("qps_used_for_inner_scoring") is not False:
        raise RuntimeError("QPS was not excluded from inner scoring")
    if protocol.get("summary_sha256") != sha256_file(summary_path):
        raise RuntimeError("measurement summary SHA mismatch")
    summary = load_object(summary_path)
    native_log = Path(str(summary.get("native_log") or "")).resolve()
    samples_path = Path(
        str((payload.get("formal_pg_resource") or {}).get("samples_path") or "")
    ).resolve()
    for evidence in (native_log, samples_path):
        if output_root not in evidence.parents or not evidence.is_file():
            raise RuntimeError(f"measurement evidence escapes attempt: {evidence}")
    if summary.get("native_log_sha256") != sha256_file(native_log):
        raise RuntimeError("native workload log SHA mismatch")
    gate = classify_transaction_abort_gate(
        summary,
        native_log.read_text(encoding="utf-8", errors="replace"),
    )
    continuity = summary.get("database_counter_continuity") or {}
    cleanup = summary.get("worker_cleanup") or {}
    stable = bool(
        protocol.get("stable") is True
        and (summary.get("window_mean_stability") or {}).get("stable") is True
        and continuity.get("passed") is True
        and continuity.get("counter_reset_count") == 0
        and cleanup.get("fully_released") is True
        and not (cleanup.get("remaining_pids") or [])
        and cleanup.get("counter_thread_stopped") is True
    )
    utilization = {
        name: float((summary.get("resource_utilization") or {})[name])
        for name in CAPABILITY
    }
    for name, payload_field in (
        ("cpu", "cpu_utilization"),
        ("memory", "memory_utilization"),
        ("io", "io_utilization"),
    ):
        if (
            not math.isfinite(utilization[name])
            or utilization[name] < 0.0
            or not math.isclose(
                utilization[name],
                float(aggregate[payload_field]),
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
        ):
            raise RuntimeError(f"invalid resource evidence for {name}")
    normalized = dict(protocol)
    normalized.update(
        {
            "sealed_protocol_correctness_passed": protocol.get("correctness_passed"),
            "sealed_protocol_technical_failure": protocol.get("technical_failure"),
            "stable": stable,
            "correctness_passed": bool(stable and gate["valid_replay"]),
            "algorithmic_gate_rejection": False,
            "technical_failure": not bool(stable and gate["valid_replay"]),
            "correctness_gate": gate,
            "validated_summary_path": str(summary_path),
            "validated_summary_sha256": protocol["summary_sha256"],
        }
    )
    return utilization, normalized, summary


def classify_attempt(
    *,
    replay_root: Path,
    rank: int,
    attempt: int,
    candidate: Mapping[str, Any],
    baseline_score: float,
) -> Dict[str, Any]:
    point_root = point_root_for_attempt(replay_root, rank, attempt)
    payload = load_workload_payload(point_root.parent / "workload.log")
    utilization, protocol, measurement_summary = normalize_measurement_payload(
        payload,
        point_root.parent,
    )
    if protocol.get("correctness_passed") is not True or protocol.get(
        "technical_failure"
    ) is not False:
        raise RuntimeError(f"fixed policy did not validate rank{rank} attempt {attempt}")
    measured_score = actual_score(utilization)
    return {
        "rank": rank,
        "attempt": attempt,
        "selection_role": candidate.get("selection_role"),
        "trusted": candidate.get("trusted"),
        "diagnostic_only": True,
        "eligible_for_incumbent_or_accepted": False,
        "candidate_params": candidate["candidate_params"],
        "predicted_score": candidate["candidate_score"],
        "predicted_score_delta": candidate.get("candidate_score_delta"),
        "feature_ratio": candidate["feature_ratio"],
        "predicted_resource": candidate["predicted_resource"],
        "workload_plan_fingerprint": candidate["workload_plan_fingerprint"],
        "three_level_fingerprint": candidate["three_level_fingerprint"],
        "actual_utilization": utilization,
        "actual_resource": {
            name: float(utilization[name]) * CAPABILITY[name]
            for name in CAPABILITY
        },
        "actual_score": measured_score,
        "actual_score_delta_vs_baseline": measured_score - baseline_score,
        "workload_returncode": 0,
        "algorithmic_gate_rejection": False,
        "offline_plateau": bool(candidate["boundary_plateau"]),
        "incumbent_update_eligible": False,
        "consumes_successful_replay_budget": True,
        "consumes_technical_failure_budget": False,
        "benchmark": payload.get("benchmark") or {},
        "benchmark_latency": payload.get("benchmark_latency") or {},
        "measurement_protocol": protocol,
        "measurement_summary": measurement_summary,
        "payload": payload,
    }


def duplicate_audit(result: Mapping[str, Any]) -> Dict[str, Any]:
    protocol = result["measurement_protocol"]
    return {
        "contract": "valid_duplicate_measurement_noncounting_v1",
        "rank": int(result["rank"]),
        "attempt": int(result["attempt"]),
        "summary_path": protocol["validated_summary_path"],
        "summary_sha256": protocol["validated_summary_sha256"],
        "valid_replay_evidence": True,
        "duplicate_candidate": True,
        "consumes_successful_replay_budget": False,
        "consumes_physical_call_budget": True,
        "eligible_for_incumbent_or_accepted": False,
        "reason": "retry caused only by the superseded correctness policy",
    }


def atomic_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp.v37_reclassify")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def append_events(raw: bytes, events: Sequence[Mapping[str, Any]]) -> bytes:
    output = raw
    if output and not output.endswith(b"\n"):
        output += b"\n"
    for event in events:
        output += (json.dumps(dict(event), sort_keys=True) + "\n").encode("utf-8")
    return output


def require_sha(path: Path, expected: str, label: str) -> None:
    actual = sha256_file(path)
    if actual != expected:
        raise RuntimeError(f"{label} SHA mismatch: expected={expected} actual={actual}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--replay-root", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--formal-state", type=Path, required=True)
    parser.add_argument("--expected-selection-sha", required=True)
    parser.add_argument("--expected-ledger-sha", required=True)
    parser.add_argument("--expected-events-sha", required=True)
    parser.add_argument("--expected-summary-sha", required=True)
    parser.add_argument("--expected-formal-sha", required=True)
    parser.add_argument("--output-report", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    require_sha(CORRECTNESS_POLICY_PATH, CORRECTNESS_POLICY_SHA256, "correctness policy")
    for path, expected, label in (
        (args.selection, args.expected_selection_sha, "selection"),
        (args.ledger, args.expected_ledger_sha, "ledger"),
        (args.events, args.expected_events_sha, "events"),
        (args.summary, args.expected_summary_sha, "summary"),
        (args.formal_state, args.expected_formal_sha, "formal state"),
    ):
        require_sha(path.resolve(), expected, label)

    args.lock.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = args.lock.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("diagnostic runner lock is still held") from exc

        formal_before = args.formal_state.read_bytes()
        ledger_before = args.ledger.read_bytes()
        summary_before = args.summary.read_bytes()
        events, events_before = load_events(args.events)
        reserved = validate_terminal_events(events)
        ledger = json.loads(ledger_before)
        summary = json.loads(summary_before)
        selection = load_object(args.selection)
        starts = find_start_events(events)
        if not FALSE_TECHNICAL_BINDINGS.issubset(reserved):
            raise RuntimeError("the four rank1/rank3 physical bindings are not sealed")
        failures = list(ledger.get("technical_failures") or [])
        false_rows = [
            item
            for item in failures
            if (int(item["rank"]), int(item["attempt"])) in FALSE_TECHNICAL_BINDINGS
        ]
        if {(int(item["rank"]), int(item["attempt"])) for item in false_rows} != (
            FALSE_TECHNICAL_BINDINGS
        ) or any(
            item.get("error") != "RuntimeError: structured workload point failed technical gate"
            for item in false_rows
        ):
            raise RuntimeError("ledger does not exactly contain both false technical rows")
        if int(ledger.get("real_workload_invocation_count", -1)) != len(reserved):
            raise RuntimeError("ledger physical count does not equal reserved event bindings")
        if int(summary.get("real_workload_invocation_count", -1)) != len(reserved):
            raise RuntimeError("summary physical count does not equal reserved event bindings")
        if summary.get("baseline_params_restored") is not True or summary.get(
            "baseline_snapshot_restored"
        ) is not True:
            raise RuntimeError("baseline is not proven restored")
        if any(
            summary.get(field) not in (False, 0, None)
            for field in (
                "incumbent_updated",
                "range_updated",
                "gradient_update_applied",
                "state_commit_eligible",
            )
        ) or int(summary.get("accepted_count_delta", -1)) != 0:
            raise RuntimeError("diagnostic summary claims a forbidden formal state update")

        candidates: Dict[Tuple[int, int], Dict[str, Any]] = {}
        for binding in sorted(FALSE_TECHNICAL_BINDINGS):
            rank, _attempt = binding
            selected = candidate_for_rank(selection, rank)
            candidates[binding] = validate_start_candidate(
                starts[binding], selected, rank
            )
        for rank in (1, 3):
            if candidates[(rank, 1)] != candidates[(rank, 2)]:
                raise RuntimeError(f"rank{rank} retry was not the same sealed candidate")
        baseline_score = float(summary["baseline_actual_score"])
        classified = {
            binding: classify_attempt(
                replay_root=args.replay_root.resolve(),
                rank=binding[0],
                attempt=binding[1],
                candidate=candidates[binding],
                baseline_score=baseline_score,
            )
            for binding in sorted(FALSE_TECHNICAL_BINDINGS)
        }
        canonical = [classified[binding] for binding in sorted(CANONICAL_BINDINGS)]
        duplicates = [classified[binding] for binding in sorted(DUPLICATE_BINDINGS)]

        updated_ledger = copy.deepcopy(ledger)
        updated_ledger["technical_failures"] = [
            item for item in failures if item not in false_rows
        ]
        existing_success = list(updated_ledger.get("successful") or [])
        if any(int(item["rank"]) in {1, 3} for item in existing_success):
            raise RuntimeError("rank1 or rank3 already exists in successful results")
        updated_ledger["successful"] = sorted(
            [*canonical, *existing_success], key=lambda item: int(item["rank"])
        )
        audits = list(updated_ledger.get("audited_noncounting_summaries") or [])
        if audits:
            raise RuntimeError("unexpected pre-existing non-counting audit rows")
        updated_ledger["audited_noncounting_summaries"] = [
            duplicate_audit(item) for item in duplicates
        ]
        updated_ledger["correctness_reclassification"] = {
            "contract": "tpcc_v37_false_technical_zero_call_reclassification_v1",
            "canonical_bindings": [[1, 1], [3, 1]],
            "duplicate_noncounting_bindings": [[1, 2], [3, 2]],
            "physical_call_count_unchanged": len(reserved),
            "new_real_workloads_executed": 0,
            "superseded_technical_failures": false_rows,
            "formal_state_sha256": args.expected_formal_sha,
            "correctness_policy_sha256": CORRECTNESS_POLICY_SHA256,
        }

        updated_summary = copy.deepcopy(summary)
        updated_summary["successful"] = updated_ledger["successful"]
        updated_summary["technical_failures"] = updated_ledger["technical_failures"]
        updated_summary["successful_real_replay_count"] = len(
            updated_ledger["successful"]
        )
        updated_summary["technical_failure_count"] = len(
            updated_ledger["technical_failures"]
        )
        updated_summary["zero_call_correctness_reclassification"] = copy.deepcopy(
            updated_ledger["correctness_reclassification"]
        )
        # The batch remains ineligible: rank1/rank3 retries consumed two calls,
        # so it cannot provide five unique valid Top-5 observations.
        updated_summary["diagnostic_entry_gate_eligible"] = False
        updated_summary["state_commit_eligible"] = False

        now = time.time()
        appended = [
            {
                "event": "tpcc_gradient_real_replay_technical_failure_reclassified_valid",
                "rank": rank,
                "attempt": attempt,
                "canonical": (rank, attempt) in CANONICAL_BINDINGS,
                "audited_noncounting_duplicate": (
                    (rank, attempt) in DUPLICATE_BINDINGS
                ),
                "new_real_workloads_executed": 0,
                "timestamp_epoch": now,
            }
            for rank, attempt in sorted(FALSE_TECHNICAL_BINDINGS)
        ]
        events_after = append_events(events_before, appended)
        ledger_after = json_bytes(updated_ledger)
        summary_after = json_bytes(updated_summary)
        report = {
            "schema_version": "tpcc_v37_false_technical_reclassification_report_v1",
            "status": "dry_run_validated" if args.dry_run else "reclassified",
            "new_real_workloads_executed": 0,
            "physical_call_count": len(reserved),
            "canonical_valid_bindings": [[1, 1], [3, 1]],
            "duplicate_noncounting_bindings": [[1, 2], [3, 2]],
            "unique_valid_replay_count": len(updated_ledger["successful"]),
            "technical_failure_count": len(updated_ledger["technical_failures"]),
            "formal_state_sha256_before": sha256_bytes(formal_before),
            "formal_state_sha256_after": sha256_file(args.formal_state),
            "would_write_ledger_sha256": sha256_bytes(ledger_after),
            "would_write_events_sha256": sha256_bytes(events_after),
            "would_write_summary_sha256": sha256_bytes(summary_after),
            "formal_start_authorized": False,
            "reason": "v37 has fewer than five unique valid Top-5 observations",
        }
        if args.dry_run:
            print(json.dumps(report, sort_keys=True))
            return 0

        timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        backups: Dict[Path, Path] = {}
        for path, raw in (
            (args.ledger, ledger_before),
            (args.events, events_before),
            (args.summary, summary_before),
        ):
            backup = path.with_name(
                f"{path.name}.pre_v37_reclassify.{timestamp}.{sha256_bytes(raw)[:12]}.bak"
            )
            backup.write_bytes(raw)
            backups[path] = backup
        try:
            atomic_bytes(args.ledger, ledger_after)
            atomic_bytes(args.events, events_after)
            atomic_bytes(args.summary, summary_after)
            if args.formal_state.read_bytes() != formal_before:
                raise RuntimeError("formal state changed during zero-call reclassification")
            post_events, _ = load_events(args.events)
            if len(validate_terminal_events(post_events)) != len(reserved):
                raise RuntimeError("post-write physical call count changed")
            report["formal_state_sha256_after"] = sha256_file(args.formal_state)
            report["ledger_sha256"] = sha256_file(args.ledger)
            report["events_sha256"] = sha256_file(args.events)
            report["summary_sha256"] = sha256_file(args.summary)
            report["backups"] = {str(path): str(backup) for path, backup in backups.items()}
            atomic_bytes(args.output_report, json_bytes(report))
        except BaseException:
            atomic_bytes(args.ledger, ledger_before)
            atomic_bytes(args.events, events_before)
            atomic_bytes(args.summary, summary_before)
            raise
        print(json.dumps(report, sort_keys=True))
        return 0
    finally:
        lock_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
