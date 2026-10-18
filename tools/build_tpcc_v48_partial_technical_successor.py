#!/usr/bin/env python3
"""Build a fresh state-neutral Top-5 after the sealed v47 partial batch."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, Iterable, Tuple

try:
    import build_tpcc_v44_plan_regime_score_separation as base
except ModuleNotFoundError:
    from tools import build_tpcc_v44_plan_regime_score_separation as base


CONTRACT = "tpcc_v48_partial_technical_successor_v1"
SELECTION_CONTRACT = "tpcc_v48_partial_technical_successor_trusted3_probe2_v1"
STATE_MUTATION_NONE = {
    "accepted_update": False,
    "gradient_update": False,
    "incumbent_update": False,
    "range_update": False,
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"TPC-C v48 expected JSON object: {path}")
    return value


def _events(path: Path) -> list[Dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _bindings(
    rows: Iterable[Dict[str, Any]], event_name: str
) -> set[Tuple[int, int]]:
    return {
        (int(row["rank"]), int(row["attempt"]))
        for row in rows
        if row.get("event") == event_name
    }


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
    recovery_path: Path,
    recovery_sha256: str,
    formal_state_path: Path,
    formal_state_sha256: str,
    output_root: Path,
) -> Dict[str, Any]:
    pins = {
        "selection": (selection_path.resolve(), selection_sha256),
        "ledger": (ledger_path.resolve(), ledger_sha256),
        "events": (events_path.resolve(), events_sha256),
        "recovery": (recovery_path.resolve(), recovery_sha256),
        "formal_state": (formal_state_path.resolve(), formal_state_sha256),
    }
    for label, (path, expected) in pins.items():
        if not path.is_file() or _sha(path) != expected:
            raise RuntimeError(f"TPC-C v48 sealed SHA mismatch: {label}")
    output_root = output_root.resolve()
    if output_root.exists():
        raise FileExistsError("TPC-C v48 successor output root already exists")
    selection = _load(selection_path)
    ledger = _load(ledger_path)
    recovery = _load(recovery_path)
    formal = _load(formal_state_path)
    rows = _events(events_path)
    started = _bindings(rows, "tpcc_gradient_real_replay_started")
    reserved = _bindings(rows, "tpcc_gradient_real_replay_workload_invocation_reserved")
    completed = _bindings(rows, "tpcc_gradient_real_replay_complete")
    technical = _bindings(rows, "tpcc_gradient_real_replay_technical_failure")
    if (
        selection.get("selection_contract")
        != "tpcc_v47_exact_plan_regime_calibrated_trusted3_probe2_v1"
        or int(ledger.get("real_workload_invocation_count", -1)) != 5
        or [int(row["rank"]) for row in ledger.get("successful") or []]
        != [1, 2, 3, 4]
        or [int(row["rank"]) for row in ledger.get("technical_failures") or []]
        != [5]
        or started != {(rank, 1) for rank in range(1, 6)}
        or reserved != started
        or completed != {(rank, 1) for rank in range(1, 5)}
        or technical != {(5, 1)}
        or recovery.get("workload_calls_added") != 0
        or recovery.get("physical_calls") != 5
        or recovery.get("valid_replays") != 4
        or recovery.get("technical_failures") != 1
        or recovery.get("orphan_rank") != 5
        or recovery.get("batch_clean5_eligible") is not False
        or recovery.get("batch_formal_gate_eligible") is not False
        or int(formal.get("accepted_count", -1)) != 0
        or int(formal.get("formal_physical_call_count", -1)) != 0
    ):
        raise RuntimeError("TPC-C v48 partial recovery authority mismatch")

    recovery_summary = {
        "schema_version": "tpcc_v48_partial_recovery_summary_v1",
        "successful_real_replay_count": 4,
        "technical_failure_count": 1,
        "preworkload_failure_count": 0,
        "real_workload_invocation_count": 5,
        "invoked_ranks": [1, 2, 3, 4, 5],
        "baseline_params_restored": False,
        "baseline_snapshot_restored": False,
        "baseline_restore_evidence_after_crash": False,
        "accepted_count_delta": 0,
        "gradient_update_applied": False,
        "incumbent_updated": False,
        "range_updated": False,
        "state_commit_eligible": False,
        "batch_clean5_eligible": False,
        "batch_formal_gate_eligible": False,
        "formal_start_authorized": False,
        "workload_calls_added": 0,
    }
    recovery_gate = {
        "schema_version": base.PARTIAL_TECHNICAL_GATE_SCHEMA,
        "passed": False,
        "formal_start_authorized": False,
        "recovery_complete": True,
        "recovery_gate": {
            "passed": True,
            "physical_calls": 5,
            "valid_replays": 4,
            "technical_failures": 1,
            "technical_rank": 5,
            "rank5_retry_allowed": False,
            "batch_clean5_eligible": False,
            "next_action": "automatic_fresh_top5",
        },
        "quality_gate": {
            "passed": False,
            "checks": {
                "durable_accounting_clean5": False,
                "top5_no_technical_failure": False,
                "baseline_restored": False,
                "selection_algorithm_gate": True,
            },
        },
        "state_isolation": {
            "passed": True,
            "formal_state_sha256": formal_state_sha256,
            "accepted_count_delta": 0,
            "gradient_update_applied": False,
            "incumbent_updated": False,
            "range_updated": False,
        },
        "workload_calls_added": 0,
    }

    output_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output_root.name}.stage-", dir=str(output_root.parent)
    ) as temporary_name:
        stage = Path(temporary_name)
        summary_path = stage / "partial_recovery_summary.json"
        gate_path = stage / "partial_recovery_gate.json"
        _atomic_json(summary_path, recovery_summary)
        _atomic_json(gate_path, recovery_gate)
        successor = base.build(
            selection_path=selection_path.resolve(),
            selection_sha256=selection_sha256,
            ledger_path=ledger_path.resolve(),
            ledger_sha256=ledger_sha256,
            events_path=events_path.resolve(),
            events_sha256=events_sha256,
            summary_path=summary_path,
            summary_sha256=_sha(summary_path),
            gate_path=gate_path,
            gate_sha256=_sha(gate_path),
            formal_state_path=formal_state_path.resolve(),
            formal_state_sha256=formal_state_sha256,
        )
        successor.pop("v47_compatibility_contract", None)
        successor["schema_version"] = "tpcc_v48_partial_technical_successor_selection_v1"
        successor["selection_contract"] = SELECTION_CONTRACT
        successor["v48_partial_successor_contract"] = {
            "contract": CONTRACT,
            "source_partial_batch_physical_calls": 5,
            "source_partial_batch_valid_replays": 4,
            "source_partial_batch_technical_failures": 1,
            "technical_rank_excluded_from_calibration": 5,
            "source_batch_clean5_eligible": False,
            "source_batch_formal_gate_eligible": False,
            "fresh_successor_is_diagnostic_only": True,
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
        authority = successor["v43_build_contract"]["sealed_v42_authority"]
        authority["summary"] = {
            "path": str((output_root / summary_path.name).resolve()),
            "sha256": _sha(summary_path),
        }
        authority["gate"] = {
            "path": str((output_root / gate_path.name).resolve()),
            "sha256": _sha(gate_path),
        }
        top5 = successor.get("top5") or []
        roles = [row.get("selection_role") for row in top5]
        identifiers = [
            str((row.get("three_level_fingerprint") or {}).get("id") or "")
            for row in top5
        ]
        source_ids = {
            str((row.get("three_level_fingerprint") or {}).get("id") or "")
            for row in selection.get("top5") or []
        }
        if (
            roles
            != ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
            or len(set(identifiers)) != 5
            or "" in identifiers
            or bool(set(identifiers) & source_ids)
            or successor.get("state_mutation_contract") != STATE_MUTATION_NONE
            or any(row.get("boundary_corner") is not False for row in top5)
            or any(
                int(row.get("rank", -1)) == 5
                for candidate in top5
                for row in (candidate.get("source_provenance") or {}).get(
                    "trusted_observations", []
                )
                if row.get("is_current_batch") is True
            )
        ):
            raise RuntimeError("TPC-C v48 fresh 3T2P successor gate failed")
        _atomic_json(stage / "top5.json", successor)
        manifest = {
            "schema_version": "tpcc_v48_partial_successor_manifest_v1",
            "contract": CONTRACT,
            "workload_calls_added": 0,
            "formal_state_sha256": formal_state_sha256,
            "roles": roles,
            "fresh_fingerprint_count": len(set(identifiers)),
            "files": {
                "top5.json": _sha(stage / "top5.json"),
                summary_path.name: _sha(summary_path),
                gate_path.name: _sha(gate_path),
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
    for name in ("selection", "ledger", "events", "recovery", "formal-state"):
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
        recovery_path=args.recovery,
        recovery_sha256=args.recovery_sha256,
        formal_state_path=args.formal_state,
        formal_state_sha256=args.formal_state_sha256,
        output_root=args.output_root,
    )
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
