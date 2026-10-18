#!/usr/bin/env python3
"""Fresh TPC-H Algorithm C with same-worker client-QPS normalization."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping, MutableMapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_tpch_worker_qps_normalization_v1 as normalization
from scripts import run_mysql_tpch_algorithm_c_client_qps_fresh_v1 as predecessor


alg = predecessor.predecessor.alg
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_tpch_bc_client_qps_20260830_v1"
    / "algorithm_c_same_worker_qps_contract.json"
)
SCENE = "tpch_sf4_memory"


def _validate_contract() -> dict[str, Any]:
    value = alg.load(CONTRACT)
    if (
        value.get("schema_version")
        != "mysql_tpch_algorithm_c_same_worker_qps_v1"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("normalization_schema_version")
        != normalization.SCHEMA_VERSION
        or value.get("fresh_baseline_capture_required") is not True
    ):
        raise RuntimeError("TPC-H C same-worker QPS contract differs")
    for binding in (value.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("TPC-H C normalization binding is not an object")
        path = ROOT / str(binding.get("path") or "")
        if (
            not path.is_file() or path.is_symlink()
            or alg.sha(path) != binding.get("sha256")
        ):
            raise RuntimeError(f"TPC-H C normalization binding differs: {path}")
    return value


def install_runtime() -> None:
    _validate_contract()
    predecessor.install_runtime()
    base_measure_batch = alg.measure_batch
    base_state_document = predecessor.predecessor._state_document
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
                raise RuntimeError("TPC-H C candidate row must be mutable")
            if str(row.get("scene") or "") != SCENE:
                raise RuntimeError("TPC-H C normalization saw another scene")
            normalization.normalize_candidate_in_place(
                row, normalization_contract,
            )
            if str(row.get("worker_id") or "") != str(worker_id):
                raise RuntimeError("TPC-H C worker identity differs")
        document["measurements"] = copy.deepcopy(dict(measured))
        document.update({
            "same_worker_qps_normalization": copy.deepcopy(
                normalization_contract
            ),
            "raw_worker_client_qps_preserved": True,
            "outer_acceptance_uses_same_worker_normalized_client_qps": True,
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
                    "same_worker_qps_normalization": copy.deepcopy(
                        normalization_contract
                    ),
                    "same_worker_qps_normalization_path": str(audit_path),
                    "same_worker_qps_normalization_sha256": alg.sha(audit_path),
                    "outer_acceptance_uses_same_worker_normalized_client_qps": True,
                })
                alg.atomic(state_path, state)
        return measured

    def state_document(**kwargs: Any) -> dict[str, Any]:
        document = base_state_document(**kwargs)
        document.update({
            "controller_source_path": str(Path(__file__).resolve()),
            "controller_source_sha256": alg.sha(Path(__file__).resolve()),
            "same_worker_qps_contract_path": str(CONTRACT),
            "same_worker_qps_contract_sha256": alg.sha(CONTRACT),
            "same_worker_qps_normalization_schema_version": (
                normalization.SCHEMA_VERSION
            ),
            "same_worker_qps_normalization_capture": (
                "fresh_formal_incumbent_baseline"
            ),
            "raw_worker_client_qps_preserved": True,
            "outer_acceptance_uses_same_worker_normalized_client_qps": True,
        })
        return document

    alg.measure_batch = normalized_measure_batch
    predecessor.predecessor._state_document = state_document


def main() -> int:
    install_runtime()
    return int(predecessor.predecessor.main())


if __name__ == "__main__":
    raise SystemExit(main())
