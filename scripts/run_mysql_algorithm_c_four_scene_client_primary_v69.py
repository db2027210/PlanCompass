#!/usr/bin/env python3
"""Carry SHA-bound TPC-H same-worker QPS normalization across outer resume."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v68 as predecessor


runtime = predecessor.runtime
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_full_resume_20260923_v159/"
    "algorithm_c_full_resume_contract.json"
)
SOURCE_STATE = ROOT / (
    "history/mysql_four_scene_20260809/"
    "c_v156_four_scene_candidate_search_20260923_tpch/run_state.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def validate_contract() -> dict[str, Any]:
    value = load(CONTRACT)
    if (
        value.get("schema_version") != "mysql_algorithm_c_full_resume_contract_v159"
        or value.get("release_version") != "v159-tpch-normalization-lineage-resume"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("tpch_same_worker_normalization_preserved") is not True
        or value.get("failed_v158_tpch_outer_credit") != 0
        or value.get("full_search_runs_until_runtime_terminal_condition") is not True
        or value.get("parent_contract", {}).get("sha256") != sha(predecessor.CONTRACT)
    ):
        raise RuntimeError("Algorithm C v159 resume contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v159 source differs: {name}: {path}")
    return value


def install_normalization_state(
    original,
    *, args, checkpoint_path: Path, checkpoint: Mapping[str, Any],
    reconstructed: Mapping[str, Any],
) -> dict[str, Any]:
    state = original(
        args=args, checkpoint_path=checkpoint_path,
        checkpoint=checkpoint, reconstructed=reconstructed,
    )
    if args.scene != "tpch_sf4_memory":
        return state
    source = load(SOURCE_STATE)
    normalization_path = Path(
        str(source.get("same_worker_qps_normalization_path") or "")
    ).resolve()
    normalization = load(normalization_path)
    embedded = dict(source.get("same_worker_qps_normalization") or {})
    factors = dict(normalization.get("worker_to_canonical_factors") or {})
    if (
        source.get("active_scenes") != ["tpch_sf4_memory"]
        or source.get("same_worker_qps_normalization_sha256") != sha(normalization_path)
        or source.get("outer_acceptance_uses_same_worker_normalized_client_qps") is not True
        or any(
            embedded.get(name) != normalization.get(name)
            for name in (
                "schema_version", "scene", "canonical_baseline_client_qps",
                "comparison_semantics", "worker_baseline_client_qps",
                "worker_to_canonical_factors",
            )
        )
        or normalization.get("schema_version") != "mysql_tpch_same_worker_client_qps_normalization_v1"
        or normalization.get("scene") != "tpch_sf4_memory"
        or set(factors) != {"tpch_w1", "tpch_w2"}
        or any(float(value) <= 0 for value in factors.values())
    ):
        raise RuntimeError("TPC-H same-worker normalization lineage differs")
    for name in (
        "same_worker_qps_normalization",
        "same_worker_qps_normalization_path",
        "same_worker_qps_normalization_sha256",
        "outer_acceptance_uses_same_worker_normalized_client_qps",
    ):
        state[name] = copy.deepcopy(source[name])
    state["tpch_resume_normalization_lineage"] = {
        "status": "PASS",
        "source_state_path": str(SOURCE_STATE),
        "source_state_sha256": sha(SOURCE_STATE),
        "normalization_path": str(normalization_path),
        "normalization_sha256": sha(normalization_path),
        "physical_recalibration_performed": False,
        "completed_outer_credit_changed": False,
    }
    return state


def main() -> int:
    predecessor.validate_contract()
    validate_contract()
    predecessor.predecessor.validate_resume_boundary()
    original = runtime._state_document

    def state_document(**kwargs):
        return install_normalization_state(original, **kwargs)

    runtime._state_document = state_document
    try:
        return int(predecessor.main())
    finally:
        runtime._state_document = original


if __name__ == "__main__":
    raise SystemExit(main())
