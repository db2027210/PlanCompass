#!/usr/bin/env python3
"""Persist the v47 clean5 failed gate and its automatic fresh successor."""

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


CONTRACT = "tpcc_v48_clean5_failed_gate_automatic_successor_v1"
STATE_MUTATION_NONE = {
    "accepted_update": False,
    "gradient_update": False,
    "incumbent_update": False,
    "range_update": False,
}


def _sha(path: Path) -> str:
    return builder.v6._sha256(path.resolve())


def _load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"TPC-C v48 expected JSON object: {path}")
    return value


def _atomic_json(path: Path, value: Dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def build(
    *,
    selection_path: Path,
    selection_sha256: str,
    ledger_path: Path,
    ledger_sha256: str,
    events_path: Path,
    events_sha256: str,
    summary_path: Path,
    summary_sha256: str,
    supersession_path: Path,
    supersession_sha256: str,
    formal_state_path: Path,
    formal_state_sha256: str,
    output_root: Path,
) -> Dict[str, Any]:
    pins = {
        "selection": (selection_path.resolve(), selection_sha256),
        "ledger": (ledger_path.resolve(), ledger_sha256),
        "events": (events_path.resolve(), events_sha256),
        "summary": (summary_path.resolve(), summary_sha256),
        "supersession": (supersession_path.resolve(), supersession_sha256),
        "formal_state": (formal_state_path.resolve(), formal_state_sha256),
    }
    for label, (path, expected) in pins.items():
        if not path.is_file() or _sha(path) != expected:
            raise RuntimeError(f"TPC-C v48 sealed SHA mismatch: {label}")
    output_root = output_root.resolve()
    if output_root.exists():
        raise FileExistsError("TPC-C v48 successor output root already exists")
    source = _load(selection_path)
    ledger = _load(ledger_path)
    summary = _load(summary_path)
    supersession = _load(supersession_path)
    if (
        [int(row["rank"]) for row in ledger.get("successful") or []]
        != [1, 2, 3, 4, 5]
        or ledger.get("technical_failures") not in ([], None)
        or int(ledger.get("real_workload_invocation_count", -1)) != 5
        or int(summary.get("successful_real_replay_count", -1)) != 5
        or int(summary.get("technical_failure_count", -1)) != 0
        or summary.get("baseline_params_restored") is not True
        or summary.get("baseline_snapshot_restored") is not True
        or supersession.get("valid_replays") != 5
        or supersession.get("effective_technical_failures") != 0
        or supersession.get("workload_calls_added") != 0
    ):
        raise RuntimeError("TPC-C v48 clean5 supersession authority mismatch")
    gate = auditor.audit_v44(
        selection_path.resolve(), ledger_path.resolve(), events_path.resolve(),
        summary_path.resolve(), formal_state_path.resolve(), formal_state_sha256,
    )
    checks = (gate.get("quality_gate") or {}).get("checks") or {}
    failed = {name for name, passed in checks.items() if passed is not True}
    if (
        gate.get("passed") is not False
        or gate.get("formal_start_authorized") is not False
        or failed != {"actual_best_in_predicted_top3"}
        or (gate.get("accounting") or {}).get("physical_calls") != 5
        or (gate.get("accounting") or {}).get("valid_replays") != 5
        or (gate.get("accounting") or {}).get("technical_failures") != 0
        or (gate.get("accounting") or {}).get("raw_technical_failures") != 1
        or (gate.get("state_isolation") or {}).get("passed") is not True
    ):
        raise RuntimeError("TPC-C v48 sealed clean5 failed-gate mismatch")

    output_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output_root.name}.stage-", dir=str(output_root.parent)
    ) as temporary_name:
        stage = Path(temporary_name)
        gate_path = stage / "postrun_gate.json"
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
        successor.pop("v47_compatibility_contract", None)
        successor["schema_version"] = "tpcc_v48_clean5_failed_gate_successor_selection_v1"
        successor["v48_successor_contract"] = {
            "contract": CONTRACT,
            "source_gate_failure": "actual_best_in_predicted_top3",
            "source_actual_best_predicted_rank": 5,
            "source_effective_physical_calls": 5,
            "source_effective_valid_replays": 5,
            "source_effective_technical_failures": 0,
            "source_raw_superseded_technical_events": 1,
            "fresh_successor_diagnostic_only": True,
            "manual_candidate_selection": False,
            "threshold_changed": False,
            "state_neutral": True,
            "workload_calls_added": 0,
            "formal_state_mutated": False,
            "sealed_authority": {
                label: {"path": str(path), "sha256": expected}
                for label, (path, expected) in pins.items()
            },
        }
        successor["v43_build_contract"]["sealed_v42_authority"]["gate"] = {
            "path": str((output_root / "postrun_gate.json").resolve()),
            "sha256": _sha(gate_path),
        }
        top5 = successor.get("top5") or []
        roles = [row.get("selection_role") for row in top5]
        identifiers = [
            str((row.get("three_level_fingerprint") or {}).get("id") or "")
            for row in top5
        ]
        old_ids = {
            str((row.get("three_level_fingerprint") or {}).get("id") or "")
            for row in source.get("top5") or []
        }
        if (
            roles
            != ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
            or len(set(identifiers)) != 5
            or "" in identifiers
            or bool(set(identifiers) & old_ids)
            or successor.get("state_mutation_contract") != STATE_MUTATION_NONE
            or any(row.get("boundary_corner") is not False for row in top5)
            or _sha(formal_state_path) != formal_state_sha256
        ):
            raise RuntimeError("TPC-C v48 fresh successor admission failed")
        _atomic_json(stage / "top5.json", successor)
        manifest = {
            "schema_version": "tpcc_v48_clean5_failed_gate_successor_manifest_v1",
            "contract": CONTRACT,
            "workload_calls_added": 0,
            "formal_state_sha256": formal_state_sha256,
            "roles": roles,
            "fresh_fingerprint_count": 5,
            "files": {
                "top5.json": _sha(stage / "top5.json"),
                "postrun_gate.json": _sha(gate_path),
            },
        }
        _atomic_json(stage / "manifest.json", manifest)
        os.replace(stage, output_root)
    return {
        "output_root": str(output_root),
        "selection": str(output_root / "top5.json"),
        "selection_sha256": _sha(output_root / "top5.json"),
        "roles": roles,
        "workload_calls_added": 0,
        "formal_state_mutated": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "selection", "ledger", "events", "summary", "supersession", "formal-state"
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
        parser.add_argument(f"--{name}-sha256", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = build(
        selection_path=args.selection,
        selection_sha256=args.selection_sha256,
        ledger_path=args.ledger,
        ledger_sha256=args.ledger_sha256,
        events_path=args.events,
        events_sha256=args.events_sha256,
        summary_path=args.summary,
        summary_sha256=args.summary_sha256,
        supersession_path=args.supersession,
        supersession_sha256=args.supersession_sha256,
        formal_state_path=args.formal_state,
        formal_state_sha256=args.formal_state_sha256,
        output_root=args.output_root,
    )
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
