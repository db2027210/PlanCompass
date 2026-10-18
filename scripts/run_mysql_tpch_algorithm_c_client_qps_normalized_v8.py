#!/usr/bin/env python3
"""TPC-H C successor admitting valid baseline rows to normalization only."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping, MutableMapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_tpch_client_qps_measurement_v2 as metric
from scripts import mysql_tpch_worker_qps_normalization_v1 as normalization
from scripts import run_mysql_four_scene_algorithm_b_scene_independent_measurement_v5 as scene_measurement
from scripts import run_mysql_tpch_algorithm_c_client_qps_normalized_v7 as predecessor


alg = predecessor.alg
boundary = predecessor.boundary
SCENE = predecessor.SCENE
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_tpch_c_semantic_batch_20260830_v8"
    / "algorithm_c_semantic_batch_contract.json"
)


def _validate_contract() -> dict[str, Any]:
    value = alg.load(CONTRACT)
    if (
        value.get("schema_version") != "mysql_tpch_algorithm_c_semantic_batch_v8"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("scene") != SCENE
        or value.get("qps_metric_contract") != metric.contract()
        or value.get("normalization_schema_version") != normalization.SCHEMA_VERSION
        or value.get("valid_baseline_rows_admitted_to_normalization_only") is not True
        or value.get("baseline_rows_receive_outer_credit") is not False
        or value.get("algorithm_a_parameter_or_measurement_read") is not False
        or value.get("external_algorithm_evidence_used") is not False
    ):
        raise RuntimeError("TPC-H C baseline-normalization contract differs")
    for binding in (value.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("TPC-H C baseline-normalization binding is not an object")
        path = ROOT / str(binding.get("path") or "")
        if not path.is_file() or path.is_symlink() or alg.sha(path) != binding.get("sha256"):
            raise RuntimeError(f"TPC-H C baseline-normalization binding differs: {path}")
    return value


def install_runtime() -> None:
    _validate_contract()
    base_scene_measure_batch = scene_measurement.measure_batch

    def baseline_eligibility_bridge(*args: Any, **kwargs: Any) -> dict[str, Any]:
        measured = base_scene_measure_batch(*args, **kwargs)
        output = Path(args[3] if len(args) > 3 else kwargs["output"])
        if output.name != "formal_incumbent_baseline":
            return measured
        for row in measured.values():
            if not isinstance(row, MutableMapping):
                raise RuntimeError("TPC-H C baseline row must be mutable")
            qps = float(row.get("db_qps") or 0.0)
            eligible = (
                row.get("hard_gate_passed") is True
                and row.get("coverage_complete") is True
                and qps > 0.0
            )
            row["eligible_for_outer_acceptance"] = eligible
            row["baseline_normalization_eligibility_only"] = eligible
            row["accepted_state_credit_increment"] = 0
        return measured

    scene_measurement.measure_batch = baseline_eligibility_bridge
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
            "valid_baseline_rows_admitted_to_normalization_only": True,
            "baseline_rows_receive_outer_credit": False,
            "algorithm_a_parameter_or_measurement_read": False,
        })
        return document

    boundary._state_document = state_document


def main() -> int:
    install_runtime()
    return int(boundary.main())


if __name__ == "__main__":
    raise SystemExit(main())
