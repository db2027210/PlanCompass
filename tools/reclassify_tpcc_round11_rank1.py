#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Mapping, Tuple

import run_tpcc_round11_calibrated_top5_strict_serial as runner
from tpcc_remaining_budget_selector import select_remaining_candidates


ROOT = Path(
    os.environ.get(
        "TPCC_ROUND_ROOT",
        str(Path(__file__).resolve().parent),
    )
).resolve()
REPLAY = ROOT / "top5_real_replay_round11_calibrated_v1"
LEDGER = ROOT / "strict_top5_real_replay_round11_ledger_v1.json"
EVENTS = REPLAY / "events.jsonl"
SELECTION = ROOT / "tpcc_round11_calibrated_top5.json"
LOCK = ROOT / "strict_top5_real_replay.lock"

EXPECTED_INPUT_SHA256 = {
    LEDGER.name: "a608b9958ca5c855e2391ece01b711f2194ed0aa51b81c41a590dc02801123dc",
    "events.jsonl": "8f48c764c12c5d7f7232086efef96e0c09aa6ede0e5de792934ba38f61f0b596",
    SELECTION.name: "08bd6ec14011d69c4517e8491b8528c7877329b7861416bd514d2378deddc307",
}
EXPECTED_SOURCE_HISTORY_SHA256 = (
    "97b8aab10a0baa01b24ee3e39180274db281f474274586b6f3a083cbf27629e8"
)
EXPECTED_REPLAY_ROOT = (
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "gradient_debug_round11_tpcc_calibrated_deadband_v1_20260728/"
    "top5_real_replay_round11_calibrated_v1"
)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp.reclassify")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(payload, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def load_payload(log_path: Path) -> Dict[str, Any]:
    marker = "RESOURCE_ADAPTIVE_BENCHMARK_JSON="
    lines = log_path.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines()
    for line in reversed(lines):
        if marker in line:
            parsed = json.loads(line.split(marker, 1)[1].strip())
            if not isinstance(parsed, dict):
                break
            return parsed
    raise ValueError(f"benchmark payload missing: {log_path}")


def load_events() -> Tuple[List[Dict[str, Any]], bytes]:
    original = EVENTS.read_bytes()
    parsed: List[Dict[str, Any]] = []
    for line_number, line in enumerate(
        original.decode("utf-8").splitlines(),
        1,
    ):
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError(f"event line {line_number} is not an object")
        parsed.append(event)
    return parsed, original


def load_start_events(
    events: List[Dict[str, Any]],
) -> Dict[Tuple[int, int], Dict[str, Any]]:
    starts: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for event in events:
        if event.get("event") != "tpcc_gradient_real_replay_started":
            continue
        binding = (int(event["rank"]), int(event["attempt"]))
        if binding in starts:
            raise ValueError(f"duplicate start event for {binding}")
        starts[binding] = event
    return starts


def classify_rank1_attempt(point_root: Path, candidate: dict) -> dict:
    payload = load_payload(point_root.parent / "workload.log")
    utilization, protocol, measurement_summary = (
        runner.normalize_measurement_payload(payload, point_root.parent)
    )
    if (
        protocol.get("correctness_passed") is not True
        or protocol.get("technical_failure") is not False
    ):
        raise ValueError("patched policy did not validate rank1 evidence")
    workload_returncode = measurement_summary.get("workload_returncode")
    if isinstance(workload_returncode, bool) or not isinstance(
        workload_returncode,
        int,
    ):
        raise ValueError("measurement summary lacks integer workload returncode")
    gate_rejection = protocol.get("algorithmic_gate_rejection") is True
    return {
        "rank": 1,
        "attempt": int(point_root.parent.name.split("_")[-1]),
        "candidate_params": candidate["candidate_params"],
        "predicted_score": candidate["candidate_score"],
        "predicted_score_delta": candidate.get("candidate_score_delta"),
        "feature_ratio": candidate["feature_ratio"],
        "predicted_resource": candidate["predicted_resource"],
        "workload_plan_fingerprint": candidate["workload_plan_fingerprint"],
        "three_level_fingerprint": candidate["three_level_fingerprint"],
        "actual_utilization": utilization,
        "actual_resource": {
            name: float(utilization[name]) * runner.CAPABILITY[name]
            for name in runner.CAPABILITY
        },
        "actual_score": runner.actual_score(utilization),
        "workload_returncode": workload_returncode,
        "algorithmic_gate_rejection": gate_rejection,
        "offline_plateau": bool(candidate["boundary_plateau"]),
        "incumbent_update_eligible": False,
        "eligible_for_incumbent_or_accepted": False,
        "diagnostic_only": True,
        "consumes_successful_replay_budget": True,
        "consumes_physical_call_budget": True,
        "consumes_technical_failure_budget": False,
        "benchmark": payload.get("benchmark") or {},
        "benchmark_latency": payload.get("benchmark_latency") or {},
        "measurement_protocol": protocol,
        "measurement_summary": measurement_summary,
        "payload": payload,
    }


