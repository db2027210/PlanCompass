#!/usr/bin/env python3
"""Continue the current TPC-H B cohort with client-QPS outer progress.

The source campaign finished its two-worker baseline and all fifteen physical
calibration points before the first outer decision.  Its generic four-scene
adapter retained server Questions QPS for TPC-H.  This successor reinterprets
the already recorded full-suite client QPS, rebuilds the model from those raw
measurements, and discards every partial Outer-1 measurement.  No accepted or
rejected outer state is inherited.
"""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping, MutableMapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_tpch_client_qps_measurement_v2 as client_qps
from scripts import mysql_tpch_worker_qps_normalization_v1 as normalization
from scripts import run_mysql_algorithm_b_four_scene_client_primary_v5 as predecessor


HISTORY = ROOT / "history/mysql_four_scene_20260809"
SOURCE = HISTORY / "v1178_to_b_client_primary_four_scene_20260903_tpch"
CONTRACT_ROOT = ROOT / (
    "config/formal/"
    "frozen_mysql_four_scene_algorithm_b_client_primary_20260903_v72"
)
CONTRACT = CONTRACT_ROOT / "algorithm_b_top4_contract.json"
COORDINATION = CONTRACT_ROOT / "coordination_contract.json"
SCENE = "tpch_sf4_memory"


