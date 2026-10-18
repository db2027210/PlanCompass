#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Dict


ATTEMPT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726"
)
ROOT = (
    ATTEMPT
    / "gradient_debug_batch06_minimal_unknown_probe2_v2_20260729T2340"
)
SELECTION = ROOT / "top2.json"
LEDGER = ROOT / "strict_top2_real_replay_acquisition_ledger_v1.json"
SUMMARY = ROOT / "top2_real_replay_acquisition_v1/summary.json"
FORMAL_STATE = ATTEMPT / "formal10_tpcc_state.json"
OUTPUT = Path("/tmp/tpcc_batch06_minimal_probe2_exact_regime_calibration_v1.json")

SELECTION_SHA = "90628961dc0227ff52dcffd6597cfe06e30a2936c82c2a503ce9dbe2f8b85cdb"
LEDGER_SHA = "2def171537a279098190fd706d9a20f0d4a1af64774c374bfc5c3c4cadd2b323"
SUMMARY_SHA = "4af0bf32a8d612c38c050e709398f04d658d6c2356d1182f522276d9c382e5f1"
FORMAL_STATE_SHA = "4a273208d8503f2d18a108293bf5714043418bdf72766bb44e8e10f16e5f9d5f"
RESOURCES = ("cpu", "io", "memory")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
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


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"output already exists: {OUTPUT}")
    for path, expected, label in (
        (SELECTION, SELECTION_SHA, "selection"),
        (LEDGER, LEDGER_SHA, "ledger"),
        (SUMMARY, SUMMARY_SHA, "summary"),
        (FORMAL_STATE, FORMAL_STATE_SHA, "formal state"),
    ):
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"{label} SHA mismatch: {path}")

    selection = load(SELECTION)
    ledger = load(LEDGER)
    summary = load(SUMMARY)
    state = load(FORMAL_STATE)
    mutation_contract = {
        "accepted_update": False,
        "incumbent_update": False,
        "range_update": False,
        "gradient_update": False,
    }
    if (
        selection.get("selection_contract")
        != "tpcc_minimal_unknown_acquisition_probe2_v1"
        or selection.get("maximum_real_workload_invocations") != 2
        or selection.get("state_mutation_contract") != mutation_contract
        or ledger.get("contract")
        != "tpcc_minimal_unknown_acquisition_probe2_strict_serial_replay_v1"
        or ledger.get("real_workload_invocation_count") != 2
        or ledger.get("technical_failures") != []
        or len(ledger.get("successful") or []) != 2
        or summary.get("fatal_error") is not None
        or summary.get("round_complete") is not True
        or summary.get("full_top5_complete") is not True
        or summary.get("successful_real_replay_count") != 2
        or summary.get("technical_failure_count") != 0
        or summary.get("baseline_params_restored") is not True
        or summary.get("baseline_snapshot_restored") is not True
        or summary.get("accepted_count_delta") != 0
        or summary.get("incumbent_updated") is not False
        or summary.get("range_updated") is not False
        or summary.get("gradient_update_applied") is not False
        or summary.get("state_commit_eligible") is not False
        or state.get("accepted_count") != 1
        or not math.isclose(
            float(state.get("incumbent_score")),
            4.031902957998083,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    ):
        raise RuntimeError("minimal probe2 replay or formal state contract mismatch")

    selected = {
        int(item["rank"]): item for item in selection.get("top5") or []
    }
    successful = {
        int(item["rank"]): item for item in ledger.get("successful") or []
    }
    if set(selected) != {1, 2} or set(successful) != {1, 2}:
        raise RuntimeError("rank coverage mismatch")

    regimes: Dict[str, Dict[str, Any]] = {}
    for rank in (1, 2):
        candidate = selected[rank]
        result = successful[rank]
        protocol = result.get("measurement_protocol") or {}
        summary_path = Path(str(protocol.get("validated_summary_path") or ""))
        plan = str(result.get("workload_plan_fingerprint") or "")
        if (
            candidate.get("selection_role") != "uncertain_probe"
            or candidate.get("trusted") is not False
            or candidate.get("diagnostic_only") is not True
            or candidate.get("incumbent_update_eligible") is not False
            or result.get("selection_role") != "uncertain_probe"
            or result.get("trusted") is not False
            or result.get("diagnostic_only") is not True
            or result.get("eligible_for_incumbent_or_accepted") is not False
            or protocol.get("stable") is not True
            or protocol.get("correctness_passed") is not True
            or protocol.get("technical_failure") is not False
            or plan != candidate.get("workload_plan_fingerprint")
            or result.get("three_level_fingerprint", {}).get("id")
            != candidate.get("three_level_fingerprint", {}).get("id")
            or not summary_path.is_file()
            or sha256(summary_path) != protocol.get("validated_summary_sha256")
            or plan in regimes
        ):
            raise RuntimeError(f"rank {rank} evidence mismatch")
        raw = candidate.get("uncalibrated_predicted_resource") or {}
        actual = result.get("actual_resource") or {}
        scalar = {
            name: float(actual[name]) / float(raw[name]) for name in RESOURCES
        }
        if any(
            not math.isfinite(value) or not 0.1 <= value <= 10.0
            for value in scalar.values()
        ):
            raise RuntimeError(f"rank {rank} calibration magnitude failure")
        regimes[plan] = {
            "plan": plan,
            "observation_count": 1,
            "trusted_eligible": True,
            "single_observation_relative_half_width": 0.20,
            "resource_scalar": scalar,
            "uncalibrated_predicted_resource": {
                name: float(raw[name]) for name in RESOURCES
            },
            "actual_resource": {
                name: float(actual[name]) for name in RESOURCES
            },
            "actual_score": float(result["actual_score"]),
            "diagnostic_rank": rank,
            "three_level_fingerprint": result["three_level_fingerprint"]["id"],
            "summary_path": str(summary_path),
            "summary_sha256": sha256(summary_path),
        }

    output = {
        "schema_version": "tpcc_exact_plan_regime_calibration_v1",
        "audit_passed": True,
        "zero_formal_state_mutation_verified": True,
        "physical_calls": 2,
        "valid_replays": 2,
        "technical_failures": 0,
        "baseline_restored": True,
        "accepted_before": 1,
        "accepted_after": 1,
        "selection_path": str(SELECTION),
        "selection_sha256": SELECTION_SHA,
        "ledger_path": str(LEDGER),
        "ledger_sha256": LEDGER_SHA,
        "summary_path": str(SUMMARY),
        "summary_sha256": SUMMARY_SHA,
        "formal_state_path": str(FORMAL_STATE),
        "formal_state_sha256": FORMAL_STATE_SHA,
        "calibration_scope": "exact_workload_plan_fingerprint_only",
        "unknown_regime_policy": "probe_only_until_clean_real_replay",
        "regimes": regimes,
    }
    atomic_json(OUTPUT, output)
    print(
        json.dumps(
            {
                "status": "ok",
                "output": str(OUTPUT),
                "sha256": sha256(OUTPUT),
                "regimes": regimes,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
