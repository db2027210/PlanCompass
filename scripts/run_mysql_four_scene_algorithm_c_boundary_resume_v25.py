#!/usr/bin/env python3
"""Resume C with companion-aware partial Top-5 inheritance.

Completed batches contain one measurement for every physical scene.  Once a
scene has converged, that row is an incumbent companion rather than a decision
candidate.  The predecessor partial validator incorrectly required the batch
cardinality to equal only the active decision scene count.  This release keeps
the immutable full batch binding and exposes only active rows to the inherited
decision-evaluation reconstruction.
"""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v24 as v24
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v10 as v10


runtime = v24.v22.runtime
ORIGINAL_PARTIAL_VALIDATOR = v10.v4.validate_formal_partial_continuation


def validate_formal_partial_continuation(**kwargs: Any) -> dict[str, Any]:
    source_root = Path(kwargs["source_root"]).resolve()
    checkpoint = kwargs["checkpoint"]
    reconstructed = kwargs["reconstructed"]
    start_rank = int(kwargs["start_rank"])
    expected_outer = int(reconstructed["outer_attempt"]) + 1
    ranking_path = source_root / f"outer_{expected_outer:03d}/predicted_rankings.json"
    ranking = runtime.alg.load(ranking_path)
    active = set(ranking.get("active_decision_scenes") or [])
    if not active:
        raise RuntimeError("partial continuation requires active decision scenes")
    batch_paths = {
        (
            source_root / f"outer_{expected_outer:03d}"
            / f"top5_batch_{rank}/batch_result.json"
        ).resolve()
        for rank in range(1, start_rank)
    }
    original_load = runtime.alg.load

    def companion_aware_load(path: Path) -> dict[str, Any]:
        document = original_load(path)
        resolved = Path(path).resolve()
        if resolved not in batch_paths or document.get("status") != "PASS":
            return document
        measurements = document.get("measurements")
        if not isinstance(measurements, dict):
            return document
        observed_scenes = {str(row.get("scene")) for row in measurements.values()}
        if not active <= observed_scenes:
            raise RuntimeError("partial completed batch lacks an active scene")
        filtered = copy.deepcopy(document)
        filtered["measurements"] = {
            worker_id: copy.deepcopy(row)
            for worker_id, row in measurements.items()
            if str(row.get("scene")) in active
        }
        filtered["partial_continuation_companion_audit"] = {
            "full_batch_path": str(resolved),
            "full_batch_sha256": runtime.alg.sha(resolved),
            "active_decision_scenes": sorted(active),
            "companion_scenes": sorted(observed_scenes - active),
            "full_physical_measurement_count": len(measurements),
            "decision_measurement_count": len(filtered["measurements"]),
            "full_batch_evidence_mutated": False,
        }
        return filtered

    try:
        runtime.alg.load = companion_aware_load
        result = ORIGINAL_PARTIAL_VALIDATOR(**kwargs)
    finally:
        runtime.alg.load = original_load
    result["companion_aware_partial_inheritance"] = {
        "release_version": "v25",
        "source_path": str(Path(__file__).resolve()),
        "source_sha256": runtime.alg.sha(Path(__file__).resolve()),
        "active_decision_scenes": sorted(active),
        "completed_full_batch_bindings_unchanged": True,
        "companion_rows_used_for_decision_evaluations": False,
        "companion_full_workload_evidence_preserved": True,
    }
    return result


def main() -> int:
    # v10 is the last wrapper before the shared runtime and reinstalls v4's
    # validator immediately before entering it.  Patch that exact handoff, not
    # only the runtime global that v10 would otherwise overwrite.
    v10.v4.validate_formal_partial_continuation = (
        validate_formal_partial_continuation
    )
    runtime._validate_formal_partial_continuation = (
        validate_formal_partial_continuation
    )
    return v24.main()


if __name__ == "__main__":
    raise SystemExit(main())
