#!/usr/bin/env python3
"""Freeze the v145-aware JOB-to-XP4 handoff contract."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "config/formal/frozen_mysql_algorithm_a_external_diagnostic_to_xp4_20260824_v9/transition_contract.json"
CONFIG = ROOT / "config/formal/frozen_mysql_algorithm_a_external_diagnostic_to_xp4_20260824_v10"
CONTRACT = CONFIG / "transition_contract.json"
CPU_RECOVERY = ROOT / "config/formal/frozen_mysql_algorithm_a_xp_cpu_w2_reset_recovery_20260824_v2/transition_contract.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def main() -> int:
    document: dict[str, Any] = json.loads(SOURCE.read_text(encoding="utf-8"))
    document.update({
        "release_version": "external_diagnostic_xp4_v10_job2_cpu_v145_fast_reset",
        "xp_cpu_run": "v145_to_a_xp_cpu_w2_reset_external_diagnostic_resume_20260824_attempt001_xp_cpu",
        "xp_cpu_cached_attempted_rank_last": 88,
        "xp_cpu_failed_wave_credited": False,
    })
    bindings = document["source_bindings"]
    replacements = {
        "xp4_monitor": ROOT / "scripts/monitor_mysql_algorithm_a_external_diagnostic_to_xp4_v8.py",
        "xp4_executor": ROOT / "scripts/execute_mysql_algorithm_a_external_diagnostic_to_xp4_v7.py",
        "current_xp_cpu_w1_recovery_contract": CPU_RECOVERY,
    }
    for name, path in replacements.items():
        bindings[name] = {"path": relative(path), "sha256": sha(path)}
    CONFIG.mkdir(parents=True, exist_ok=True)
    temporary = CONTRACT.with_name(f".{CONTRACT.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, CONTRACT)
    print(json.dumps({"contract": str(CONTRACT), "sha256": sha(CONTRACT)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
