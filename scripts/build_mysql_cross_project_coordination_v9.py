#!/usr/bin/env python3
"""Bind the fair R0 waiter and final controller sources into coordination v9."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260813_v8/coordination_contract.json"
NEW = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260813_v9/coordination_contract.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if NEW.exists():
        raise SystemExit(f"refusing to replace: {NEW}")
    value = json.loads(OLD.read_text(encoding="utf-8"))
    value["predecessor"] = {"path": str(OLD.relative_to(ROOT)), "sha256": sha(OLD)}
    value["successor_reason"] = "fair_kernel_queue_for_v18_r0_prevents_mysql_starvation_without_stopping_postgresql"
    bindings = value["source_bindings"]
    bindings["v18_r0_fair_waiter_path"] = "scripts/wait_and_run_mysql_four_scene_r0_v18.py"
    bindings["v18_r0_fair_waiter_sha256"] = sha(ROOT / bindings["v18_r0_fair_waiter_path"])
    for key, raw in list(bindings.items()):
        if key.endswith("_path") and key[:-5] + "_sha256" in bindings:
            bindings[key[:-5] + "_sha256"] = sha((ROOT / str(raw)).resolve())
    NEW.parent.mkdir(parents=True, exist_ok=False)
    NEW.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    NEW.with_name(NEW.name + ".sha256").write_text(f"{sha(NEW)}  {NEW.name}\n")
    print(json.dumps({"path": str(NEW), "sha256": sha(NEW)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
