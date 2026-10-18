#!/usr/bin/env python3
"""Recover all XP workers after the v11 partial reset and v12 identity stop.

The v11 outer-3 rank-3 reset replaced xp_cpu_w1 and xp_io_w2 before the
credential metadata timeout stopped the wave.  xp_cpu_w2 and xp_io_w1 remain
the exact stopped predecessors from the preceding completed wave.  This
recovery binds those two generations explicitly, resets all four XP workers,
and emits a new prepared/accounts pair for the v13 continuation.  JOB and
TPC-H workers are only read back and are never mutated here.
"""

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

from scripts import reset_mysql_xp_data_planes_v8 as reset_v8
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v32 as c32


HISTORY = ROOT / "history/mysql_four_scene_20260809"
V11 = HISTORY / "v11_formal_independent_parallel221_outer1_score_resume_20260818_attempt003"
V12 = HISTORY / "v12_formal_parallel221_outer3_rank3_reset_resume_20260818_attempt004"
PREPARED = (
    HISTORY / "v68_outer16_post_baseline_rebind_20260817_attempt006_rebind"
    / "prepared_binding.json"
)
ACCOUNTS = (
    HISTORY / "v68_outer16_post_baseline_rebind_20260817_attempt006_accounts"
    / "benchmark_accounts_binding.json"
)
FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18"
    / "four_scene_effective_resource_successor.json"
)
XP_IDS = {"xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"}
PARTIAL_SOURCES = {
    "xp_cpu_w1": V11 / "xp_resets/algorithm_b_outer_003_rank_3/xp_cpu_w1/reset_worker.json",
    "xp_io_w2": V11 / "xp_resets/algorithm_b_outer_003_rank_3/xp_io_w2/reset_worker.json",
    "xp_cpu_w2": V11 / "xp_resets/algorithm_b_outer_003_rank_1/xp_cpu_w2/reset_worker.json",
    "xp_io_w1": V11 / "xp_resets/algorithm_b_outer_003_rank_1/xp_io_w1/reset_worker.json",
}
V11_ERROR = (
    "TimeoutExpired: Command '['sudo', '-n', 'chown', '999:999', "
    "'/disk2/resource_adaptive_mysql_algorithm_c_disk2_v4/xp_io_w1/"
    "credentials/reset-init.sql']' timed out after 30 seconds"
)
V12_ERROR = "RuntimeError: xp_cpu_w2 live container identity differs"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return __import__("hashlib").sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    sidecar = path.with_name(path.name + ".sha256")
    if sidecar.is_file() and sidecar.read_text(encoding="ascii").split()[0] != sha(path):
        raise RuntimeError(f"checksum sidecar differs: {path}")
    return value


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    c32.runtime.alg.atomic(path, dict(value))


def inspect(container: str) -> dict[str, Any]:
    completed = subprocess.run(
        ["docker", "inspect", container], capture_output=True, text=True,
        check=True, timeout=30,
    )
    value = json.loads(completed.stdout)
    if not isinstance(value, list) or len(value) != 1:
        raise RuntimeError(f"container inspect cardinality differs: {container}")
    return dict(value[0])


def classify_stopped_or_live(worker: Mapping[str, Any]) -> dict[str, Any]:
    """Extend the normal drain proof to an exact stopped predecessor.

    The shared classifier only recognizes a running container with no live
    mysqld.  A Docker container in Exited state with PID 0 is stronger proof
    of no retained SQL sessions.  Bind name, ID and both formal labels twice
    so this exception cannot adopt an unrelated stopped container.
    """
    algorithm = c32.runtime.alg
    original = classify_stopped_or_live.original
    first = inspect(str(worker.get("container") or ""))
    first_state = first.get("State") or {}
    first_labels = (first.get("Config") or {}).get("Labels") or {}
    exact_stopped = (
        first.get("Id") == worker.get("container_id")
        and first_state.get("Status") == "exited"
        and first_state.get("Running") is False
        and int(first_state.get("Pid") or 0) == 0
        and first_labels.get("ra.owner") == "mysql_four_scene_v5"
        and first_labels.get("ra.worker_id") == worker.get("worker_id")
        and algorithm.reset.XP_CONTAINER_ALLOWLIST.get(worker.get("worker_id"))
        == worker.get("container")
    )
    if not exact_stopped:
        return original(worker)
    second = inspect(str(worker["container"]))
    second_state = second.get("State") or {}
    second_labels = (second.get("Config") or {}).get("Labels") or {}
    stable = (
        second.get("Id") == first.get("Id")
        and second_state.get("Status") == "exited"
        and second_state.get("Running") is False
        and int(second_state.get("Pid") or 0) == 0
        and second_labels.get("ra.owner") == "mysql_four_scene_v5"
        and second_labels.get("ra.worker_id") == worker.get("worker_id")
    )
    if not stable:
        raise RuntimeError(f"{worker.get('worker_id')} stopped identity changed")
    return {
        "worker_id": worker["worker_id"],
        "expected_container": worker["container"],
        "observed_container": worker["container"],
        "expected_container_id": worker["container_id"],
        "first_container_id": first["Id"],
        "second_container_id": second["Id"],
        "docker_running": False,
        "docker_status": "exited",
        "container_identity_stable": True,
        "mysqld_rows": [],
        "live_mysqld_rows": [],
        "safe_to_discard_unresponsive_predecessor": True,
        "session_count_semantics": "zero_by_exact_stopped_container_pid_zero",
        "status": "PROVEN_NO_LIVE_MYSQLD",
    }


