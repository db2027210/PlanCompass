#!/usr/bin/env python3
"""Publish an empty replay quarantine for the clean v18 pruning-v2 campaign."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FREEZE = ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18/four_scene_effective_resource_successor.json"
CHECKPOINT = ROOT / "history/mysql_four_scene_20260809/algorithm_c_v18_pruning_v2_checkpoint_v2_20260813T1810p0800/prepared_binding.json"
PRUNING = ROOT / "config/formal/mysql_algorithm_c_plan_replay_pruning_20260813_v2/contract.json"
OUTPUT = ROOT / "history/mysql_four_scene_20260809/algorithm_c_v18_replay_quarantine_v6_20260813T1800p0800/replay_quarantine.json"
SCENES = ("job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bound(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": sha(path)}


def main() -> int:
    if OUTPUT.exists() or OUTPUT.parent.exists():
        raise SystemExit(f"fresh output directory required: {OUTPUT.parent}")
    checkpoint = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    if (
        checkpoint.get("status") != "PREPARED"
        or checkpoint.get("freeze_sha256") != sha(FREEZE)
        or checkpoint.get("completed_outer_attempts") != 4
        or set((checkpoint.get("reconstructed_state") or {}).get("accepted_rounds") or {}) != set(SCENES)
    ):
        raise SystemExit("v18 pruning-v2 checkpoint authority differs")
    empty = {scene: [] for scene in SCENES}
    zeros = {scene: 0 for scene in SCENES}
    document = {
        "schema_version": "mysql_algorithm_c_replay_quarantine_v6",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_sha256": sha(FREEZE),
        "resume_checkpoint_outer_attempt": checkpoint["completed_outer_attempts"],
        "formal_state_mutation": False,
        "accepted_rounds_mutation": False,
        "rejected_rounds_mutation": False,
        "interval_convergence_mutation": False,
        "quarantined_counts_by_scene": zeros,
        "soft_replayed_counts_by_scene": dict(zeros),
        "permanent_invalid_counts_by_scene": dict(zeros),
        "quarantined_param_keys_by_scene": empty,
        "soft_replayed_param_keys_by_scene": {scene: [] for scene in SCENES},
        "permanent_invalid_param_keys_by_scene": {scene: [] for scene in SCENES},
        "rows_by_scene": {scene: [] for scene in SCENES},
        "cycle_reset_may_clear_soft_replayed": True,
        "cycle_reset_may_clear_permanent_invalid": False,
        "initialization": "empty_new_v18_pruning_v2_campaign",
        "pre_v18_timeout_and_replay_evidence_imported": False,
        "evidence": {
            "checkpoint": bound(CHECKPOINT),
            "resource_freeze": bound(FREEZE),
            "pruning_contract": bound(PRUNING),
        },
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": sha(Path(__file__).resolve()),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=False)
    OUTPUT.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n"
    )
    print(json.dumps({"path": str(OUTPUT), "sha256": sha(OUTPUT), "counts": zeros}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
