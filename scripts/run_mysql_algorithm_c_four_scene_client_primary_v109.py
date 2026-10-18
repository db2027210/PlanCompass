#!/usr/bin/env python3
"""Resume one scene with current PG-C logic and legacy cap retired."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v12 as checkpoint_v12
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v101 as resume_v101
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v102 as release_v102
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v103 as release_v103
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v104 as release_v104
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v105 as release_v105
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v106 as release_v106
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v107 as release_v107
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v82 as cap_retirement_v82
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v83 as cap_compat_v83
from scripts import run_mysql_algorithm_c_async_scene_v49 as async_v49
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v20 as boundary_v20
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v21 as boundary_v21
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v23 as boundary_v23
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v29 as boundary_v29
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v32 as boundary_v32
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v58 as runtime_v58


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_legacy_cap_recovery_20260929_v213/"
    "legacy_cap_recovery_contract.json"
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
        value.get("release_version") != "v213-legacy-accepted-cap-recovery"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("normal_stop_uses_actual_interval_width") is not True
        or value.get("accepted_round_target") is not None
        or value.get("legacy_post_target_cap_retired") is not True
        or value.get("post_search_stability_confirmation") is not False
        or value.get("current_pg_runtime_release") != "v58"
        or value.get("dynamic_gate_release") != "v210"
        or value.get("workload_or_candidate_policy_changed") is not False
        or value.get("resource_envelope_changed") is not False
    ):
        raise RuntimeError("Algorithm C v213 recovery contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = Path(str(raw.get("path") or ""))
        if not path.is_absolute():
            path = ROOT / path
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v213 source differs: {name}: {path}")
    return value


def actual_width_state_document(**kwargs: Any) -> dict[str, Any]:
    document = boundary_v29.ORIGINAL_STATE_DOCUMENT(**kwargs)
    document.pop("post_target_stability_confirmation_release", None)
    document.pop("post_target_stability_confirmation_release_v29", None)
    document.pop("durable_post_target_confirmation_release", None)
    document["post_target_stability_confirmation_counts"] = {}
    document.setdefault("attempt_budget", {}).pop(
        "post_target_stability_confirmation", None
    )
    document["actual_width_credit_release"] = {
        "release_version": "v213",
        "source_path": str(Path(__file__).resolve()),
        "source_sha256": sha(Path(__file__).resolve()),
        "normal_stop_uses_actual_interval_width": True,
        "accepted_round_target": None,
        "accepted_improvements_remain_credited_after_round_10": True,
        "legacy_post_target_confirmation_cap_retired": True,
        "post_search_stability_confirmation": False,
        "candidate_scoring_workload_and_interval_rules_changed": False,
    }
    return document


def main() -> int:
    validate_contract()
    for release in (
        release_v102, release_v103, release_v104,
        release_v105, release_v106, release_v107,
    ):
        release.validate_contract()
    if "--fresh-search" in sys.argv[1:]:
        raise RuntimeError("v213 recovery must preserve completed fresh-search state")

    old_resume_runtime = resume_v101.runtime
    old_resume_checkpoint = resume_v101.checkpoint_v10
    old_inventory = release_v106.coordination_v27._inventory
    old_gate = release_v106.coordination_v27.rolling_quiet_gate
    old_v28_inventory = release_v106.coordination_v28._inventory
    old_v28_gate = release_v106.coordination_v28.rolling_quiet_gate
    old_prepare = release_v106.reset_v4._ORIGINAL_PREPARE
    old_probe = release_v106.users._run_worker
    old_v21_original = boundary_v21.ORIGINAL_STATE_DOCUMENT
    old_rehydrate = boundary_v23._rehydrate
    old_v29_state = boundary_v29.state_document
    old_v29_validate = boundary_v29.validate_three_confirmation_release
    old_v32_base = boundary_v32.ORIGINAL_V29_STATE_DOCUMENT
    old_async_base = async_v49.ORIGINAL_V27_STATE_DOCUMENT

    resume_v101.runtime = runtime_v58
    resume_v101.checkpoint_v10 = checkpoint_v12
    release_v106.coordination_v28._inventory = release_v107._inventory_trampoline
    release_v106.coordination_v28.rolling_quiet_gate = release_v107._gate_trampoline
    release_v106.coordination_v28.install()
    release_v106.reset_v4._ORIGINAL_PREPARE = release_v106.reset_affinity.prepare_fresh_xp
    release_v106.users._run_worker = release_v106.probe_retry.wrap(old_probe)
    boundary_v21.ORIGINAL_STATE_DOCUMENT = boundary_v20.ORIGINAL_STATE_DOCUMENT
    boundary_v23._rehydrate = lambda document: document
    boundary_v29.state_document = actual_width_state_document
    boundary_v29.validate_three_confirmation_release = lambda: None
    boundary_v32.ORIGINAL_V29_STATE_DOCUMENT = actual_width_state_document
    async_v49.ORIGINAL_V27_STATE_DOCUMENT = cap_compat_v83.actual_width_v27_state_document
    try:
        return int(resume_v101.main())
    finally:
        async_v49.ORIGINAL_V27_STATE_DOCUMENT = old_async_base
        boundary_v32.ORIGINAL_V29_STATE_DOCUMENT = old_v32_base
        boundary_v29.validate_three_confirmation_release = old_v29_validate
        boundary_v29.state_document = old_v29_state
        boundary_v23._rehydrate = old_rehydrate
        boundary_v21.ORIGINAL_STATE_DOCUMENT = old_v21_original
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
