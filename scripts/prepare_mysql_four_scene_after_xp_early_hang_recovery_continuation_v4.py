#!/usr/bin/env python3
"""Rebind the formal campaign after an evidenced Outer-16 XP early hang reset."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v1 as coordination
from scripts import prepare_mysql_four_scene_after_xp_recovery_continuation_v2 as base


XP_IDS = {"xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"}
NON_XP_IDS = {"job_w1", "job_w2", "tpch_w1", "tpch_w2"}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def validate_authority(
    recovered_path: Path, source_r0_path: Path, failed_root: Path,
) -> tuple[dict[str, Any], dict[str, Any], Path, Path]:
    recovered = base.load(recovered_path)
    source_r0 = base.load(source_r0_path)
    failed_state_path = failed_root / "run_state.json"
    receipt_path = failed_root / "launch_receipt.json"
    samples_path = failed_root / "formal_incumbent_baseline/resource_samples.jsonl"
    failed_state = base.load(failed_state_path)
    receipt = base.load(receipt_path)
    freeze = base.load(base.FREEZE)
    workers = {str(row.get("worker_id")): row for row in recovered.get("workers") or []}
    evidence_state = recovered.get("failed_state") or {}
    evidence_receipt = recovered.get("failed_launch_receipt") or {}
    evidence_samples = recovered.get("early_hang_resource_samples") or {}
    window = evidence_samples.get("last_30_second_window") or {}
    xp_quiet = all(float((window.get(worker) or {}).get("cpu_cores") or 1) < 0.01 for worker in XP_IDS)
    non_xp_active = all(float((window.get(worker) or {}).get("cpu_cores") or 0) > 1 for worker in NON_XP_IDS)
    terminal = recovered.get("terminal_unit_readback") or {}
    if (
        recovered_path.parent.parent != base.HISTORY
        or recovered.get("schema_version") != "mysql_algorithm_c_outer16_early_hang_recovery_v1"
        or recovered.get("status") != "PASS"
        or recovered.get("formal_scope") != "mysql_only"
        or recovered.get("cross_engine_comparison_prohibited") is not True
        or recovered.get("freeze_sha256") != base.sha(base.FREEZE)
        or set(workers) != XP_IDS
        or any(row.get("ready") is not True for row in workers.values())
        or recovered.get("reset_worker_ids") != sorted(XP_IDS)
        or recovered.get("live_recorded_client_pids") not in ([], None)
        or recovered.get("workload_started") is not False
        or recovered.get("formal_state_credit_increment") != 0
        or recovered.get("accepted_rounds_mutation") is not False
        or recovered.get("rejected_rounds_mutation") is not False
        or recovered.get("interval_convergence_mutation") is not False
        or recovered.get("external_container_mutation") is not False
        or evidence_state.get("path") != str(failed_state_path)
        or evidence_state.get("sha256") != base.sha(failed_state_path)
        or evidence_receipt.get("path") != str(receipt_path)
        or evidence_receipt.get("sha256") != base.sha(receipt_path)
        or evidence_samples.get("path") != str(samples_path)
        or evidence_samples.get("sha256") != base.sha(samples_path)
        or set(window) != XP_IDS | NON_XP_IDS
        or not xp_quiet
        or not non_xp_active
        or terminal.get("ActiveState") != "inactive"
        or terminal.get("SubState") != "dead"
        or terminal.get("MainPID") != "0"
        or source_r0_path.parent.parent != base.HISTORY
        or source_r0.get("status") != "PASS"
        or source_r0.get("fresh_r0_complete") is not True
        or source_r0.get("formal_outer_launch_authorized") is not True
        or source_r0.get("formal_outer_started") is not False
        or source_r0.get("formal_scope") != "mysql_only"
        or source_r0.get("cross_engine_comparison_prohibited") is not True
        or source_r0.get("freeze_sha256") != base.sha(base.FREEZE)
        or source_r0.get("failures") not in ([], None)
        or source_r0.get("client_isolation_failures") not in ([], None)
        or (source_r0.get("target_bottleneck_gate") or {}).get("passed") is not True
        or failed_root.parent != base.HISTORY
        or failed_state.get("status") != "RUNNING"
        or failed_state.get("stage") != "FORMAL_INCUMBENT_BASELINE_MEASURING"
        or int(failed_state.get("outer_attempt") or -1) != 15
        or receipt.get("status") != "STARTED"
        or freeze.get("formal_scope") != "mysql_only"
    ):
        raise RuntimeError("early-hang recovery continuation authority differs")
    return recovered, source_r0, failed_state_path, samples_path


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
    source_r0_path = args.source_r0.expanduser().resolve()
    failed_root = args.failed_root.expanduser().resolve()
    _, source_r0, failed_state_path, samples_path = validate_authority(
        recovered_path, source_r0_path, failed_root,
    )
    output = base.HISTORY / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh preparation output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    state_path = output / "preparation_state.json"
    state: dict[str, Any] = {
        "schema_version": "mysql_four_scene_post_xp_early_hang_recovery_preparation_v4",
        "status": "STARTING", "started_at": now(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "recovered_xp_binding": str(recovered_path),
        "recovered_xp_binding_sha256": base.sha(recovered_path),
        "source_r0": str(source_r0_path), "source_r0_sha256": base.sha(source_r0_path),
        "failed_state": str(failed_state_path), "failed_state_sha256": base.sha(failed_state_path),
        "early_hang_resource_samples": str(samples_path),
        "early_hang_resource_samples_sha256": base.sha(samples_path),
        "external_container_mutation": False,
    }
    base.atomic(state_path, state)
    lease = None
    try:
        lease = coordination.acquire_exclusive_blocking(timeout_seconds=10.0)
        state.update({"status": "LEASE_ACQUIRED", "lease_acquired_at": lease.acquired_at,
                      "lease_wait_seconds": lease.wait_seconds})
        base.atomic(state_path, state)
        env = dict(os.environ, MYSQL_V18_RECOVERED_XP_BINDING=str(recovered_path))
        preflight = base.PREFLIGHT_ROOT / f"{args.run_id}_preflight.json"
        rc = base.run_preflight_in_process(
            output=preflight, recovered_path=recovered_path, lease_holder_pid=os.getpid(),
        )
        state.update({"preflight": str(preflight), "preflight_returncode": rc})
        if rc != 0:
            raise RuntimeError("fresh recovered-XP preflight failed closed")

        campaign_preflight = base.load(base.CAMPAIGN_PREFLIGHT)
        if (
            campaign_preflight.get("status") != "PASS"
            or campaign_preflight.get("formal_launch_authorized") is not True
            or float(campaign_preflight.get("observed_seconds") or 0) < 60
            or campaign_preflight.get("freeze_sha256") != base.sha(base.FREEZE)
        ):
            raise RuntimeError("campaign launch preflight authority differs")
        env["MYSQL_V18_PASS_PREFLIGHT"] = str(base.CAMPAIGN_PREFLIGHT)
        rebind_id = f"{args.run_id}_rebind"
        result = base.run([str(base.PYTHON), str(base.REBIND), "--run-id", rebind_id], env, 300)
        prepared = base.HISTORY / rebind_id / "prepared_binding.json"
        state.update({"prepared": str(prepared), "rebind_returncode": result.returncode,
                      "rebind_stderr_tail": result.stderr[-2000:]})
        if result.returncode != 0:
            raise RuntimeError("fresh recovered-XP rebind failed closed")

        env["MYSQL_V18_FORMAL_PREPARED"] = str(prepared)
        accounts_id = f"{args.run_id}_accounts"
        result = base.run([str(base.PYTHON), str(base.ACCOUNTS), "--run-id", accounts_id,
                           "--verify-existing"], env, 300)
        accounts = base.HISTORY / accounts_id / "benchmark_accounts_binding.json"
        state.update({"accounts": str(accounts), "accounts_returncode": result.returncode,
                      "accounts_stderr_tail": result.stderr[-2000:]})
        if result.returncode != 0:
            raise RuntimeError("fresh recovered-XP account verification failed closed")

        preflight_doc, prepared_doc, accounts_doc = (
            base.load(preflight), base.load(prepared), base.load(accounts)
        )
        if (
            preflight_doc.get("status") != "PASS"
            or preflight_doc.get("formal_launch_authorized") is not True
            or float(preflight_doc.get("observed_seconds") or 0) < 15
            or preflight_doc.get("recovered_xp_binding_sha256") != base.sha(recovered_path)
            or prepared_doc.get("preflight_sha256") != base.sha(base.CAMPAIGN_PREFLIGHT)
            or accounts_doc.get("prepared_binding_sha256") != base.sha(prepared)
        ):
            raise RuntimeError("continuation live lineage readback differs")

        r0_root = output / "continuation_r0"
        r0_root.mkdir(mode=0o700)
        r0_path = r0_root / "r0_binding.json"
        derived = dict(source_r0)
        derived.update({
            "schema_version": "mysql_four_scene_campaign_continuation_r0_v4",
            "completed_at": now(), "preflight_path": str(base.CAMPAIGN_PREFLIGHT),
            "preflight_sha256": base.sha(base.CAMPAIGN_PREFLIGHT),
            "preparation_preflight_path": str(preflight),
            "preparation_preflight_sha256": base.sha(preflight),
            "prepared_binding_path": str(prepared), "prepared_binding_sha256": base.sha(prepared),
            "benchmark_accounts_path": str(accounts), "benchmark_accounts_sha256": base.sha(accounts),
            "r0_source_path": str(Path(__file__).resolve()),
            "r0_source_sha256": base.sha(Path(__file__).resolve()),
            "campaign_continuation_release": {
                "version": "xp_early_hang_sealed_reset_continuation_v4",
                "source_r0_path": str(source_r0_path), "source_r0_sha256": base.sha(source_r0_path),
                "recovered_xp_binding_path": str(recovered_path),
                "recovered_xp_binding_sha256": base.sha(recovered_path),
                "failed_state_path": str(failed_state_path),
                "failed_state_sha256": base.sha(failed_state_path),
                "early_hang_resource_samples_path": str(samples_path),
                "early_hang_resource_samples_sha256": base.sha(samples_path),
                "technical_failure_formal_state_credit": 0,
                "workload_rerun": False, "raw_r0_results_mutated": False,
                "resource_envelope_changed": False, "workload_contract_changed": False,
                "optimizer_state_mutated": False, "rolling_window_preflight_rerun": True,
                "rolling_window_required_clean_seconds": 15,
                "continuation_preflight_path": str(preflight),
                "continuation_preflight_sha256": base.sha(preflight),
                "all_live_resource_and_account_readbacks_rerun": True,
                "formal_baseline_rerun_required": True,
            },
        })
        base.atomic(r0_path, derived)
        r0_path.with_name(r0_path.name + ".sha256").write_text(
            f"{base.sha(r0_path)}  {r0_path.name}\n", encoding="ascii",
        )
        state.update({"status": "PASS", "completed_at": now(), "r0": str(r0_path),
                      "preflight_sha256": base.sha(preflight), "prepared_sha256": base.sha(prepared),
                      "accounts_sha256": base.sha(accounts), "r0_sha256": base.sha(r0_path)})
        return 0
    except BaseException as error:
        state.update({"status": "FAILED_CLOSED", "failed_at": now(),
                      "error": f"{type(error).__name__}: {error}"})
        return 4
    finally:
        if lease is not None:
            state["lease_held_seconds"] = coordination.release(lease)
        base.atomic(state_path, state)


if __name__ == "__main__":
    raise SystemExit(main())
