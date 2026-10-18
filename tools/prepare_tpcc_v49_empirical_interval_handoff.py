#!/usr/bin/env python3
"""Audit a completed v48 batch and prepare a zero-call v49 handoff."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, Mapping

try:
    import audit_tpcc_v44_postrun_gate as auditor
    import build_tpcc_v49_empirical_interval_successor as builder
except ModuleNotFoundError:
    from tools import audit_tpcc_v44_postrun_gate as auditor
    from tools import build_tpcc_v49_empirical_interval_successor as builder


CONTRACT = "tpcc_v49_zero_call_conditional_handoff_v1"


def _sha(path: Path) -> str:
    return builder.v6._sha256(path.resolve())


def _require(path: Path, expected: str, label: str) -> None:
    if not path.is_file() or _sha(path) != expected:
        raise RuntimeError(f"TPC-C v49 {label} SHA mismatch")


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def prepare(
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
        _require(path, expected, label)
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if (
        int(ledger.get("real_workload_invocation_count", -1)) != 5
        or [int(row.get("rank", -1)) for row in ledger.get("successful") or []]
        != [1, 2, 3, 4, 5]
        or ledger.get("technical_failures") not in ([], None)
        or int(summary.get("successful_real_replay_count", -1)) != 5
        or int(summary.get("technical_failure_count", -1)) != 0
        or summary.get("baseline_params_restored") is not True
        or summary.get("baseline_snapshot_restored") is not True
        or int(summary.get("accepted_count_delta", -1)) != 0
        or summary.get("gradient_update_applied") is not False
        or summary.get("incumbent_updated") is not False
        or summary.get("range_updated") is not False
        or _sha(formal_state_path) != formal_state_sha256
    ):
        raise RuntimeError("TPC-C v49 source batch is not clean5/state-neutral")
    gate = auditor.audit_v44(
        selection_path.resolve(),
        ledger_path.resolve(),
        events_path.resolve(),
        summary_path.resolve(),
        formal_state_path.resolve(),
        formal_state_sha256,
    )
    if gate.get("passed") is True:
        return {
            "status": "source_gate_passed_resume_stopped_v48_controller",
            "formal_start_authorized": True,
            "workload_calls_added": 0,
            "formal_state_mutated": False,
        }
    if gate.get("formal_start_authorized") is not False:
        raise RuntimeError("TPC-C v49 failed gate has inconsistent authorization")
    output_root = output_root.resolve()
    if output_root.exists():
        raise FileExistsError("TPC-C v49 output root already exists")
    output_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output_root.name}.stage-", dir=str(output_root.parent)
    ) as temporary_name:
        stage = Path(temporary_name)
        gate_path = stage / "source_postrun_gate.json"
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
        successor["v49_handoff_contract"] = {
            "contract": CONTRACT,
            "source_gate_passed": False,
            "source_failed_checks": sorted(
                name
                for name, passed in (gate.get("quality_gate") or {}).get("checks", {}).items()
                if passed is not True
            ),
            "sealed_authority": {
                label: {"path": str(path), "sha256": expected}
                for label, (path, expected) in pins.items()
            },
            "formal_state_mutated": False,
            "workload_calls_added": 0,
        }
        top5 = successor.get("top5") or []
        roles = [row.get("selection_role") for row in top5]
        identifiers = [
            str((row.get("three_level_fingerprint") or {}).get("id") or "")
            for row in top5
        ]
        if (
            roles != ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
            or len(set(identifiers)) != 5
            or "" in identifiers
            or successor.get("state_mutation_contract")
            != {
                "accepted_update": False,
                "gradient_update": False,
                "incumbent_update": False,
                "range_update": False,
            }
            or _sha(formal_state_path) != formal_state_sha256
        ):
            raise RuntimeError("TPC-C v49 successor contract admission failed")
        selection_out = stage / "top5.json"
        _atomic_json(selection_out, successor)
        manifest = {
            "schema_version": "tpcc_v49_empirical_interval_manifest_v1",
            "contract": CONTRACT,
            "formal_state_sha256": formal_state_sha256,
            "roles": roles,
            "fresh_fingerprint_count": 5,
            "workload_calls_added": 0,
            "formal_state_mutated": False,
            "files": {
                "top5.json": _sha(selection_out),
                "source_postrun_gate.json": _sha(gate_path),
            },
        }
        _atomic_json(stage / "manifest.json", manifest)
        os.replace(stage, output_root)
    return {
        "status": "v49_zero_call_handoff_prepared",
        "output_root": str(output_root),
        "selection": str(output_root / "top5.json"),
        "selection_sha256": _sha(output_root / "top5.json"),
        "manifest_sha256": _sha(output_root / "manifest.json"),
        "formal_state_sha256": formal_state_sha256,
        "workload_calls_added": 0,
        "formal_state_mutated": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("selection", "ledger", "events", "summary", "formal-state"):
        parser.add_argument(f"--{name}", type=Path, required=True)
        parser.add_argument(f"--{name}-sha256", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(
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