def validate_rank2_interruption() -> Dict[str, Any]:
    attempt_root = REPLAY / "rank_02" / "attempt_01"
    points = sorted(attempt_root.glob("point_*"))
    if len(points) != 1:
        raise ValueError("rank2 interruption must have exactly one point root")
    point_root = points[0]
    if (point_root / "summary.json").exists():
        raise ValueError("rank2 unexpectedly has a sealed summary")
    evidence = {}
    for name in (
        "resource_samples.jsonl",
        "database_counter_samples.jsonl",
        "run_manifest.json",
    ):
        path = point_root / name
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError(f"rank2 physical evidence missing: {path}")
        evidence[name] = {
            "path": str(path),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
    return {
        "rank": 2,
        "attempt": 1,
        "error": (
            "TerminationRequested: workload was physically invoked and "
            "interrupted before a sealed summary"
        ),
        "diagnostic_only": True,
        "valid_replay": False,
        "technical_failure": True,
        "consumes_successful_replay_budget": False,
        "consumes_physical_call_budget": True,
        "eligible_for_incumbent_or_accepted": False,
        "evidence": evidence,
    }


def validate_inputs(
    ledger: Dict[str, Any],
    events: List[Dict[str, Any]],
) -> Dict[Tuple[int, int], Dict[str, Any]]:
    observed_hashes = {
        LEDGER.name: sha256_file(LEDGER),
        "events.jsonl": sha256_file(EVENTS),
        SELECTION.name: sha256_file(SELECTION),
    }
    if observed_hashes != EXPECTED_INPUT_SHA256:
        raise ValueError(
            "sealed input SHA mismatch: "
            + json.dumps(observed_hashes, sort_keys=True)
        )
    if (
        ledger.get("contract")
        != "tpcc_round11_calibrated_top5_strict_serial_replay_v1"
        or ledger.get("source_history_sha256")
        != EXPECTED_SOURCE_HISTORY_SHA256
        or ledger.get("replay_root") != EXPECTED_REPLAY_ROOT
        or ledger.get("successful") != []
        or ledger.get("audited_noncounting_summaries") != []
        or ledger.get("adaptive_decisions") != []
        or ledger.get("adaptive_stop_requested") is not False
    ):
        raise ValueError("ledger does not exactly match pre-fix state")
    failures = ledger.get("technical_failures")
    if not isinstance(failures, list) or [
        (item.get("rank"), item.get("attempt")) for item in failures
    ] != [(1, 1), (1, 2)]:
        raise ValueError("ledger technical failures do not match rank1 attempts")
    starts = load_start_events(events)
    if set(starts) != {(1, 1), (1, 2), (2, 1)}:
        raise ValueError("start-event bindings do not match observed calls")
    if runner.count_real_workload_invocations(EVENTS) != 3:
        raise ValueError("physical evidence does not prove exactly three calls")
    if runner.load_invoked_ranks(EVENTS) != [1, 2]:
        raise ValueError("invoked ranks do not match physical evidence")
    return starts


def bind_candidate(
    event: Dict[str, Any],
    candidate: Dict[str, Any],
    binding: Tuple[int, int],
) -> None:
    event_candidate = event.get("candidate") or {}
    if (
        event_candidate.get("candidate_params")
        != candidate.get("candidate_params")
        or event_candidate.get("workload_plan_fingerprint")
        != candidate.get("workload_plan_fingerprint")
        or event_candidate.get("three_level_fingerprint")
        != candidate.get("three_level_fingerprint")
    ):
        raise ValueError(f"start event {binding} is not bound to candidate")


def main() -> int:
    dry_run = os.environ.get("TPCC_RECLASSIFY_DRY_RUN") == "1"
    lock_handle = LOCK.open(
        "r" if dry_run else "a+",
        encoding="utf-8",
    )
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        ledger_original = LEDGER.read_bytes()
        ledger = json.loads(ledger_original)
        events, events_original = load_events()
        starts = validate_inputs(ledger, events)

        selection = json.loads(SELECTION.read_text(encoding="utf-8"))
        candidates = selection.get("top5")
        if not isinstance(candidates, list) or len(candidates) != 5:
            raise ValueError("selection does not contain a sealed Top-5")
        by_rank = {int(item["rank"]): item for item in candidates}
        if sorted(by_rank) != [1, 2, 3, 4, 5]:
            raise ValueError("candidate ranks are not exactly 1..5")
        bind_candidate(starts[(1, 1)], by_rank[1], (1, 1))
        bind_candidate(starts[(1, 2)], by_rank[1], (1, 2))
        bind_candidate(starts[(2, 1)], by_rank[2], (2, 1))

        attempts: List[dict] = []
        attempt_dirs = sorted((REPLAY / "rank_01").glob("attempt_*"))
        if [path.name for path in attempt_dirs] != [
            "attempt_01",
            "attempt_02",
        ]:
            raise ValueError("rank1 attempt directories changed")
        for attempt_dir in attempt_dirs:
            summaries = list(attempt_dir.glob("point_*/summary.json"))
            if len(summaries) != 1:
                raise ValueError(
                    f"rank1 attempt must have one summary: {attempt_dir}"
                )
            attempts.append(
                classify_rank1_attempt(summaries[0].parent, by_rank[1])
            )

        canonical, duplicate = attempts
        duplicate["consumes_successful_replay_budget"] = False
        duplicate_summary = Path(
            duplicate["measurement_protocol"]["validated_summary_path"]
        )
        rank2_failure = validate_rank2_interruption()
        remaining = select_remaining_candidates(
            candidates,
            remaining_physical_budget=2,
            invoked_ranks=[1, 2],
        )
        if (
            len(remaining.get("selected_ranks") or []) != 2
            or any(
                rank not in {3, 4, 5}
                for rank in remaining["selected_ranks"]
            )
        ):
            raise ValueError("remaining-budget selector did not choose two points")

        original_failures = list(ledger["technical_failures"])
        ledger["successful"] = [canonical]
        ledger["technical_failures"] = [rank2_failure]
        ledger["real_workload_invocation_count"] = 3
        ledger["maximum_real_workload_invocations"] = (
            runner.MAX_REAL_WORKLOAD_INVOCATIONS
        )
        ledger["terminal_invoked_ranks"] = [1, 2]
        ledger["audited_noncounting_summaries"] = [{
            "contract": "valid_duplicate_measurement_noncounting_v1",
            "rank": 1,
            "attempt": duplicate["attempt"],
            "summary_path": str(duplicate_summary),
            "summary_sha256": runner.sha256_file(duplicate_summary),
            "valid_replay_evidence": True,
            "duplicate_candidate": True,
            "consumes_successful_replay_budget": False,
            "consumes_physical_call_budget": True,
            "eligible_for_incumbent_or_accepted": False,
            "reason": (
                "retry was caused only by the superseded correctness policy"
            ),
        }]
        ledger["remaining_budget_selection"] = {
            key: value
            for key, value in remaining.items()
            if key != "selected_candidates"
        }
        ledger["correctness_reclassification"] = {
            "contract": (
                "tpcc_round11_stable_termination_correctness_"
                "reclassification_v2"
            ),
            "new_real_replays_executed": 0,
            "physical_call_count": 3,
            "valid_replay_count": 1,
            "remaining_physical_budget": 2,
            "canonical_attempt": 1,
            "duplicate_noncounting_attempt": 2,
            "rank2_interrupted_physical_call_quarantined": True,
            "superseded_technical_failures": original_failures,
            "accepted_update_applied": False,
            "incumbent_update_applied": False,
            "range_update_applied": False,
            "gradient_update_applied": False,
            "input_sha256": EXPECTED_INPUT_SHA256,
        }

        event = {
            "event": "tpcc_round11_zero_call_correctness_reclassified",
            "rank": 1,
            "canonical_attempt": 1,
            "duplicate_noncounting_attempt": 2,
            "rank2_interrupted_physical_call_quarantined": True,
            "physical_call_count": 3,
            "valid_replay_count": 1,
            "remaining_physical_budget": 2,
            "remaining_selected_ranks": remaining["selected_ranks"],
            "new_real_replays_executed": 0,
            "accepted_update_applied": False,
            "incumbent_update_applied": False,
            "range_update_applied": False,
            "gradient_update_applied": False,
            "canonical_actual_score": canonical["actual_score"],
            "input_sha256": EXPECTED_INPUT_SHA256,
            "timestamp_epoch": time.time(),
        }
        events_new = events_original
        if events_new and not events_new.endswith(b"\n"):
            events_new += b"\n"
        events_new += (
            json.dumps(event, sort_keys=True) + "\n"
        ).encode("utf-8")
        ledger_new = json_bytes(ledger)

        if dry_run:
            print(json.dumps({
                "status": "dry_run_validated",
                "new_real_replays_executed": 0,
                "physical_call_count": 3,
                "valid_replay_count": 1,
                "successful_ranks": [1],
                "technical_failure_ranks": [2],
                "remaining_physical_budget": 2,
                "remaining_selected_ranks": remaining["selected_ranks"],
                "would_write_ledger_sha256": sha256_bytes(ledger_new),
                "would_write_events_sha256": sha256_bytes(events_new),
            }, sort_keys=True))
            return 0

        timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        ledger_backup = LEDGER.with_name(
            f"{LEDGER.name}.pre_reclassify.{timestamp}.{sha256_bytes(ledger_original)[:12]}.bak"
        )
        events_backup = EVENTS.with_name(
            f"{EVENTS.name}.pre_reclassify.{timestamp}.{sha256_bytes(events_original)[:12]}.bak"
        )
        ledger_backup.write_bytes(ledger_original)
        events_backup.write_bytes(events_original)
        try:
            atomic_bytes(LEDGER, ledger_new)
            atomic_bytes(EVENTS, events_new)
        except BaseException:
            atomic_bytes(LEDGER, ledger_original)
            atomic_bytes(EVENTS, events_original)
            raise

        if (
            json.loads(LEDGER.read_text(encoding="utf-8"))
            != ledger
            or EVENTS.read_bytes() != events_new
            or runner.count_real_workload_invocations(EVENTS) != 3
            or runner.load_invoked_ranks(EVENTS) != [1, 2]
        ):
            atomic_bytes(LEDGER, ledger_original)
            atomic_bytes(EVENTS, events_original)
            raise RuntimeError("post-write audit failed; inputs restored")

        print(json.dumps({
            "status": "reclassified",
            "new_real_replays_executed": 0,
            "physical_call_count": 3,
            "valid_replay_count": 1,
            "successful_ranks": [1],
            "technical_failure_ranks": [2],
            "remaining_physical_budget": 2,
            "remaining_selected_ranks": remaining["selected_ranks"],
            "ledger_backup": str(ledger_backup),
            "events_backup": str(events_backup),
            "ledger_sha256": sha256_file(LEDGER),
            "events_sha256": sha256_file(EVENTS),
        }, sort_keys=True))
        return 0
    finally:
        lock_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
