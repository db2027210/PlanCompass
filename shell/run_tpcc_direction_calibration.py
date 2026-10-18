#!/usr/bin/env python3
"""Collect fresh TPC-C CPU direction evidence without starting optimization.

Each block owns one fresh incumbent scope.  It measures the PostgreSQL default
cost vector and ten single-coordinate probes.  The resulting
``fresh_baseline.json`` and ``fresh_model_calibration.jsonl`` files are directly
consumable by ``build_replay_direction_dataset.py``.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence

from run_tpcc_sensitive_stable_point import terminate_owned_runtime_processes


ROOT = Path(__file__).resolve().parents[1]
RESTORE = ROOT / "shell/restore_pg5_tpcc_from_template.sh"
POINT_RUNNER = ROOT / "shell/run_tpcc_sensitive_stable_point.py"
PARAMETERS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
DEFAULT_PARAMS: Mapping[str, float] = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}
# Sign of an increase expected to reduce CPU according to the probe-only prior.
CPU_REDUCING_INCREASE: Mapping[str, bool] = {
    "seq_page_cost": False,
    "random_page_cost": False,
    "cpu_tuple_cost": True,
    "cpu_index_tuple_cost": False,
    "cpu_operator_cost": True,
    "parallel_setup_cost": True,
    "parallel_tuple_cost": True,
}
def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--blocks", type=int, default=3)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume an existing output directory and reuse strictly valid evidence.",
    )
    parser.add_argument(
        "--retry-technical-failures",
        action="store_true",
        help=(
            "With --resume, retry only IDs whose latest terminal state is "
            "technical_failure; retain the previous failure records."
        ),
    )
    parser.add_argument("--python", default="/home/anonymous/miniconda3/bin/python3")
    parser.add_argument("--container", default="pg5_tpcc_d2_20260722")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=59640)
    parser.add_argument("--database", default="fullgrid_tpcc")
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--password", default="postgres")
    parser.add_argument("--cpu-limit-cores", type=float, default=4.0)
    parser.add_argument("--memory-limit-bytes", type=int, default=5 * 1024 ** 3)
    parser.add_argument("--io-total-limit-mib-s", type=float, default=54.0)
    parser.add_argument("--io-read-cap-mib-s", type=float, default=27.0)
    parser.add_argument("--io-write-cap-mib-s", type=float, default=27.0)
    return parser.parse_args(argv)


def append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def run_checked(
    command: Sequence[str],
    *,
    env: Optional[Mapping[str, str]] = None,
) -> None:
    process = subprocess.run(command, check=False, text=True, env=env)
    if process.returncode != 0:
        raise RuntimeError(f"command failed ({process.returncode}): {' '.join(command)}")


def apply_params(args: argparse.Namespace, params: Mapping[str, float]) -> None:
    env = os.environ.copy()
    env["PGPASSWORD"] = str(args.password)
    prefix = [
            "psql", "-h", str(args.host), "-p", str(args.port),
            "-U", str(args.user), "-d", str(args.database),
            "-v", "ON_ERROR_STOP=1", "-Atc",
    ]
    # ALTER SYSTEM must be sent as standalone commands; combining multiple
    # statements in one psql -c can place them in a transaction block.
    for name in PARAMETERS:
        run_checked(
            prefix + [f"ALTER SYSTEM SET {name} = '{float(params[name]):.17g}';"],
            env=env,
        )
    run_checked(prefix + ["SELECT pg_reload_conf();"], env=env)


def reconcile_container_resources(args: argparse.Namespace) -> Dict[str, Any]:
    """Apply and verify the physical cgroup contract used by the denominator."""
    run_checked(
        [
            "docker",
            "update",
            "--cpus",
            str(args.cpu_limit_cores),
            "--memory",
            str(args.memory_limit_bytes),
            "--memory-swap",
            str(args.memory_limit_bytes),
            str(args.container),
        ]
    )
    process = subprocess.run(
        ["docker", "inspect", str(args.container)],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(process.stdout)
    if not isinstance(payload, list) or len(payload) != 1:
        raise RuntimeError(f"unexpected docker inspect result for {args.container}")
    host_config = payload[0].get("HostConfig") or {}
    nano_cpus = int(host_config.get("NanoCpus") or 0)
    memory = int(host_config.get("Memory") or 0)
    memory_swap = int(host_config.get("MemorySwap") or 0)
    swap_devices = [
        line
        for line in Path("/proc/swaps").read_text(encoding="utf-8").splitlines()[1:]
        if line.strip()
    ]
    expected_nano_cpus = int(round(args.cpu_limit_cores * 1_000_000_000))
    if nano_cpus != expected_nano_cpus:
        raise RuntimeError(
            f"CPU cgroup readback mismatch: {nano_cpus} != {expected_nano_cpus}"
        )
    if memory != args.memory_limit_bytes:
        raise RuntimeError(
            f"memory cgroup readback mismatch: {memory} != {args.memory_limit_bytes}"
        )
    swap_limit_verified = memory_swap == args.memory_limit_bytes
    swap_effectively_disabled = memory_swap == -1 and not swap_devices
    if not swap_limit_verified and not swap_effectively_disabled:
        raise RuntimeError(
            "memory+swap cgroup readback mismatch: "
            f"{memory_swap} != {args.memory_limit_bytes}"
        )
    return {
        "container": args.container,
        "nano_cpus": nano_cpus,
        "cpu_limit_cores": nano_cpus / 1_000_000_000,
        "memory_limit_bytes": memory,
        "memory_swap_limit_bytes": memory_swap,
        "host_swap_devices": swap_devices,
        "swap_effectively_disabled": swap_effectively_disabled,
        "cpuset_cpus": str(host_config.get("CpusetCpus") or ""),
        "verified": True,
    }


def restart_owned_container(args: argparse.Namespace) -> Dict[str, Any]:
    """Reset PostgreSQL state after sealing its physical cgroup contract."""
    readback = reconcile_container_resources(args)
    run_checked(["docker", "restart", str(args.container)])
    env = os.environ.copy()
    env["PGPASSWORD"] = str(args.password)
    deadline = time.monotonic() + 180.0
    while time.monotonic() < deadline:
        process = subprocess.run(
            [
                "psql", "-h", str(args.host), "-p", str(args.port),
                "-U", str(args.user), "-d", "postgres", "-Atqc", "SELECT 1;",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        if process.returncode == 0 and process.stdout.strip() == "1":
            return readback
        time.sleep(2.0)
    raise RuntimeError(f"PostgreSQL did not become ready after restarting {args.container}")


def point_command(
    args: argparse.Namespace,
    point_root: Path,
    *,
    seed: int,
    require_r0_profile: bool,
) -> List[str]:
    command = [
        str(args.python), "-u", str(POINT_RUNNER),
        "--output-root", str(point_root),
        "--container", str(args.container),
        "--host", str(args.host),
        "--port", str(args.port),
        "--database", str(args.database),
        "--user", str(args.user),
        "--password", str(args.password),
        "--cpu-limit-cores", str(args.cpu_limit_cores),
        "--memory-limit-bytes", str(args.memory_limit_bytes),
        "--io-total-limit-mib-s", str(args.io_total_limit_mib_s),
        "--io-read-cap-mib-s", str(args.io_read_cap_mib_s),
        "--io-write-cap-mib-s", str(args.io_write_cap_mib_s),
        "--stability-target-resource", "cpu",
        "--seed", str(seed),
    ]
    if require_r0_profile:
        command.extend(["--r0-target-resource", "cpu"])
    return command


def raw_result(
    args: argparse.Namespace,
    summary: Mapping[str, Any],
    params: Mapping[str, float],
) -> Dict[str, Any]:
    stable = bool((summary.get("window_mean_stability") or {}).get("stable"))
    cleanup_passed = bool((summary.get("worker_cleanup") or {}).get("fully_released"))
    sql_clean = int(summary.get("native_sql_exception_occurrences") or 0) == 0
    usable = (
        summary.get("status") == "complete"
        and stable
        and cleanup_passed
        and sql_clean
    )
    return {
        "status": "ok" if usable else "failed",
        "double_loop_measurement_usable": usable,
        "workload": "tpcc_sensitive",
        "target_resource": "cpu",
        "scenario": "cpu",
        "params": dict(params),
        "db_qps": summary.get("db_qps"),
        "resource_utilization": summary.get("resource_utilization"),
        "runtime_capability": {
            "cpu": args.cpu_limit_cores,
            "memory": args.memory_limit_bytes / 1024 ** 3,
            "io": args.io_total_limit_mib_s,
            "memory_limit_bytes": args.memory_limit_bytes,
            "io_read_cap_mib_s": args.io_read_cap_mib_s,
            "io_write_cap_mib_s": args.io_write_cap_mib_s,
        },
        "candidate_measurement_protocol": {
            "stable": stable,
            "worker_cleanup_passed": cleanup_passed,
            "native_sql_exception_occurrences": int(
                summary.get("native_sql_exception_occurrences") or 0
            ),
            "effective_measurement_seconds": summary.get("effective_measurement_seconds"),
            "stop_reason": summary.get("stop_reason"),
        },
        "summary_path": summary.get("_summary_path"),
    }


def run_point(
    args: argparse.Namespace,
    *,
    point_root: Path,
    params: Mapping[str, float],
    seed: int,
    label: str,
    require_r0_profile: bool = False,
) -> Dict[str, Any]:
    for attempt in range(1, args.max_attempts + 1):
        print(f"[{label}] attempt {attempt}/{args.max_attempts}: restore/apply/run", flush=True)
        cleanup = terminate_owned_runtime_processes(point_root)
        if cleanup["matched_pids"]:
            print(
                f"[{label}] released stale owned workload processes: {cleanup}",
                flush=True,
            )
        if not cleanup["fully_released"]:
            raise RuntimeError(
                f"failed to release stale TPC-C workload processes: {cleanup}"
            )
        cgroup_readback = restart_owned_container(args)
        (point_root / f"{label}_attempt{attempt}_runtime_cgroup_readback.json").write_text(
            json.dumps(cgroup_readback, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_checked([str(RESTORE)])
        apply_params(args, params)
        before = {path.resolve() for path in point_root.glob("point_*") if path.is_dir()}
        log_path = point_root / f"{label}_attempt{attempt}.log"
        with log_path.open("w", encoding="utf-8") as log_handle:
            process = subprocess.run(
                point_command(
                    args,
                    point_root,
                    seed=seed + attempt - 1,
                    require_r0_profile=require_r0_profile,
                ),
                check=False,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
            )
        created = [
            path for path in point_root.glob("point_*")
            if path.is_dir() and path.resolve() not in before
        ]
        if not created:
            print(f"[{label}] no point directory; see {log_path}", flush=True)
            continue
        point_dir = max(created, key=lambda path: path.stat().st_mtime_ns)
        summary_path = point_dir / "summary.json"
        if not summary_path.is_file():
            print(f"[{label}] missing summary; see {log_path}", flush=True)
            continue
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["_summary_path"] = str(summary_path.resolve())
        raw = raw_result(args, summary, params)
        r0_passed = (summary.get("r0_profile_construction_gate") or {}).get("passed")
        profile_ok = not require_r0_profile or r0_passed is True
        if process.returncode == 0 and raw["double_loop_measurement_usable"] and profile_ok:
            return raw
        print(f"[{label}] unstable/failed; see {summary_path}", flush=True)
    raise RuntimeError(f"{label} failed after {args.max_attempts} attempts")


def r0_profile_passes(summary: Mapping[str, Any]) -> bool:
    utilization = summary.get("resource_utilization") or {}
    try:
        values = {
            name: float(utilization[name]) * 100.0
            for name in ("cpu", "memory", "io")
        }
    except (KeyError, TypeError, ValueError):
        return False
    return (
        85.0 <= values["cpu"] <= 90.0
        and 30.0 <= values["memory"] <= 50.0
        and 30.0 <= values["io"] <= 50.0
    )


def recover_r0_baseline(
    args: argparse.Namespace,
    block_dir: Path,
    *,
    params: Mapping[str, float],
) -> Optional[Dict[str, Any]]:
    """Recover only evidence rejected by the former target-only R0 field bug."""
    calibration_path = block_dir / "fresh_model_calibration.jsonl"
    if calibration_path.exists() and calibration_path.stat().st_size > 0:
        return None
    for summary_path in sorted((block_dir / "points").glob("point_*/summary.json")):
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        recoverable = (
            summary.get("status") == "failed"
            and summary.get("stop_reason") == "r0_profile_reconstruct_early_stop"
            and summary.get("intentionally_terminated_after_stability") is True
            and int(summary.get("workload_returncode") or 0) in (-15, -9)
            and int(summary.get("native_sql_exception_occurrences") or 0) == 0
            and (summary.get("worker_cleanup") or {}).get("fully_released") is True
            and (summary.get("window_mean_stability") or {}).get("stable") is True
            and r0_profile_passes(summary)
        )
        if not recoverable:
            continue
        summary["_summary_path"] = str(summary_path.resolve())
        raw = raw_result(args, summary, params)
        raw.update(
            {
                "status": "ok",
                "double_loop_measurement_usable": True,
                "recovered_from_r0_profile_field_bug": True,
                "original_summary_status": "failed",
            }
        )
        print(f"[resume] recovered valid R0 baseline: {summary_path}", flush=True)
        return raw
    return None


def completed_calibration_ids(
    path: Path,
    *,
    retry_technical_failures: bool = False,
) -> set:
    statuses: Dict[str, str] = {}
    if not path.is_file():
        return set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("status") in {"usable", "technical_failure"} and row.get(
                "calibration_id"
            ):
                statuses[str(row["calibration_id"])] = str(row["status"])
    return {
        calibration_id
        for calibration_id, status in statuses.items()
        if status == "usable"
        or (status == "technical_failure" and not retry_technical_failures)
    }


def calibration_design(block_index: int) -> List[Dict[str, Any]]:
    factor = (1.25, 1.5, 2.0)[(block_index - 1) % 3]
    rows: List[Dict[str, Any]] = []
    for name in PARAMETERS:
        increase = CPU_REDUCING_INCREASE[name]
        params = dict(DEFAULT_PARAMS)
        params[name] *= factor if increase else 1.0 / factor
        rows.append({"parameter": name, "direction": "increase" if increase else "decrease", "params": params})
    # Rotate three opposite probes so all seven axes obtain both signs across
    # three independent incumbent scopes without increasing a block above 10.
    reverse_names = [PARAMETERS[(3 * (block_index - 1) + offset) % len(PARAMETERS)] for offset in range(3)]
    for name in reverse_names:
        increase = not CPU_REDUCING_INCREASE[name]
        params = dict(DEFAULT_PARAMS)
        params[name] *= factor if increase else 1.0 / factor
        rows.append({"parameter": name, "direction": "increase" if increase else "decrease", "params": params})
    return rows


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.retry_technical_failures and not args.resume:
        raise ValueError("--retry-technical-failures requires --resume")
    if args.blocks < 3:
        raise ValueError("--blocks must be at least 3 for grouped holdout")
    if args.max_attempts < 1 or args.max_attempts > 3:
        raise ValueError("--max-attempts must be in [1,3]")
    if args.cpu_limit_cores <= 0 or args.memory_limit_bytes <= 0:
        raise ValueError("CPU and memory limits must be positive")
    if min(
        args.io_total_limit_mib_s,
        args.io_read_cap_mib_s,
        args.io_write_cap_mib_s,
    ) <= 0:
        raise ValueError("I/O limits must be positive")
    io_sum = args.io_read_cap_mib_s + args.io_write_cap_mib_s
    if abs(io_sum - args.io_total_limit_mib_s) > 1e-9:
        raise ValueError(
            "read and write I/O caps must sum to --io-total-limit-mib-s"
        )
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=args.resume)
    try:
        for block_index in range(1, args.blocks + 1):
            block_dir = output_dir / f"scope_{block_index:02d}" / "tpcc_sensitive" / "cpu"
            points_dir = block_dir / "points"
            points_dir.mkdir(parents=True, exist_ok=args.resume)
            baseline_path = block_dir / "fresh_baseline.json"
            if baseline_path.is_file() and args.resume:
                baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
                baseline_raw = baseline["raw"]
                print(f"[resume] baseline already complete: {baseline_path}", flush=True)
            else:
                baseline_raw = (
                    recover_r0_baseline(args, block_dir, params=DEFAULT_PARAMS)
                    if args.resume
                    else None
                )
                if baseline_raw is None:
                    baseline_raw = run_point(
                        args,
                        point_root=points_dir,
                        params=DEFAULT_PARAMS,
                        seed=4508333 + block_index * 1000,
                        label=f"scope{block_index:02d}_baseline",
                        require_r0_profile=True,
                    )
                baseline = {
                    "event": "fresh_baseline",
                    "scenario": "cpu",
                    "fresh_prefix_source": f"tpcc_direction_calibration_scope_{block_index:02d}",
                    "params": dict(DEFAULT_PARAMS),
                    "measurement": {
                        "db_qps": baseline_raw["db_qps"],
                        **dict(baseline_raw["resource_utilization"] or {}),
                    },
                    "raw": baseline_raw,
                }
                baseline_path.write_text(
                    json.dumps(baseline, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            calibration_path = block_dir / "fresh_model_calibration.jsonl"
            completed_ids = completed_calibration_ids(
                calibration_path,
                retry_technical_failures=args.retry_technical_failures,
            )
            for candidate_index, design in enumerate(calibration_design(block_index), start=1):
                calibration_id = f"scope{block_index:02d}_axis_{candidate_index:02d}"
                if calibration_id in completed_ids:
                    print(f"[resume] calibration already complete: {calibration_id}", flush=True)
                    continue
                prior_attempt_logs = sorted(
                    points_dir.glob(f"{calibration_id}_attempt*.log")
                )
                if (
                    args.resume
                    and not args.retry_technical_failures
                    and len(prior_attempt_logs) >= args.max_attempts
                ):
                    append_jsonl(
                        calibration_path,
                        {
                            "event": "fresh_model_calibration_replay",
                            "calibration_id": calibration_id,
                            "phase": "coordinate_axis_direction_calibration",
                            "design": "single_coordinate_current_profile",
                            "parameter": design["parameter"],
                            "direction": design["direction"],
                            "changed_parameters": [design["parameter"]],
                            "params": design["params"],
                            "status": "technical_failure",
                            "current_run_only": True,
                            "used_for_optimizer_candidate_selection": False,
                            "error": "recovered exhausted technical-failure attempts",
                            "attempts_exhausted": len(prior_attempt_logs),
                            "attempt_logs": [
                                str(path.resolve()) for path in prior_attempt_logs
                            ],
                        },
                    )
                    print(
                        f"[resume] recorded exhausted technical failure: "
                        f"{calibration_id}",
                        flush=True,
                    )
                    continue
                try:
                    run_label = (
                        f"{calibration_id}_technical_retry"
                        if args.retry_technical_failures
                        else calibration_id
                    )
                    raw = run_point(
                        args,
                        point_root=points_dir,
                        params=design["params"],
                        seed=4508333 + block_index * 1000 + candidate_index * 10,
                        label=run_label,
                    )
                except RuntimeError as exc:
                    append_jsonl(
                        calibration_path,
                        {
                            "event": "fresh_model_calibration_replay",
                            "calibration_id": calibration_id,
                            "phase": "coordinate_axis_direction_calibration",
                            "design": "single_coordinate_current_profile",
                            "parameter": design["parameter"],
                            "direction": design["direction"],
                            "changed_parameters": [design["parameter"]],
                            "params": design["params"],
                            "status": "technical_failure",
                            "current_run_only": True,
                            "used_for_optimizer_candidate_selection": False,
                            "error": str(exc),
                            "attempts_exhausted": args.max_attempts,
                        },
                    )
                    print(
                        f"[{calibration_id}] recorded technical failure; "
                        "continuing with the remaining calibration batch",
                        flush=True,
                    )
                    continue
                append_jsonl(
                    calibration_path,
                    {
                        "event": "fresh_model_calibration_replay",
                        "calibration_id": calibration_id,
                        "phase": "coordinate_axis_direction_calibration",
                        "design": "single_coordinate_current_profile",
                        "parameter": design["parameter"],
                        "direction": design["direction"],
                        "changed_parameters": [design["parameter"]],
                        "params": design["params"],
                        "status": "usable",
                        "current_run_only": True,
                        "used_for_optimizer_candidate_selection": False,
                        "raw_result": raw,
                    },
                )
            print(f"[scope {block_index}/{args.blocks}] complete: {block_dir}", flush=True)
    finally:
        try:
            apply_params(args, DEFAULT_PARAMS)
        except Exception as exc:
            print(f"[WARN] failed to restore PostgreSQL defaults: {exc}", file=sys.stderr, flush=True)
    print(f"calibration complete: {output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