def install_runtime() -> Any:
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.CONTRACT = CONTRACT
    predecessor.COORDINATION = COORDINATION
    core = predecessor.install_runtime()
    impl = core._impl
    alg = impl["alg"]
    original_mirror_batch = impl["_mirror_batch"]
    original_measurement_payload = impl["measurement_payload"]
    original_state_document = impl["state_document"]
    measurement_module = impl["single_measurement"]
    base_measure_batch = measurement_module.measure_batch
    normalization_contract: dict[str, Any] = {}

    measurement_module.measure_batch = client_qps.wrap_measure_batch(
        base_measure_batch, load=alg.load, atomic=alg.atomic,
    )
    alg.measure_batch = measurement_module.measure_batch
    impl["runtime"]._measure_batch_with_fair_coordination.__globals__[
        "alg"
    ].measure_batch = measurement_module.measure_batch

    def verified(path: Path) -> dict[str, Any]:
        impl["_verified_sidecar"](path)
        return copy.deepcopy(dict(alg.load(path)))

    def rebind_batch_rows(
        document: Mapping[str, Any], *, normalize: bool,
    ) -> dict[str, dict[str, Any]]:
        rows = client_qps.rebind_measurements(
            dict(document.get("measurements") or {})
        )
        if normalize:
            if not normalization_contract:
                raise RuntimeError("TPC-H B client normalization contract absent")
            for row in rows.values():
                normalization.normalize_candidate_in_place(
                    row, normalization_contract,
                )
        return rows

    def validate_resume_source(source: Path) -> dict[str, Any]:
        source = source.resolve()
        if source != SOURCE.resolve():
            raise RuntimeError("TPC-H client-primary continuation source differs")
        contract = alg.load(CONTRACT)
        source_contract = dict(contract.get("technical_continuation") or {})
        state_path = source / "run_state.json"
        state = verified(state_path)
        if (
            source_contract.get("source_run_root") != str(source)
            or source_contract.get("source_state_sha256") != alg.sha(state_path)
            or state.get("algorithm") != "B"
            or state.get("async_scene") != SCENE
            or state.get("status") != "RUNNING"
            or not str(state.get("stage") or "").startswith(
                "ALGORITHM_B_OUTER_001_"
            )
            or int(state.get("outer_attempt") or 0) != 0
            or int((state.get("accepted_rounds") or {}).get(SCENE) or 0) != 0
            or int((state.get("rejected_outer_attempts") or {}).get(SCENE) or 0) != 0
        ):
            raise RuntimeError("TPC-H pre-decision continuation state differs")

        baseline_path = source / "formal_independent_baseline/batch_result.json"
        baseline = verified(baseline_path)
        baseline_rows = rebind_batch_rows(baseline, normalize=False)
        if (
            baseline.get("status") != "PASS"
            or baseline.get("failures") not in ([], None)
            or set(baseline_rows) != {"tpch_w1", "tpch_w2"}
            or any(
                row.get("hard_gate_passed") is not True
                or row.get("coverage_complete") is not True
                for row in baseline_rows.values()
            )
        ):
            raise RuntimeError("TPC-H continuation baseline differs")
        normalization_contract.clear()
        normalization_contract.update(normalization.baseline_contract(
            {
                "status": "PASS",
                "failures": [],
                "measurements": baseline_rows,
            },
            scene=SCENE,
        ))

        design_path = source / "calibration_design.json"
        design = verified(design_path)
        wave_rows: dict[int, dict[str, dict[str, Any]]] = {}
        wave_evidence: list[dict[str, Any]] = []
        expected = dict(source_contract.get("calibration_batch_sha256") or {})
        for rank in range(1, 16):
            path = source / f"fresh_calibration/wave_{rank:02d}/batch_result.json"
            batch = verified(path)
            rows = rebind_batch_rows(batch, normalize=True)
            if (
                expected.get(f"wave_{rank:02d}") != alg.sha(path)
                or batch.get("status") != "PASS"
                or batch.get("failures") not in ([], None)
                or len(rows) != 1
                or any(
                    row.get("scene") != SCENE
                    or int(row.get("rank") or 0) != rank
                    or row.get("hard_gate_passed") is not True
                    or row.get("coverage_complete") is not True
                    for row in rows.values()
                )
            ):
                raise RuntimeError(
                    f"TPC-H completed calibration wave {rank} differs"
                )
            wave_rows[rank] = rows
            wave_evidence.append({
                "rank": rank,
                "path": str(path),
                "sha256": alg.sha(path),
                "role": "raw_full_suite_measurement_rebound_to_client_qps",
            })

        impl["REUSE_VERIFIED_BASELINE"] = True
        impl["RESUME_CALIBRATION_WAVES"] = 15
        impl["REUSE_HISTORICAL_OUTER_EVIDENCE"] = False
        impl["RESUME_VALID_RANKS"] = ()
        impl["RESUME_INVALID_RANKS"] = ()
        return {
            "source": source,
            "state": {
                "params_by_scene": copy.deepcopy(state["params_by_scene"]),
                "ranges_by_scene": copy.deepcopy(state["ranges_by_scene"]),
                "active_by_scene": copy.deepcopy(state["active_by_scene"]),
            },
            "state_path": state_path,
            "state_sha256": alg.sha(state_path),
            "baseline_path": baseline_path,
            "baseline_rows": baseline_rows,
            "baseline_sha256": alg.sha(baseline_path),
            "design_path": design_path,
            "design": design,
            "design_sha256": alg.sha(design_path),
            "wave_rows": wave_rows,
            "wave_evidence": wave_evidence,
            "quality_gate_evidence": [],
            "rankings_path": state_path,
            "rankings_sha256": alg.sha(state_path),
            "rankings": {},
            "valid_batch_path": state_path,
            "valid_batch_sha256": alg.sha(state_path),
            "valid_batch_rows": {},
            "invalid_batch_path": state_path,
            "invalid_batch_sha256": alg.sha(state_path),
            "invalid_batch": {},
        }

    def mirror_batch(source: Path, destination: Path) -> None:
        original_mirror_batch(source, destination)
        document = copy.deepcopy(dict(alg.load(destination)))
        normalize_rows = "fresh_calibration" in destination.parts
        document["measurements"] = rebind_batch_rows(
            document, normalize=normalize_rows,
        )
        document.update({
            "schema_version": "mysql_single_scene_tpch_client_primary_batch_v1",
            "qps_metric": client_qps.METRIC,
            "qps_metric_schema_version": client_qps.SCHEMA_VERSION,
            "server_db_qps_preserved_as_audit": True,
            "client_qps_controls_outer_acceptance": True,
            "same_worker_qps_normalization_applied": normalize_rows,
        })
        alg.atomic(destination, document)

    def measurement_payload(row: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(row, MutableMapping):
            raise RuntimeError("TPC-H B candidate row must be mutable")
        normalization.normalize_candidate_in_place(
            row, normalization_contract,
        )
        return original_measurement_payload(row)

    def state_document(run_id: str) -> dict[str, Any]:
        document = original_state_document(run_id)
        document.update({
            "controller_source_path": str(Path(__file__).resolve()),
            "controller_source_sha256": alg.sha(Path(__file__).resolve()),
            "tpch_client_qps_successor_contract_path": str(CONTRACT),
            "tpch_client_qps_successor_contract_sha256": alg.sha(CONTRACT),
            "qps_metric_contract": copy.deepcopy(client_qps.contract()),
            "outer_acceptance_uses_same_worker_normalized_client_qps": True,
            "server_questions_qps_role": "audit_only",
            "same_worker_qps_normalization": copy.deepcopy(
                normalization_contract
            ),
            "source_partial_outer_measurements_reused": False,
            "source_accepted_or_rejected_outer_credit_reused": False,
            "algorithm_a_parameter_or_measurement_read": False,
            "algorithm_a_used_as_search_prior": False,
        })
        return document

    impl["CONTRACT"] = CONTRACT
    impl["COORDINATION_CONTRACT"] = COORDINATION
    impl["validate_resume_source"] = validate_resume_source
    impl["_mirror_batch"] = mirror_batch
    impl["measurement_payload"] = measurement_payload
    impl["state_document"] = state_document
    return core


def main() -> int:
    return int(install_runtime()._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
