#!/usr/bin/env python3
"""Rebind the empty v18 quarantine to the final checkpoint v3."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "history/mysql_four_scene_20260809/algorithm_c_v18_replay_quarantine_v6_20260813T1800p0800/replay_quarantine.json"
CHECKPOINT = ROOT / "history/mysql_four_scene_20260809/algorithm_c_v18_pruning_v2_checkpoint_v3_20260813T1830p0800/prepared_binding.json"
OUTPUT = ROOT / "history/mysql_four_scene_20260809/algorithm_c_v18_replay_quarantine_v6_final_20260813T1835p0800/replay_quarantine.json"

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main() -> int:
    if OUTPUT.exists() or OUTPUT.parent.exists():
        raise SystemExit(f"fresh output required: {OUTPUT.parent}")
    value = copy.deepcopy(json.loads(SOURCE.read_text(encoding="utf-8")))
    value["evidence"]["checkpoint"] = {"path": str(CHECKPOINT.resolve()), "sha256": sha(CHECKPOINT)}
    value["evidence"]["source_quarantine"] = {"path": str(SOURCE.resolve()), "sha256": sha(SOURCE)}
    value["builder_source_path"] = str(Path(__file__).resolve())
    value["builder_source_sha256"] = sha(Path(__file__).resolve())
    OUTPUT.parent.mkdir(parents=True, exist_ok=False)
    OUTPUT.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n")
    print(json.dumps({"path": str(OUTPUT), "sha256": sha(OUTPUT)}))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
