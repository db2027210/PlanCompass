#!/usr/bin/env python3
"""Publish the final source-bound pruning-v2 optimization v4 contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260813_v3/optimization_contract.json"
NEW = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260813_v4/optimization_contract.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if NEW.exists():
        raise SystemExit(f"refusing to replace: {NEW}")
    value = json.loads(OLD.read_text(encoding="utf-8"))
    value["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v4"
    value["predecessor"] = {"path": str(OLD.relative_to(ROOT)), "sha256": sha(OLD)}
    value["successor_reason"] = "bind_final_tier_rank_and_qps_nondecline_counterexample_pruning_v2_source"
    bindings = value["bindings"]
    for key, raw in list(bindings.items()):
        if key.endswith("_path") and key[:-5] + "_sha256" in bindings:
            path = (ROOT / str(raw)).resolve()
            bindings[key[:-5] + "_sha256"] = sha(path)
    NEW.parent.mkdir(parents=True, exist_ok=False)
    NEW.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    NEW.with_name(NEW.name + ".sha256").write_text(f"{sha(NEW)}  {NEW.name}\n")
    print(json.dumps({"path": str(NEW), "sha256": sha(NEW)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
