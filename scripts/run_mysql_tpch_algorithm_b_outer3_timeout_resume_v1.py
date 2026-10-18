#!/usr/bin/env python3
"""Resume TPC-H B after Outer-3 rank-1 timeout adapter failure.

The completed current-run baseline, all fifteen calibration measurements, and
committed Outer 1--2 state are restored.  The failed Outer-3 physical wave is
reused once as immutable evidence: rank 1 remains formally infeasible and the
valid rank-2 companion remains usable.  No accepted/rejected credit is granted
for the partial outer wave before the normal outer decision runs.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v24 as gate_v24
from scripts import mysql_tpch_client_qps_measurement_v2 as client_qps_v2
from scripts import mysql_tpch_worker_qps_normalization_v1 as normalization
from scripts import run_mysql_algorithm_b_top4_boundary_resume_v1 as boundary
from scripts import run_mysql_tpch_algorithm_b_client_qps_fresh_v1 as tpch_release
from scripts import run_mysql_tpch_algorithm_b_client_qps_fresh_rerun_v5 as runtime_v5


HISTORY = ROOT / "history/mysql_four_scene_20260809"
SOURCE = HISTORY / "v1167_to_b_tpch_pg_rejection_fresh_rerun_20260830_tpch"
SOURCE_WAVE = SOURCE / "outer_003/top5_batch_1/batch_result.json"
SCENE = "tpch_sf4_memory"
FRESH_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_tpch_b_fresh_rerun_20260830_v8"
    / "algorithm_b_fresh_rerun_contract.json"
)
CONTINUATION_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_tpch_b_outer3_timeout_resume_20260830_v1"
    / "continuation_contract.json"
)
TOP4_CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260826_v53"
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_continuation_contract() -> dict[str, Any]:
    value = json.loads(CONTINUATION_CONTRACT.read_text(encoding="utf-8"))
    if (
        value.get("schema_version")
        != "mysql_tpch_b_outer3_timeout_resume_v1"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("scene") != SCENE
        or value.get("committed_outer_state_reused_through") != 2
        or value.get("partial_outer") != 3
        or value.get("failed_rank") != 1
        or value.get("valid_companion_rank") != 2
        or value.get("accepted_credit_for_partial_outer") != 0
    ):
        raise RuntimeError("TPC-H B Outer-3 continuation contract differs")
    for binding in (value.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("TPC-H B continuation binding is not an object")
        path = ROOT / str(binding.get("path") or "")
        if (
            not path.is_file()
            or path.is_symlink()
            or _sha(path) != str(binding.get("sha256") or "")
        ):
            raise RuntimeError(f"TPC-H B continuation binding differs: {path}")
    return value


class _RuntimeFacade:
    @staticmethod
    def install_runtime() -> Any:
        # The v2 adapter changes only handling of rows that already failed the
        # formal workload hard gate.  Valid-row Client-QPS semantics are
        # unchanged.
        tpch_release.client_qps = client_qps_v2
        tpch_release._validate_successor_contract = (
            lambda alg: alg.load(FRESH_CONTRACT)
        )
        runtime_v5.CONTRACT = FRESH_CONTRACT
        return runtime_v5.install_runtime()


def install_runtime() -> Any:
    _validate_continuation_contract()
    boundary.SOURCE = SOURCE
    boundary.SCENE = SCENE
    boundary.EXPECTED_ERROR = (
        "RuntimeError: full-suite client QPS must be finite and positive"
    )
    boundary.EXPECTED_ACCEPTED = 1
    boundary.EXPECTED_REJECTED = 1
    boundary.CALIBRATION_COMPLETED = 15
    boundary.COMMITTED_OUTER = 2
    boundary.PARTIAL_OUTER = 0
    boundary.PARTIAL_RANKS = ()
    boundary.RUNTIME_PREDECESSOR = _RuntimeFacade()
    boundary.CONTRACT_ROOT = TOP4_CONTRACT_ROOT
    boundary.CONTRACT = TOP4_CONTRACT_ROOT / "algorithm_b_top4_contract.json"
    boundary.COORDINATION = TOP4_CONTRACT_ROOT / "coordination_contract.json"
    core = boundary.install_runtime()
    impl = core._impl
    alg = impl["alg"]
    source_state = alg.load(SOURCE / "run_state.json")
    normalization_contract = copy.deepcopy(
        source_state["same_worker_qps_normalization"]
    )

    def measurement_payload(row: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(row, dict):
            row = dict(row)
        normalization.normalize_candidate_in_place(row, normalization_contract)
        resources = {
            name: float(dict(row["resource_means"])[name])
            for name in ("cpu", "memory", "io")
        }
        return {
            "resource": resources,
            "resources": copy.deepcopy(resources),
            "db_qps": float(row["db_qps"]),
            "p95_ms": float(row["p95_ms"]),
            "p99_ms": float(row["p99_ms"]),
            "hard_gate_passed": row.get("hard_gate_passed") is True,
            "coverage_complete": row.get("coverage_complete") is True,
            "source_worker_id": row.get("worker_id"),
            "source_rank": row.get("rank"),
        }

    original_configure = impl["configure_runtime"]

    def configure_runtime() -> Mapping[str, Any]:
        value = original_configure()
        alg.r0mod.dynamic_gate = gate_v24
        impl["no_lease_runtime"].coordination = gate_v24
        return value

    original_install_scene = impl["install_scene"]

    def install_scene(scene: str) -> None:
        original_install_scene(scene)
        scheduler = impl["scheduler"]
        original_measure = scheduler._ORIGINAL_MEASURE

        def restore_failed_wave(**kwargs: Any):
            output = Path(kwargs["output"])
            match = re.search(r"outer_(\d+)/top5_batch_(\d+)$", str(output))
            if match and (int(match.group(1)), int(match.group(2))) == (3, 1):
                source_document = copy.deepcopy(alg.load(SOURCE_WAVE))
                source_sha = alg.sha(SOURCE_WAVE)
                rebound = client_qps_v2.rebind_measurements(
                    source_document["measurements"]
                )
                destination = output / "batch_result.json"
                destination.parent.mkdir(parents=True, exist_ok=True)
                source_document["measurements"] = copy.deepcopy(rebound)
                source_document.update({
                    "qps_metric": client_qps_v2.METRIC,
                    "qps_metric_schema_version": client_qps_v2.SCHEMA_VERSION,
                    "source_failed_physical_wave_reused": True,
                    "source_failed_physical_wave_path": str(SOURCE_WAVE),
                    "source_failed_physical_wave_sha256": source_sha,
                    "accepted_state_credit_increment": 0,
                })
                alg.atomic(destination, source_document)
                state = kwargs["state"]
                state["stage"] = (
                    "ALGORITHM_B_OUTER_003_RANK_1_RESTORED_TIMEOUT_EVIDENCE"
                )
                state["outer3_timeout_evidence_reuse"] = {
                    "path": str(SOURCE_WAVE),
                    "sha256": source_sha,
                    "failed_rank": 1,
                    "valid_companion_rank": 2,
                    "accepted_state_credit_increment": 0,
                }
                alg.atomic(Path(kwargs["state_path"]), state)
                return rebound, state
            return original_measure(**kwargs)

        scheduler._ORIGINAL_MEASURE = restore_failed_wave

    original_state_document = impl["state_document"]

    def state_document(run_id: str) -> dict[str, Any]:
        value = original_state_document(run_id)
        value.update({
            "controller_source_path": str(Path(__file__).resolve()),
            "controller_source_sha256": alg.sha(Path(__file__).resolve()),
            "outer3_timeout_continuation_contract_path": str(
                CONTINUATION_CONTRACT
            ),
            "outer3_timeout_continuation_contract_sha256": alg.sha(
                CONTINUATION_CONTRACT
            ),
            "same_worker_qps_normalization": copy.deepcopy(
                normalization_contract
            ),
            "invalid_qps_adapter_release": client_qps_v2.contract(),
        })
        return value

    impl["measurement_payload"] = measurement_payload
    impl["configure_runtime"] = configure_runtime
    impl["install_scene"] = install_scene
    impl["state_document"] = state_document
    return core


def main() -> int:
    core = install_runtime()
    return int(core._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
