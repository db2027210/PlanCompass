#!/usr/bin/env python3
"""Audit one completed TPC-C diagnostic batch from durable events only."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple


RESOURCES = ("cpu", "io", "memory")


def _load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _events(path: Path) -> List[Dict[str, Any]]:
    result = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid event JSON at line {line_number}") from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"event at line {line_number} is not an object")
        result.append(value)
    return result


def _binding(event: Mapping[str, Any]) -> Tuple[int, int]:
    if event.get("rank") is None or event.get("attempt") is None:
        raise RuntimeError("invocation event lacks exact rank/attempt binding")
    return int(event["rank"]), int(event["attempt"])


def _rank(values: Sequence[float]) -> List[float]:
    order = sorted(range(len(values)), key=lambda index: (values[index], index))
    result = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and values[order[end]] == values[order[cursor]]:
            end += 1
        average_rank = (cursor + 1 + end) / 2.0
        for position in range(cursor, end):
            result[order[position]] = average_rank
        cursor = end
    return result


def _spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_rank = _rank(left)
    right_rank = _rank(right)
    left_mean = sum(left_rank) / len(left_rank)
    right_mean = sum(right_rank) / len(right_rank)
    numerator = sum(
        (a - left_mean) * (b - right_mean) for a, b in zip(left_rank, right_rank)
    )
    left_norm = math.sqrt(sum((value - left_mean) ** 2 for value in left_rank))
    right_norm = math.sqrt(sum((value - right_mean) ** 2 for value in right_rank))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else None


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def audit(
    selection_path: Path,
    ledger_path: Path,
    events_path: Path,
    summary_path: Path,
    formal_state_path: Path,
    expected_formal_sha256: str,
) -> Dict[str, Any]:
    selection = _load(selection_path)
    ledger = _load(ledger_path)
    summary = _load(summary_path)
    events = _events(events_path)
    formal_sha = _sha256(formal_state_path)
    if formal_sha != expected_formal_sha256:
        raise RuntimeError("formal state SHA changed during diagnostic replay")

    event_groups: Dict[str, List[Dict[str, Any]]] = {}
    for event in events:
        event_groups.setdefault(str(event.get("event") or ""), []).append(event)
    started = {_binding(event) for event in event_groups.get("tpcc_gradient_real_replay_started", [])}
    reserved = {
        _binding(event)
        for event in event_groups.get("tpcc_gradient_real_replay_workload_invocation_reserved", [])
    }
    completed = {_binding(event) for event in event_groups.get("tpcc_gradient_real_replay_complete", [])}
    raw_technical = {
        _binding(event)
        for event in event_groups.get("tpcc_gradient_real_replay_technical_failure", [])
    }
    supersession_events = event_groups.get(
        "tpcc_gradient_recovery_technical_superseded_by_late_validated_completion",
        [],
    )
    superseded_technical = {_binding(event) for event in supersession_events}
    for event in supersession_events:
        binding = _binding(event)
        point_result_path = Path(str(event.get("point_result_path") or "")).resolve()
        point_summary_path = Path(str(event.get("point_summary_path") or "")).resolve()
        runner_summary_path = Path(str(event.get("runner_summary_path") or "")).resolve()
        formal_pin_path = Path(str(event.get("formal_state_path") or "")).resolve()
        if (
            event.get("contract")
            != "tpcc_v48_late_validated_completion_supersession_v1"
            or event.get("superseded_recovery_contract")
            != "tpcc_v48_orphan_reserved_zero_call_recovery_v1"
            or binding not in raw_technical
            or binding not in completed
            or int(event.get("superseded_technical_event_line", -1))
            >= int(event.get("late_completion_event_line", -1))
            or not point_result_path.is_file()
            or _sha256(point_result_path) != event.get("point_result_sha256")
            or not point_summary_path.is_file()
            or _sha256(point_summary_path) != event.get("point_summary_sha256")
            or not runner_summary_path.is_file()
            or _sha256(runner_summary_path) != event.get("runner_summary_sha256")
            or formal_pin_path != formal_state_path.resolve()
            or _sha256(formal_pin_path) != event.get("formal_state_sha256")
            or event.get("effective_classification") != "valid_replay"
            or int(event.get("physical_call_count_delta", -1)) != 0
            or int(event.get("workload_calls_added", -1)) != 0
        ):
            raise RuntimeError(
                "late-completion technical supersession evidence mismatch"
            )
    technical = raw_technical - superseded_technical
    preworkload = event_groups.get("tpcc_gradient_real_replay_preworkload_failure", [])
    ledger_success = {
        (int(item["rank"]), int(item["attempt"])) for item in ledger.get("successful") or []
    }
    ledger_technical = {
        (int(item["rank"]), int(item["attempt"]))
        for item in ledger.get("technical_failures") or []
    }
    physical = int(ledger.get("real_workload_invocation_count", -1))
    accounting_passed = bool(
        physical == 5
        and len(started) == len(reserved) == physical
        and reserved == completed | technical
        and not completed.intersection(technical)
        and completed == ledger_success
        and technical == ledger_technical
        and not preworkload
    )
    if not accounting_passed:
        raise RuntimeError("durable event/ledger accounting mismatch")

    restore_events = event_groups.get("tpcc_gradient_baseline_restore_finally", [])
    baseline_event = restore_events[-1] if restore_events else {}
    state_isolation_passed = bool(
        baseline_event.get("baseline_params_restored") is True
        and baseline_event.get("baseline_snapshot_restored") is True
        and summary.get("baseline_params_restored") is True
        and summary.get("baseline_snapshot_restored") is True
        and int(summary.get("accepted_count_delta", -1)) == 0
        and summary.get("incumbent_updated") is False
        and summary.get("range_updated") is False
        and summary.get("gradient_update_applied") is False
        and summary.get("state_commit_eligible") is False
    )
    if not state_isolation_passed:
        raise RuntimeError("diagnostic replay state-isolation contract failed")

    candidates = {int(item["rank"]): item for item in selection.get("top5") or []}
    observations = {int(item["rank"]): item for item in ledger.get("successful") or []}
    roles = [str(candidates[rank].get("selection_role") or "") for rank in sorted(candidates)]
    fingerprints = [
        str((candidates[rank].get("three_level_fingerprint") or {}).get("id") or "")
        for rank in sorted(candidates)
    ]
    trusted_ranks = [rank for rank in sorted(candidates) if candidates[rank].get("selection_role") == "trusted"]
    trusted_predicted = [float(candidates[rank]["predicted_score"]) for rank in trusted_ranks]
    trusted_actual = [float(observations[rank]["actual_score"]) for rank in trusted_ranks]
    trusted_spearman = _spearman(trusted_predicted, trusted_actual)
    actual_best_rank = max(observations, key=lambda rank: float(observations[rank]["actual_score"]))
    # A state-neutral evidence-acquisition batch is allowed to contain probe5
    # and therefore has no trusted rows by construction.  Keep the durable
    # accounting/isolation audit usable for that batch, while making every
    # trusted-only quality metric explicitly unavailable.  The caller must
    # route this through its acquisition gate; ``None`` can never satisfy the
    # formal trusted3 gate below.
    trusted_score_mae = (
        sum(
            abs(
                float(candidates[rank]["predicted_score"])
                - float(observations[rank]["actual_score"])
            )
            for rank in trusted_ranks
        )
        / len(trusted_ranks)
        if trusted_ranks
        else None
    )
    resource_errors: Dict[str, List[float]] = {name: [] for name in RESOURCES}
    resource_ratios: Dict[str, List[float]] = {name: [] for name in RESOURCES}
    for rank in trusted_ranks:
        for name in RESOURCES:
            predicted = float(candidates[rank]["predicted_resource"][name])
            actual = float(observations[rank]["actual_resource"][name])
            capability = {"cpu": 3.8, "io": 54.0, "memory": 6.0511731087}[name]
            resource_errors[name].append(abs(predicted - actual) / capability * 100.0)
            resource_ratios[name].append(actual / predicted if predicted else math.inf)
    resource_mae_pp = {
        name: (sum(values) / len(values) if values else None)
        for name, values in resource_errors.items()
    }
    magnitude_passed = bool(trusted_ranks) and all(
        0.1 <= ratio <= 10.0 for values in resource_ratios.values() for ratio in values
    )
    quality_checks = {
        "five_unique_noncorner_fingerprints": bool(
            len(candidates) == 5
            and len(set(fingerprints)) == 5
            and all(fingerprints)
            and not any(candidates[rank].get("boundary_corner") is True for rank in candidates)
        ),
        "trusted3_probe2": roles == ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"],
        "top5_no_technical_failure": not technical,
        "trusted_spearman_positive": trusted_spearman is not None and trusted_spearman > 0.0,
        "actual_best_in_predicted_top3": actual_best_rank <= 3,
        "trusted_resource_no_order_of_magnitude_error": magnitude_passed,
        "baseline_and_state_isolation": state_isolation_passed,
    }
    gate_passed = all(quality_checks.values())
    summary_invoked = sorted(int(value) for value in summary.get("invoked_ranks") or [])
    canonical_invoked = sorted({rank for rank, _attempt in reserved})
    return {
        "schema_version": "tpcc_v36_durable_postrun_audit_v1",
        "diagnostic_only": True,
        "formal_state_sha256_before": expected_formal_sha256,
        "formal_state_sha256_after": formal_sha,
        "input_sha256": {
            "selection": _sha256(selection_path),
            "ledger": _sha256(ledger_path),
            "events": _sha256(events_path),
            "runner_summary": _sha256(summary_path),
        },
        "accounting": {
            "passed": accounting_passed,
            "physical_calls": physical,
            "valid_replays": len(completed),
            "technical_failures": len(technical),
            "raw_technical_failures": len(raw_technical),
            "superseded_technical_bindings": sorted(superseded_technical),
            "preworkload_failures": len(preworkload),
            "started_bindings": sorted(started),
            "reserved_bindings": sorted(reserved),
            "complete_bindings": sorted(completed),
            "canonical_invoked_ranks": canonical_invoked,
            "runner_summary_invoked_ranks": summary_invoked,
            "runner_summary_invoked_ranks_mismatch": summary_invoked != canonical_invoked,
            "runner_summary_parser_defect_quarantined": summary_invoked != canonical_invoked,
        },
        "state_isolation": {
            "passed": state_isolation_passed,
            "baseline_params_restored": True,
            "baseline_snapshot_restored": True,
            "accepted_count_delta": 0,
            "incumbent_updated": False,
            "range_updated": False,
            "gradient_update_applied": False,
        },
        "quality": {
            "checks": quality_checks,
            "passed": gate_passed,
            "trusted_ranks": trusted_ranks,
            "trusted_spearman": trusted_spearman,
            "trusted_score_mae": trusted_score_mae,
            "trusted_resource_mae_pp": resource_mae_pp,
            "trusted_actual_to_predicted_resource_ratios": resource_ratios,
            "actual_best_predicted_rank": actual_best_rank,
            "probe_rows_excluded_from_spearman": True,
        },
        "formal_entry_eligible": gate_passed,
        "formal_state_mutated": False,
        "accepted_count_delta": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--formal-state", type=Path, required=True)
    parser.add_argument("--expected-formal-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(
        args.selection.resolve(),
        args.ledger.resolve(),
        args.events.resolve(),
        args.summary.resolve(),
        args.formal_state.resolve(),
        args.expected_formal_sha256,
    )
    _atomic_json(args.output.resolve(), result)
    print(json.dumps({"output": str(args.output.resolve()), "formal_entry_eligible": result["formal_entry_eligible"], "quality": result["quality"]}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
