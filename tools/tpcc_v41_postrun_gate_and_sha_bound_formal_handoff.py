#!/usr/bin/env python3
"""Gate a completed v40 diagnostic and hand it to SHA-bound formal v4."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, Mapping

try:
    import audit_tpcc_v40_postrun_gate as gate_module
    import build_tpcc_unique_exact_validation_v6 as v6
    import tpcc_v40_postrun_gate_and_formal_handoff as v40_handoff
except ModuleNotFoundError:
    from tools import audit_tpcc_v40_postrun_gate as gate_module
    from tools import build_tpcc_unique_exact_validation_v6 as v6
    from tools import tpcc_v40_postrun_gate_and_formal_handoff as v40_handoff


SCHEMA = "tpcc_v41_sha_bound_formal_handoff_seal_v1"


def _require_sha(path: Path, expected: str, label: str) -> None:
    if (
        len(expected) != 64
        or any(character not in "0123456789abcdef" for character in expected)
        or not path.is_file()
        or v6._sha256(path) != expected
    ):
        raise RuntimeError(f"TPC-C v41 {label} SHA mismatch")


def _descriptor(path: Path) -> Dict[str, str]:
    return {"path": str(path.resolve()), "sha256": v6._sha256(path.resolve())}


def build_formal_gate(
    gate: Mapping[str, Any],
    formal_selection_sha256: str,
    expected_formal_state_sha256: str,
    formal_runner_sha256: str,
    diagnostic_runner_sha256: str,
    baseline_sha256: str,
) -> Dict[str, Any]:
    """Build the immutable gate consumed by formal runner v4."""
    return {
        "schema_version": "tpcc_v41_autonomous_formal_gate_v1",
        "passed": True,
        "formal_start_authorized": True,
        "selection_sha256": formal_selection_sha256,
        "expected_formal_state_sha256": expected_formal_state_sha256,
        "formal_runner_sha256": formal_runner_sha256,
        "diagnostic_runner_sha256": diagnostic_runner_sha256,
        "baseline_sha256": baseline_sha256,
        "quality_gate": copy.deepcopy(gate["quality_gate"]),
        "v39_algorithm_gate": copy.deepcopy(gate["v39_algorithm_gate"]),
        "v40_algorithm_gate": copy.deepcopy(gate["v40_algorithm_gate"]),
        "source_diagnostic_selection_sha256": gate["selection_sha256"],
        "score_span_gate": copy.deepcopy(gate["score_span_gate"]),
        "qps_role": "outer_audit_only_not_inner_score",
    }


def load_and_validate_handoff_seal(
    seal_path: Path,
    expected_formal_state_sha256: str,
) -> Dict[str, Any]:
    """Reload the persisted seal and validate every referenced file."""
    seal = v6._load(seal_path.resolve())
    files = seal.get("files") or {}
    required = {
        "diagnostic_selection",
        "diagnostic_ledger",
        "diagnostic_events",
        "diagnostic_summary",
        "formal_state",
        "postrun_gate",
        "formal_selection",
        "formal_gate",
        "formal_runner",
        "diagnostic_runner",
        "baseline",
    }
    if (
        seal.get("schema_version") != SCHEMA
        or seal.get("expected_formal_state_sha256")
        != expected_formal_state_sha256
        or set(files) != required
    ):
        raise RuntimeError("TPC-C v41 handoff seal contract mismatch")
    for label, descriptor in files.items():
        if not isinstance(descriptor, Mapping):
            raise RuntimeError(f"TPC-C v41 invalid seal descriptor: {label}")
        _require_sha(
            Path(str(descriptor.get("path") or "")).resolve(),
            str(descriptor.get("sha256") or ""),
            f"sealed {label}",
        )
    if files["formal_state"]["sha256"] != expected_formal_state_sha256:
        raise RuntimeError("TPC-C v41 seal is bound to another formal state")
    formal_gate = v6._load(Path(str(files["formal_gate"]["path"])))
    if (
        formal_gate.get("expected_formal_state_sha256")
        != expected_formal_state_sha256
        or formal_gate.get("formal_runner_sha256")
        != files["formal_runner"]["sha256"]
        or formal_gate.get("diagnostic_runner_sha256")
        != files["diagnostic_runner"]["sha256"]
        or formal_gate.get("baseline_sha256") != files["baseline"]["sha256"]
        or formal_gate.get("selection_sha256")
        != files["formal_selection"]["sha256"]
    ):
        raise RuntimeError("TPC-C v41 formal gate/seal cross-binding mismatch")
    return seal


def _validate_input_shas(args: argparse.Namespace) -> None:
    for label in (
        "selection",
        "ledger",
        "events",
        "summary",
        "formal_state",
        "formal_runner",
        "diagnostic_runner",
        "baseline",
    ):
        _require_sha(
            getattr(args, label).resolve(),
            getattr(args, f"expected_{label}_sha256"),
            label,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("selection", "ledger", "events", "summary"):
        parser.add_argument(f"--{name}", type=Path, required=True)
        parser.add_argument(f"--expected-{name}-sha256", required=True)
    parser.add_argument("--formal-state", type=Path, required=True)
    parser.add_argument("--expected-formal-state-sha256", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--formal-batch-root", type=Path, required=True)
    parser.add_argument("--formal-runner", type=Path, required=True)
    parser.add_argument("--expected-formal-runner-sha256", required=True)
    parser.add_argument("--diagnostic-runner", type=Path, required=True)
    parser.add_argument("--expected-diagnostic-runner-sha256", required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--expected-baseline-sha256", required=True)
    parser.add_argument("--armed", action="store_true")
    args = parser.parse_args()

    _validate_input_shas(args)
    output_root = args.output_root.resolve()
    if output_root.exists():
        raise FileExistsError(f"handoff output root already exists: {output_root}")
    output_root.mkdir(parents=True)
    gate = gate_module.audit_v40(
        args.selection.resolve(),
        args.ledger.resolve(),
        args.events.resolve(),
        args.summary.resolve(),
        args.formal_state.resolve(),
        args.expected_formal_state_sha256,
    )
    gate_path = output_root / "postrun_gate.json"
    gate_module.base.base.durable._atomic_json(gate_path, gate)
    if gate.get("passed") is not True:
        print(
            json.dumps({"status": "gate_failed", "gate_path": str(gate_path)}, sort_keys=True),
            flush=True,
        )
        return 3
    if (
        gate.get("selection_sha256") != args.expected_selection_sha256
        or gate.get("ledger_sha256") != args.expected_ledger_sha256
        or gate.get("events_sha256") != args.expected_events_sha256
        or gate.get("summary_sha256") != args.expected_summary_sha256
        or gate.get("formal_state_sha256")
        != args.expected_formal_state_sha256
    ):
        raise RuntimeError("TPC-C v41 audit result/input SHA cross-binding mismatch")

    formal_state = v6._load(args.formal_state.resolve())
    promoted = v40_handoff.promote_v40(
        v6._load(args.selection.resolve()),
        v6._load(args.ledger.resolve()),
        gate,
        args.selection.resolve(),
        float(formal_state["incumbent_score"]),
    )
    promoted["promoted_from_v41_sha_bound_clean5"] = {
        "contract": "tpcc_v41_clean_gate_bound_formal_promotion_v1",
        "diagnostic_selection_sha256": args.expected_selection_sha256,
        "expected_formal_state_sha256": args.expected_formal_state_sha256,
        "diagnostic_gate": copy.deepcopy(gate["v40_algorithm_gate"]),
        "threshold_changed": False,
        "historical_probe_formal_state_update_rows": 0,
        "manual_candidate_selection": False,
    }
    formal_selection_path = output_root / "formal_top5.json"
    v6.repeat_v2.import_base().atomic_json(formal_selection_path, promoted)
    formal_selection_sha = v6._sha256(formal_selection_path)
    formal_gate = build_formal_gate(
        gate,
        formal_selection_sha,
        args.expected_formal_state_sha256,
        args.expected_formal_runner_sha256,
        args.expected_diagnostic_runner_sha256,
        args.expected_baseline_sha256,
    )
    formal_gate_path = output_root / "formal_gate.json"
    gate_module.base.base.durable._atomic_json(formal_gate_path, formal_gate)

    seal = {
        "schema_version": SCHEMA,
        "armed": args.armed,
        "workload_calls_added_during_preparation": 0,
        "expected_formal_state_sha256": args.expected_formal_state_sha256,
        "manual_candidate_selection": False,
        "formal_batch_root": str(args.formal_batch_root.resolve()),
        "files": {
            "diagnostic_selection": _descriptor(args.selection),
            "diagnostic_ledger": _descriptor(args.ledger),
            "diagnostic_events": _descriptor(args.events),
            "diagnostic_summary": _descriptor(args.summary),
            "formal_state": _descriptor(args.formal_state),
            "postrun_gate": _descriptor(gate_path),
            "formal_selection": _descriptor(formal_selection_path),
            "formal_gate": _descriptor(formal_gate_path),
            "formal_runner": _descriptor(args.formal_runner),
            "diagnostic_runner": _descriptor(args.diagnostic_runner),
            "baseline": _descriptor(args.baseline),
        },
    }
    seal_path = output_root / "handoff_seal.json"
    gate_module.base.base.durable._atomic_json(seal_path, seal)
    validated_seal = load_and_validate_handoff_seal(
        seal_path, args.expected_formal_state_sha256
    )
    if not args.armed:
        print(
            json.dumps(
                {"status": "prepared_not_launched", "handoff": validated_seal},
                sort_keys=True,
            ),
            flush=True,
        )
        return 0

    load_and_validate_handoff_seal(seal_path, args.expected_formal_state_sha256)
    command = [
        sys.executable,
        "-u",
        str(args.formal_runner.resolve()),
        str(formal_selection_path),
        formal_selection_sha,
        str(formal_gate_path),
        v6._sha256(formal_gate_path),
        str(args.diagnostic_runner.resolve()),
        str(args.baseline.resolve()),
        str(args.formal_state.resolve()),
        args.expected_formal_state_sha256,
        str(args.formal_batch_root.resolve()),
    ]
    completed = subprocess.run(command, check=False, timeout=10800)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
