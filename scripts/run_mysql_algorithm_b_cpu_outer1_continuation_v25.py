#!/usr/bin/env python3
"""Resume CPU Top4-B after the post-measurement score-interface failure."""

from __future__ import annotations

import copy
from pathlib import Path
import re
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_b_xp_top4_async_v24 as release


HISTORY = ROOT / "history/mysql_four_scene_20260809"
SOURCE = HISTORY / "v255_to_b_top4_fresh_20260826_attempt028_xp_cpu"


def _verified(core: Any, path: Path) -> dict[str, Any]:
    core._impl["_verified_sidecar"](path)
    value = core._impl["alg"].load(path)
    if not isinstance(value, Mapping):
        raise RuntimeError(f"CPU-B continuation JSON object required: {path}")
    return dict(value)


def _valid_batch(core: Any, path: Path, *, expected_rows: int, rank: int | None = None) -> dict[str, Any]:
    value = _verified(core, path)
    rows = copy.deepcopy(dict(value.get("measurements") or {}))
    if (
        value.get("status") != "PASS"
        or value.get("failures") not in ([], None)
        or len(rows) != expected_rows
        or any(
            row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            or (rank is not None and int(row.get("rank") or 0) != rank)
            for row in rows.values()
        )
    ):
        raise RuntimeError(f"CPU-B continuation batch differs: {path}")
    return {"document": value, "rows": rows}


def main() -> int:
    core = release.install_runtime()
    alg = core._impl["alg"]
    runtime = core._impl["runtime"]
    original_install_scene = core._impl["install_scene"]
    original_state_document = core._impl["state_document"]
    original_measure = runtime._measure_batch_with_fair_coordination

    source_state_path = SOURCE / "run_state.json"
    source_state = _verified(core, source_state_path)
    if (
        source_state.get("status") != "FAILED_CLOSED"
        or source_state.get("stage") != "FAILED"
        or source_state.get("error")
        != "TypeError: FreshBaselineObjective.score() missing 1 required positional argument: 'raw'"
        or int(source_state.get("outer_attempt") or 0) != 0
        or int((source_state.get("accepted_rounds") or {}).get("hybench_xp_cpu") or 0) != 0
        or int((source_state.get("rejected_outer_attempts") or {}).get("hybench_xp_cpu") or 0) != 0
    ):
        raise RuntimeError("CPU-B score continuation boundary differs")

    baseline_path = SOURCE / "formal_independent_baseline/batch_result.json"
    baseline = _valid_batch(core, baseline_path, expected_rows=4)
    design_path = SOURCE / "calibration_design.json"
    design = _verified(core, design_path)
    rankings_path = SOURCE / "outer_001/predicted_rankings.json"
    rankings = _verified(core, rankings_path)
    wave_rows: dict[int, dict[str, Any]] = {}
    wave_evidence: list[dict[str, Any]] = []
    for rank in range(1, 16):
        path = SOURCE / f"fresh_calibration/wave_{rank:02d}/batch_result.json"
        batch = _valid_batch(core, path, expected_rows=1, rank=rank)
        wave_rows[rank] = batch["rows"]
        wave_evidence.append({
            "rank": rank, "path": str(path), "sha256": alg.sha(path),
            "role": "completed_calibration_rank_reused",
        })
    outer_batches = {
        rank: SOURCE / f"outer_001/top5_batch_{rank}/batch_result.json"
        for rank in range(1, 6)
    }
    outer_rows = {
        rank: _valid_batch(core, path, expected_rows=1, rank=rank)["rows"]
        for rank, path in outer_batches.items()
    }

    def validate_resume_source(source: Path) -> dict[str, Any]:
        if source.resolve() != SOURCE.resolve():
            raise RuntimeError("CPU-B score continuation source differs")
        return {
            "source": SOURCE,
            "state": {
                "params_by_scene": copy.deepcopy(source_state["params_by_scene"]),
                "ranges_by_scene": copy.deepcopy(source_state["ranges_by_scene"]),
                "active_by_scene": copy.deepcopy(source_state["active_by_scene"]),
            },
            "state_path": source_state_path,
            "state_sha256": alg.sha(source_state_path),
            "baseline_path": baseline_path,
            "baseline_rows": copy.deepcopy(baseline["rows"]),
            "baseline_sha256": alg.sha(baseline_path),
            "design_path": design_path,
            "design": copy.deepcopy(design),
            "design_sha256": alg.sha(design_path),
            "wave_rows": copy.deepcopy(wave_rows),
            "wave_evidence": copy.deepcopy(wave_evidence),
            "companion_path": source_state_path,
            "companion_sha256": alg.sha(source_state_path),
            "failure_evidence_path": source_state_path,
            "failure_evidence_sha256": alg.sha(source_state_path),
            "quality_gate_evidence": [],
            "rankings_path": rankings_path,
            "rankings_sha256": alg.sha(rankings_path),
            "rankings": copy.deepcopy(rankings),
            "valid_batch_path": outer_batches[1],
            "valid_batch_sha256": alg.sha(outer_batches[1]),
            "valid_batch_rows": copy.deepcopy(outer_rows[1]),
            "invalid_batch_path": outer_batches[2],
            "invalid_batch_sha256": alg.sha(outer_batches[2]),
            "invalid_batch": _verified(core, outer_batches[2]),
        }

    def replay_outer_tail(**kwargs: Any):
        output = Path(kwargs["output"])
        match = re.search(r"outer_001/top5_batch_([3-5])$", str(output))
        if match is None:
            return original_measure(**kwargs)
        rank = int(match.group(1))
        core._impl["_mirror_batch"](outer_batches[rank], output / "batch_result.json")
        state = kwargs["state"]
        state["stage"] = f"ALGORITHM_B_OUTER_001_RANK_{rank}_RESTORED_VERIFIED"
        alg.atomic(Path(kwargs["state_path"]), state)
        return copy.deepcopy(outer_rows[rank]), state

    continuation = {
        "kind": "cpu_b_post_measurement_score_interface_continuation_v1",
        "source_run_root": str(SOURCE),
        "baseline_reused": True,
        "calibration_ranks_reused": list(range(1, 16)),
        "outer1_candidate_ranks_reused": list(range(1, 6)),
        "candidate_measurements_replayed": False,
        "accepted_or_rejected_outer_credit_reused": False,
        "score_api_raw_payload_supplied": True,
    }

    def install_scene(scene: str) -> None:
        original_install_scene(scene)
        runtime._measure_batch_with_fair_coordination = replay_outer_tail

    def state_document(run_id: str) -> dict[str, Any]:
        value = original_state_document(run_id)
        value.setdefault("technical_continuation", {}).update(copy.deepcopy(continuation))
        return value

    core._impl["validate_resume_source"] = validate_resume_source
    core._impl["install_scene"] = install_scene
    core._impl["state_document"] = state_document
    core._impl["RESUME_CALIBRATION_WAVES"] = 15
    core._impl["REUSE_VERIFIED_BASELINE"] = True
    core._impl["REUSE_HISTORICAL_OUTER_EVIDENCE"] = True
    core._impl["RESUME_OUTER_ATTEMPT"] = 1
    return int(core._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
