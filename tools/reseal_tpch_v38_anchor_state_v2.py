#!/usr/bin/env python3
"""Zero-call reseal of a clean TPC-H v38 two-observation anchor."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any, Dict


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def verify(path: Path, expected: str, label: str) -> None:
    if not path.is_file() or sha256(path) != expected:
        raise RuntimeError(f"{label} SHA256 mismatch: {path}")


def import_builder(path: Path) -> Any:
    specification = importlib.util.spec_from_file_location(
        "tpch_v38_anchor_builder_v2", path
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot import anchor builder: {path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-formal-state", type=Path, required=True)
    parser.add_argument("--source-formal-state-sha256", required=True)
    parser.add_argument("--diagnostic-state", type=Path, required=True)
    parser.add_argument("--diagnostic-state-sha256", required=True)
    parser.add_argument("--raw-anchor-state", type=Path, required=True)
    parser.add_argument("--raw-anchor-state-sha256", required=True)
    parser.add_argument("--anchor-builder", type=Path, required=True)
    parser.add_argument("--anchor-builder-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    for path, expected, label in (
        (
            args.source_formal_state,
            args.source_formal_state_sha256,
            "source formal state",
        ),
        (args.diagnostic_state, args.diagnostic_state_sha256, "diagnostic state"),
        (args.raw_anchor_state, args.raw_anchor_state_sha256, "raw anchor state"),
        (args.anchor_builder, args.anchor_builder_sha256, "anchor builder"),
    ):
        verify(path, expected, label)
    source = load(args.source_formal_state)
    diagnostic = load(args.diagnostic_state)
    raw_anchor = load(args.raw_anchor_state)
    audit = raw_anchor.get("current_epoch_anchor_revalidation") or {}
    observations = diagnostic.get("observations") or []
    if not (
        diagnostic.get("status") == "complete"
        and diagnostic.get("diagnostic_only") is True
        and int(diagnostic.get("physical_call_count", -1)) == 2
        and int(diagnostic.get("valid_replay_count", -1)) == 2
        and int(diagnostic.get("technical_failure_count", -1)) == 0
        and int(diagnostic.get("accepted_count", -1)) == 0
        and diagnostic.get("baseline_restored") is True
        and diagnostic.get("formal_state_mutation_allowed") is False
        and diagnostic.get("accepted_update_allowed") is False
        and diagnostic.get("incumbent_update_allowed") is False
        and diagnostic.get("range_update_allowed") is False
        and diagnostic.get("gradient_update_allowed") is False
        and diagnostic.get("source_formal_state_mutated") is False
        and diagnostic.get("accepted_counter_mutated") is False
        and diagnostic.get("current_anchor_state_sha256")
        == args.raw_anchor_state_sha256
        and len(observations) == 2
        and all(
            row.get("valid_replay") is True
            and row.get("technical_failure") is False
            and row.get("baseline_restored_after_observation") is True
            for row in observations
        )
        and audit.get("contract") == "tpch_current_epoch_anchor_revalidation_v1"
        and audit.get("state_neutral") is True
        and audit.get("observations") == observations
        and audit.get("source_formal_state_sha256")
        == args.source_formal_state_sha256
    ):
        raise RuntimeError("raw anchor evidence is not clean and exactly bound")
    builder = import_builder(args.anchor_builder)
    corrected = builder.build_anchor_state(
        source,
        audit["candidate_params"],
        observations,
        {
            key: value
            for key, value in audit.items()
            if key
            in {
                "anchor_mode",
                "source_formal_state",
                "source_formal_state_sha256",
                "runner_module",
                "runner_module_sha256",
                "workload",
                "workload_sha256",
                "workload_driver",
                "workload_driver_sha256",
                "runtime_dependencies",
                "config",
                "config_sha256",
            }
        },
        float(audit["maximum_score_span_allowed"]),
        float(audit["maximum_resource_factor_allowed"]),
    )
    corrected_audit = corrected["current_epoch_anchor_revalidation"]
    corrected_audit.update(
        {
            "zero_call_reseal_contract": "tpch_v38_anchor_zero_call_reseal_v2",
            "raw_anchor_state": str(args.raw_anchor_state),
            "raw_anchor_state_sha256": args.raw_anchor_state_sha256,
            "diagnostic_state": str(args.diagnostic_state),
            "diagnostic_state_sha256": args.diagnostic_state_sha256,
            "new_physical_calls": 0,
        }
    )
    builder.atomic_json(args.output, corrected)
    print(
        json.dumps(
            {
                "status": "ready",
                "output": str(args.output),
                "output_sha256": sha256(args.output),
                "new_physical_calls": 0,
                "incumbent_score": corrected["incumbent_score"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
