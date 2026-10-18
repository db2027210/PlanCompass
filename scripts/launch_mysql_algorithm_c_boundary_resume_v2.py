#!/usr/bin/env python3
"""Start and verify an optimized Algorithm C completed-outer boundary resume."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
CONTROLLER = ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v2.py"
RESET_SOURCE = ROOT / "scripts/reset_mysql_xp_data_planes_v5.py"
ALGORITHM_SOURCE = ROOT / "scripts/run_mysql_four_scene_algorithm_c_v2.py"
WATCHDOG_SOURCE = ROOT / "scripts/watch_mysql_formal_external_postgres_interference_v1.py"
AFFINITY_GUARD_SOURCE = ROOT / "scripts/enforce_frozen_external_pg_client_affinity_v1.py"
OPTIMIZATION_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v46"
    / "optimization_contract.json"
)
MANAGEMENT_CPUSET = "240-241,496-497"
SCENES = ["job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io"]
STARTUP_STATE_TIMEOUT_SECONDS = 120.0


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def regular_path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.is_symlink():
        raise argparse.ArgumentTypeError(f"regular non-symlink file required: {path}")
    return path


def regular_directory(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_dir() or path.is_symlink():
        raise argparse.ArgumentTypeError(
            f"regular non-symlink directory required: {path}"
        )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--checkpoint", type=regular_path, required=True)
    parser.add_argument("--runtime-preflight", type=regular_path, required=True)
    parser.add_argument("--prepared-binding", type=regular_path, required=True)
    parser.add_argument("--benchmark-accounts", type=regular_path, required=True)
    parser.add_argument("--fresh-r0", type=regular_path, required=True)
    parser.add_argument("--replay-quarantine", type=regular_path, required=True)
    parser.add_argument("--interference-override-pilot", action="store_true")
    parser.add_argument("--interference-evidence", type=regular_path)
    parser.add_argument("--pilot-start-rank", type=int, default=1)
    parser.add_argument("--pilot-baseline", type=regular_path)
    parser.add_argument("--formal-partial-root", type=regular_directory)
    parser.add_argument("--formal-start-rank", type=int, default=1)
    parser.add_argument("--formal-completed-rank5-root", type=regular_directory)
    parser.add_argument("--fresh-search", action="store_true")
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run-id")
    if args.fresh_search and (
        args.interference_override_pilot
        or args.formal_partial_root is not None
        or args.formal_start_rank != 1
        or args.formal_completed_rank5_root is not None
    ):
        parser.error("fresh search cannot inherit pilot or partial state")
    if args.interference_override_pilot:
        if args.formal_partial_root is not None or args.formal_start_rank != 1:
            parser.error("formal partial continuation is incompatible with pilot mode")
        if "pilot_nonformal" not in args.run_id or args.interference_evidence is None:
            parser.error("pilot run id and interference evidence are required")
        interference = load(args.interference_evidence)
        if (
            interference.get("status") != "BLOCKED_FAIL_CLOSED"
            or interference.get("formal_launch_authorized") is not False
            or interference.get("formal_scope") != "mysql_only"
            or interference.get("cross_engine_comparison_prohibited") is not True
            or not (interference.get("blockers") or [])
        ):
            parser.error("blocked live interference evidence required for pilot")
        if args.pilot_start_rank not in range(1, 6):
            parser.error("pilot start rank must be 1..5")
        if args.pilot_start_rank > 1 and args.pilot_baseline is None:
            parser.error("pilot continuation requires a passed pilot baseline")
    elif args.interference_evidence is not None:
        parser.error("interference evidence is pilot-only")
    elif args.pilot_start_rank != 1 or args.pilot_baseline is not None:
        parser.error("pilot continuation arguments are pilot-only")
    elif args.formal_partial_root is None and args.formal_start_rank != 1:
        parser.error("formal partial root is required when formal start rank is above 1")
    elif args.formal_partial_root is not None and args.formal_start_rank not in range(1, 6):
        parser.error("formal partial continuation start rank must be 1..5")
    elif args.formal_completed_rank5_root is not None and (
        args.formal_partial_root is None or args.formal_start_rank != 5
    ):
        parser.error("completed rank 5 recovery requires partial root and start rank 5")
    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh formal output exists: {output}")
    if not PYTHON.is_file():
        raise RuntimeError(f"Python interpreter absent: {PYTHON}")
    for path in (
        CONTROLLER, RESET_SOURCE, ALGORITHM_SOURCE, WATCHDOG_SOURCE,
        AFFINITY_GUARD_SOURCE,
        OPTIMIZATION_CONTRACT,
    ):
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"sealed launch source absent or symlink: {path}")
    checkpoint = load(args.checkpoint)
    preflight = load(args.runtime_preflight)
    prepared = load(args.prepared_binding)
    accounts = load(args.benchmark_accounts)
    optimization = load(OPTIMIZATION_CONTRACT)
    replay_quarantine = load(args.replay_quarantine)
    fresh_r0 = load(args.fresh_r0)
    pilot_r0_waiver = (
        fresh_r0.get("status") == "R0_INVALIDATED_FAIL_CLOSED"
        and fresh_r0.get("formal_result_credit_prohibited") is True
        and fresh_r0.get("formal_outer_launch_prohibited") is True
        and fresh_r0.get("external_process_mutation") is False
        and fresh_r0.get("docker_mutation") is False
    )
    quarantine_keys = replay_quarantine.get("quarantined_param_keys_by_scene") or {}
    soft_keys = replay_quarantine.get("soft_replayed_param_keys_by_scene") or {}
    permanent_keys = replay_quarantine.get("permanent_invalid_param_keys_by_scene") or {}
    quarantine_counts = replay_quarantine.get("quarantined_counts_by_scene") or {}
    soft_counts = replay_quarantine.get("soft_replayed_counts_by_scene") or {}
    permanent_counts = replay_quarantine.get("permanent_invalid_counts_by_scene") or {}
    bindings = optimization.get("bindings") or {}
    completed = int(checkpoint.get("completed_outer_attempts") or 0)
    if (
        checkpoint.get("status") != "PREPARED"
        or completed < 2
        or preflight.get("status") != "PASS"
        or float(preflight.get("observed_seconds", 0)) < 60.0
        or preflight.get("formal_launch_authorized") is not True
        or prepared.get("status") != "PREPARED"
        or prepared.get("preflight_sha256") != sha(args.runtime_preflight)
        or accounts.get("status") != "PASS"
        or accounts.get("prepared_binding_sha256") != sha(args.prepared_binding)
        or accounts.get("passwords_emitted") is not False
        or len(accounts.get("workers") or []) != 8
        or (args.interference_override_pilot and not pilot_r0_waiver)
        or (
            not args.interference_override_pilot
            and (
                fresh_r0.get("status") != "PASS"
                or fresh_r0.get("fresh_r0_complete") is not True
                or fresh_r0.get("formal_outer_launch_authorized") is not True
                or fresh_r0.get("freeze_sha256") != checkpoint.get("freeze_sha256")
                or fresh_r0.get("preflight_sha256") != sha(args.runtime_preflight)
                or fresh_r0.get("prepared_binding_sha256") != sha(args.prepared_binding)
                or fresh_r0.get("benchmark_accounts_sha256") != sha(args.benchmark_accounts)
            )
        )
        or replay_quarantine.get("status") != "PASS"
        or replay_quarantine.get("formal_scope") != "mysql_only"
        or replay_quarantine.get("cross_engine_comparison_prohibited") is not True
        or replay_quarantine.get("freeze_sha256") != checkpoint.get("freeze_sha256")
        or replay_quarantine.get("resume_checkpoint_outer_attempt") != completed
        or replay_quarantine.get("formal_state_mutation") is not False
        or replay_quarantine.get("schema_version")
        != "mysql_algorithm_c_replay_quarantine_v6"
        or any(set(mapping) != set(SCENES) for mapping in (
            quarantine_keys, soft_keys, permanent_keys,
            quarantine_counts, soft_counts, permanent_counts,
        ))
        or replay_quarantine.get("cycle_reset_may_clear_soft_replayed") is not True
        or replay_quarantine.get("cycle_reset_may_clear_permanent_invalid") is not False
        or any(
            int(quarantine_counts[scene]) < 0
            or int(soft_counts[scene]) < 0
            or int(permanent_counts[scene]) < 0
            or len(quarantine_keys[scene]) != int(quarantine_counts[scene])
            or len(set(quarantine_keys[scene])) != int(quarantine_counts[scene])
            or len(soft_keys[scene]) != int(soft_counts[scene])
            or len(set(soft_keys[scene])) != int(soft_counts[scene])
            or len(permanent_keys[scene]) != int(permanent_counts[scene])
            or len(set(permanent_keys[scene])) != int(permanent_counts[scene])
            or int(soft_counts[scene]) + int(permanent_counts[scene])
            != int(quarantine_counts[scene])
            or set(soft_keys[scene]).intersection(permanent_keys[scene])
            or set(soft_keys[scene]) | set(permanent_keys[scene])
            != set(quarantine_keys[scene])
            for scene in SCENES
        )
        or optimization.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or bindings.get("reset_source_sha256") != sha(RESET_SOURCE)
        or bindings.get("algorithm_source_sha256") != sha(ALGORITHM_SOURCE)
        or bindings.get("external_pg_affinity_guard_sha256")
        != sha(AFFINITY_GUARD_SOURCE)
        or (
            args.interference_override_pilot
            and interference.get("freeze_sha256") != checkpoint.get("freeze_sha256")
        )
    ):
        raise RuntimeError("optimized boundary launch authority differs")

    log_root = ROOT / "history/mysql_four_scene_20260809/formal_launch_logs"
    log_root.mkdir(parents=True, exist_ok=True)
    stdout_path = log_root / f"{args.run_id}.stdout.log"
    stderr_path = log_root / f"{args.run_id}.stderr.log"
    unit = f"mysql-four-scene-algc-{args.run_id}.service"
    affinity_guard_unit = (
        f"mysql-four-scene-algc-affinity-{args.run_id}.service"
    )
    affinity_guard_log = log_root / f"{args.run_id}.external_pg_affinity.jsonl"
    affinity_guard_stdout = log_root / f"{args.run_id}.affinity.stdout.log"
    affinity_guard_stderr = log_root / f"{args.run_id}.affinity.stderr.log"
    affinity_guard_command = [
        "systemd-run", "--user",
        f"--unit={affinity_guard_unit.removesuffix('.service')}",
        "--description=MySQL formal frozen external PG client affinity guard",
        f"--property=WorkingDirectory={ROOT}",
        "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{affinity_guard_stdout}",
        f"--property=StandardError=append:{affinity_guard_stderr}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET, str(PYTHON),
        str(AFFINITY_GUARD_SOURCE),
        "--formal-unit", unit,
        "--log", str(affinity_guard_log),
        "--interval-seconds", "0.25",
        "--startup-grace-seconds", "60",
    ]
    affinity_started = subprocess.run(
        affinity_guard_command, cwd=ROOT, text=True, capture_output=True,
        check=False, timeout=30,
    )
    if affinity_started.returncode != 0:
        raise RuntimeError(
            "external PG affinity guard launch failed: "
            + affinity_started.stderr[-1000:]
        )
    affinity_deadline = time.monotonic() + 10.0
    affinity_guard_pid = 0
    while time.monotonic() < affinity_deadline:
        shown = subprocess.run(
            ["systemctl", "--user", "show", affinity_guard_unit,
             "-p", "ActiveState", "-p", "SubState", "-p", "MainPID"],
            text=True, capture_output=True, check=True, timeout=10,
        ).stdout
        affinity_fields = dict(
            line.split("=", 1) for line in shown.splitlines() if "=" in line
        )
        affinity_guard_pid = int(affinity_fields.get("MainPID", "0"))
        if affinity_fields.get("ActiveState") == "active" and affinity_guard_pid > 0:
            break
        if affinity_fields.get("ActiveState") in {"failed", "inactive"}:
            raise RuntimeError(
                f"external PG affinity guard exited during startup: {affinity_fields}"
            )
        time.sleep(0.25)
    else:
        subprocess.run(
            ["systemctl", "--user", "stop", affinity_guard_unit],
            check=False, timeout=30,
        )
        raise RuntimeError("external PG affinity guard did not become active in 10s")
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        "--description=" + (
            "MySQL four-scene interference override non-formal pilot"
            if args.interference_override_pilot
            else "MySQL four-scene optimized Algorithm C boundary resume"
        ),
        f"--property=WorkingDirectory={ROOT}",
        "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout_path}",
        f"--property=StandardError=append:{stderr_path}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET, str(PYTHON),
        str(CONTROLLER), "--run-id", args.run_id,
        "--checkpoint", str(args.checkpoint),
        "--runtime-preflight", str(args.runtime_preflight),
        "--prepared-binding", str(args.prepared_binding),
        "--benchmark-accounts", str(args.benchmark_accounts),
        "--fresh-r0", str(args.fresh_r0),
        "--replay-quarantine", str(args.replay_quarantine),
    ]
    if args.fresh_search:
        command.append("--fresh-search")
    if args.interference_override_pilot:
        command.extend([
            "--interference-override-pilot",
            "--interference-evidence", str(args.interference_evidence),
            "--pilot-start-rank", str(args.pilot_start_rank),
        ])
        if args.pilot_baseline is not None:
            command.extend(["--pilot-baseline", str(args.pilot_baseline)])
    elif args.formal_partial_root is not None:
        command.extend([
            "--formal-partial-root", str(args.formal_partial_root),
            "--formal-start-rank", str(args.formal_start_rank),
        ])
        if args.formal_completed_rank5_root is not None:
            command.extend([
                "--formal-completed-rank5-root",
                str(args.formal_completed_rank5_root),
            ])
    started = subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True,
        check=False, timeout=30,
    )
    if started.returncode != 0:
        subprocess.run(
            ["systemctl", "--user", "stop", affinity_guard_unit],
            check=False, timeout=30,
        )
        raise RuntimeError(f"systemd boundary launch failed: {started.stderr[-1000:]}")

    # The controller may need to complete the mandatory rolling-window
    # interference gate before it can atomically publish RUNNING state.  That
    # gate alone can consume roughly 25--30 seconds, so the old 30-second
    # launcher deadline could stop a healthy formal controller in the narrow
    # interval between gate completion and the next systemd poll.  This is an
    # upper bound only: verification still returns immediately once the state
    # is visible.
    deadline = time.monotonic() + STARTUP_STATE_TIMEOUT_SECONDS
    state_path = output / "run_state.json"
    state: dict[str, Any] | None = None
    main_pid = 0
    fields: dict[str, str] = {}
    while time.monotonic() < deadline:
        shown = subprocess.run(
            ["systemctl", "--user", "show", unit, "-p", "ActiveState",
             "-p", "SubState", "-p", "MainPID", "-p", "Result"],
            text=True, capture_output=True, check=True, timeout=10,
        ).stdout
        fields = dict(
            line.split("=", 1) for line in shown.splitlines() if "=" in line
        )
        main_pid = int(fields.get("MainPID", "0"))
        if fields.get("ActiveState") == "failed" or (
            fields.get("ActiveState") == "inactive" and main_pid == 0
        ):
            subprocess.run(
                ["systemctl", "--user", "stop", affinity_guard_unit],
                check=False, timeout=30,
            )
            raise RuntimeError(
                f"boundary controller exited during startup: {fields}; "
                f"stderr={stderr_path}"
            )
        if state_path.is_file():
            state = load(state_path)
            if (
                main_pid > 0
                and state.get("status") == "RUNNING"
                and state.get("active_scenes") == SCENES
                and state.get("completed_predecessor_outer_attempts") == completed
                and state.get("optimization_contract_sha256")
                == sha(OPTIMIZATION_CONTRACT)
                and state.get("formal_evidence_eligible")
                is (not args.interference_override_pilot)
                and state.get("accepted_state_credit_prohibited")
                is args.interference_override_pilot
            ):
                break
        time.sleep(0.25)
    else:
        subprocess.run(
            ["systemctl", "--user", "stop", unit], check=False, timeout=30
        )
        subprocess.run(
            ["systemctl", "--user", "stop", affinity_guard_unit],
            check=False, timeout=30,
        )
        raise RuntimeError(
            "boundary controller did not publish RUNNING state in "
            f"{STARTUP_STATE_TIMEOUT_SECONDS:g}s"
        )
    assert state is not None
    watchdog_unit: str | None = None
    watchdog_pid: int | None = None
    watchdog_output: Path | None = None
    if not args.interference_override_pilot:
        watchdog_unit = f"mysql-four-scene-algc-watchdog-{args.run_id}.service"
        watchdog_output = output / "runtime_external_interference_watchdog.jsonl"
        watchdog_stdout = log_root / f"{args.run_id}.watchdog.stdout.log"
        watchdog_stderr = log_root / f"{args.run_id}.watchdog.stderr.log"
        watchdog_command = [
            "systemd-run", "--user",
            f"--unit={watchdog_unit.removesuffix('.service')}",
            "--description=MySQL formal full-wave external workload watchdog",
            f"--property=WorkingDirectory={ROOT}",
            "--property=KillMode=control-group",
            f"--property=StandardOutput=append:{watchdog_stdout}",
            f"--property=StandardError=append:{watchdog_stderr}",
            str(PYTHON), str(WATCHDOG_SOURCE),
            "--formal-unit", unit,
            "--formal-run-id", args.run_id,
            "--output", str(watchdog_output),
            "--interval-seconds", "1",
        ]
        watchdog_started = subprocess.run(
            watchdog_command, cwd=ROOT, text=True, capture_output=True,
            check=False, timeout=30,
        )
        if watchdog_started.returncode != 0:
            subprocess.run(
                ["systemctl", "--user", "stop", unit], check=False, timeout=30,
            )
            subprocess.run(
                ["systemctl", "--user", "stop", affinity_guard_unit],
                check=False, timeout=30,
            )
            raise RuntimeError(
                "formal watchdog launch failed; controller stopped fail closed: "
                + watchdog_started.stderr[-1000:]
            )
        watchdog_deadline = time.monotonic() + 10.0
        while time.monotonic() < watchdog_deadline:
            shown = subprocess.run(
                ["systemctl", "--user", "show", watchdog_unit,
                 "-p", "ActiveState", "-p", "SubState", "-p", "MainPID"],
                text=True, capture_output=True, check=True, timeout=10,
            ).stdout
            watchdog_fields = dict(
                line.split("=", 1) for line in shown.splitlines() if "=" in line
            )
            watchdog_pid = int(watchdog_fields.get("MainPID", "0"))
            if watchdog_fields.get("ActiveState") == "active" and watchdog_pid > 0:
                break
            if watchdog_fields.get("ActiveState") in {"failed", "inactive"}:
                subprocess.run(
                    ["systemctl", "--user", "stop", unit],
                    check=False, timeout=30,
                )
                subprocess.run(
                    ["systemctl", "--user", "stop", affinity_guard_unit],
                    check=False, timeout=30,
                )
                raise RuntimeError(
                    f"formal watchdog exited during startup: {watchdog_fields}"
                )
            time.sleep(0.25)
        else:
            subprocess.run(
                ["systemctl", "--user", "stop", unit], check=False, timeout=30,
            )
            subprocess.run(
                ["systemctl", "--user", "stop", affinity_guard_unit],
                check=False, timeout=30,
            )
            raise RuntimeError("formal watchdog did not become active in 10s")
    receipt = {
        "schema_version": "mysql_algorithm_c_optimized_boundary_launch_receipt_v2",
        "status": "STARTED",
        "verified_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "execution_class": (
            "INTERFERENCE_OVERRIDE_PILOT"
            if args.interference_override_pilot else "FORMAL_MYSQL_ONLY"
        ),
        "formal_evidence_eligible": not args.interference_override_pilot,
        "accepted_state_credit_prohibited": args.interference_override_pilot,
        "active_scenes": SCENES,
        "completed_predecessor_outer_attempts": completed,
        "fresh_search": bool(args.fresh_search),
        "predecessor_outer_state_credit": not args.fresh_search,
        "pid": main_pid,
        "pid_alive_at_verification": True,
        "systemd_unit": unit,
        "systemd_managed": True,
        "management_cpuset": MANAGEMENT_CPUSET,
        "controller_source_path": str(CONTROLLER),
        "controller_source_sha256": sha(CONTROLLER),
        "reset_source_path": str(RESET_SOURCE),
        "reset_source_sha256": sha(RESET_SOURCE),
        "algorithm_source_path": str(ALGORITHM_SOURCE),
        "algorithm_source_sha256": sha(ALGORITHM_SOURCE),
        "runtime_watchdog_source_path": str(WATCHDOG_SOURCE),
        "runtime_watchdog_source_sha256": sha(WATCHDOG_SOURCE),
        "runtime_watchdog_unit": watchdog_unit,
        "runtime_watchdog_pid": watchdog_pid,
        "runtime_watchdog_output": str(watchdog_output) if watchdog_output else None,
        "external_pg_affinity_guard_source_path": str(AFFINITY_GUARD_SOURCE),
        "external_pg_affinity_guard_source_sha256": sha(AFFINITY_GUARD_SOURCE),
        "external_pg_affinity_guard_unit": affinity_guard_unit,
        "external_pg_affinity_guard_pid": affinity_guard_pid,
        "external_pg_affinity_guard_log": str(affinity_guard_log),
        "external_pg_affinity_guard_management_cpuset": MANAGEMENT_CPUSET,
        "optimization_contract_path": str(OPTIMIZATION_CONTRACT),
        "optimization_contract_sha256": sha(OPTIMIZATION_CONTRACT),
        "checkpoint_path": str(args.checkpoint),
        "checkpoint_sha256": sha(args.checkpoint),
        "runtime_preflight_path": str(args.runtime_preflight),
        "runtime_preflight_sha256": sha(args.runtime_preflight),
        "prepared_binding_path": str(args.prepared_binding),
        "prepared_binding_sha256": sha(args.prepared_binding),
        "benchmark_accounts_path": str(args.benchmark_accounts),
        "benchmark_accounts_sha256": sha(args.benchmark_accounts),
        "fresh_r0_path": str(args.fresh_r0),
        "fresh_r0_sha256": sha(args.fresh_r0),
        "replay_quarantine_path": str(args.replay_quarantine),
        "replay_quarantine_sha256": sha(args.replay_quarantine),
        "interference_evidence_path": (
            str(args.interference_evidence)
            if args.interference_override_pilot else None
        ),
        "interference_evidence_sha256": (
            sha(args.interference_evidence)
            if args.interference_override_pilot else None
        ),
        "pilot_start_rank": args.pilot_start_rank if args.interference_override_pilot else None,
        "pilot_baseline_path": str(args.pilot_baseline) if args.pilot_baseline else None,
        "pilot_baseline_sha256": sha(args.pilot_baseline) if args.pilot_baseline else None,
        "formal_partial_root": (
            str(args.formal_partial_root) if args.formal_partial_root else None
        ),
        "formal_start_rank": (
            args.formal_start_rank if args.formal_partial_root else None
        ),
        "run_state_path": str(state_path),
        "run_state_sha256_at_verification": sha(state_path),
        "stage_at_verification": state.get("stage"),
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
    }
    atomic(output / "launch_receipt.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
