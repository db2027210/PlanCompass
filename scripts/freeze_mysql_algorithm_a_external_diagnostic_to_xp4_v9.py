#!/usr/bin/env python3
"""Freeze the v139-aware parallel-reset XP4 handoff."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "config/formal/frozen_mysql_algorithm_a_external_diagnostic_to_xp4_20260824_v8/transition_contract.json"
CONFIG = ROOT / "config/formal/frozen_mysql_algorithm_a_external_diagnostic_to_xp4_20260824_v9"
CONTRACT = CONFIG / "transition_contract.json"
JOB_CONTRACT = ROOT / "config/formal/frozen_mysql_algorithm_a_job_two_worker_canonical_resume_20260824_v1/resume_contract.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def main() -> int:
    document: dict[str, Any] = json.loads(SOURCE.read_text(encoding="utf-8"))
    document.update({
        "release_version": "external_diagnostic_xp4_v9_job2_fast_reset",
        "job_run": "v139_to_a_job2_canonical_resume_20260824_attempt001_job",
        "job_physical_replay_parallelism_before_handoff": 2,
        "job_round_index_canonicalized": True,
        "job_four_worker_failed_wave_valid_candidates": 0,
        "job_four_worker_failed_wave_credited": False,
        "current_job4_resume_contract": {
            "path": relative(JOB_CONTRACT), "sha256": sha(JOB_CONTRACT),
        },
    })
    bindings = document["source_bindings"]
    replacements = {
        "xp4_monitor": ROOT / "scripts/monitor_mysql_algorithm_a_external_diagnostic_to_xp4_v7.py",
        "xp4_executor": ROOT / "scripts/execute_mysql_algorithm_a_external_diagnostic_to_xp4_v6.py",
        "current_job4_resume_contract": JOB_CONTRACT,
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
