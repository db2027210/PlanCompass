#!/usr/bin/env python3
"""Freeze the v2 XP/IO point replay with its current C49 adapter."""

from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import build_mysql_xp_io_device_fix_point_replay_v1 as predecessor


OUT = ROOT / "config/formal/frozen_mysql_xp_io_device_fix_point_replay_20260829_v2"
CONTRACT = OUT / "point_replay_contract.json"
C49_ADAPTER = ROOT / "config/formal/frozen_mysql_algorithm_a_c49_measurement_adapter_20260829_v3/algorithm_c_measurement_adapter_contract.json"


def main() -> int:
    if OUT.exists() or OUT.is_symlink():
        raise RuntimeError(f"fresh point-replay contract required: {OUT}")
    predecessor.OUT = OUT
    predecessor.CONTRACT = CONTRACT
    predecessor.main()
    document = json.loads(CONTRACT.read_text(encoding="utf-8"))
    document["schema_version"] = "mysql_xp_io_mixed_device_point_replay_contract_v2"
    document["predecessor_contract"] = {
        "path": "config/formal/frozen_mysql_xp_io_device_fix_point_replay_20260829_v1/point_replay_contract.json",
        "sha256": predecessor.sha(ROOT / "config/formal/frozen_mysql_xp_io_device_fix_point_replay_20260829_v1/point_replay_contract.json"),
    }
    document["source_bindings"]["c49_measurement_adapter"] = {
        "path": str(C49_ADAPTER.relative_to(ROOT)),
        "sha256": predecessor.sha(C49_ADAPTER),
    }
    predecessor.atomic(CONTRACT, document)
    print(json.dumps({"status": "PASS", "contract": str(CONTRACT), "sha256": predecessor.sha(CONTRACT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
