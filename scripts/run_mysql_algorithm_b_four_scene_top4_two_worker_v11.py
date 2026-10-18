#!/usr/bin/env python3
"""Resume Top4-B after a pre-measurement dynamic-gate interruption."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_b_four_scene_top4_two_worker_v9 as predecessor
from scripts import mysql_cross_project_wave_coordination_v10 as gate_v10

CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260826_v54"
CONTRACT = CONTRACT_ROOT / "algorithm_b_top4_contract.json"
COORDINATION = CONTRACT_ROOT / "coordination_contract.json"


def install_runtime() -> Any:
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.CONTRACT = CONTRACT
    predecessor.COORDINATION = COORDINATION
    core = predecessor.install_runtime()
    impl = core._impl
    alg = impl["alg"]
    original_validate = impl["validate_resume_source"]
    original_state_document = impl["state_document"]
    original_configure_runtime = impl["configure_runtime"]
    continuation: dict[str, Any] = {"applied": False}

    def verified(path: Path) -> dict[str, Any]:
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"regular continuation evidence required: {path}")
        sidecar = path.with_name(path.name + ".sha256")
        if not sidecar.is_file() or sidecar.read_text(encoding="ascii").split()[0] != alg.sha(path):
            raise RuntimeError(f"continuation sidecar differs: {path}")
        value = alg.load(path)
        if not isinstance(value, Mapping):
            raise RuntimeError(f"JSON object required: {path}")
        return dict(value)

    def validate_resume_source(source: Path) -> dict[str, Any]:
        source = source.resolve()
        state_path = source / "run_state.json"
        if not state_path.is_file():
            continuation["applied"] = False
            return original_validate(source)
        state = verified(state_path)
        scene = str(state.get("async_scene") or "")
        if not (
            scene in impl["TOP4_TABLES"]
            and state.get("algorithm") == "B"
            and state.get("status") == "FAILED_CLOSED"
            and state.get("stage") == "FAILED"
            and state.get("error") == "RuntimeError: dynamic non-interference gate failed closed"
            and int(state.get("outer_attempt") or 0) == 0
            and int((state.get("accepted_rounds") or {}).get(scene) or 0) == 0
            and int((state.get("rejected_outer_attempts") or {}).get(scene) or 0) == 0
        ):
            continuation["applied"] = False
            return original_validate(source)
        baseline_path = source / "formal_independent_baseline/batch_result.json"
        baseline = verified(baseline_path)
        baseline_rows = copy.deepcopy(dict(baseline.get("measurements") or {}))
        expected_workers = {
            worker_id for worker_id, row in baseline_rows.items()
            if row.get("scene") == scene
        }
        if (
            baseline.get("status") != "PASS"
            or baseline.get("failures") not in ([], None)
            or len(expected_workers) != 2
            or set(baseline_rows) != expected_workers
            or any(
                row.get("hard_gate_passed") is not True
                or row.get("coverage_complete") is not True
                for row in baseline_rows.values()
            )
        ):
            raise RuntimeError("dynamic-gate continuation baseline differs")
        design_path = source / "calibration_design.json"
        design = verified(design_path)
        wave_rows: dict[int, dict[str, dict[str, Any]]] = {}
        wave_evidence: list[dict[str, Any]] = []
        completed = 0
        for rank in range(1, 16):
            path = source / f"fresh_calibration/wave_{rank:02d}/batch_result.json"
            if not path.is_file():
                break
            batch = verified(path)
            rows = copy.deepcopy(dict(batch.get("measurements") or {}))
            if (
                batch.get("status") != "PASS"
                or batch.get("failures") not in ([], None)
                or len(rows) != 1
                or not set(rows).issubset(expected_workers)
                or any(
                    int(row.get("rank") or 0) != rank
                    or row.get("scene") != scene
                    or row.get("hard_gate_passed") is not True
                    or row.get("coverage_complete") is not True
                    for row in rows.values()
                )
            ):
                raise RuntimeError(f"completed calibration wave {rank} differs")
            completed = rank
            wave_rows[rank] = rows
            wave_evidence.append({
                "rank": rank, "path": str(path), "sha256": alg.sha(path),
                "role": "completed_pre_gate_calibration_wave_reused",
            })
        if completed < 1 or completed > 15:
            raise RuntimeError("partial calibration continuation boundary differs")
        failed_gate = (
            source / f"fresh_calibration/wave_{completed + 1:02d}_dynamic_noninterference_gate.json"
            if completed < 15 else
            source / "outer_001/top5_batch_1_dynamic_noninterference_gate.json"
        )
        gate = verified(failed_gate)
        if gate.get("status") != "BLOCKED_FAIL_CLOSED" or gate.get("errors") != ["dynamic_noninterference_window_timeout"]:
            raise RuntimeError("failed next-wave dynamic gate evidence differs")
        continuation.update({
            "applied": True, "source": str(source), "scene": scene,
            "completed_calibration_waves_reused": completed,
            "resume_calibration_wave": completed + 1 if completed < 15 else None,
            "baseline_reused": True,
            "failed_gate_measurement_reused": False,
            "accepted_or_rejected_outer_credit_reused": False,
        })
        impl["REUSE_VERIFIED_BASELINE"] = True
        impl["RESUME_CALIBRATION_WAVES"] = completed
        placeholder = state_path
        return {
            "source": source,
            "state": {
                "params_by_scene": copy.deepcopy(state["params_by_scene"]),
                "ranges_by_scene": copy.deepcopy(state["ranges_by_scene"]),
                "active_by_scene": copy.deepcopy(state["active_by_scene"]),
            },
            "state_path": state_path, "state_sha256": alg.sha(state_path),
            "baseline_path": baseline_path, "baseline_rows": baseline_rows,
            "baseline_sha256": alg.sha(baseline_path),
            "design_path": design_path, "design": design,
            "design_sha256": alg.sha(design_path),
            "wave_rows": wave_rows, "wave_evidence": wave_evidence,
            "quality_gate_evidence": [], "rankings_path": placeholder,
            "rankings_sha256": alg.sha(placeholder), "rankings": {},
            "valid_batch_path": placeholder, "valid_batch_sha256": alg.sha(placeholder),
            "valid_batch_rows": {}, "invalid_batch_path": placeholder,
            "invalid_batch_sha256": alg.sha(placeholder), "invalid_batch": {},
        }

    def state_document(run_id: str) -> dict[str, Any]:
        value = original_state_document(run_id)
        value["dynamic_gate_calibration_continuation_release"] = {
            "source_path": str(Path(__file__).resolve()),
            "source_sha256": alg.sha(Path(__file__).resolve()),
            "completed_measurements_only_may_be_reused": True,
            "failed_gate_measurement_reused": False,
            "accepted_or_rejected_outer_credit_reused": False,
        }
        return value

    def configure_runtime() -> Mapping[str, Any]:
        contract = original_configure_runtime()
        alg.r0mod.dynamic_gate = gate_v10
        impl["no_lease_runtime"].coordination = gate_v10
        return contract

    impl["CONTRACT"] = CONTRACT
    impl["COORDINATION_CONTRACT"] = COORDINATION
    impl["validate_resume_source"] = validate_resume_source
    impl["state_document"] = state_document
    impl["configure_runtime"] = configure_runtime
    return core


def main() -> int:
    core = install_runtime()
    return int(core._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
