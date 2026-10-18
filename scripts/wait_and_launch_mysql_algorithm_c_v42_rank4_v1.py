#!/usr/bin/env python3
"""Wait without touching external work, then launch formal Outer 10 rank 4."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_c_boundary_resume_v2.py"
CHECKPOINT = ROOT / "history/mysql_four_scene_20260809/algorithm_c_v38_outer9_checkpoint_v11_20260815T1228p0800/prepared_binding.json"
RUNTIME_PREFLIGHT = ROOT / "preflight/mysql_four_scene_20260809/v18_formal_bounded_prestate_v10_20260814T1000p0800_preflight_attempt_0001.json"
PREPARED = ROOT / "history/mysql_four_scene_20260809/v18_formal_bounded_prestate_v10_20260814T1000p0800_rebind/prepared_binding.json"
ACCOUNTS = ROOT / "history/mysql_four_scene_20260809/v18_formal_bounded_prestate_v10_20260814T1000p0800_accounts/benchmark_accounts_binding.json"
R0 = ROOT / "history/mysql_four_scene_20260809/v18_formal_bounded_prestate_v10_20260814T1000p0800_r0/r0_binding.json"
QUARANTINE = ROOT / "history/mysql_four_scene_20260809/algorithm_c_v38_replay_quarantine_v8_outer9_v11_20260815T1232p0800/replay_quarantine.json"
PARTIAL = ROOT / "history/mysql_four_scene_20260809/v41_formal_outer10_rank3_retry2_20260815T1314p0800"
OPTIMIZATION = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v46/optimization_contract.json"
LOG = ROOT / "history/mysql_four_scene_20260809/formal_launch_logs/v43_rank4_auto_wait.jsonl"
RESULT = ROOT / "history/mysql_four_scene_20260809/formal_launch_logs/v43_rank4_auto_wait_result.json"
BASE_RUN_ID = "v43_formal_outer10_rank4_auto_20260815"
# This service only polls the fail-closed gate; it does not reserve resources,
# pause containers, or mutate external processes.  Keep it alive across long
# co-resident campaigns so the first genuinely isolated boundary is not lost.
MAXIMUM_WAIT_SECONDS = 72 * 60 * 60
POLL_SECONDS = 30
START_CONFIRMATION_SECONDS = 180
INHERITED_ACCEPTED_ROUNDS = {
    "job_memory": 5,
    "tpch_sf4_memory": 5,
    "hybench_xp_cpu": 4,
    "hybench_xp_io": 3,
}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def append(value: dict[str, Any]) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def atomic(value: dict[str, Any]) -> None:
    temporary = RESULT.with_name(f".{RESULT.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, RESULT)
    RESULT.with_name(RESULT.name + ".sha256").write_text(
        f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii",
    )


def retryable_preworkload_lease_race(
    state: dict[str, Any], *, run_root: Path,
) -> bool:
    return (
        state.get("status") == "FAILED_CLOSED"
        and state.get("stage") == "FAILED"
        and state.get("error")
        == "RuntimeError: external physical workload bypassed the exclusive wave lease"
        and int(state.get("outer_attempt") or -1) == 9
        and state.get("accepted_rounds") == INHERITED_ACCEPTED_ROUNDS
        and not (run_root / "outer_010/top5_batch_4/batch_result.json").exists()
        and not any((run_root / "outer_010").glob("*.decision.json"))
    )


def configure_authority() -> None:
    from scripts import run_mysql_four_scene_algorithm_c_v2 as alg

    optimization = json.loads(OPTIMIZATION.read_text(encoding="utf-8"))
    freeze = (ROOT / optimization["bindings"]["resource_freeze_path"]).resolve()
    alg.FREEZE = freeze
    alg.r0mod.FREEZE = freeze
    alg.r0mod.FREEZE_SCHEMA = str(json.loads(freeze.read_text())["schema_version"])
    alg.r0mod.PREFLIGHT = RUNTIME_PREFLIGHT
    alg.r0mod.RUNTIME_PREFLIGHT = RUNTIME_PREFLIGHT
    alg.r0mod.PREPARED = PREPARED
    alg.r0mod.BENCHMARK_ACCOUNTS = ACCOUNTS


def authority_probe() -> tuple[bool, str | None]:
    from scripts import mysql_cross_project_wave_coordination_v1 as coordination
    from scripts import run_mysql_four_scene_algorithm_c_v2 as alg

    lease = None
    try:
        lease = coordination.acquire_exclusive_blocking(timeout_seconds=10)
        alg.r0mod.COORDINATED_LEASE_HOLDER_PID = os.getpid()
        alg.r0mod.COORDINATION_CONTRACT = coordination.CONTRACT_PATH
        alg.r0mod._validate_authority()
        return True, None
    except Exception as error:
        return False, f"{type(error).__name__}: {error}"
    finally:
        alg.r0mod.COORDINATED_LEASE_HOLDER_PID = None
        alg.r0mod.COORDINATION_CONTRACT = None
        if lease is not None:
            coordination.release(lease)


def main() -> int:
    global PARTIAL, LOG, RESULT, BASE_RUN_ID, MAXIMUM_WAIT_SECONDS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formal-partial-root", type=Path, default=PARTIAL)
    parser.add_argument("--log", type=Path, default=LOG)
    parser.add_argument("--result", type=Path, default=RESULT)
    parser.add_argument("--base-run-id", default=BASE_RUN_ID)
    parser.add_argument(
        "--maximum-wait-seconds", type=float, default=MAXIMUM_WAIT_SECONDS,
    )
    args = parser.parse_args()
    PARTIAL = args.formal_partial_root.expanduser().resolve()
    LOG = args.log.expanduser().resolve()
    RESULT = args.result.expanduser().resolve()
    BASE_RUN_ID = str(args.base_run_id)
    MAXIMUM_WAIT_SECONDS = float(args.maximum_wait_seconds)
    if (
        not PARTIAL.is_dir()
        or PARTIAL.is_symlink()
        or not BASE_RUN_ID
        or not all(character.isalnum() or character in "_.-" for character in BASE_RUN_ID)
        or MAXIMUM_WAIT_SECONDS <= 0.0
    ):
        parser.error("regular partial root, safe run id, and positive wait required")
    if RESULT.exists():
        raise RuntimeError(f"fresh result required: {RESULT}")
    for path in (
        LAUNCHER, CHECKPOINT, RUNTIME_PREFLIGHT, PREPARED, ACCOUNTS,
        R0, QUARANTINE, OPTIMIZATION,
    ):
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"bound regular input differs: {path}")
    configure_authority()
    started = time.monotonic()
    attempt = 0
    append({
        "event": "waiter_started", "captured_at": now(),
        "maximum_wait_seconds": MAXIMUM_WAIT_SECONDS,
        "external_process_mutation_prohibited": True,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "optimization_contract_path": str(OPTIMIZATION),
        "optimization_contract_sha256": sha(OPTIMIZATION),
        "formal_partial_root": str(PARTIAL), "formal_start_rank": 4,
    })
    last_error = None
    while time.monotonic() - started < MAXIMUM_WAIT_SECONDS:
        passed, error = authority_probe()
        if not passed:
            if error != last_error:
                append({
                    "event": "authority_still_blocked", "captured_at": now(),
                    "error_sha256": hashlib.sha256(
                        str(error).encode("utf-8")
                    ).hexdigest(),
                })
                last_error = error
            time.sleep(POLL_SECONDS)
            continue
        attempt += 1
        run_id = f"{BASE_RUN_ID}_attempt{attempt:03d}"
        command = [
            str(PYTHON), str(LAUNCHER), "--run-id", run_id,
            "--checkpoint", str(CHECKPOINT),
            "--runtime-preflight", str(RUNTIME_PREFLIGHT),
            "--prepared-binding", str(PREPARED),
            "--benchmark-accounts", str(ACCOUNTS),
            "--fresh-r0", str(R0),
            "--replay-quarantine", str(QUARANTINE),
            "--formal-partial-root", str(PARTIAL),
            "--formal-start-rank", "4",
        ]
        completed = subprocess.run(
            command, cwd=ROOT, text=True, capture_output=True,
            check=False, timeout=60,
        )
        append({
            "event": "formal_launch_attempt", "captured_at": now(),
            "attempt": attempt, "run_id": run_id,
            "returncode": completed.returncode,
            "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
            "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
        })
        if completed.returncode == 0:
            receipt = json.loads(completed.stdout)
            run_root = ROOT / "history/mysql_four_scene_20260809" / run_id
            state_path = Path(str(receipt["run_state_path"])).resolve()
            confirmation_started = time.monotonic()
            retry = False
            while time.monotonic() - confirmation_started < START_CONFIRMATION_SECONDS:
                if not state_path.exists():
                    time.sleep(2)
                    continue
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if retryable_preworkload_lease_race(state, run_root=run_root):
                    append({
                        "event": "preworkload_lease_race_excluded_and_retrying",
                        "captured_at": now(),
                        "attempt": attempt,
                        "run_id": run_id,
                        "accepted_state_credit_increment": 0,
                        "rejected_outer_attempt_increment": 0,
                        "physical_candidate_replay_started": False,
                    })
                    retry = True
                    break
                if state.get("status") in {"FAILED_CLOSED", "NON_CONVERGED"}:
                    value = {
                        "status": "FAILED_CLOSED",
                        "captured_at": now(),
                        "run_id": run_id,
                        "attempt": attempt,
                        "formal_scope": "mysql_only",
                        "cross_engine_comparison_prohibited": True,
                        "launch_receipt_path": str(run_root / "launch_receipt.json"),
                        "systemd_unit": receipt["systemd_unit"],
                        "run_state_path": str(state_path),
                    }
                    atomic(value)
                    return 4
                if (
                    str(state.get("stage") or "").endswith("_MEASURING")
                    or (run_root / "outer_010/top5_batch_4/batch_result.json").exists()
                ):
                    break
                time.sleep(2)
            if retry:
                time.sleep(POLL_SECONDS)
                continue
            value = {
                "status": "STARTED", "captured_at": now(),
                "run_id": run_id, "attempt": attempt,
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "launch_receipt_path": str(
                    ROOT / "history/mysql_four_scene_20260809" / run_id
                    / "launch_receipt.json"
                ),
                "systemd_unit": receipt["systemd_unit"],
                "run_state_path": receipt["run_state_path"],
            }
            atomic(value)
            return 0
        time.sleep(POLL_SECONDS)
    value = {
        "status": "TIMED_OUT", "captured_at": now(),
        "waited_seconds": time.monotonic() - started,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "external_process_mutation_performed": False,
    }
    atomic(value)
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
