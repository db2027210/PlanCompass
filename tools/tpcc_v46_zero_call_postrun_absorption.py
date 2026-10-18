#!/usr/bin/env python3
"""Absorb a sealed TPC-C v45 probe5 batch without another workload call.

The v45 controller completed the state-neutral acquisition but an older
trusted-only auditor divided by zero before it could persist the acquisition
gate.  This recovery command replays only the offline audit/build transition:
it verifies all source SHAs, persists the probe-only gate, calibrates within
the exact validated plan regime, and emits a fresh 3-trusted/2-probe
diagnostic selection.  It never imports or invokes a workload runner.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Dict

try:
    import audit_tpcc_v44_postrun_gate as auditor
    import build_tpcc_v44_plan_regime_score_separation as builder
except ModuleNotFoundError:
    from tools import audit_tpcc_v44_postrun_gate as auditor
    from tools import build_tpcc_v44_plan_regime_score_separation as builder


ABSORPTION_CONTRACT = "tpcc_v46_zero_call_probe5_postrun_absorption_v1"
REQUIRED_CANDIDATE_FIELDS = {
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
}
STATE_MUTATION_NONE = {
    "accepted_update": False,
    "gradient_update": False,
    "incumbent_update": False,
    "range_update": False,
}


def _sha(path: Path) -> str:
    return builder.v6._sha256(path.resolve())


def _require_sha(path: Path, expected: str, label: str) -> None:
    if not path.is_file() or _sha(path) != expected:
        raise RuntimeError(f"TPC-C v46 sealed SHA mismatch: {label}")


def _load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"TPC-C v46 expected JSON object: {path}")
    return value


def _atomic_json(path: Path, value: Dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _validate_successor(
    selection: Dict[str, Any],
    source_selection: Dict[str, Any],
    *,
    final_gate_path: Path,
    gate_sha256: str,
) -> None:
    top5 = selection.get("top5") or []
    roles = [candidate.get("selection_role") for candidate in top5]
    identifiers = [
        str((candidate.get("three_level_fingerprint") or {}).get("id") or "")
        for candidate in top5
    ]
    historical_ids = builder.v37._historical_fingerprint_ids(
        selection.get("source_batches") or []
    )
    trusted = top5[:3]
    probes = top5[3:]
    gate_pin = (
        (selection.get("v43_build_contract") or {})
        .get("sealed_v42_authority", {})
        .get("gate", {})
    )
    target_plan = str(
        (source_selection.get("v44_build_contract") or {}).get(
            "target_plan_regime"
        )
        or ""
    )
    if (
        roles != ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
        or len(identifiers) != 5
        or "" in identifiers
        or len(set(identifiers)) != 5
        or bool(set(identifiers) & historical_ids)
        or selection.get("state_mutation_contract") != STATE_MUTATION_NONE
        or any(REQUIRED_CANDIDATE_FIELDS - set(candidate) for candidate in top5)
        or any(candidate.get("boundary_corner") is not False for candidate in top5)
        or gate_pin.get("path") != str(final_gate_path.resolve())
        or gate_pin.get("sha256") != gate_sha256
    ):
        raise RuntimeError("TPC-C v46 fresh 3T2P admission failed")
    if (
        not target_plan.startswith("ratio-plan-sha256:")
        or any(
            candidate.get("workload_plan_fingerprint") != target_plan
            or candidate.get("trusted") is not True
            or candidate.get("exact_plan_regime_validated") is not True
            or candidate.get("trusted_eligible_v43") is not True
            or (candidate.get("plan_regime_calibration") or {}).get(
                "trusted_eligible"
            )
            is not True
            for candidate in trusted
        )
    ):
        raise RuntimeError("TPC-C v46 trusted exact-plan calibration gate failed")
    if any(
        candidate.get("trusted") is not False
        or candidate.get("incumbent_update_eligible") is not False
        or candidate.get("used_for_gradient") is not False
        or candidate.get("formal_improvement_eligible") is not False
        for candidate in probes
    ):
        raise RuntimeError("TPC-C v46 probe isolation gate failed")
    for candidate in top5:
        provenance = candidate.get("source_provenance") or {}
        promoted_rows = [
            row
            for row in provenance.get("trusted_observations") or []
            if row.get("calibration_trusted_eligible") is True
        ]
        if int(
            provenance.get(
                "v44_acquisition_probe_rows_used_to_establish_trust", -1
            )
        ) != len(promoted_rows):
            raise RuntimeError("TPC-C v46 promoted evidence accounting mismatch")


def absorb(
    *,
    selection_path: Path,
    selection_sha256: str,
    ledger_path: Path,
    ledger_sha256: str,
    events_path: Path,
    events_sha256: str,
    summary_path: Path,
    summary_sha256: str,
    formal_state_path: Path,
    formal_state_sha256: str,
    output_root: Path,
) -> Dict[str, Any]:
    pins = {
        "selection": (selection_path.resolve(), selection_sha256),
        "ledger": (ledger_path.resolve(), ledger_sha256),
        "events": (events_path.resolve(), events_sha256),
        "summary": (summary_path.resolve(), summary_sha256),
        "formal_state": (formal_state_path.resolve(), formal_state_sha256),
    }
    for label, (path, expected) in pins.items():
        _require_sha(path, expected, label)
    output_root = output_root.resolve()
    if output_root.exists():
        raise FileExistsError("TPC-C v46 output root already exists")
    source = _load(selection_path)
    source_contract = source.get("v44_build_contract") or {}
    if (
        source_contract.get("mode") != "score_separation_acquisition"
        or [row.get("selection_role") for row in source.get("top5") or []]
        != ["uncertain_probe"] * 5
        or source.get("state_mutation_contract") != STATE_MUTATION_NONE
    ):
        raise RuntimeError("TPC-C v46 source is not a state-neutral probe5 batch")

    gate = auditor.audit_v44(
        selection_path.resolve(),
        ledger_path.resolve(),
        events_path.resolve(),
        summary_path.resolve(),
        formal_state_path.resolve(),
        formal_state_sha256,
    )
    acquisition = gate.get("acquisition_gate") or {}
    if (
        gate.get("schema_version") != builder.ACQUISITION_GATE_SCHEMA
        or gate.get("passed") is not False
        or gate.get("formal_start_authorized") is not False
        or gate.get("acquisition_complete") is not True
        or acquisition.get("passed") is not True
        or not all(value is True for value in (acquisition.get("checks") or {}).values())
    ):
        raise RuntimeError("TPC-C v46 probe5 acquisition audit failed closed")

    # Build against a temporary, SHA-addressed copy of the gate because the
    # v44 builder requires every authority input to exist and be sealed.
    output_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output_root.name}.stage-", dir=str(output_root.parent)
    ) as temporary_name:
        stage = Path(temporary_name)
        gate_path = stage / "probe5_postrun_gate.json"
        _atomic_json(gate_path, gate)
        successor = builder.build(
            selection_path=selection_path.resolve(),
            selection_sha256=selection_sha256,
            ledger_path=ledger_path.resolve(),
            ledger_sha256=ledger_sha256,
            events_path=events_path.resolve(),
            events_sha256=events_sha256,
            summary_path=summary_path.resolve(),
            summary_sha256=summary_sha256,
            gate_path=gate_path,
            gate_sha256=_sha(gate_path),
            formal_state_path=formal_state_path.resolve(),
            formal_state_sha256=formal_state_sha256,
        )
        gate_sha256 = _sha(gate_path)
        # The builder seals the path it read.  The entire staging directory is
        # renamed atomically below, so rebase only that path to its final
        # location while retaining the exact content SHA.  Without this step
        # the successor would contain a valid SHA bound to a vanished staging
        # pathname.
        successor["v43_build_contract"]["sealed_v42_authority"]["gate"] = {
            "path": str((output_root / "probe5_postrun_gate.json").resolve()),
            "sha256": gate_sha256,
        }
        _validate_successor(
            successor,
            source,
            final_gate_path=output_root / "probe5_postrun_gate.json",
            gate_sha256=gate_sha256,
        )
        if _sha(formal_state_path) != formal_state_sha256:
            raise RuntimeError("TPC-C v46 absorption mutated formal state")

        absorption = {
            "schema_version": ABSORPTION_CONTRACT,
            "contract": ABSORPTION_CONTRACT,
            "source_authority": {
                label: {"path": str(path), "sha256": expected}
                for label, (path, expected) in pins.items()
            },
            "source_physical_calls": 5,
            "source_valid_replays": 5,
            "source_technical_failures": 0,
            "baseline_restored": True,
            "workload_calls_added": 0,
            "accepted_count_delta": 0,
            "formal_state_mutated": False,
            "probe5_rows_formal_or_gradient_eligible": False,
            "exact_plan_regime_calibration_only": True,
            "successor_roles": [
                "trusted",
                "trusted",
                "trusted",
                "uncertain_probe",
                "uncertain_probe",
            ],
            "formal_start_authorized": False,
            "next_action": "run_successor_3t2p_diagnostic_gate",
        }
        successor["v46_zero_call_absorption"] = copy.deepcopy(absorption)
        successor_path = stage / "next_top5_trusted3_probe2.json"
        _atomic_json(successor_path, successor)
        _atomic_json(stage / "zero_call_absorption.json", absorption)
        manifest = {
            "schema_version": "tpcc_v46_zero_call_absorption_manifest_v1",
            "contract": ABSORPTION_CONTRACT,
            "workload_calls_added": 0,
            "formal_state_sha256": formal_state_sha256,
            "files": {
                "probe5_postrun_gate.json": _sha(gate_path),
                "next_top5_trusted3_probe2.json": _sha(successor_path),
                "zero_call_absorption.json": _sha(
                    stage / "zero_call_absorption.json"
                ),
            },
        }
        _atomic_json(stage / "manifest.json", manifest)
        os.replace(stage, output_root)

    return {
        "output_root": str(output_root),
        "manifest": str(output_root / "manifest.json"),
        "next_selection": str(output_root / "next_top5_trusted3_probe2.json"),
        "workload_calls_added": 0,
        "formal_state_mutated": False,
        "roles": ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("selection", "ledger", "events", "summary", "formal-state"):
        parser.add_argument(f"--{name}", type=Path, required=True)
        parser.add_argument(f"--{name}-sha256", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = absorb(
        selection_path=args.selection,
        selection_sha256=args.selection_sha256,
        ledger_path=args.ledger,
        ledger_sha256=args.ledger_sha256,
        events_path=args.events,
        events_sha256=args.events_sha256,
        summary_path=args.summary,
        summary_sha256=args.summary_sha256,
        formal_state_path=args.formal_state,
        formal_state_sha256=args.formal_state_sha256,
        output_root=args.output_root,
    )
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