classify_stopped_or_live.original = (  # type: ignore[attr-defined]
    c32.runtime.alg.classify_xp_mysql_liveness
)


def validate_sources() -> tuple[
    dict[str, Any], dict[str, Any], dict[str, Any], dict[str, dict[str, Any]],
]:
    v11_state = load(V11 / "run_state.json")
    v12_state = load(V12 / "run_state.json")
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
    if (
        v11_state.get("status") != "FAILED_CLOSED"
        or v11_state.get("stage") != "FAILED"
        or v11_state.get("error") != V11_ERROR
        or int(v11_state.get("outer_attempt") or 0) != 2
        or v12_state.get("status") != "FAILED_CLOSED"
        or v12_state.get("stage") != "FAILED"
        or v12_state.get("error") != V12_ERROR
        or int(v12_state.get("outer_attempt", -1)) != 0
        or prepared.get("status") != "PREPARED"
        or prepared.get("formal_scope") != "mysql_only"
        or prepared.get("cross_engine_comparison_prohibited") is not True
        or prepared.get("freeze_sha256") != sha(FREEZE)
        or accounts.get("status") != "PASS"
        or accounts.get("freeze_sha256") != sha(FREEZE)
        or accounts.get("prepared_binding_sha256") != sha(PREPARED)
        or set(prepared_workers) != set(frozen_workers)
        or set(account_workers) != set(frozen_workers)
        or not XP_IDS.issubset(frozen_workers)
        or (V11 / "outer_003/top5_batch_3/batch_result.json").exists()
        or any((V12 / name).exists() for name in ("formal_independent_baseline", "outer_001"))
    ):
        raise RuntimeError("v12 partial-XP-reset recovery authority differs")

    workers = {
        worker_id: {
            **frozen_workers[worker_id],
            **prepared_workers[worker_id],
            **account_workers[worker_id],
        }
        for worker_id in frozen_workers
    }
    for worker_id, source in PARTIAL_SOURCES.items():
        row = load(source)
        live = inspect(str(frozen_workers[worker_id]["container"]))
        state = live.get("State") or {}
        expected_running = worker_id in {"xp_cpu_w1", "xp_io_w2"}
        if (
            row.get("worker_id") != worker_id
            or row.get("ready") is not True
            or live.get("Id") != row.get("container_id")
            or bool(state.get("Running")) is not expected_running
            or (live.get("Config") or {}).get("Labels", {}).get("ra.owner")
            != "mysql_four_scene_v5"
        ):
            raise RuntimeError(f"{worker_id} partial reset identity differs")
        workers[worker_id] = {**frozen_workers[worker_id], **row}

    # The non-XP planes must remain the exact prepared identities.  This
    # recovery has no authority to restart or replace them.
    for worker_id in sorted(set(workers) - XP_IDS):
        live = inspect(str(frozen_workers[worker_id]["container"]))
        if (
            live.get("Id") != prepared_workers[worker_id].get("container_id")
            or (live.get("State") or {}).get("Running") is not True
        ):
            raise RuntimeError(f"{worker_id} non-XP identity differs")
    return prepared, accounts, freeze, workers


