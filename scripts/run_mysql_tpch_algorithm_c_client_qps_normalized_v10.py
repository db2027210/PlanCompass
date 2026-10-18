#!/usr/bin/env python3
"""TPC-H C successor prohibiting stale cached measurements from acceptance."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_tpch_client_qps_measurement_v2 as metric
from scripts import mysql_tpch_worker_qps_normalization_v1 as normalization
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v40 as boundary
from scripts import run_mysql_tpch_algorithm_c_client_qps_normalized_v6 as normalized_v6
from scripts import run_mysql_tpch_algorithm_c_client_qps_normalized_v7 as normalized_v7
from scripts import run_mysql_tpch_algorithm_c_client_qps_normalized_v8 as normalized_v8
from scripts import run_mysql_tpch_algorithm_c_client_qps_normalized_v9 as predecessor


alg = predecessor.alg
SCENE = predecessor.SCENE
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_tpch_c_semantic_batch_20260830_v10"
    / "algorithm_c_semantic_batch_contract.json"
)


def _validate_contract() -> dict[str, Any]:
    value = alg.load(CONTRACT)
    if (
        value.get("schema_version") != "mysql_tpch_algorithm_c_semantic_batch_v10"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("scene") != SCENE
        or value.get("qps_metric_contract") != metric.contract()
        or value.get("normalization_schema_version") != normalization.SCHEMA_VERSION
        or value.get("cached_measurements_grant_acceptance_credit") is not False
        or value.get("acceptance_requires_current_outer_physical_measurement") is not True
        or value.get("valid_rows_explicitly_eligible") is not True
        or value.get("hard_gate_failed_rows_classified_infeasible") is not True
        or value.get("failed_rows_receive_acceptance_credit") is not False
        or value.get("algorithm_a_parameter_or_measurement_read") is not False
        or value.get("external_algorithm_evidence_used") is not False
    ):
        raise RuntimeError("TPC-H C fresh-physical acceptance contract differs")
    for binding in (value.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("TPC-H C fresh-physical binding is not an object")
        path = ROOT / str(binding.get("path") or "")
        if not path.is_file() or path.is_symlink() or alg.sha(path) != binding.get("sha256"):
            raise RuntimeError(f"TPC-H C fresh-physical binding differs: {path}")
    return value


def install_runtime() -> None:
    _validate_contract()
    for module in (normalized_v6, normalized_v7, normalized_v8, predecessor):
        module.boundary = boundary
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
            "cached_measurements_grant_acceptance_credit": False,
            "acceptance_requires_current_outer_physical_measurement": True,
            "algorithm_a_parameter_or_measurement_read": False,
        })
        return document

    boundary._state_document = state_document


def main() -> int:
    install_runtime()
    return int(boundary.main())


if __name__ == "__main__":
    raise SystemExit(main())
