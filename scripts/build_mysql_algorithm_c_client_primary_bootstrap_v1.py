#!/usr/bin/env python3
"""Build the immutable v20 fresh-search checkpoint and empty quarantine."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_outer_checkpoint_v2 as checkpoint


HISTORY = ROOT / "history/mysql_four_scene_20260809"
SOURCE_CHECKPOINT = HISTORY / (
    "v301_to_four_scene_two_worker_live_authority_20260826/"
    "checkpoint/prepared_binding.json"
)
SOURCE_QUARANTINE = HISTORY / (
    "v868_to_c_cpu_a_slots_fresh_checkpoint_20260828/"
    "replay_quarantine/replay_quarantine.json"
)
FREEZE = ROOT / (
    "config/formal/frozen_mysql_four_scene_two_worker_20260903_v20/"
    "four_scene_effective_resource_successor.json"
)
OUTPUT = HISTORY / "v1185_to_c_client_primary_bootstrap_20260903"


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh bootstrap output required: {OUTPUT}")
    value = checkpoint.alg.load(SOURCE_CHECKPOINT)
    if (
        value.get("status") != "PREPARED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("formal_outer_started") is not False
        or value.get("workload_started") is not False
    ):
        raise RuntimeError("source fresh checkpoint differs")
    value["freeze_path"] = str(FREEZE)
    value["freeze_sha256"] = checkpoint.alg.sha(FREEZE)
    value["builder_source_path"] = str(Path(__file__).resolve())
    value["builder_source_sha256"] = checkpoint.alg.sha(Path(__file__).resolve())
    value["client_primary_fresh_search"] = True
    value["algorithm_a_or_bc_prior_used"] = False
    value.pop("checkpoint_sha256", None)
    value["checkpoint_sha256"] = checkpoint._canonical_sha256(value)
    checkpoint_path = OUTPUT / "checkpoint/prepared_binding.json"
    checkpoint_path.parent.mkdir(parents=True, mode=0o700)
    checkpoint.alg.atomic(checkpoint_path, value)
    checkpoint.verify_checkpoint(checkpoint_path)

    quarantine = checkpoint.alg.load(SOURCE_QUARANTINE)
    empty = {scene: [] for scene in checkpoint.alg.SCENES}
    zero = {scene: 0 for scene in checkpoint.alg.SCENES}
    quarantine.update({
        "created_at": checkpoint.alg.now(),
        "freeze_sha256": checkpoint.alg.sha(FREEZE),
        "resume_checkpoint_outer_attempt": value["completed_outer_attempts"],
        "quarantined_param_keys_by_scene": copy.deepcopy(empty),
        "rows_by_scene": copy.deepcopy(empty),
        "soft_replayed_param_keys_by_scene": copy.deepcopy(empty),
        "permanent_invalid_param_keys_by_scene": copy.deepcopy(empty),
        "quarantined_counts_by_scene": copy.deepcopy(zero),
        "soft_replayed_counts_by_scene": copy.deepcopy(zero),
        "permanent_invalid_counts_by_scene": copy.deepcopy(zero),
        "evidence": {
            "checkpoint": {
                "path": str(checkpoint_path),
                "sha256": checkpoint.alg.sha(checkpoint_path),
            }
        },
        "client_primary_fresh_search": True,
        "historical_candidate_quarantine_reused": False,
    })
    quarantine_path = OUTPUT / "replay_quarantine/replay_quarantine.json"
    quarantine_path.parent.mkdir(parents=True, mode=0o700)
    checkpoint.alg.atomic(quarantine_path, quarantine)
    print(json.dumps({
        "status": "PREPARED",
        "checkpoint": str(checkpoint_path),
        "checkpoint_sha256": checkpoint.alg.sha(checkpoint_path),
        "replay_quarantine": str(quarantine_path),
        "replay_quarantine_sha256": checkpoint.alg.sha(quarantine_path),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
