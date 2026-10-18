#!/usr/bin/env python3
"""TPC-H Algorithm B with same-worker client-QPS normalization."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping, MutableMapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_tpch_worker_qps_normalization_v1 as normalization
from scripts import run_mysql_tpch_algorithm_b_client_qps_continue_v2 as predecessor


CONTRACT = (
    ROOT / "config/formal/frozen_mysql_tpch_bc_client_qps_20260830_v1"
    / "algorithm_b_same_worker_qps_contract.json"
)


def install_runtime() -> Any:
    core = predecessor.install_runtime()
    impl = core._impl
    alg = impl["alg"]
    original_validate_resume_source = impl["validate_resume_source"]
    original_measurement_payload = impl["measurement_payload"]
    original_state_document = impl["state_document"]
    normalization_contract: dict[str, Any] = {}

    def validate_contract() -> dict[str, Any]:
        value = alg.load(CONTRACT)
        if (
            value.get("schema_version")
            != "mysql_tpch_algorithm_b_same_worker_qps_v1"
            or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
            or value.get("formal_scope") != "mysql_only"
            or value.get("cross_engine_comparison_prohibited") is not True
            or value.get("normalization_schema_version")
            != normalization.SCHEMA_VERSION
            or value.get("outer_state_reused") is not False
        ):
            raise RuntimeError("TPC-H B same-worker QPS contract differs")
        for binding in (value.get("source_bindings") or {}).values():
            if not isinstance(binding, Mapping):
                raise RuntimeError("TPC-H B normalization binding is not an object")
            path = ROOT / str(binding.get("path") or "")
            if (
                not path.is_file() or path.is_symlink()
                or alg.sha(path) != binding.get("sha256")
            ):
                raise RuntimeError(f"TPC-H B normalization binding differs: {path}")
        return value

    def validate_resume_source(source: Path) -> dict[str, Any]:
        resume = original_validate_resume_source(source)
        baseline = alg.load(Path(resume["baseline_path"]))
        normalization_contract.clear()
        normalization_contract.update(normalization.baseline_contract(
            baseline, scene="tpch_sf4_memory",
        ))
        return resume

    def measurement_payload(row: Mapping[str, Any]) -> dict[str, Any]:
        if not normalization_contract:
            raise RuntimeError("TPC-H B same-worker baseline contract absent")
        if not isinstance(row, MutableMapping):
            raise RuntimeError("TPC-H B candidate row must be mutable")
        normalization.normalize_candidate_in_place(row, normalization_contract)
        return original_measurement_payload(row)

    def state_document(run_id: str) -> dict[str, Any]:
        document = original_state_document(run_id)
        document.update({
            "controller_source_path": str(Path(__file__).resolve()),
            "controller_source_sha256": alg.sha(Path(__file__).resolve()),
            "same_worker_qps_contract_path": str(CONTRACT),
            "same_worker_qps_contract_sha256": alg.sha(CONTRACT),
            "same_worker_qps_normalization": copy.deepcopy(
                normalization_contract
            ),
            "raw_worker_client_qps_preserved": True,
            "outer_acceptance_uses_same_worker_normalized_client_qps": True,
            "outer_state_reused": False,
        })
        return document

    validate_contract()
    impl["validate_resume_source"] = validate_resume_source
    impl["measurement_payload"] = measurement_payload
    impl["state_document"] = state_document
    return core


def main() -> int:
    core = install_runtime()
    return int(core._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
