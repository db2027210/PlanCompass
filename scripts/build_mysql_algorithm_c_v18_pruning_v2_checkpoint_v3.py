#!/usr/bin/env python3
"""Rebind v18 pruning-v2 checkpoint to final R0/baseline runtime v7."""

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

SOURCE = ROOT / "history/mysql_four_scene_20260809/algorithm_c_v18_pruning_v2_checkpoint_v2_20260813T1810p0800/prepared_binding.json"
OPTIMIZATION = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260813_v7/optimization_contract.json"
OUTPUT = ROOT / "history/mysql_four_scene_20260809/algorithm_c_v18_pruning_v2_checkpoint_v3_20260813T1830p0800/prepared_binding.json"

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main() -> int:
    if OUTPUT.exists():
        raise SystemExit(f"refusing to replace: {OUTPUT}")
    value = copy.deepcopy(checkpointmod.verify_checkpoint(SOURCE))
    value.pop("checkpoint_sha256", None)
    value["source_run_root"] = str(OUTPUT.parent.resolve())
    value["plan_replay_pruning_v2"]["optimization_contract"] = {"path": str(OPTIMIZATION.resolve()), "sha256": sha(OPTIMIZATION)}
    value["evidence"] = {"source_checkpoint": {"path": str(SOURCE.resolve()), "sha256": sha(SOURCE)}, "baseline": value["evidence"]["baseline"], "outers": {}}
    value["builder_source_path"] = str(Path(__file__).resolve())
    value["builder_source_sha256"] = sha(Path(__file__).resolve())
    value["checkpoint_sha256"] = checkpointmod._canonical_sha256(value)
    OUTPUT.parent.mkdir(parents=True, exist_ok=False)
    OUTPUT.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n")
    checkpointmod.verify_checkpoint(OUTPUT)
    print(json.dumps({"path": str(OUTPUT), "sha256": sha(OUTPUT)}))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
