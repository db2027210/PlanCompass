#!/usr/bin/env python3
"""Add an empty, content-bound pruning-v2 history to the v18 checkpoint."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_outer_checkpoint_v2 as checkpointmod
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg


SOURCE = ROOT / "history/mysql_four_scene_20260809/algorithm_c_v18_transition_checkpoint_20260813T1640p0800/prepared_binding.json"
PRUNING = ROOT / "config/formal/mysql_algorithm_c_plan_replay_pruning_20260813_v2/contract.json"
OPTIMIZATION = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260813_v5/optimization_contract.json"
OUTPUT = ROOT / "history/mysql_four_scene_20260809/algorithm_c_v18_pruning_v2_checkpoint_20260813T1730p0800/prepared_binding.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bound(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": sha(path)}


def main() -> int:
    if OUTPUT.exists():
        raise SystemExit(f"refusing to replace: {OUTPUT}")
    source = checkpointmod.verify_checkpoint(SOURCE)
    value = copy.deepcopy(source)
    value.pop("checkpoint_sha256", None)
    value["source_run_root"] = str(OUTPUT.parent.resolve())
    value["reconstructed_state"]["full_plan_replay_history_v2"] = {
        scene: [] for scene in alg.SCENES
    }
    value["plan_replay_pruning_v2"] = {
        "contract": bound(PRUNING),
        "optimization_contract": bound(OPTIMIZATION),
        "history_initialization": "empty_new_campaign_no_v15_reject_import",
        "accepted_counterexamples_and_declines_recorded_only_after_v2_activation": True,
        "formal_state_mutation": False,
    }
    value["evidence"] = {
        "source_checkpoint": bound(SOURCE),
        "baseline": source["evidence"]["baseline"],
        "outers": {},
    }
    value["builder_source_path"] = str(Path(__file__).resolve())
    value["builder_source_sha256"] = sha(Path(__file__).resolve())
    value["checkpoint_sha256"] = checkpointmod._canonical_sha256(value)
    OUTPUT.parent.mkdir(parents=True, exist_ok=False)
    OUTPUT.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n")
    checkpointmod.verify_checkpoint(OUTPUT)
    print(json.dumps({"path": str(OUTPUT), "sha256": sha(OUTPUT), "accepted_rounds": value["reconstructed_state"]["accepted_rounds"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
