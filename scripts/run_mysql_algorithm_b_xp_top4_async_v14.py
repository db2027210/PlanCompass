#!/usr/bin/env python3
"""Resume CPU Top4 B after the global-disk settle technical failure."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v8 as gate_v8
from scripts import mysql_xp_post_reset_scene_scoped_settle_v1 as scene_settle
from scripts import run_mysql_algorithm_b_xp_top4_async_v6 as successor

HISTORY = ROOT / "history/mysql_four_scene_20260809"
SOURCE = HISTORY / "v229_to_b_top4_fresh_20260826_attempt018_xp_cpu"
successor.CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v23"
)
successor.gate_v7 = gate_v8


def _load_verified(path: Path) -> dict[str, Any]:
    core = successor.predecessor
    core._verified_sidecar(path)
    return core._impl["alg"].load(path)


def validate_resume_source(source: Path) -> dict[str, Any]:
    source = source.resolve()
    if source != SOURCE.resolve():
        raise RuntimeError("CPU B calibration continuation source differs")
    state_path = source / "run_state.json"
    state = _load_verified(state_path)
    if (
        state.get("algorithm") != "B"
        or state.get("async_scene") != "hybench_xp_cpu"
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error")
        != "RuntimeError: disk2 post-reset writeback did not settle in 120s"
        or int(state.get("outer_attempt") or 0) != 0
        or int((state.get("accepted_rounds") or {}).get("hybench_xp_cpu") or 0) != 0
        or int((state.get("rejected_outer_attempts") or {}).get("hybench_xp_cpu") or 0) != 0
    ):
        raise RuntimeError("CPU B calibration continuation state differs")
    baseline_path = source / "formal_independent_baseline/batch_result.json"
    baseline = _load_verified(baseline_path)
    baseline_rows = copy.deepcopy(dict(baseline.get("measurements") or {}))
    if baseline.get("status") != "PASS" or len(baseline_rows) != 4:
        raise RuntimeError("CPU B continuation baseline differs")
    design_path = source / "calibration_design.json"
    design = _load_verified(design_path)
    wave_rows: dict[int, dict[str, dict[str, Any]]] = {}
    wave_evidence: list[dict[str, Any]] = []
    for rank in range(1, 5):
        path = source / f"fresh_calibration/wave_{rank:02d}/batch_result.json"
        batch = _load_verified(path)
        rows = copy.deepcopy(dict(batch.get("measurements") or {}))
        if (
            batch.get("status") != "PASS" or batch.get("failures") not in ([], None)
            or len(rows) != 1
            or any(
                row.get("hard_gate_passed") is not True
                or row.get("coverage_complete") is not True
                or int(row.get("rank") or 0) != rank
                for row in rows.values()
            )
        ):
            raise RuntimeError(f"CPU B completed calibration wave {rank} differs")
        wave_rows[rank] = rows
        wave_evidence.append({"rank": rank, "path": str(path), "sha256": successor.predecessor._impl["alg"].sha(path)})
    placeholder = state_path
    return {
        "source": source,
        "state": {
            "params_by_scene": copy.deepcopy(state["params_by_scene"]),
            "ranges_by_scene": copy.deepcopy(state["ranges_by_scene"]),
            "active_by_scene": copy.deepcopy(state["active_by_scene"]),
        },
        "state_path": state_path,
        "state_sha256": successor.predecessor._impl["alg"].sha(state_path),
        "baseline_path": baseline_path,
        "baseline_rows": baseline_rows,
        "baseline_sha256": successor.predecessor._impl["alg"].sha(baseline_path),
        "design_path": design_path, "design": design,
        "design_sha256": successor.predecessor._impl["alg"].sha(design_path),
        "wave_rows": wave_rows, "wave_evidence": wave_evidence,
        "companion_path": placeholder,
        "companion_sha256": successor.predecessor._impl["alg"].sha(placeholder),
        "failure_evidence_path": placeholder,
        "failure_evidence_sha256": successor.predecessor._impl["alg"].sha(placeholder),
        "quality_gate_evidence": [],
        "rankings_path": placeholder,
        "rankings_sha256": successor.predecessor._impl["alg"].sha(placeholder),
        "rankings": {}, "valid_batch_path": placeholder,
        "valid_batch_sha256": successor.predecessor._impl["alg"].sha(placeholder),
        "valid_batch_rows": {}, "invalid_batch_path": placeholder,
        "invalid_batch_sha256": successor.predecessor._impl["alg"].sha(placeholder),
        "invalid_batch": {},
    }


def main() -> int:
    core = successor.predecessor
    core.install()
    successor.install()
    original_configure = successor.configure_runtime

    def configure_runtime() -> Mapping[str, Any]:
        contract = original_configure()
        core._impl["alg"].reset_xp = scene_settle.reset_xp
        return contract

    core._impl["configure_runtime"] = configure_runtime
    core._impl["validate_resume_source"] = validate_resume_source
    core._impl["RESUME_CALIBRATION_WAVES"] = 4
    core._impl["REUSE_VERIFIED_BASELINE"] = True
    core._impl["alg"].reset_xp = scene_settle.reset_xp
    return int(core._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
