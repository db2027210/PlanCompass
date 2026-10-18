#!/usr/bin/env python3
"""Authorize TPC-C formal batch 05 against immutable batch 04 evidence."""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys
from typing import Any, Dict, List, Set

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tools.authorize_tpcc_formal_batch04_continuation as common
import tools.build_tpcc_round14_robust_regime_top5 as robust


ENTRY_GATE_SHA = common.ENTRY_GATE_SHA
FORMAL_STATE_SHA = "c802eb584841a098de2ccee7bc2cea8bdf23d3fb7440081c85aeb5f5e958b13c"
BATCH04_TOP5_SHA = "e6f3410f52387c5671503df8dca6425bc2ee290423a3843a132bfcd32a0f4777"
BATCH04_LEDGER_SHA = "1c597b937a89a93615415a86fbdae734435ed620da5f31046abab25d91411f54"
BATCH04_SUMMARY_SHA = "42568804aa9045aa79258934686b29e6dca6e7fb54a55f8922027c87fcd6cd78"
REQUIRED_AUDIT_FIELDS = common.REQUIRED_AUDIT_FIELDS


def _spearman_without_ties(predicted: List[float], actual: List[float]) -> float:
    if len(predicted) != len(actual) or len(predicted) < 2:
        raise RuntimeError("trusted Spearman requires at least two paired points")

    def ranks(values: List[float]) -> List[int]:
        if len(set(values)) != len(values):
            raise RuntimeError("trusted Spearman input contains ties")
        order = sorted(range(len(values)), key=lambda index: -values[index])
        result = [0] * len(values)
        for rank, index in enumerate(order, 1):
            result[index] = rank
        return result

    predicted_ranks = ranks(predicted)
    actual_ranks = ranks(actual)
    count = len(predicted)
    squared = sum(
        (predicted_ranks[index] - actual_ranks[index]) ** 2
        for index in range(count)
    )
    return 1.0 - (6.0 * squared) / (count * (count * count - 1))


def historical_evidence(selection: Dict[str, Any]) -> tuple[Set[str], Set[str]]:
    ids: Set[str] = set()
    params: Set[str] = set()
    source_batches = selection.get("source_batches")
    if not isinstance(source_batches, list) or not source_batches:
        raise RuntimeError("selection has no immutable source batch manifest")
    names = set()
    for source in source_batches:
        name = str(source.get("name") or "")
        selection_path = Path(str(source.get("selection_path") or "")).resolve()
        ledger_path = Path(str(source.get("ledger_path") or "")).resolve()
        if (
            not name
            or not selection_path.is_file()
            or not ledger_path.is_file()
            or common.sha256(selection_path) != source.get("selection_sha256")
            or common.sha256(ledger_path) != source.get("ledger_sha256")
        ):
            raise RuntimeError(f"source batch manifest mismatch: {name}")
        names.add(name)
        historical = common.load(selection_path)
        for item in historical.get("top5", []):
            ids.add(item["three_level_fingerprint"]["id"])
            params.add(robust.parameter_key(item["optimized_params"]))
    required = {
        "formal_batch01",
        "formal_batch02",
        "formal_batch03",
        "formal_batch04",
    }
    if not required.issubset(names):
        raise RuntimeError("one or more formal batches are missing from history")
    return ids, params


