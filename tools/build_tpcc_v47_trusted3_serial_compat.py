#!/usr/bin/env python3
"""Build a zero-call v47 routing-compatible copy of the sealed v46 3T2P."""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
from typing import Any, Dict

try:
    import build_tpcc_v44_plan_regime_score_separation as v44
except ModuleNotFoundError:
    from tools import build_tpcc_v44_plan_regime_score_separation as v44


SELECTION_CONTRACT = "tpcc_v47_exact_plan_regime_calibrated_trusted3_probe2_v1"
COMPATIBILITY_CONTRACT = "tpcc_v47_trusted3_serial_routing_compatibility_v1"
SOURCE_SELECTION_CONTRACT = "tpcc_v44_plan_regime_score_separation_probe5_v1"
STATE_MUTATION_NONE = {
    "accepted_update": False,
    "gradient_update": False,
    "incumbent_update": False,
    "range_update": False,
}


def _sha(path: Path) -> str:
    return v44.v6._sha256(path.resolve())


def _load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"TPC-C v47 expected JSON object: {path}")
    return value


def _require(path: Path, expected: str, label: str) -> None:
    if not path.is_file() or _sha(path) != expected:
        raise RuntimeError(f"TPC-C v47 sealed SHA mismatch: {label}")


def _atomic_json(path: Path, value: Dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def build(
    *,
    source_selection_path: Path,
    source_selection_sha256: str,
    source_manifest_path: Path,
    source_manifest_sha256: str,
    formal_state_path: Path,
    formal_state_sha256: str,
) -> Dict[str, Any]:
    pins = {
        "v46_selection": (source_selection_path.resolve(), source_selection_sha256),
        "v46_manifest": (source_manifest_path.resolve(), source_manifest_sha256),
        "formal_state": (formal_state_path.resolve(), formal_state_sha256),
    }
    for label, (path, expected) in pins.items():
        _require(path, expected, label)
    source = _load(source_selection_path)
    manifest = _load(source_manifest_path)
    top5 = source.get("top5") or []
    if (
        source.get("selection_contract") != SOURCE_SELECTION_CONTRACT
        or (source.get("v44_build_contract") or {}).get("mode")
        != "trusted3_probe2"
        or [row.get("selection_role") for row in top5]
        != ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
        or source.get("state_mutation_contract") != STATE_MUTATION_NONE
        or manifest.get("workload_calls_added") != 0
        or manifest.get("formal_state_sha256") != formal_state_sha256
    ):
        raise RuntimeError("TPC-C v47 source v46 selection contract mismatch")
    result = copy.deepcopy(source)
    result["schema_version"] = "tpcc_v47_trusted3_serial_compat_selection_v1"
    result["selection_contract"] = SELECTION_CONTRACT
    result["v47_compatibility_contract"] = {
        "contract": COMPATIBILITY_CONTRACT,
        "effective_selection_contract": SELECTION_CONTRACT,
        "source_selection_contract": SOURCE_SELECTION_CONTRACT,
        "routing_defect": (
            "trusted3_successor_inherited_probe5_selection_contract"
        ),
        "candidate_payload_changed": False,
        "candidate_roles_changed": False,
        "candidate_scores_changed": False,
        "candidate_fingerprints_changed": False,
        "threshold_changed": False,
        "manual_candidate_selection": False,
        "workload_calls_added": 0,
        "formal_state_mutated": False,
        "state_neutral": True,
        "sealed_authority": {
            label: {"path": str(path), "sha256": expected}
            for label, (path, expected) in pins.items()
        },
    }
    if result["top5"] != source["top5"]:
        raise RuntimeError("TPC-C v47 compatibility build changed candidate payload")
    if _sha(formal_state_path) != formal_state_sha256:
        raise RuntimeError("TPC-C v47 compatibility build mutated formal state")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-selection", type=Path, required=True)
    parser.add_argument("--source-selection-sha256", required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--source-manifest-sha256", required=True)
    parser.add_argument("--formal-state", type=Path, required=True)
    parser.add_argument("--formal-state-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError("TPC-C v47 output already exists")
    result = build(
        source_selection_path=args.source_selection,
        source_selection_sha256=args.source_selection_sha256,
        source_manifest_path=args.source_manifest,
        source_manifest_sha256=args.source_manifest_sha256,
        formal_state_path=args.formal_state,
        formal_state_sha256=args.formal_state_sha256,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json(output, result)
    print(
        json.dumps(
            {
                "output": str(output),
                "sha256": _sha(output),
                "workload_calls_added": 0,
                "candidate_payload_changed": False,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
