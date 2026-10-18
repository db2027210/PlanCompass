#!/usr/bin/env python3
"""Resume one v211 scene with the current PG-C runtime and isolation gates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v11 as checkpoint_v11
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v101 as resume_v101
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v102 as release_v102
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v103 as release_v103
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v104 as release_v104
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v105 as release_v105
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v106 as release_v106
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v107 as release_v107
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v58 as runtime_v58


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_tpch_cleanup_recovery_20260929_v212/"
    "tpch_cleanup_recovery_contract.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def validate_contract() -> dict[str, Any]:
    value = load(CONTRACT)
    if (
        value.get("release_version") != "v212-tpch-cleanup-timeout-recovery"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("completed_outer_attempts_preserved") != 10
        or value.get("discarded_incomplete_outer") != 11
        or value.get("discarded_incomplete_outer_credit") != 0
        or value.get("fresh_search_reset_on_resume") is not False
        or value.get("current_pg_runtime_release") != "v58"
        or value.get("dynamic_gate_release") != "v210"
        or value.get("workload_or_candidate_policy_changed") is not False
        or value.get("resource_envelope_changed") is not False
    ):
        raise RuntimeError("Algorithm C v212 recovery contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = Path(str(raw.get("path") or ""))
        if not path.is_absolute():
            path = ROOT / path
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v212 source differs: {name}: {path}")
    return value


def main() -> int:
    validate_contract()
    for release in (
        release_v102, release_v103, release_v104,
        release_v105, release_v106, release_v107,
    ):
        release.validate_contract()
    if "--fresh-search" in sys.argv[1:]:
        raise RuntimeError("v212 recovery must preserve the completed fresh-search state")

    old_resume_runtime = resume_v101.runtime
    old_resume_checkpoint = resume_v101.checkpoint_v10
    old_inventory = release_v106.coordination_v27._inventory
    old_gate = release_v106.coordination_v27.rolling_quiet_gate
    old_v28_inventory = release_v106.coordination_v28._inventory
    old_v28_gate = release_v106.coordination_v28.rolling_quiet_gate
    old_prepare = release_v106.reset_v4._ORIGINAL_PREPARE
    old_probe = release_v106.users._run_worker

    resume_v101.runtime = runtime_v58
    resume_v101.checkpoint_v10 = checkpoint_v11
    release_v106.coordination_v28._inventory = release_v107._inventory_trampoline
    release_v106.coordination_v28.rolling_quiet_gate = release_v107._gate_trampoline
    release_v106.coordination_v28.install()
    release_v106.reset_v4._ORIGINAL_PREPARE = release_v106.reset_affinity.prepare_fresh_xp
    release_v106.users._run_worker = release_v106.probe_retry.wrap(old_probe)
    try:
        return int(resume_v101.main())
    finally:
        release_v106.users._run_worker = old_probe
        release_v106.reset_v4._ORIGINAL_PREPARE = old_prepare
        release_v106.coordination_v28.rolling_quiet_gate = old_v28_gate
        release_v106.coordination_v28._inventory = old_v28_inventory
        release_v106.coordination_v27.rolling_quiet_gate = old_gate
        release_v106.coordination_v27._inventory = old_inventory
        resume_v101.checkpoint_v10 = old_resume_checkpoint
        resume_v101.runtime = old_resume_runtime


if __name__ == "__main__":
    raise SystemExit(main())

