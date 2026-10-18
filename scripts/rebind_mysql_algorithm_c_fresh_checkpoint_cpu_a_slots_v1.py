#!/usr/bin/env python3
"""Rebind the empty fresh-search C checkpoint to the CPU-A slot freeze."""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_outer_checkpoint_v2 as checkpoint


SOURCE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v301_to_four_scene_two_worker_live_authority_20260826/checkpoint/prepared_binding.json"
)
FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_cpu_a_slots_two_worker_20260828_v2"
    / "four_scene_effective_resource_successor.json"
)
OUTPUT = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v868_to_c_cpu_a_slots_fresh_checkpoint_20260828/checkpoint/prepared_binding.json"
)


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh output required: {OUTPUT}")
    value = json.loads(SOURCE.read_text(encoding="utf-8"))
    state = dict(value.get("reconstructed_state") or {})
    if (
        value.get("status") != "PREPARED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("formal_outer_started") is not False
    ):
        raise RuntimeError("source fresh checkpoint boundary differs")
    value["freeze_path"] = str(FREEZE)
    value["freeze_sha256"] = checkpoint.alg.sha(FREEZE)
    value["builder_source_path"] = str(Path(__file__).resolve())
    value["builder_source_sha256"] = checkpoint.alg.sha(Path(__file__).resolve())
    value.pop("checkpoint_sha256", None)
    value["checkpoint_sha256"] = checkpoint._canonical_sha256(value)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    checkpoint.alg.atomic(OUTPUT, value)
    checkpoint.verify_checkpoint(OUTPUT)
    print(json.dumps({"status": "PREPARED", "checkpoint": str(OUTPUT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
