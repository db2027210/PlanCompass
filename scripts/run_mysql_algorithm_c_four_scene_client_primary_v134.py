#!/usr/bin/env python3
"""Resume TPC-H with server-confirmed, bounded revenue0 cleanup."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v18 as checkpoint_v18
from scripts import mysql_algorithm_b_benchmark_session_drain_v1 as session_drain
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v109 as release_v109
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v59 as runtime_v59


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_tpch_confirmed_cleanup_recovery_20260929_v243/"
    "tpch_confirmed_cleanup_recovery_contract.json"
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
        value.get("release_version") != "v243-tpch-confirmed-cleanup-recovery"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("normal_stop_uses_actual_interval_width") is not True
        or value.get("accepted_round_target") is not None
        or value.get("legacy_post_target_cap_retired") is not True
        or value.get("post_search_stability_confirmation") is not False
        or value.get("technical_timeout_state_credit") != 0
        or value.get("technical_timeout_rejected_round_increment") != 0
        or value.get("bounded_tpch_cleanup_retry") is not True
        or value.get("tpch_cleanup_attempt_limit") != 3
        or value.get("tpch_cleanup_readback_after_error") is not True
        or value.get("tpch_cleanup_lock_wait_timeout_seconds") != 5
        or value.get("current_pg_runtime_release") != "v59"
        or value.get("permanent_invalid_exclusion_all_candidate_paths") is not True
        or value.get("permanent_invalid_candidate_count") != 17
        or value.get("soft_replayed_candidate_count") != 8
        or value.get("dynamic_gate_release") != "v210"
        or value.get("workload_or_candidate_policy_changed") is not False
        or value.get("resource_envelope_changed") is not False
    ):
        raise RuntimeError("Algorithm C v243 TPC-H recovery contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = Path(str(raw.get("path") or ""))
        if not path.is_absolute():
            path = ROOT / path
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v243 source differs: {name}: {path}")
    return value


BASE_ACTUAL_WIDTH_STATE_DOCUMENT = release_v109.actual_width_state_document


def actual_width_state_document(**kwargs: Any) -> dict[str, Any]:
    document = BASE_ACTUAL_WIDTH_STATE_DOCUMENT(**kwargs)
    document["actual_width_credit_release"] = {
        **dict(document["actual_width_credit_release"]),
        "release_version": "v243",
        "source_path": str(Path(__file__).resolve()),
        "source_sha256": sha(Path(__file__).resolve()),
        "technical_timeout_candidates_are_zero_credit_exclusions": True,
        "bounded_tpch_cleanup_retry": True,
        "tpch_cleanup_attempt_limit": 3,
        "tpch_cleanup_readback_after_error": True,
        "tpch_cleanup_lock_wait_timeout_seconds": 5,
        "permanent_invalid_exclusion_all_candidate_paths": True,
        "permanent_invalid_candidate_count": 17,
        "soft_replayed_candidate_count": 8,
    }
    return document


DROP_REVENUE0 = "DROP VIEW IF EXISTS tpch.revenue0"
READ_REVENUE0_COUNT = (
    "SELECT COUNT(*) FROM information_schema.VIEWS "
    "WHERE TABLE_SCHEMA='tpch' AND TABLE_NAME='revenue0'"
)


def cleanup_with_server_confirmation(
    original_mysql: Any,
    worker: Any,
    sql: str,
    *,
    sleep: Any = time.sleep,
) -> str:
    """Run the TPC-H cleanup with bounded retries and authoritative readback."""
    if sql != DROP_REVENUE0:
        return original_mysql(worker, sql)

    last_error: BaseException | None = None
    for attempt in range(1, 4):
        try:
            return original_mysql(
                worker,
                "SET SESSION lock_wait_timeout=5; " + DROP_REVENUE0,
            )
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as error:
            last_error = error

        try:
            count = int(original_mysql(worker, READ_REVENUE0_COUNT) or 0)
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, ValueError) as error:
            last_error = error
        else:
            if count == 0:
                return ""

        if attempt < 3:
            sleep(2 if attempt == 1 else 5)

    if last_error is not None:
        raise last_error
    raise RuntimeError("TPC-H revenue0 cleanup remained present after 3 attempts")


def main() -> int:
    validate_contract()
    if "--fresh-search" in sys.argv[1:]:
        raise RuntimeError("v243 recovery must preserve completed fresh-search state")
    original_mysql = session_drain._mysql

    def cleanup_retry_mysql(worker: Any, sql: str) -> str:
        return cleanup_with_server_confirmation(original_mysql, worker, sql)

    old_validate = release_v109.validate_contract
    old_checkpoint = release_v109.checkpoint_v12
    old_runtime = release_v109.runtime_v58
    old_state_document = release_v109.actual_width_state_document
    release_v109.validate_contract = validate_contract
    release_v109.checkpoint_v12 = checkpoint_v18
    release_v109.runtime_v58 = runtime_v59
    release_v109.actual_width_state_document = actual_width_state_document
    session_drain._mysql = cleanup_retry_mysql
    try:
        return int(release_v109.main())
    finally:
        session_drain._mysql = original_mysql
        release_v109.actual_width_state_document = old_state_document
        release_v109.runtime_v58 = old_runtime
        release_v109.checkpoint_v12 = old_checkpoint
        release_v109.validate_contract = old_validate


if __name__ == "__main__":
    raise SystemExit(main())
