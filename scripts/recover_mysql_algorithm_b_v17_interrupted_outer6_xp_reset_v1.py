#!/usr/bin/env python3
"""Recover all four exact stopped XP planes after the v17 clean-boundary stop."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_data_planes_v8 as reset_v8
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v32 as c32
from scripts import recover_mysql_algorithm_b_v12_partial_xp_reset_v1 as prior


HISTORY = ROOT / "history/mysql_four_scene_20260809"
SOURCE = (
    HISTORY
    / "v17_formal_parallel221_outer5_scene_independent_resume_20260818_attempt012"
)
PREPARED = (
    HISTORY / "v13_b_partial_xp_reset_recovery_20260818_attempt006"
    / "prepared_binding.json"
)
ACCOUNTS = PREPARED.with_name("benchmark_accounts_binding.json")
FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18"
    / "four_scene_effective_resource_successor.json"
)
XP_IDS = {"xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"}
TOKEN = "algorithm_b_v17_interrupted_outer6_reset_recovery_v1"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def load(path: Path) -> dict[str, Any]:
    return prior.load(path)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    c32.runtime.alg.atomic(path, dict(value))


def validate_sources() -> tuple[
    dict[str, Any], dict[str, Any], dict[str, Any],
    dict[str, dict[str, Any]], dict[str, dict[str, Any]],
]:
    state = load(SOURCE / "run_state.json")
    prepared = load(PREPARED)
    accounts = load(ACCOUNTS)
    freeze = load(FREEZE)
    prepared_workers = {
        str(row["worker_id"]): dict(row) for row in prepared.get("workers") or []
    }
    account_workers = {
        str(row["worker_id"]): dict(row) for row in accounts.get("workers") or []
    }
    frozen_workers = {
        str(row["worker_id"]): dict(row) for row in freeze.get("workers") or []
    }
    decisions = sorted((SOURCE / "outer_005").glob("*.decision.json"))
    outer6_batch = SOURCE / "outer_006/top5_batch_1/batch_result.json"
    drain = (
        SOURCE / "xp_resets/algorithm_b_outer_006_rank_1"
        / "pre_reset_session_drain.json"
    )
    if (
        state.get("status") != "RUNNING"
        or int(state.get("outer_attempt") or 0) != 5
        or state.get("stage") != "ALGORITHM_B_OUTER_006_RANK_1_RESETTING_XP"
        or state.get("accepted_rounds") != {
            "job_memory": 1, "tpch_sf4_memory": 0,
            "hybench_xp_cpu": 1, "hybench_xp_io": 0,
        }
        or state.get("rejected_outer_attempts") != {
            "job_memory": 4, "tpch_sf4_memory": 5,
            "hybench_xp_cpu": 4, "hybench_xp_io": 5,
        }
        or len(decisions) != 4
        or outer6_batch.exists()
        or load(drain).get("status") != "PASS"
        or prepared.get("status") != "PREPARED"
        or prepared.get("freeze_sha256") != prior.sha(FREEZE)
        or accounts.get("status") != "PASS"
        or accounts.get("prepared_binding_sha256") != prior.sha(PREPARED)
        or set(prepared_workers) != set(frozen_workers)
        or set(account_workers) != set(frozen_workers)
    ):
        raise RuntimeError("v17 interrupted Outer-6 recovery boundary differs")

    workers = {
        worker_id: {
            **frozen_workers[worker_id],
            **prepared_workers[worker_id],
            **account_workers[worker_id],
        }
        for worker_id in frozen_workers
    }
    stopped_evidence: dict[str, dict[str, Any]] = {}
    for worker_id in sorted(workers):
        inspected = prior.inspect(str(workers[worker_id]["container"]))
        state_row = inspected.get("State") or {}
        labels = (inspected.get("Config") or {}).get("Labels") or {}
        if inspected.get("Id") != workers[worker_id].get("container_id"):
            raise RuntimeError(f"{worker_id} prepared identity differs")
        if worker_id in XP_IDS:
            if (
                state_row.get("Status") != "exited"
                or state_row.get("Running") is not False
                or int(state_row.get("Pid") or 0) != 0
                or labels.get("ra.owner") != "mysql_four_scene_v5"
                or labels.get("ra.worker_id") != worker_id
            ):
                raise RuntimeError(f"{worker_id} exact stopped proof differs")
            stopped_evidence[worker_id] = {
                "container": workers[worker_id]["container"],
                "container_id": inspected["Id"],
                "status": state_row.get("Status"),
                "pid": state_row.get("Pid"),
                "exit_code": state_row.get("ExitCode"),
                "oom_killed": state_row.get("OOMKilled"),
                "finished_at": state_row.get("FinishedAt"),
                "identity_labels_verified": True,
                "no_live_mysqld_proved_by_pid_zero": True,
            }
        elif state_row.get("Running") is not True:
            raise RuntimeError(f"{worker_id} non-XP live identity differs")
    return prepared, accounts, freeze, workers, stopped_evidence


def build_bindings(
    *, output: Path, prepared: dict[str, Any], accounts: dict[str, Any],
    freeze: dict[str, Any], workers: dict[str, dict[str, Any]],
    stopped_evidence: dict[str, dict[str, Any]],
) -> tuple[Path, Path, Path]:
    reset_binding = output / "xp_resets" / TOKEN / "reset_binding.json"
    reset_document = load(reset_binding)
    if (
        reset_document.get("status") != "PASS"
        or set(reset_document.get("reset_worker_ids") or []) != XP_IDS
    ):
        raise RuntimeError("v17 recovered XP reset binding differs")

    recovery_path = output / "recovered_xp_binding.json"
    atomic(recovery_path, {
        "schema_version": "mysql_algorithm_b_v17_interrupted_outer6_xp_recovery_v1",
        "status": "PASS", "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "source_run_root": str(SOURCE),
        "source_state_path": str(SOURCE / "run_state.json"),
        "source_state_sha256": prior.sha(SOURCE / "run_state.json"),
        "source_completed_outer_attempt": 5,
        "source_outer6_real_replay_started": False,
        "source_outer6_partial_rankings_reusable": False,
        "predecessor_prepared": {"path": str(PREPARED), "sha256": prior.sha(PREPARED)},
        "predecessor_accounts": {"path": str(ACCOUNTS), "sha256": prior.sha(ACCOUNTS)},
        "freeze_path": str(FREEZE), "freeze_sha256": prior.sha(FREEZE),
        "point_reset_binding": {"path": str(reset_binding),
                                "sha256": prior.sha(reset_binding)},
        "stopped_predecessor_evidence": stopped_evidence,
        "workers": [workers[worker_id] for worker_id in sorted(XP_IDS)],
        "reset_worker_ids": sorted(XP_IDS),
        "job_tpch_container_mutation": False,
        "external_container_mutation": False,
        "workload_started_during_recovery": False,
        "formal_state_credit_increment": 0,
        "accepted_rounds_mutation": False,
        "rejected_rounds_mutation": False,
        "interval_convergence_mutation": False,
        "recovery_source_path": str(Path(__file__).resolve()),
        "recovery_source_sha256": prior.sha(Path(__file__).resolve()),
    })

    prepared_path = output / "prepared_binding.json"
    prepared_document = copy.deepcopy(prepared)
    prepared_document.update({
        "schema_version": "mysql_algorithm_b_v18_post_interrupted_reset_prepared_v1",
        "status": "PREPARED", "created_at": now(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE), "freeze_sha256": prior.sha(FREEZE),
        "predecessor_prepared_path": str(PREPARED),
        "predecessor_prepared_sha256": prior.sha(PREPARED),
        "xp_reset_prepared_path": str(recovery_path),
        "xp_reset_prepared_sha256": prior.sha(recovery_path),
        "workers": [workers[str(row["worker_id"])]
                    for row in prepared.get("workers") or []],
        "workload_started": False, "fresh_r0_complete": False,
        "formal_outer_started": False,
        "accepted_rounds": {scene: 0 for scene in freeze["active_scenes"]},
        "resource_envelope_change": False,
    })
    atomic(prepared_path, prepared_document)

    accounts_path = output / "benchmark_accounts_binding.json"
    account_document = copy.deepcopy(accounts)
    account_document.update({
        "schema_version": "mysql_algorithm_b_v18_post_interrupted_reset_accounts_v1",
        "status": "PASS", "created_at": now(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE), "freeze_sha256": prior.sha(FREEZE),
        "prepared_binding_path": str(prepared_path),
        "prepared_binding_sha256": prior.sha(prepared_path),
        "workers": [workers[str(row["worker_id"])]
                    for row in accounts.get("workers") or []],
        "passwords_emitted": False, "workload_started": False,
        "fresh_r0_complete": False, "formal_outer_started": False,
        "accepted_rounds": {scene: 0 for scene in freeze["active_scenes"]},
        "failures": [], "source_path": str(Path(__file__).resolve()),
        "source_sha256": prior.sha(Path(__file__).resolve()),
    })
    atomic(accounts_path, account_document)
    return recovery_path, prepared_path, accounts_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("safe run id required")
    output = HISTORY / args.run_id
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh recovery output required: {output}")

    prepared, accounts, freeze, workers, stopped = validate_sources()
    reset_v8.install()
    c32.runtime.alg.reset = reset_v8._v6
    c32.runtime.alg.FREEZE = FREEZE
    c32.runtime.alg.classify_xp_mysql_liveness = prior.classify_stopped_or_live
    c32.reset_xp(freeze, workers, output, TOKEN, worker_ids=set(XP_IDS))

    for worker_id in sorted(XP_IDS):
        live = prior.inspect(str(workers[worker_id]["container"]))
        if (
            live.get("Id") != workers[worker_id].get("container_id")
            or (live.get("State") or {}).get("Running") is not True
            or c32.runtime.alg.xp_active_client_session_count(workers[worker_id]) != 0
        ):
            raise RuntimeError(f"{worker_id} recovered live readback differs")
    recovery, prepared_path, accounts_path = build_bindings(
        output=output, prepared=prepared, accounts=accounts, freeze=freeze,
        workers=workers, stopped_evidence=stopped,
    )
    print(json.dumps({
        "status": "PASS", "recovery_binding": str(recovery),
        "prepared_binding": str(prepared_path),
        "benchmark_accounts_binding": str(accounts_path),
        "workers": {worker_id: workers[worker_id]["container_id"]
                    for worker_id in sorted(XP_IDS)},
    }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
