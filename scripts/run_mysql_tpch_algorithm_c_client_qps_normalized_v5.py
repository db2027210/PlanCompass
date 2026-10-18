#!/usr/bin/env python3
"""TPC-H C successor excluding stale cached no-move points."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v36 as boundary
from scripts import run_mysql_tpch_algorithm_c_client_qps_normalized_v4 as predecessor


alg = predecessor.alg
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_tpch_c_timeout_rows_20260830_v5"
    / "algorithm_c_timeout_rows_contract.json"
)
SCENE = predecessor.SCENE


def _validate_contract() -> dict[str, Any]:
    value = alg.load(CONTRACT)
    if (
        value.get("schema_version") != "mysql_tpch_algorithm_c_timeout_rows_v5"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("scene") != SCENE
        or value.get("qps_metric_contract") != predecessor.predecessor.metric.contract()
        or value.get("normalization_schema_version")
        != predecessor.predecessor.normalization.SCHEMA_VERSION
        or value.get("fresh_baseline_capture_required") is not True
        or value.get("scene_scoped_relational_measurement_required") is not True
        or value.get("cached_no_move_prephysical_exclusion_required") is not True
        or value.get("predicted_and_plan_tree_expansion_after_first_reject") is not True
        or value.get("failed_rows_receive_acceptance_credit") is not False
        or value.get("external_algorithm_evidence_used") is not False
    ):
        raise RuntimeError("TPC-H C cached no-move successor contract differs")
    for binding in (value.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("TPC-H C cached no-move binding is not an object")
        path = ROOT / str(binding.get("path") or "")
        if (
            not path.is_file() or path.is_symlink()
            or alg.sha(path) != binding.get("sha256")
        ):
            raise RuntimeError(f"TPC-H C cached no-move binding differs: {path}")
    return value


def install_runtime() -> None:
    _validate_contract()
    normalized_v3 = predecessor.predecessor
    fresh_runtime = normalized_v3.predecessor
    fresh_runtime.predecessor = boundary
    predecessor.CONTRACT = CONTRACT
    predecessor._validate_contract = _validate_contract
    predecessor.install_runtime()

    base_state_document = boundary._state_document

    def state_document(**kwargs: Any) -> dict[str, Any]:
        document = base_state_document(**kwargs)
        document.update({
            "controller_source_path": str(Path(__file__).resolve()),
            "controller_source_sha256": alg.sha(Path(__file__).resolve()),
            "timeout_rows_contract_path": str(CONTRACT),
            "timeout_rows_contract_sha256": alg.sha(CONTRACT),
            "cached_no_move_prephysical_exclusion": True,
            "predicted_and_plan_tree_expansion_after_first_reject": True,
            "algorithm_a_parameter_or_measurement_read": False,
        })
        return document

    boundary._state_document = state_document


def main() -> int:
    install_runtime()
    return int(boundary.main())


if __name__ == "__main__":
    raise SystemExit(main())