def main() -> int:
    if len(sys.argv) != 6:
        raise SystemExit(
            "usage: authorize_tpcc_formal_batch05_continuation.py "
            "ENTRY_GATE FORMAL_STATE BATCH04_ROOT NEW_TOP5 OUTPUT_GATE"
        )
    entry_path, state_path, batch04_root, selection_path, output_path = map(
        lambda item: Path(item).resolve(), sys.argv[1:]
    )
    batch04_top5 = batch04_root / "top5.json"
    batch04_ledger = (
        batch04_root / "strict_top5_real_replay_round14_ledger_v1.json"
    )
    batch04_summary = batch04_root / "formal_batch_summary.json"
    for path, expected, label in (
        (entry_path, ENTRY_GATE_SHA, "original entry gate"),
        (state_path, FORMAL_STATE_SHA, "formal state"),
        (batch04_top5, BATCH04_TOP5_SHA, "batch04 Top-5"),
        (batch04_ledger, BATCH04_LEDGER_SHA, "batch04 ledger"),
        (batch04_summary, BATCH04_SUMMARY_SHA, "batch04 summary"),
    ):
        if not path.is_file() or common.sha256(path) != expected:
            raise RuntimeError(f"{label} SHA or path mismatch: {path}")

    entry = common.load(entry_path)
    if (
        entry.get("passed") is not True
        or entry.get("formal_start_authorized") is not True
    ):
        raise RuntimeError("original entry gate no longer authorizes formal search")
    state = common.load(state_path)
    if (
        state.get("status") != "ready"
        or state.get("accepted_count") != 1
        or state.get("accepted_target") != 10
        or state.get("formal_physical_call_count") != 20
        or state.get("formal_valid_replay_count") != 20
        or state.get("formal_technical_failure_count") != 0
        or state.get("qps_used_for_inner_scoring") is not False
        or Path(str(state.get("last_batch_root") or "")).resolve() != batch04_root
    ):
        raise RuntimeError("formal state is not the clean post-batch04 state")
    summary = common.load(batch04_summary)
    if (
        summary.get("status") != "complete"
        or summary.get("physical_call_count") != 5
        or summary.get("valid_replay_count") != 5
        or summary.get("technical_failure_count") != 0
        or summary.get("accepted_before") != 1
        or summary.get("accepted_after") != 1
        or summary.get("batch_added_accepted") != 0
        or summary.get("baseline_restored") is not True
        or summary.get("gradient_update_applied") is not False
        or summary.get("incumbent_update_applied") is not False
        or summary.get("range_update_applied") is not False
        or summary.get("formal_state_sha256") != FORMAL_STATE_SHA
    ):
        raise RuntimeError("batch04 summary violates continuation contract")
    ledger = common.load(batch04_ledger)
    successful = ledger.get("successful") or []
    if (
        ledger.get("real_workload_invocation_count") != 5
        or ledger.get("technical_failures") != []
        or len(successful) != 5
    ):
        raise RuntimeError("batch04 ledger is not clean 5/5 evidence")
    previous_selection = common.load(batch04_top5)
    selected_by_rank = {
        int(item["rank"]): item for item in previous_selection.get("top5", [])
    }
    trusted_results = [
        item
        for item in successful
        if selected_by_rank[int(item["rank"])].get("selection_role") == "trusted"
    ]
    if len(trusted_results) != 3:
        raise RuntimeError("batch04 does not contain exactly three trusted results")
    trusted_predicted = [float(item["predicted_score"]) for item in trusted_results]
    trusted_actual = [float(item["actual_score"]) for item in trusted_results]
    trusted_spearman = _spearman_without_ties(
        trusted_predicted,
        trusted_actual,
    )
    if trusted_spearman <= 0.0:
        raise RuntimeError("batch04 trusted-only Spearman is not positive")
    actual_best_rank = int(
        max(successful, key=lambda item: float(item["actual_score"]))["rank"]
    )
    if actual_best_rank > 3:
        raise RuntimeError("batch04 actual best is outside predicted Top-3")
    for result in trusted_results:
        for resource_name in ("cpu", "io", "memory"):
            predicted = float(result["predicted_resource"][resource_name])
            actual = float(result["actual_resource"][resource_name])
            ratio = predicted / actual
            if not math.isfinite(ratio) or not 0.1 <= ratio <= 10.0:
                raise RuntimeError(
                    f"batch04 trusted {resource_name} magnitude mismatch"
                )

    selection = common.load(selection_path)
    if selection.get("selection_contract") != (
        "tpcc_robust_exact_plan_regime_trusted3_probe2_v1"
    ):
        raise RuntimeError("batch05 selection contract mismatch")
    top5 = selection.get("top5") or []
    roles = [item.get("selection_role") for item in top5]
    if roles != [
        "trusted",
        "trusted",
        "trusted",
        "uncertain_probe",
        "uncertain_probe",
    ]:
        raise RuntimeError("batch05 selection role contract mismatch")
    historical_ids, historical_params = historical_evidence(selection)
    new_ids = [item["three_level_fingerprint"]["id"] for item in top5]
    new_params = [robust.parameter_key(item["optimized_params"]) for item in top5]
    if (
        len(set(new_ids)) != 5
        or historical_ids.intersection(new_ids)
        or len(set(new_params)) != 5
        or historical_params.intersection(new_params)
    ):
        raise RuntimeError("batch05 candidates are not five distinct novel points")
    for item in top5:
        if (
            item.get("boundary_corner") is not False
            or item.get("qps_used_for_inner_scoring") is not False
            or item.get("diagnostic_only") is not True
            or item.get("incumbent_update_eligible") is not False
            or any(
                field not in item or item[field] is None
                for field in REQUIRED_AUDIT_FIELDS
            )
            or item.get("three_level_fingerprint", {}).get("valid") is not True
        ):
            raise RuntimeError(
                f"batch05 candidate contract failure: rank={item.get('rank')}"
            )
        calibration = item.get("plan_regime_calibration") or {}
        if item.get("selection_role") == "trusted":
            regime = calibration.get("regime") or {}
            if (
                item.get("trusted") is not True
                or item.get("exact_plan_regime_validated") is not True
                or calibration.get("mode")
                != "exact_regime_componentwise_median_with_interval"
                or calibration.get("trusted_eligible") is not True
                or regime.get("plan") != item.get("workload_plan_fingerprint")
            ):
                raise RuntimeError(
                    f"trusted rank lacks exact regime: {item.get('rank')}"
                )
        elif (
            item.get("trusted") is not False
            or item.get("exact_plan_regime_validated") is not False
            or calibration.get("mode") != "unknown_plan_regime_probe_only"
            or calibration.get("regime") is not None
            or calibration.get("trusted_eligible") is not False
        ):
            raise RuntimeError(
                f"probe is not strictly unknown-only: rank={item.get('rank')}"
            )

    output = {
        "schema_version": "tpcc_formal_continuation_gate_v1",
        "passed": True,
        "formal_start_authorized": True,
        "authorization_kind": "formal_continuation_after_clean_batch04",
        "original_entry_gate_path": str(entry_path),
        "original_entry_gate_sha256": ENTRY_GATE_SHA,
        "formal_state_path": str(state_path),
        "formal_state_sha256": FORMAL_STATE_SHA,
        "previous_batch_root": str(batch04_root),
        "previous_batch_summary_sha256": BATCH04_SUMMARY_SHA,
        "previous_batch_ledger_sha256": BATCH04_LEDGER_SHA,
        "accepted_count_before": 1,
        "accepted_target": 10,
        "selection_path": str(selection_path),
        "selection_sha256": common.sha256(selection_path),
        "batch04_trusted_only_spearman": trusted_spearman,
        "batch04_actual_best_predicted_rank": actual_best_rank,
        "checks": {
            "original_entry_gate_passed": True,
            "formal_state_ready_and_sha_pinned": True,
            "batch04_clean_5_of_5": True,
            "batch04_no_formal_state_mutation": True,
            "baseline_restored": True,
            "batch04_trusted_only_spearman_positive": True,
            "batch04_actual_best_in_predicted_top3": True,
            "batch04_trusted_resource_magnitude_sane": True,
            "five_new_distinct_noncorner_fingerprints": True,
            "five_new_distinct_parameter_points": True,
            "trusted3_probe2": True,
            "trusted_exact_plan_regime_only": True,
            "unknown_regime_probe_only": True,
            "candidate_audit_fields_complete": True,
            "all_source_batches_sha_pinned": True,
            "qps_outer_only": True,
            "same_scenario_real_replay_serial": True,
            "maximum_physical_calls": 5,
        },
    }
    if output_path.exists():
        raise FileExistsError(f"output already exists: {output_path}")
    common.atomic_json(output_path, output)
    print(
        json.dumps(
            {"gate": str(output_path), "sha256": common.sha256(output_path)}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
