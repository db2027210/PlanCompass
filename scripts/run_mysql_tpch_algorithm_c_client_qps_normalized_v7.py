#!/usr/bin/env python3
"""TPC-H C successor binding the timeout-safe client-QPS adapter."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_tpch_algorithm_c_client_qps_normalized_v6 as predecessor


alg = predecessor.alg
boundary = predecessor.boundary
SCENE = predecessor.SCENE
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_tpch_c_semantic_batch_20260830_v7"
    / "algorithm_c_semantic_batch_contract.json"
)


def _validate_contract() -> dict[str, Any]:
    value = alg.load(CONTRACT)
    metric_contract = predecessor.predecessor.predecessor.predecessor.metric.contract()
    normalization_schema = (
        predecessor.predecessor.predecessor.predecessor.normalization.SCHEMA_VERSION
    )
    if (
        value.get("schema_version")
        != "mysql_tpch_algorithm_c_semantic_batch_v7"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("scene") != SCENE
        or value.get("qps_metric_contract") != metric_contract
        or value.get("normalization_schema_version") != normalization_schema
        or value.get("fresh_baseline_capture_required") is not True
        or value.get("scene_scoped_relational_measurement_required") is not True
        or value.get("cached_no_move_prephysical_exclusion_required") is not True
        or value.get("pg_first_accepting_semantic_batch_preserved") is not True
        or value.get("later_semantic_batches_grant_state_credit") is not False
        or value.get("active_scene_only_terminal_status") is not True
        or value.get("predicted_and_plan_tree_expansion_after_first_reject")
        is not True
        or value.get("failed_rows_receive_acceptance_credit") is not False
        or value.get("external_algorithm_evidence_used") is not False
    ):
        raise RuntimeError("TPC-H C timeout-safe semantic-batch contract differs")
    for binding in (value.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("TPC-H C timeout-safe binding is not an object")
        path = ROOT / str(binding.get("path") or "")
        if not path.is_file() or path.is_symlink() or alg.sha(path) != binding.get("sha256"):
            raise RuntimeError(f"TPC-H C timeout-safe binding differs: {path}")
    return value


def install_runtime() -> None:
    _validate_contract()
    predecessor.CONTRACT = CONTRACT
    predecessor._validate_contract = _validate_contract
    predecessor.install_runtime()
    base_state_document = boundary._state_document

    def state_document(**kwargs: Any) -> dict[str, Any]:
        document = base_state_document(**kwargs)
        document.update({
            "controller_source_path": str(Path(__file__).resolve()),
            "controller_source_sha256": alg.sha(Path(__file__).resolve()),
            "semantic_batch_contract_path": str(CONTRACT),
            "semantic_batch_contract_sha256": alg.sha(CONTRACT),
            "timeout_safe_client_qps_adapter": True,
            "algorithm_a_parameter_or_measurement_read": False,
        })
        return document

    boundary._state_document = state_document


def main() -> int:
    install_runtime()
    return int(boundary.main())


if __name__ == "__main__":
    raise SystemExit(main())
