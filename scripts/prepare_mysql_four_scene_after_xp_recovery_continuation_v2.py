#!/usr/bin/env python3
"""Rebind an in-progress formal campaign after a sealed XP reset.

The campaign R0 measurements are immutable and remain authoritative because
the reset uses the same sealed templates and frozen resource envelope that are
already used between candidate ranks.  This release reruns the rolling-window
preflight and all live identity/resource/account readbacks, then derives a
continuation R0 binding without replaying the four workloads.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v1 as coordination


PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
HISTORY = ROOT / "history/mysql_four_scene_20260809"
PREFLIGHT_ROOT = ROOT / "preflight/mysql_four_scene_20260809"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18"
    / "four_scene_effective_resource_successor.json"
)
CAMPAIGN_PREFLIGHT = (
    ROOT / "preflight/mysql_four_scene_20260809"
    / "v54_recovered_xp_rebind_r0_20260817_attempt012_preflight.json"
)
PREFLIGHT = ROOT / "scripts/preflight_mysql_four_scene_live_v18_recovered_xp_continuation_v2.py"
REBIND = ROOT / "scripts/rebind_mysql_four_scene_live_v18_recovered_xp_v1.py"
ACCOUNTS = ROOT / "scripts/provision_mysql_four_scene_benchmark_users_v18_formal.py"
XP_IDS = {"xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular non-symlink JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic(path: Path, document: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def run(command: list[str], env: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=ROOT, env=env, text=True, capture_output=True,
        check=False, timeout=timeout,
    )


def run_preflight_in_process(
    *, output: Path, recovered_path: Path, lease_holder_pid: int,
) -> int:
    """Keep the flock owner and its kernel PID in the validating process."""

    os.environ["MYSQL_V18_RECOVERED_XP_BINDING"] = str(recovered_path)
    from scripts import preflight_mysql_four_scene_live_v18_recovered_xp_continuation_v2 as release

    prior_argv = sys.argv
    sys.argv = [
        str(PREFLIGHT), "--output", str(output),
        "--window-seconds", "15", "--interval-seconds", "1",
        "--coordinated-workload-lease-holder-pid", str(lease_holder_pid),
        "--coordination-contract", str(release.watchdog.isolation.CONTRACT_PATH),
    ]
    try:
        return int(release.main())
    finally:
        sys.argv = prior_argv


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--recovered-xp-binding", type=Path, required=True)
    parser.add_argument("--source-r0", type=Path, required=True)
    parser.add_argument("--failed-root", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", args.run_id) is None:
        parser.error("safe run id required")

    recovered_path = args.recovered_xp_binding.expanduser().resolve()
    recovered = load(recovered_path)
    source_r0_path = args.source_r0.expanduser().resolve()
    source_r0 = load(source_r0_path)
    failed_root = args.failed_root.expanduser().resolve()
    failed_state_path = failed_root / "run_state.json"
    failed_state = load(failed_state_path)
    technical_batch_path = failed_root / "outer_015/top5_batch_2/batch_result.json"
    technical_batch = load(technical_batch_path)
    freeze = load(FREEZE)
    worker_ids = {str(row.get("worker_id")) for row in recovered.get("workers") or []}
    if (
        recovered_path.parent.parent != HISTORY
        or recovered.get("status") != "PASS"
        or recovered.get("formal_scope") != "mysql_only"
        or recovered.get("cross_engine_comparison_prohibited") is not True
        or recovered.get("freeze_sha256") != sha(FREEZE)
        or worker_ids != XP_IDS
        or any(row.get("ready") is not True for row in recovered.get("workers") or [])
        or recovered.get("workload_started") is not False
        or recovered.get("accepted_rounds_mutation") is not False
        or recovered.get("rejected_rounds_mutation") is not False
        or recovered.get("interval_convergence_mutation") is not False
        or source_r0_path.parent.parent != HISTORY
        or source_r0.get("status") != "PASS"
        or source_r0.get("fresh_r0_complete") is not True
        or source_r0.get("formal_outer_launch_authorized") is not True
        or source_r0.get("formal_outer_started") is not False
        or source_r0.get("formal_scope") != "mysql_only"
        or source_r0.get("cross_engine_comparison_prohibited") is not True
        or source_r0.get("freeze_sha256") != sha(FREEZE)
        or source_r0.get("failures") not in ([], None)
        or source_r0.get("client_isolation_failures") not in ([], None)
        or (source_r0.get("target_bottleneck_gate") or {}).get("passed") is not True
        or failed_root.parent != HISTORY
        or failed_state.get("status") != "FAILED_CLOSED"
        or failed_state.get("stage") != "FAILED"
        or int(failed_state.get("outer_attempt") or -1) != 14
        or not str(failed_state.get("error") or "").startswith(
            "RuntimeError: XP active client sessions were not proven drained before "
        )
        or technical_batch.get("status") != "FAILED_CLOSED"
        or not (900.0 <= float(technical_batch.get("elapsed_seconds") or 0) <= 910.0)
    ):
        raise RuntimeError("recovered XP preparation authority differs")

    output = HISTORY / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh preparation output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    state_path = output / "preparation_state.json"
    state: dict[str, Any] = {
        "schema_version": "mysql_four_scene_post_xp_recovery_preparation_v1",
        "status": "STARTING",
        "started_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "recovered_xp_binding": str(recovered_path),
        "recovered_xp_binding_sha256": sha(recovered_path),
        "source_r0": str(source_r0_path),
        "source_r0_sha256": sha(source_r0_path),
        "failed_state": str(failed_state_path),
        "failed_state_sha256": sha(failed_state_path),
        "technical_rank2_batch": str(technical_batch_path),
        "technical_rank2_batch_sha256": sha(technical_batch_path),
        "external_container_mutation": False,
    }
    atomic(state_path, state)
    lease = None
    try:
        lease = coordination.acquire_exclusive_blocking(timeout_seconds=10.0)
        state.update({
            "status": "LEASE_ACQUIRED",
            "coordination_lock_path": str(coordination.LOCK_PATH),
            "lease_acquired_at": lease.acquired_at,
            "lease_wait_seconds": lease.wait_seconds,
        })
        atomic(state_path, state)
        env = dict(
            os.environ,
            MYSQL_V18_RECOVERED_XP_BINDING=str(recovered_path),
        )
        preflight = PREFLIGHT_ROOT / f"{args.run_id}_preflight.json"
        preflight_returncode = run_preflight_in_process(
            output=preflight,
            recovered_path=recovered_path,
            lease_holder_pid=os.getpid(),
        )
        state.update({
            "preflight": str(preflight),
            "preflight_returncode": preflight_returncode,
            "preflight_lease_holder_pid": os.getpid(),
        })
        if preflight_returncode != 0:
            raise RuntimeError("fresh recovered-XP preflight failed closed")

        rebind_id = f"{args.run_id}_rebind"
        campaign_preflight = load(CAMPAIGN_PREFLIGHT)
        if (
            campaign_preflight.get("status") != "PASS"
            or campaign_preflight.get("formal_launch_authorized") is not True
            or float(campaign_preflight.get("observed_seconds") or 0) < 60.0
            or campaign_preflight.get("freeze_sha256") != sha(FREEZE)
        ):
            raise RuntimeError("campaign launch preflight authority differs")
        env["MYSQL_V18_PASS_PREFLIGHT"] = str(CAMPAIGN_PREFLIGHT)
        completed = run([
            str(PYTHON), str(REBIND), "--run-id", rebind_id,
        ], env, 300)
        prepared = HISTORY / rebind_id / "prepared_binding.json"
        state.update({
            "prepared": str(prepared),
            "rebind_returncode": completed.returncode,
            "rebind_stdout_tail": completed.stdout[-2000:],
            "rebind_stderr_tail": completed.stderr[-2000:],
        })
        if completed.returncode != 0:
            raise RuntimeError("fresh recovered-XP rebind failed closed")

        accounts_id = f"{args.run_id}_accounts"
        env["MYSQL_V18_FORMAL_PREPARED"] = str(prepared)
        completed = run([
            str(PYTHON), str(ACCOUNTS), "--run-id", accounts_id,
            "--verify-existing",
        ], env, 300)
        accounts = HISTORY / accounts_id / "benchmark_accounts_binding.json"
        state.update({
            "accounts": str(accounts),
            "accounts_returncode": completed.returncode,
            "accounts_stdout_tail": completed.stdout[-2000:],
            "accounts_stderr_tail": completed.stderr[-2000:],
        })
        if completed.returncode != 0:
            raise RuntimeError("fresh recovered-XP account verification failed closed")

        preflight_document = load(preflight)
        prepared_document = load(prepared)
        accounts_document = load(accounts)
        if (
            preflight_document.get("status") != "PASS"
            or preflight_document.get("formal_launch_authorized") is not True
            or float(preflight_document.get("observed_seconds") or 0) < 15.0
            or preflight_document.get("recovered_xp_binding_sha256") != sha(recovered_path)
            or prepared_document.get("preflight_sha256") != sha(CAMPAIGN_PREFLIGHT)
            or accounts_document.get("prepared_binding_sha256") != sha(prepared)
        ):
            raise RuntimeError("continuation live lineage readback differs")

        r0_root = output / "continuation_r0"
        r0_root.mkdir(mode=0o700)
        r0_path = r0_root / "r0_binding.json"
        derived_r0 = dict(source_r0)
        derived_r0.update({
            "schema_version": "mysql_four_scene_campaign_continuation_r0_v1",
            "completed_at": now(),
            "preflight_path": str(CAMPAIGN_PREFLIGHT),
            "preflight_sha256": sha(CAMPAIGN_PREFLIGHT),
            "preparation_preflight_path": str(preflight),
            "preparation_preflight_sha256": sha(preflight),
            "prepared_binding_path": str(prepared),
            "prepared_binding_sha256": sha(prepared),
            "benchmark_accounts_path": str(accounts),
            "benchmark_accounts_sha256": sha(accounts),
            "r0_source_path": str(Path(__file__).resolve()),
            "r0_source_sha256": sha(Path(__file__).resolve()),
            "campaign_continuation_release": {
                "version": "xp_sealed_reset_continuation_v1",
                "source_r0_path": str(source_r0_path),
                "source_r0_sha256": sha(source_r0_path),
                "recovered_xp_binding_path": str(recovered_path),
                "recovered_xp_binding_sha256": sha(recovered_path),
                "failed_state_path": str(failed_state_path),
                "failed_state_sha256": sha(failed_state_path),
                "technical_rank2_batch_path": str(technical_batch_path),
                "technical_rank2_batch_sha256": sha(technical_batch_path),
                "workload_rerun": False,
                "raw_r0_results_mutated": False,
                "raw_r0_resource_summary_mutated": False,
                "resource_envelope_changed": False,
                "workload_contract_changed": False,
                "optimizer_state_mutated": False,
                "rolling_window_preflight_rerun": True,
                "rolling_window_required_clean_seconds": 15,
                "continuation_preflight_path": str(preflight),
                "continuation_preflight_sha256": sha(preflight),
                "campaign_launch_preflight_path": str(CAMPAIGN_PREFLIGHT),
                "campaign_launch_preflight_sha256": sha(CAMPAIGN_PREFLIGHT),
                "all_live_resource_and_account_readbacks_rerun": True,
                "rank3_quiet_gate_still_required": True,
            },
        })
        atomic(r0_path, derived_r0)
        r0_path.with_name(r0_path.name + ".sha256").write_text(
            f"{sha(r0_path)}  {r0_path.name}\n", encoding="ascii",
        )
        state["r0_returncode"] = 0
        state["r0"] = str(r0_path)
        state.update({
            "status": "PASS",
            "completed_at": now(),
            "preflight_sha256": sha(preflight),
            "prepared_sha256": sha(prepared),
            "accounts_sha256": sha(accounts),
            "r0_sha256": sha(r0_path),
        })
        return 0
    except BaseException as error:
        state.update({
            "status": "FAILED_CLOSED",
            "failed_at": now(),
            "error": f"{type(error).__name__}: {error}",
        })
        return 4
    finally:
        if lease is not None:
            state["lease_held_seconds"] = coordination.release(lease)
        atomic(state_path, state)


if __name__ == "__main__":
    raise SystemExit(main())
