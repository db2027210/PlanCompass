#!/usr/bin/env python3
"""TPC-H C with normalized client-QPS and scene-scoped measurement."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import (
    run_mysql_four_scene_algorithm_b_scene_independent_measurement_v5
    as scene_measurement,
)
from scripts import run_mysql_tpch_algorithm_c_client_qps_normalized_v3 as predecessor


alg = predecessor.alg
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_tpch_c_timeout_rows_20260830_v4"
    / "algorithm_c_timeout_rows_contract.json"
)
SCENE = predecessor.SCENE


def _validate_contract() -> dict[str, Any]:
    value = alg.load(CONTRACT)
    if (
        value.get("schema_version") != "mysql_tpch_algorithm_c_timeout_rows_v4"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("scene") != SCENE
        or value.get("qps_metric_contract") != predecessor.metric.contract()
        or value.get("normalization_schema_version")
        != predecessor.normalization.SCHEMA_VERSION
        or value.get("fresh_baseline_capture_required") is not True
        or value.get("scene_scoped_relational_measurement_required") is not True
        or value.get("coverage_admission_identity")
        != "parameter_vector_with_actual_plan_preserved_for_audit"
        or value.get("failed_rows_receive_acceptance_credit") is not False
        or value.get("external_algorithm_evidence_used") is not False
    ):
        raise RuntimeError("TPC-H C scene-scoped successor contract differs")
    for binding in (value.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("TPC-H C scene-scoped binding is not an object")
        path = ROOT / str(binding.get("path") or "")
        if (
            not path.is_file() or path.is_symlink()
            or alg.sha(path) != binding.get("sha256")
        ):
            raise RuntimeError(f"TPC-H C scene-scoped binding differs: {path}")
    return value


def install_runtime() -> None:
    _validate_contract()
    predecessor.CONTRACT = CONTRACT
    predecessor._validate_contract = _validate_contract
    # The four-scene predecessor submits a relational task only when JOB and
    # TPC-H siblings are both present.  A TPC-H-only controller must use the
    # already validated scene-scoped adapter, otherwise its task pool is empty.
    alg.measure_batch = scene_measurement.measure_batch
    predecessor.install_runtime()

    runtime = predecessor.predecessor.predecessor
    base_state_document = runtime._state_document

    def state_document(**kwargs: Any) -> dict[str, Any]:
        document = base_state_document(**kwargs)
        document.update({
            "controller_source_path": str(Path(__file__).resolve()),
            "controller_source_sha256": alg.sha(Path(__file__).resolve()),
            "timeout_rows_contract_path": str(CONTRACT),
            "timeout_rows_contract_sha256": alg.sha(CONTRACT),
            "scene_scoped_relational_measurement": True,
            "scene_scoped_measurement_source_path": str(
                Path(scene_measurement.__file__).resolve()
            ),
            "scene_scoped_measurement_source_sha256": alg.sha(
                Path(scene_measurement.__file__).resolve()
            ),
            "sibling_relational_scene_required": False,
            "algorithm_a_parameter_or_measurement_read": False,
        })
        return document

    runtime._state_document = state_document


def main() -> int:
    install_runtime()
    return int(predecessor.predecessor.predecessor.main())


if __name__ == "__main__":
    raise SystemExit(main())
