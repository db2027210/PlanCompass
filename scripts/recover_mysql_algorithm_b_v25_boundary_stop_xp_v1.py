#!/usr/bin/env python3
"""Recover four XP planes stopped at the v25 Outer-2 reset boundary."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import recover_mysql_algorithm_b_v17_interrupted_outer6_xp_reset_v1 as base


HISTORY = ROOT / "history/mysql_four_scene_20260809"
SOURCE = HISTORY / "v25_formal_fresh_common_plan_20260819_attempt024"
PREPARED = (
    HISTORY / "v18_b_outer6_interrupted_reset_recovery_20260818_attempt001"
    / "prepared_binding.json"
)
ACCOUNTS = PREPARED.with_name("benchmark_accounts_binding.json")
FREEZE = base.FREEZE
XP_IDS = set(base.XP_IDS)
TOKEN = "algorithm_b_v25_boundary_stop_xp_recovery_v1"
UNIT = "mysql-four-scene-algb-v25_formal_fresh_common_plan_20260819_attempt024.service"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def unit_inactive() -> bool:
    completed = subprocess.run(
        ["systemctl", "--user", "show", UNIT, "-p", "ActiveState", "-p", "MainPID"],
        text=True, capture_output=True, check=True, timeout=20,
    )
    fields = dict(
        line.split("=", 1) for line in completed.stdout.splitlines() if "=" in line
    )
    return (
        fields.get("ActiveState") in {"inactive", "failed"}
        and int(fields.get("MainPID") or 0) == 0
    )


def validate_sources() -> tuple[
    dict[str, Any], dict[str, Any], dict[str, Any],
    dict[str, dict[str, Any]], dict[str, dict[str, Any]],
]:
    state = base.load(SOURCE / "run_state.json")
    prepared = base.load(PREPARED)
    accounts = base.load(ACCOUNTS)
    freeze = base.load(FREEZE)
    drain = base.load(
        SOURCE / "xp_resets/algorithm_b_outer_002_rank_1"
        / "pre_reset_session_drain.json"
    )
    prepared_workers = {
        str(row["worker_id"]): dict(row) for row in prepared.get("workers") or []
    }
    account_workers = {
        str(row["worker_id"]): dict(row) for row in accounts.get("workers") or []
    }
    frozen_workers = {
        str(row["worker_id"]): dict(row) for row in freeze.get("workers") or []
    }
    if (
        state.get("status") != "RUNNING"
        or state.get("stage") != "ALGORITHM_B_OUTER_002_RANK_1_RESETTING_XP"
        or int(state.get("outer_attempt") or 0) != 1
        or state.get("accepted_rounds") != {
            "job_memory": 1, "tpch_sf4_memory": 0,
            "hybench_xp_cpu": 0, "hybench_xp_io": 0,
        }
        or state.get("rejected_outer_attempts") != {
            "job_memory": 0, "tpch_sf4_memory": 1,
            "hybench_xp_cpu": 1, "hybench_xp_io": 1,
        }
        or any(int(value) != 0 for value in
               (state.get("interval_stable_rounds") or {}).values())
        or (SOURCE / "outer_002/top5_batch_1/batch_result.json").exists()
        or list((SOURCE / "xp_resets/algorithm_b_outer_002_rank_1").glob("*/reset_worker.json"))
        or drain.get("status") != "PASS"
        or not unit_inactive()
        or prepared.get("status") != "PREPARED"
        or prepared.get("freeze_sha256") != base.prior.sha(FREEZE)
        or accounts.get("status") != "PASS"
        or accounts.get("prepared_binding_sha256") != base.prior.sha(PREPARED)
        or set(prepared_workers) != set(frozen_workers)
        or set(account_workers) != set(frozen_workers)
    ):
        raise RuntimeError("v25 boundary-stop XP recovery authority differs")

    workers = {
        worker_id: {
            **frozen_workers[worker_id],
            **prepared_workers[worker_id],
            **account_workers[worker_id],
        }
        for worker_id in frozen_workers
    }
    stopped: dict[str, dict[str, Any]] = {}
    for worker_id in sorted(workers):
        inspected = base.prior.inspect(str(workers[worker_id]["container"]))
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
            stopped[worker_id] = {
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
    return prepared, accounts, freeze, workers, stopped


def rewrite_bindings(
    *, recovery_path: Path, prepared_path: Path, accounts_path: Path,
) -> None:
    recovery = base.load(recovery_path)
    recovery.update({
        "schema_version": "mysql_algorithm_b_v25_boundary_stop_xp_recovery_v1",
        "created_at": now(),
        "source_completed_outer_attempt": 1,
        "source_outer2_real_replay_started": False,
        "source_outer2_partial_rankings_reusable": False,
        "source_outer6_real_replay_started": None,
        "recovery_source_path": str(Path(__file__).resolve()),
        "recovery_source_sha256": base.prior.sha(Path(__file__).resolve()),
    })
    base.atomic(recovery_path, recovery)

    prepared = base.load(prepared_path)
    prepared.update({
        "schema_version": "mysql_algorithm_b_v26_post_boundary_stop_prepared_v1",
        "created_at": now(),
        "xp_reset_prepared_sha256": base.prior.sha(recovery_path),
        "predecessor_v25_outer_credit": False,
        "resource_envelope_change": False,
    })
    base.atomic(prepared_path, prepared)

    accounts = base.load(accounts_path)
    accounts.update({
        "schema_version": "mysql_algorithm_b_v26_post_boundary_stop_accounts_v1",
        "created_at": now(),
        "prepared_binding_sha256": base.prior.sha(prepared_path),
        "source_path": str(Path(__file__).resolve()),
        "source_sha256": base.prior.sha(Path(__file__).resolve()),
    })
    base.atomic(accounts_path, accounts)


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
    base.reset_v8.install()
    base.c32.runtime.alg.reset = base.reset_v8._v6
    base.c32.runtime.alg.FREEZE = FREEZE
    base.c32.runtime.alg.classify_xp_mysql_liveness = base.prior.classify_stopped_or_live
    base.c32.reset_xp(freeze, workers, output, TOKEN, worker_ids=set(XP_IDS))

    for worker_id in sorted(XP_IDS):
        live = base.prior.inspect(str(workers[worker_id]["container"]))
        if (
            live.get("Id") != workers[worker_id].get("container_id")
            or (live.get("State") or {}).get("Running") is not True
            or base.c32.runtime.alg.xp_active_client_session_count(workers[worker_id]) != 0
        ):
            raise RuntimeError(f"{worker_id} recovered live readback differs")

    base.SOURCE = SOURCE
    base.PREPARED = PREPARED
    base.ACCOUNTS = ACCOUNTS
    base.FREEZE = FREEZE
    base.TOKEN = TOKEN
    recovery, prepared_path, accounts_path = base.build_bindings(
        output=output, prepared=prepared, accounts=accounts, freeze=freeze,
        workers=workers, stopped_evidence=stopped,
    )
    rewrite_bindings(
        recovery_path=recovery, prepared_path=prepared_path,
        accounts_path=accounts_path,
    )
    print(json.dumps({
        "status": "PASS",
        "recovery_binding": str(recovery),
        "prepared_binding": str(prepared_path),
        "benchmark_accounts_binding": str(accounts_path),
        "workers": {
            worker_id: workers[worker_id]["container_id"]
            for worker_id in sorted(XP_IDS)
        },
    }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
