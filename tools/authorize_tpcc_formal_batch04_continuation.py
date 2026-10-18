#!/usr/bin/env python3
"""Authorize TPC-C formal batch 04 against immutable batch 03 evidence."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Dict, Set


ENTRY_GATE_SHA = "8718dc3c07ca1f33af63908104caa04cc262acb9c568f74e95f6bea33220b8b3"
FORMAL_STATE_SHA = "633a15d5cf5adfdb94f598fbd3f29720d71023be02079cc0ffbd16fa62dcbdaf"
BATCH03_TOP5_SHA = "e45afa62378ae31382f7805238e2aedac9599abe44049ee5eb6882a5e207d46f"
BATCH03_LEDGER_SHA = "b91c57dafbb602f12458583fc72ff3f1a7b0a103232922f011747cbd4a64a7ff"
BATCH03_SUMMARY_SHA = "a4a5da745611d6732e36dce9e19a39958443b4c536ca1dfab072e67f24d5d5d2"
REQUIRED_AUDIT_FIELDS = (
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


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected object: {path}")
    return value


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def historical_fingerprints(selection: Dict[str, Any]) -> Set[str]:
    ids: Set[str] = set()
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
            or sha256(selection_path) != source.get("selection_sha256")
            or sha256(ledger_path) != source.get("ledger_sha256")
        ):
            raise RuntimeError(f"source batch manifest mismatch: {name}")
        names.add(name)
        historical = load(selection_path)
        ids.update(
            item["three_level_fingerprint"]["id"]
            for item in historical.get("top5", [])
        )
    if not {"formal_batch01", "formal_batch02", "formal_batch03"}.issubset(names):
        raise RuntimeError("one or more formal batches are missing from history sources")
    return ids


def main() -> int:
    if len(sys.argv) != 6:
        raise SystemExit(
            "usage: authorize_tpcc_formal_batch04_continuation.py "
            "ENTRY_GATE FORMAL_STATE BATCH03_ROOT NEW_TOP5 OUTPUT_GATE"
        )
    entry_path, state_path, batch03_root, selection_path, output_path = map(
        lambda item: Path(item).resolve(), sys.argv[1:]
    )
    batch03_top5 = batch03_root / "top5.json"
    batch03_ledger = (
        batch03_root / "strict_top5_real_replay_round14_ledger_v1.json"
    )
    batch03_summary = batch03_root / "formal_batch_summary.json"
    pinned = (
        (entry_path, ENTRY_GATE_SHA, "original entry gate"),
        (state_path, FORMAL_STATE_SHA, "formal state"),
        (batch03_top5, BATCH03_TOP5_SHA, "batch03 Top-5"),
        (batch03_ledger, BATCH03_LEDGER_SHA, "batch03 ledger"),
        (batch03_summary, BATCH03_SUMMARY_SHA, "batch03 summary"),
    )
    for path, expected, label in pinned:
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"{label} SHA or path mismatch: {path}")

    entry = load(entry_path)
    if (
        entry.get("passed") is not True
        or entry.get("formal_start_authorized") is not True
    ):
        raise RuntimeError("original entry gate no longer authorizes formal search")
    state = load(state_path)
    if (
        state.get("status") != "ready"
        or state.get("accepted_count") != 1
        or state.get("accepted_target") != 10
        or state.get("formal_physical_call_count") != 15
        or state.get("formal_valid_replay_count") != 15
        or state.get("formal_technical_failure_count") != 0
        or state.get("qps_used_for_inner_scoring") is not False
        or Path(str(state.get("last_batch_root") or "")).resolve() != batch03_root
    ):
        raise RuntimeError("formal state is not the clean post-batch03 state")
    summary = load(batch03_summary)
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
        raise RuntimeError("batch03 summary violates the clean continuation contract")
    ledger = load(batch03_ledger)
    if (
        ledger.get("real_workload_invocation_count") != 5
        or ledger.get("technical_failures") != []
        or len(ledger.get("successful", [])) != 5
    ):
        raise RuntimeError("batch03 ledger is not clean 5/5 evidence")

    selection = load(selection_path)
    if selection.get("selection_contract") != (
        "tpcc_robust_exact_plan_regime_trusted3_probe2_v1"
    ):
        raise RuntimeError("batch04 selection contract mismatch")
    roles = [item.get("selection_role") for item in selection.get("top5", [])]
    if roles != [
        "trusted",
        "trusted",
        "trusted",
        "uncertain_probe",
        "uncertain_probe",
    ]:
        raise RuntimeError("batch04 selection role contract mismatch")
    historical_ids = historical_fingerprints(selection)
    new_ids = [
        item["three_level_fingerprint"]["id"] for item in selection["top5"]
    ]
    if len(set(new_ids)) != 5 or historical_ids.intersection(new_ids):
        raise RuntimeError("batch04 fingerprints are not five distinct novel IDs")
    for item in selection["top5"]:
        if (
            item.get("boundary_corner") is not False
            or item.get("qps_used_for_inner_scoring") is not False
            or item.get("diagnostic_only") is not True
            or item.get("incumbent_update_eligible") is not False
            or any(field not in item or item[field] is None for field in REQUIRED_AUDIT_FIELDS)
            or item.get("three_level_fingerprint", {}).get("valid") is not True
        ):
            raise RuntimeError(f"batch04 candidate contract failure: rank={item.get('rank')}")
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
                    f"trusted rank lacks exact validated regime: {item.get('rank')}"
                )
        elif item.get("trusted") is not False:
            raise RuntimeError(f"probe is incorrectly trusted: rank={item.get('rank')}")

    output = {
        "schema_version": "tpcc_formal_continuation_gate_v1",
        "passed": True,
        "formal_start_authorized": True,
        "authorization_kind": "formal_continuation_after_clean_batch03",
        "original_entry_gate_path": str(entry_path),
        "original_entry_gate_sha256": ENTRY_GATE_SHA,
        "formal_state_path": str(state_path),
        "formal_state_sha256": FORMAL_STATE_SHA,
        "previous_batch_root": str(batch03_root),
        "previous_batch_summary_sha256": BATCH03_SUMMARY_SHA,
        "previous_batch_ledger_sha256": BATCH03_LEDGER_SHA,
        "accepted_count_before": 1,
        "accepted_target": 10,
        "selection_path": str(selection_path),
        "selection_sha256": sha256(selection_path),
        "checks": {
            "original_entry_gate_passed": True,
            "formal_state_ready": True,
            "batch03_clean_5_of_5": True,
            "batch03_no_formal_state_mutation": True,
            "baseline_restored": True,
            "five_new_distinct_noncorner_fingerprints": True,
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
    atomic_json(output_path, output)
    print(json.dumps({"gate": str(output_path), "sha256": sha256(output_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
