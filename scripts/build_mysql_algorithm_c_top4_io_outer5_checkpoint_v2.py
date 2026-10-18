#!/usr/bin/env python3
"""Normalize Top4 physical waves and seal the completed IO-C Outer-5."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_independent_round_boundary_checkpoint_v1 as builder


HISTORY = ROOT / "history/mysql_four_scene_20260809"
SOURCE = HISTORY / "v225_to_c_top4_io_xp4_20260826_attempt020_xp_io"
OUTPUT = HISTORY / "v249_to_c_top4_io_outer5_checkpoint_20260826"
NORMALIZED = OUTPUT / "normalized_source"


def normalized_batch(
    source_path: Path, ranks: set[int], destination: Path,
) -> None:
    source = builder.base.alg.load(source_path)
    measurements = {
        worker_id: copy.deepcopy(row)
        for worker_id, row in (source.get("measurements") or {}).items()
        if int(row.get("rank") or 0) in ranks
    }
    if (
        source.get("status") != "PASS"
        or source.get("failures") not in ([], None)
        or len(measurements) != len(ranks)
        or {int(row.get("rank") or 0) for row in measurements.values()} != ranks
        or any(
            row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            for row in measurements.values()
        )
    ):
        raise RuntimeError(f"Top4 physical wave normalization differs: {source_path}")
    document = copy.deepcopy(source)
    document["measurements"] = measurements
    document["top4_physical_wave_normalization"] = {
        "schema_version": "mysql_algorithm_c_top4_physical_wave_normalization_v1",
        "source_path": str(source_path.resolve()),
        "source_sha256": builder.base.alg.sha(source_path),
        "selected_candidate_ranks": sorted(ranks),
        "measurement_values_unchanged": True,
        "formal_measurement_credit_increment": 0,
    }
    destination.parent.mkdir(parents=True, mode=0o700)
    builder.base.alg.atomic(destination, document)


def main() -> int:
    state = builder.base.alg.load(SOURCE / "run_state.json")
    outer5 = SOURCE / "outer_005"
    outer6 = SOURCE / "outer_006"
    if (
        OUTPUT.exists() or OUTPUT.is_symlink()
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or not str(state.get("error") or "").startswith(
            "CalledProcessError: Command '['mysql',"
        )
        or state.get("active_scenes") != ["hybench_xp_io"]
        or int(state.get("outer_attempt") or 0) != 5
        or not (outer5 / "hybench_xp_io.decision.json").is_file()
        or not (outer6 / "top5_batch_1/batch_result.json").is_file()
        or (outer6 / "top5_batch_2/batch_result.json").exists()
        or list(outer6.glob("*.decision.json"))
    ):
        raise RuntimeError("v225 Top4 IO Outer-5 normalization boundary differs")

    NORMALIZED.mkdir(parents=True, mode=0o700)
    shutil.copy2(SOURCE / "run_state.json", NORMALIZED / "run_state.json")
    normalized_outer5 = NORMALIZED / "outer_005"
    normalized_outer5.mkdir()
    for name in (
        "predicted_rankings.json", "companion_audit.json",
        "hybench_xp_io.decision.json",
    ):
        shutil.copy2(outer5 / name, normalized_outer5 / name)
    source_joint = outer5 / "top5_batch_1/batch_result.json"
    normalized_batch(
        source_joint, {1, 2}, normalized_outer5 / "top5_batch_1/batch_result.json",
    )
    normalized_batch(
        source_joint, {3, 4}, normalized_outer5 / "top5_batch_2/batch_result.json",
    )
    normalized_batch(
        outer5 / "top5_batch_2/batch_result.json", {5},
        normalized_outer5 / "top5_batch_3/batch_result.json",
    )
    (NORMALIZED / "outer_006").mkdir()

    document = builder.build(NORMALIZED)
    document["top4_physical_wave_normalization"] = {
        "schema_version": "mysql_algorithm_c_top4_checkpoint_normalization_v1",
        "source_run_root": str(SOURCE),
        "source_state_sha256": builder.base.alg.sha(SOURCE / "run_state.json"),
        "normalized_source_root": str(NORMALIZED),
        "physical_candidate_batches": [[1, 2, 3, 4], [5]],
        "normalized_checkpoint_batches": [[1, 2], [3, 4], [5]],
        "candidate_measurement_values_unchanged": True,
        "accepted_or_rejected_state_credit_increment": 0,
    }
    document["builder_source_path"] = str(Path(__file__).resolve())
    document["builder_source_sha256"] = builder.base.alg.sha(Path(__file__).resolve())
    payload = copy.deepcopy(document)
    payload.pop("checkpoint_sha256", None)
    document["checkpoint_sha256"] = builder.base._canonical_sha256(payload)
    path = OUTPUT / "prepared_binding.json"
    builder.base.alg.atomic(path, document)
    builder.verifier.verify_checkpoint(path)
    print(json.dumps({
        "status": "PREPARED", "checkpoint": str(path),
        "completed_outer_attempts": 5, "scene": "hybench_xp_io",
        "outer6_preserved_candidate_ranks": [1, 2, 3, 4],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
