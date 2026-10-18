#!/usr/bin/env python3
"""Seal the completed IO-C Outer-5 boundary beneath failed v225 Outer-6."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_outer_checkpoint_v2 as checkpoint
from scripts import build_mysql_algorithm_c_outer_checkpoint_v4 as verifier


HISTORY = ROOT / "history/mysql_four_scene_20260809"
SOURCE = HISTORY / "v225_to_c_top4_io_xp4_20260826_attempt020_xp_io"
EXPECTED_ERROR_PREFIX = "CalledProcessError: Command '['mysql',"
FIELDS = (
    "incumbents", "params_by_scene", "ranges_by_scene", "active_by_scene",
    "replayed_param_keys_by_scene", "accepted_rounds",
    "rejected_outer_attempts", "acceptance_tier",
    "consecutive_failed_rounds", "interval_stable_rounds",
    "stable_intervals", "converged", "full_plan_replay_history_v2",
)


def bound(path: Path) -> dict[str, str]:
    path = path.resolve()
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular evidence required: {path}")
    return {"path": str(path), "sha256": checkpoint.alg.sha(path)}


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def build() -> dict[str, Any]:
    state_path = SOURCE / "run_state.json"
    state = checkpoint.alg.load(state_path)
    prior_path = Path(str(state.get("checkpoint_path") or "")).resolve()
    prior = verifier.verify_checkpoint(prior_path)
    outer5 = SOURCE / "outer_005"
    outer6 = SOURCE / "outer_006"
    decision_path = outer5 / "hybench_xp_io.decision.json"
    ranking_path = outer5 / "predicted_rankings.json"
    companion_path = outer5 / "companion_audit.json"
    decision = checkpoint.alg.load(decision_path)
    if (
        state.get("algorithm") != "C"
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or not str(state.get("error") or "").startswith(EXPECTED_ERROR_PREFIX)
        or state.get("active_scenes") != ["hybench_xp_io"]
        or int(state.get("outer_attempt") or 0) != 5
        or int(prior.get("completed_outer_attempts") or 0) != 4
        or decision.get("scene") != "hybench_xp_io"
        or int(decision.get("outer_attempt") or 0) != 5
        or int(decision.get("accepted_rounds") or 0) != 3
        or int(decision.get("rejected_outer_attempts") or 0) != 2
        or not outer6.is_dir()
        or list(outer6.glob("*.decision.json"))
        or not (outer6 / "top5_batch_1/batch_result.json").is_file()
        or (outer6 / "top5_batch_2/batch_result.json").exists()
    ):
        raise RuntimeError("v225 IO Outer-5 checkpoint boundary differs")

    reconstructed = {
        field: copy.deepcopy(mapping(state.get(field), f"state {field}"))
        for field in FIELDS
    }
    reconstructed["outer_attempt"] = 5
    accepted_params = [
        row.get("params") for row in decision.get("evaluations") or []
        if isinstance(row, Mapping)
        and isinstance(row.get("outer_decision"), Mapping)
        and row["outer_decision"].get("accepted") is True
    ]
    if (
        reconstructed["accepted_rounds"]["hybench_xp_io"] != 3
        or reconstructed["rejected_outer_attempts"]["hybench_xp_io"] != 2
        or reconstructed["params_by_scene"]["hybench_xp_io"] not in accepted_params
    ):
        raise RuntimeError("v225 IO Outer-5 reconstructed state differs")

    baseline = Path(str(state["formal_incumbent_baseline"]["path"])).resolve()
    batches = [
        bound(path)
        for path in sorted(outer5.rglob("batch_result.json"))
    ]
    document = {
        "schema_version": checkpoint.CONTRACT,
        "status": "PREPARED", "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "source_run_root": str(SOURCE), "completed_outer_attempts": 5,
        "workload_started": False, "fresh_r0_complete": False,
        "formal_outer_started": False,
        "freeze_path": state["freeze_path"],
        "freeze_sha256": state["freeze_sha256"],
        "resource_model_contract_path": state["resource_model_contract_path"],
        "resource_model_contract_sha256": state["resource_model_contract_sha256"],
        "reconstructed_state": reconstructed,
        "evidence": {
            "source_checkpoint": bound(prior_path),
            "source_state": bound(state_path), "baseline": bound(baseline),
            "outers": {
                "5": {
                    "predicted_rankings": bound(ranking_path),
                    "companion_audit": bound(companion_path),
                    "batches": batches,
                    "decisions": {"hybench_xp_io": bound(decision_path)},
                }
            },
        },
        "independent_scene_boundary": {
            "scene": "hybench_xp_io", "completed_outer_attempts": 5,
            "completed_rounds_preserved": True, "discarded_incomplete_outer": 6,
            "outer6_completed_candidate_ranks_preserved_separately": [1, 2, 3, 4],
        },
        "accepted_or_rejected_state_credit_increment": 0,
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": checkpoint.alg.sha(Path(__file__).resolve()),
    }
    document["checkpoint_sha256"] = checkpoint._canonical_sha256(document)
    return document


def main() -> int:
    output = HISTORY / "v248_to_c_top4_io_outer5_checkpoint_20260826"
    path = output / "prepared_binding.json"
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh checkpoint output required: {output}")
    output.mkdir(parents=True, mode=0o700)
    checkpoint.alg.atomic(path, build())
    verifier.verify_checkpoint(path)
    print(json.dumps({
        "status": "PREPARED", "checkpoint": str(path),
        "completed_outer_attempts": 5,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