def build_bindings(
    *, output: Path, prepared: dict[str, Any], accounts: dict[str, Any],
    freeze: dict[str, Any], workers: dict[str, dict[str, Any]], token: str,
) -> tuple[Path, Path, Path]:
    reset_binding = output / "xp_resets" / token / "reset_binding.json"
    reset_document = load(reset_binding)
    reset_workers = {
        str(row["worker_id"]): dict(row)
        for row in reset_document.get("workers") or []
    }
    if (
        reset_document.get("status") != "PASS"
        or set(reset_document.get("reset_worker_ids") or []) != XP_IDS
        or not XP_IDS.issubset(reset_workers)
    ):
        raise RuntimeError("recovered XP reset binding differs")

    recovery_path = output / "recovered_xp_binding.json"
    atomic(recovery_path, {
        "schema_version": "mysql_algorithm_b_v12_partial_xp_reset_recovery_v1",
        "status": "PASS",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "v11_failed_state": {"path": str(V11 / "run_state.json"),
                              "sha256": sha(V11 / "run_state.json")},
        "v12_failed_state": {"path": str(V12 / "run_state.json"),
                              "sha256": sha(V12 / "run_state.json")},
        "predecessor_prepared": {"path": str(PREPARED), "sha256": sha(PREPARED)},
        "predecessor_accounts": {"path": str(ACCOUNTS), "sha256": sha(ACCOUNTS)},
        "freeze_path": str(FREEZE),
        "freeze_sha256": sha(FREEZE),
        "point_reset_binding": {"path": str(reset_binding),
                                "sha256": sha(reset_binding)},
        "partial_worker_sources": {
            worker_id: {"path": str(path), "sha256": sha(path)}
            for worker_id, path in sorted(PARTIAL_SOURCES.items())
        },
        "reset_worker_ids": sorted(XP_IDS),
        "workers": [workers[worker_id] for worker_id in sorted(XP_IDS)],
        "job_tpch_container_mutation": False,
        "external_container_mutation": False,
        "workload_started": False,
        "formal_state_credit_increment": 0,
        "accepted_rounds_mutation": False,
        "rejected_rounds_mutation": False,
        "interval_convergence_mutation": False,
        "recovery_source_path": str(Path(__file__).resolve()),
        "recovery_source_sha256": sha(Path(__file__).resolve()),
    })

    prepared_path = output / "prepared_binding.json"
    prepared_document = copy.deepcopy(prepared)
    prepared_document.update({
        "schema_version": "mysql_algorithm_b_v13_post_partial_xp_reset_prepared_v1",
        "status": "PREPARED",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE),
        "freeze_sha256": sha(FREEZE),
        "predecessor_prepared_path": str(PREPARED),
        "predecessor_prepared_sha256": sha(PREPARED),
        "xp_reset_prepared_path": str(recovery_path),
        "xp_reset_prepared_sha256": sha(recovery_path),
        "workers": [workers[str(row["worker_id"])]
                    for row in prepared.get("workers") or []],
        "workload_started": False,
        "fresh_r0_complete": False,
        "formal_outer_started": False,
        "accepted_rounds": {scene: 0 for scene in freeze["active_scenes"]},
        "resource_envelope_change": False,
    })
    atomic(prepared_path, prepared_document)

    accounts_path = output / "benchmark_accounts_binding.json"
    account_document = copy.deepcopy(accounts)
    account_document.update({
        "schema_version": "mysql_algorithm_b_v13_post_partial_xp_reset_accounts_v1",
        "status": "PASS",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE),
        "freeze_sha256": sha(FREEZE),
        "prepared_binding_path": str(prepared_path),
        "prepared_binding_sha256": sha(prepared_path),
        "workers": [workers[str(row["worker_id"])]
                    for row in accounts.get("workers") or []],
        "passwords_emitted": False,
        "workload_started": False,
        "fresh_r0_complete": False,
        "formal_outer_started": False,
        "accepted_rounds": {scene: 0 for scene in freeze["active_scenes"]},
        "failures": [],
        "source_path": str(Path(__file__).resolve()),
        "source_sha256": sha(Path(__file__).resolve()),
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

    prepared, accounts, freeze, workers = validate_sources()
    reset_v8.install()
    c32.runtime.alg.reset = reset_v8._v6
    c32.runtime.alg.FREEZE = FREEZE
    c32.runtime.alg.classify_xp_mysql_liveness = classify_stopped_or_live
    token = "algorithm_b_v12_partial_reset_recovery_v1"
    c32.reset_xp(freeze, workers, output, token, worker_ids=set(XP_IDS))

    for worker_id in sorted(XP_IDS):
        live = inspect(str(workers[worker_id]["container"]))
        if (
            live.get("Id") != workers[worker_id].get("container_id")
            or (live.get("State") or {}).get("Running") is not True
            or c32.runtime.alg.xp_active_client_session_count(workers[worker_id]) != 0
        ):
            raise RuntimeError(f"{worker_id} recovered live readback differs")
    recovery, prepared_path, accounts_path = build_bindings(
        output=output, prepared=prepared, accounts=accounts, freeze=freeze,
        workers=workers, token=token,
    )
    print(json.dumps({
        "status": "PASS",
        "recovery_binding": str(recovery),
        "prepared_binding": str(prepared_path),
        "benchmark_accounts_binding": str(accounts_path),
        "workers": {worker_id: workers[worker_id]["container_id"]
                    for worker_id in sorted(XP_IDS)},
    }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
