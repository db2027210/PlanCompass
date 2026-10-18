#!/usr/bin/env python3
"""TPC-H C successor with explicit valid/infeasible normalization rows."""

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
from scripts import run_mysql_tpch_algorithm_c_client_qps_normalized_v3 as normalized_v3
from scripts import run_mysql_tpch_algorithm_c_client_qps_normalized_v8 as predecessor


alg = predecessor.alg
boundary = predecessor.boundary
SCENE = predecessor.SCENE
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_tpch_c_semantic_batch_20260830_v9"
    / "algorithm_c_semantic_batch_contract.json"
)


def _validate_contract() -> dict[str, Any]:
    value = alg.load(CONTRACT)
    if (
        value.get("schema_version") != "mysql_tpch_algorithm_c_semantic_batch_v9"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("scene") != SCENE
        or value.get("qps_metric_contract") != metric.contract()
        or value.get("normalization_schema_version") != normalization.SCHEMA_VERSION
        or value.get("valid_rows_explicitly_eligible") is not True
        or value.get("hard_gate_failed_rows_classified_infeasible") is not True
        or value.get("actual_coverage_preserved") is not True
        or value.get("failed_rows_receive_acceptance_credit") is not False
        or value.get("algorithm_a_parameter_or_measurement_read") is not False
        or value.get("external_algorithm_evidence_used") is not False
    ):
        raise RuntimeError("TPC-H C explicit normalization-row contract differs")
    for binding in (value.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("TPC-H C explicit normalization binding is not an object")
        path = ROOT / str(binding.get("path") or "")
        if not path.is_file() or path.is_symlink() or alg.sha(path) != binding.get("sha256"):
            raise RuntimeError(f"TPC-H C explicit normalization binding differs: {path}")
    return value


def _install_corrected_normalization_runtime() -> None:
    normalized_v3._validate_contract()
    normalized_v3.predecessor.client_qps = metric
    normalized_v3.predecessor.CONTRACT = normalized_v3.CONTRACT
    normalized_v3.predecessor._validate_contract = normalized_v3._validate_contract
    normalized_v3.predecessor.install_runtime()
    base_measure_batch = alg.measure_batch
    base_state_document = normalized_v3.predecessor.predecessor._state_document
    normalization_contract: dict[str, Any] = {}

    def normalized_measure_batch(*args: Any, **kwargs: Any) -> dict[str, Any]:
        measured = base_measure_batch(*args, **kwargs)
        output = Path(args[3] if len(args) > 3 else kwargs["output"])
        batch_path = output / "batch_result.json"
        document = alg.load(batch_path)
        if output.name == "formal_incumbent_baseline":
            normalization_contract.clear()
            normalization_contract.update(normalization.baseline_contract(
                document, scene=SCENE,
            ))
        if not normalization_contract:
            raise RuntimeError("TPC-H C same-worker baseline contract absent")
        if not isinstance(measured, MutableMapping):
            raise RuntimeError("TPC-H C measured rows must be mutable")
        for worker_id, row in measured.items():
            if not isinstance(row, MutableMapping):
                raise RuntimeError("TPC-H C measured row must be mutable")
            if str(row.get("scene") or "") != SCENE:
                raise RuntimeError("TPC-H C normalization saw another scene")
            valid = (
                row.get("hard_gate_passed") is True
                and row.get("coverage_complete") is True
                and float(row.get("db_qps") or 0.0) > 0.0
            )
            row["eligible_for_outer_acceptance"] = valid
            row["accepted_state_credit_increment"] = 0
            if valid:
                normalization.normalize_candidate_in_place(
                    row, normalization_contract,
                )
            else:
                row["formally_infeasible_candidate"] = True
                row["infeasible_row_normalized"] = False
            if str(row.get("worker_id") or "") != str(worker_id):
                raise RuntimeError("TPC-H C worker identity differs")
        document["measurements"] = copy.deepcopy(dict(measured))
        document.update({
            "same_worker_qps_normalization": copy.deepcopy(normalization_contract),
            "raw_worker_client_qps_preserved": True,
            "outer_acceptance_uses_same_worker_normalized_client_qps": True,
            "valid_rows_explicitly_eligible": True,
            "hard_gate_failed_rows_classified_infeasible": True,
            "actual_coverage_preserved": True,
            "ineligible_timeout_rows_normalized": False,
            "ineligible_timeout_rows_receive_acceptance_credit": False,
        })
        alg.atomic(batch_path, document)
        if output.name == "formal_incumbent_baseline":
            audit_path = output / "same_worker_qps_normalization.json"
            alg.atomic(audit_path, {
                **copy.deepcopy(normalization_contract),
                "status": "PASS",
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "baseline_batch_path": str(batch_path),
                "baseline_batch_sha256": alg.sha(batch_path),
            })
            state_path_raw = kwargs.get("state_path")
            if state_path_raw is not None:
                state_path = Path(state_path_raw)
                state = alg.load(state_path)
                state.update({
                    "same_worker_qps_normalization": copy.deepcopy(normalization_contract),
                    "same_worker_qps_normalization_path": str(audit_path),
                    "same_worker_qps_normalization_sha256": alg.sha(audit_path),
                    "outer_acceptance_uses_same_worker_normalized_client_qps": True,
                })
                alg.atomic(state_path, state)
        return measured

    def state_document(**kwargs: Any) -> dict[str, Any]:
        document = base_state_document(**kwargs)
        document.update({
            "qps_metric_contract": metric.contract(),
            "same_worker_qps_normalization_schema_version": normalization.SCHEMA_VERSION,
            "valid_rows_explicitly_eligible": True,
            "hard_gate_failed_rows_classified_infeasible": True,
            "actual_coverage_preserved": True,
            "ineligible_timeout_rows_receive_acceptance_credit": False,
        })
        return document

    alg.measure_batch = normalized_measure_batch
    normalized_v3.predecessor.predecessor._state_document = state_document


def install_runtime() -> None:
    _validate_contract()
    normalized_v3.install_runtime = _install_corrected_normalization_runtime
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
            "valid_rows_explicitly_eligible": True,
            "hard_gate_failed_rows_classified_infeasible": True,
            "actual_coverage_preserved": True,
            "algorithm_a_parameter_or_measurement_read": False,
        })
        return document

    boundary._state_document = state_document


def main() -> int:
    install_runtime()
    return int(boundary.main())


if __name__ == "__main__":
    raise SystemExit(main())
