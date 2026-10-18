#!/usr/bin/env python3
"""Prepare and launch isolated formal double-loop matrix workers."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
MATRIX_ROOT = Path("/disk2/resource_adaptive_experiment_matrix_20260718")
HYBENCH_ROOT = Path("/home/anonymous/HyBench-2024-master")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_id(vm: Dict[str, Any]) -> str:
    return (
        f"{str(vm['exp_id']).lower()}_r{int(vm['repeat_id'])}_"
        f"{vm['benchmark']}_{vm['target']}"
    )


def effective_cpu(vm: Dict[str, Any]) -> int:
    """Honor the independently prepared VM manifest for every target."""
    return int(vm["cpu"])


def effective_memory_mib(vm: Dict[str, Any]) -> int:
    # The independently prepared VM manifest is the physical source of truth.
    # This preserves per-run memory pilots instead of silently replacing them
    # with an old hard-coded profile during runtime-config generation.
    return int(vm["memory_mib"])


def effective_disk_mb(vm: Dict[str, Any]) -> Tuple[float, float]:
    # The rebuilt VM manifest is the physical source of truth.  In particular,
    # IO pilots may deliberately change the read/write split while preserving
    # the same total capacity; silently restoring the historical 4.5/0.1
    # profile here would make the runtime capacity and seeded resets disagree
    # with the actual VM that was prepared.
    return float(vm["read_mb"]), float(vm["write_mb"])


def prepare_command(
    vm: Dict[str, Any],
    runtime_dir: Path,
    *,
    hybench_root: Path = HYBENCH_ROOT,
    tp_clients_override: int = 0,
    ap_clients_override: int = 0,
    hybench_ap_work_mem_mb: int = 64,
    hybench_ap_shared_buffers_mb: int = 0,
    hybench_ap_max_parallel_workers_per_gather: int = -1,
    hybench_ap_query_ids: str = "",
    hybench_ap_query_weights: str = "",
    hybench_ap_query_sequence: str = "",
    hybench_tp_percent_mix: str = "",
    vm_shm_mb: int = 0,
    qmp_socket: str = "",
    job_workers_override: int = 0,
    job_work_mem_mb: int = 0,
    job_min_seq_page_cost: float = 0.0,
    job_fixed_horizon_s: int = 0,
    workload_fixed_horizon_s: int = 0,
    statement_timeout_ms: int = 900000,
    benchbase_terminals_override: int = 0,
    benchbase_work_mem_mb: int = 0,
    benchbase_shared_buffers_mb: int = 0,
    benchbase_max_parallel_workers_per_gather: int = -1,
) -> List[str]:
    run_mins = 4 if vm["target"] == "io" else 3
    benchbase_terminals = (
        int(benchbase_terminals_override)
        if int(benchbase_terminals_override) > 0
        else 80
        if vm["benchmark"] == "tpcc" and vm["target"] == "cpu"
        else (
            32
            if vm["benchmark"] == "tpch" and vm["target"] == "memory"
            else (32 if vm["benchmark"] == "tpcc" else 16)
        )
    )
    read_mb, write_mb = effective_disk_mb(vm)
    command = [
        sys.executable,
        str(ROOT / "shell/prepare_double_loop_matrix_runtime.py"),
        "--benchmark",
        str(vm["benchmark"]),
        "--target",
        str(vm["target"]),
        "--vm-name",
        str(vm["name"]),
        "--exp-id",
        str(vm["exp_id"]),
        "--repeat-id",
        str(vm["repeat_id"]),
        "--mac",
        str(vm["mac"]),
        "--host",
        str(vm["ip"]),
        "--ssh-port",
        str(int(vm.get("ssh_port") or 22)),
        "--port",
        str(int(vm.get("postgres_port") or 25432)),
        "--output-dir",
        str(runtime_dir),
        "--hybench-root",
        str(hybench_root),
        "--cpu-cores",
        str(effective_cpu(vm)),
        "--mem-gb",
        str(float(effective_memory_mib(vm)) / 1024.0),
        "--measured-mem-gb",
        str(float(vm.get("memory_capacity_gib") or 0.0)),
        "--read-mb",
        str(read_mb),
        "--write-mb",
        str(write_mb),
        "--disk-isolation-id",
        f"{vm['name']}:{vm['overlay']}",
        "--qmp-socket",
        str(qmp_socket),
        "--run-mins",
        str(run_mins),
        "--tp-clients",
        (
            str(tp_clients_override)
            if tp_clients_override > 0 and str(vm["benchmark"]).startswith("hybench_")
            else "80"
            if vm["benchmark"] == "hybench_tp"
            else ("64" if vm["benchmark"] == "hybench_xp" and vm["target"] == "io" else "32")
        ),
        "--ap-clients",
        (
            str(ap_clients_override)
            if ap_clients_override > 0 and str(vm["benchmark"]).startswith("hybench_")
            else "16"
            if vm["benchmark"] == "hybench_ap" and vm["target"] == "memory"
            else ("8" if vm["benchmark"] == "hybench_xp" and vm["target"] == "io" else "4")
        ),
        "--hybench-ap-work-mem-mb",
        str(hybench_ap_work_mem_mb),
        "--hybench-ap-shared-buffers-mb",
        str(hybench_ap_shared_buffers_mb),
        "--hybench-ap-query-ids",
        str(hybench_ap_query_ids),
        "--hybench-ap-query-weights",
        str(hybench_ap_query_weights),
        "--hybench-ap-query-sequence",
        str(hybench_ap_query_sequence),
        "--hybench-tp-percent-mix",
        str(hybench_tp_percent_mix),
        "--hybench-ap-max-parallel-workers-per-gather",
        str(hybench_ap_max_parallel_workers_per_gather),
        "--vm-shm-mb",
        str(vm_shm_mb),
        "--benchbase-terminals",
        str(benchbase_terminals),
        "--benchbase-work-mem-mb",
        str(benchbase_work_mem_mb),
        "--benchbase-shared-buffers-mb",
        str(benchbase_shared_buffers_mb),
        "--benchbase-max-parallel-workers-per-gather",
        str(benchbase_max_parallel_workers_per_gather),
        "--job-rounds",
        "3",
        "--job-workers-override",
        str(job_workers_override),
        "--job-work-mem-mb",
        str(job_work_mem_mb),
        "--job-min-seq-page-cost",
        str(job_min_seq_page_cost),
        "--job-fixed-horizon-s",
        str(job_fixed_horizon_s),
        "--workload-fixed-horizon-s",
        str(workload_fixed_horizon_s),
        "--statement-timeout-ms",
        str(statement_timeout_ms),
    ]
    if vm.get("seed_overlay"):
        command.extend(
            [
                "--seed-overlay",
                str(vm["seed_overlay"]),
                "--work-overlay",
                str(vm["overlay"]),
            ]
        )
    return command


def formal_command(
    vm: Dict[str, Any],
    runtime_dir: Path,
    run_dir: Path,
    *,
    model_training_seed_dir: str = "",
    model_training_seed_include_candidates: bool = False,
    resume_calibration_dir: str = "",
    resume_model_training_seed_dir: str = "",
    resume_independent_exact_training_seed_dir: str = "",
    resume_model_training_seed_include_candidates: bool = False,
    resume_candidate_dir: str = "",
    model_quality_online_hard_failures_allowed: int = 0,
) -> List[str]:
    target = str(vm["target"])
    scenario_config = runtime_dir / "scenario_config.json"
    training_seed = str(model_training_seed_dir).strip()
    resume_calibration = str(resume_calibration_dir).strip()
    resume_seed = str(resume_model_training_seed_dir).strip()
    if bool(resume_calibration) != bool(resume_seed):
        raise ValueError(
            "Top15 resume requires both calibration and model-training seed directories"
        )
    if training_seed and resume_calibration:
        raise ValueError(
            "fresh model-training seed and Top15 resume are mutually exclusive"
        )
    if resume_calibration:
        command = [
            sys.executable,
            "-u",
            str(ROOT / "shell/resume_double_loop_grid_from_top15.py"),
            "--resume-calibration-dir",
            resume_calibration,
            "--model-training-seed-dir",
            resume_seed,
        ]
        if str(resume_independent_exact_training_seed_dir).strip():
            command.extend(
                [
                    "--independent-exact-training-seed-dir",
                    str(resume_independent_exact_training_seed_dir).strip(),
                ]
            )
        if resume_model_training_seed_include_candidates:
            command.append("--model-training-seed-include-candidates")
        if str(resume_candidate_dir).strip():
            command.extend(
                ["--resume-candidate-dir", str(resume_candidate_dir).strip()]
            )
    else:
        command = [
            sys.executable,
            "-u",
            str(ROOT / "shell/run_double_loop_grid_scenarios.py"),
        ]
        if training_seed:
            command.extend(["--model-training-seed-dir", training_seed])
        if model_training_seed_include_candidates:
            if not training_seed:
                raise ValueError(
                    "--model-training-seed-include-candidates requires "
                    "--model-training-seed-dir"
                )
            command.append("--model-training-seed-include-candidates")
    command.extend([
        "--mode",
        "live",
        "--generic-vm-replay",
        "--scenario-config",
        f"{target}={scenario_config}",
        "--scenarios",
        target,
        "--output-dir",
        str(run_dir),
        "--selection-policy",
        "real_score_topk",
        "--score-policy",
        "sigmoid_linear_resource",
        "--grid-points",
        "15",
        "--model-quality-calibration-replays",
        "15",
        "--model-quality-min-samples",
        "15",
        # Use a looser pre-production quality band to keep the optimization
        # flowing. This is intentionally less conservative for early-stage
        # experimentation; we can tighten after observing convergence quality.
        "--model-quality-max-relative-error-p95",
        "20.0",
        "--model-quality-max-relative-error-max",
        "50.0",
        "--model-quality-min-r2",
        "-1000000000.0",
        "--model-quality-ranking-inversion-threshold",
        "1.0",
        "--model-quality-online-max-relative-error-p95",
        "2.0",
        "--model-quality-online-max-relative-error-max",
        "10.0",
        "--model-quality-online-min-r2",
        "0.0",
        "--model-quality-online-ranking-inversion-threshold",
        "0.80",
        "--model-quality-online-score-tie-margin",
        "0.01",
        "--model-quality-online-hard-failures-allowed",
        str(model_quality_online_hard_failures_allowed),
        "--inner-workers",
        "8",
        "--pruning-workers",
        "4",
        "--max-parallel-scenarios",
        "1",
        "--max-iter",
        "20",
        "--convergence-patience",
        "2",
        "--combination-max-size",
        "4",
        "--combination-values-per-param",
        "3",
        "--combination-max-candidates",
        "0",
        "--outer-rounds",
        "0",
        "--real-top-k",
        "30",
        "--topk-controller",
        "decision_tree",
        "--adaptive-topk-min-k",
        "15",
        "--adaptive-topk-batch-size",
        "1",
        "--min-qps-gain-ratio",
        "0.15",
        "--min-target-drop-ratio",
        "0.05",
        "--max-target-drop-ratio",
        "0.10",
        "--no-improvement-patience",
        "0",
        "--epsilon",
        "0.0001",
    ])
    return command


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def parse_only(raw: str) -> set[str]:
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def selected(vm: Dict[str, Any], only: set[str]) -> bool:
    if not only:
        return True
    identity = run_id(vm)
    keys = {
        identity,
        str(vm["exp_id"]).lower(),
        str(vm["name"]).lower(),
        f"{str(vm['exp_id']).lower()}_r{int(vm['repeat_id'])}",
    }
    return bool(keys & only)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=MATRIX_ROOT)
    parser.add_argument("--only", default="")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument(
        "--hybench-root",
        type=Path,
        default=HYBENCH_ROOT,
        help="isolated HyBench runtime root forwarded to runtime preparation",
    )
    parser.add_argument("--tp-clients-override", type=int, default=0)
    parser.add_argument("--ap-clients-override", type=int, default=0)
    parser.add_argument("--hybench-ap-work-mem-mb", type=int, default=64)
    parser.add_argument("--hybench-ap-shared-buffers-mb", type=int, default=0)
    parser.add_argument("--hybench-ap-query-ids", default="")
    parser.add_argument("--hybench-ap-query-weights", default="")
    parser.add_argument("--hybench-ap-query-sequence", default="")
    parser.add_argument("--hybench-tp-percent-mix", default="")
    parser.add_argument(
        "--hybench-ap-max-parallel-workers-per-gather",
        type=int,
        default=-1,
    )
    parser.add_argument("--vm-shm-mb", type=int, default=0)
    parser.add_argument("--qmp-socket", default="")
    parser.add_argument("--job-workers-override", type=int, default=0)
    parser.add_argument("--job-work-mem-mb", type=int, default=0)
    parser.add_argument("--job-min-seq-page-cost", type=float, default=0.0)
    parser.add_argument("--job-fixed-horizon-s", type=int, default=0)
    parser.add_argument("--workload-fixed-horizon-s", type=int, default=0)
    parser.add_argument("--statement-timeout-ms", type=int, default=900000)
    parser.add_argument("--benchbase-terminals-override", type=int, default=0)
    parser.add_argument("--benchbase-work-mem-mb", type=int, default=0)
    parser.add_argument("--benchbase-shared-buffers-mb", type=int, default=0)
    parser.add_argument(
        "--benchbase-max-parallel-workers-per-gather",
        type=int,
        default=-1,
    )
    parser.add_argument(
        "--model-training-seed-dir",
        default="",
        help=(
            "anchored parameter-to-resource support for a new independent "
            "fresh-baseline plus coordinate-Top15 validation run"
        ),
    )
    parser.add_argument(
        "--model-training-seed-include-candidates",
        action="store_true",
    )
    parser.add_argument("--resume-calibration-dir", default="")
    parser.add_argument("--resume-model-training-seed-dir", default="")
    parser.add_argument(
        "--resume-independent-exact-training-seed-dir",
        default="",
    )
    parser.add_argument(
        "--resume-model-training-seed-include-candidates",
        action="store_true",
    )
    parser.add_argument("--resume-candidate-dir", default="")
    parser.add_argument(
        "--attempt-tag",
        default="",
        help="append a non-destructive retry tag to run/log/PID paths",
    )
    parser.add_argument(
        "--model-quality-online-hard-failures-allowed",
        type=int,
        default=0,
        help=(
            "online quality gate hard-stop threshold for consecutive failed batches; "
            "0 means only soft failures and continue search"
        ),
    )
    args = parser.parse_args()
    manifest_path = args.root / "vm_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    only = parse_only(args.only)
    manifest_vms = list(manifest.get("vms") or [])
    manifest_count = int(manifest.get("vm_count") or 0)
    if not manifest.get("all_ready") or manifest_count != len(manifest_vms):
        raise RuntimeError("formal launch requires a complete ready VM manifest")
    if not only and manifest_count != 18:
        raise RuntimeError(
            "a full formal launch requires an 18/18 manifest; "
            "use --only with an independently prepared subset manifest"
        )
    vms = [vm for vm in manifest_vms if selected(vm, only)]
    if not vms:
        raise RuntimeError(f"--only selected no matrix rows: {sorted(only)}")

    runtime_root = args.root / "runtimes"
    formal_root = args.root / "formal_runs"
    launch_root = args.root / "launch"
    rows: List[Dict[str, Any]] = []
    for vm in vms:
        identity = run_id(vm)
        attempt_tag = args.attempt_tag.strip().lower().replace(" ", "_")
        launch_identity = f"{identity}_{attempt_tag}" if attempt_tag else identity
        runtime_dir = runtime_root / identity
        run_dir = formal_root / launch_identity
        prepare = prepare_command(
            vm,
            runtime_dir,
            hybench_root=args.hybench_root,
            tp_clients_override=args.tp_clients_override,
            ap_clients_override=args.ap_clients_override,
            hybench_ap_work_mem_mb=args.hybench_ap_work_mem_mb,
            hybench_ap_shared_buffers_mb=args.hybench_ap_shared_buffers_mb,
            hybench_ap_query_ids=args.hybench_ap_query_ids,
            hybench_ap_query_weights=args.hybench_ap_query_weights,
            hybench_ap_query_sequence=args.hybench_ap_query_sequence,
            hybench_tp_percent_mix=args.hybench_tp_percent_mix,
            hybench_ap_max_parallel_workers_per_gather=(
                args.hybench_ap_max_parallel_workers_per_gather
            ),
            vm_shm_mb=args.vm_shm_mb,
            qmp_socket=args.qmp_socket,
            job_workers_override=args.job_workers_override,
            job_work_mem_mb=args.job_work_mem_mb,
            job_min_seq_page_cost=args.job_min_seq_page_cost,
            job_fixed_horizon_s=args.job_fixed_horizon_s,
            workload_fixed_horizon_s=args.workload_fixed_horizon_s,
            statement_timeout_ms=args.statement_timeout_ms,
            benchbase_terminals_override=args.benchbase_terminals_override,
            benchbase_work_mem_mb=args.benchbase_work_mem_mb,
            benchbase_shared_buffers_mb=args.benchbase_shared_buffers_mb,
            benchbase_max_parallel_workers_per_gather=(
                args.benchbase_max_parallel_workers_per_gather
            ),
        )
        completed = subprocess.run(
            prepare,
            cwd=str(ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=120,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"runtime preparation failed for {identity}:\n{completed.stdout}"
            )
        command = formal_command(
            vm,
            runtime_dir,
            run_dir,
            model_training_seed_dir=args.model_training_seed_dir,
            model_training_seed_include_candidates=(
                args.model_training_seed_include_candidates
            ),
            resume_calibration_dir=args.resume_calibration_dir,
            resume_model_training_seed_dir=args.resume_model_training_seed_dir,
            resume_independent_exact_training_seed_dir=(
                args.resume_independent_exact_training_seed_dir
            ),
            resume_model_training_seed_include_candidates=(
                args.resume_model_training_seed_include_candidates
            ),
            resume_candidate_dir=args.resume_candidate_dir,
            model_quality_online_hard_failures_allowed=(
                args.model_quality_online_hard_failures_allowed
            ),
        )
        row: Dict[str, Any] = {
            "run_id": identity,
            "launch_id": launch_identity,
            "exp_id": vm["exp_id"],
            "repeat_id": vm["repeat_id"],
            "benchmark": vm["benchmark"],
            "target": vm["target"],
            "vm_name": vm["name"],
            "vm_ip": vm["ip"],
            "vm_cpu_effective": effective_cpu(vm),
            "vm_memory_mib_effective": effective_memory_mib(vm),
            "vm_disk_mb_effective": list(effective_disk_mb(vm)),
            "runtime_dir": str(runtime_dir),
            "run_dir": str(run_dir),
            "algorithm_command": command,
            "algorithm_command_shell": shlex.join(command),
            "command": command,
            "command_shell": shlex.join(command),
            "prepared_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if args.prepare_only:
            row["status"] = "prepared"
            rows.append(row)
            continue
        pid_path = launch_root / f"{launch_identity}.pid"
        log_path = launch_root / f"{launch_identity}.log"
        if pid_path.exists():
            try:
                existing_pid = int(pid_path.read_text(encoding="utf-8").strip())
            except ValueError:
                existing_pid = -1
            if existing_pid > 0 and process_alive(existing_pid):
                row.update({"status": "already_running", "pid": existing_pid, "log": str(log_path)})
                rows.append(row)
                continue
        if (run_dir / target_subdir(vm) / "scenario_summary.json").is_file():
            row["status"] = "already_finished"
            rows.append(row)
            continue
        if run_dir.exists() and any(run_dir.iterdir()):
            raise RuntimeError(
                f"refusing to overwrite partial formal run without a live PID: {run_dir}"
            )
        launch_root.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir(parents=True, exist_ok=True)
        exit_audit_path = run_dir / "runner_exit_audit.json"
        guarded_command = [
            sys.executable,
            "-u",
            str(ROOT / "shell/run_with_exit_audit.py"),
            "--audit-path",
            str(exit_audit_path),
            "--",
            *command,
        ]
        row["command"] = guarded_command
        row["command_shell"] = shlex.join(guarded_command)
        row["runner_exit_audit"] = str(exit_audit_path)
        log_handle = log_path.open("ab", buffering=0)
        child_env = dict(os.environ)
        child_env["VM_RUNTIME_SSH_USER_KNOWN_HOSTS_FILE"] = "/dev/null"
        process = subprocess.Popen(
            guarded_command,
            cwd=str(ROOT),
            env=child_env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        log_handle.close()
        pid_path.write_text(f"{process.pid}\n", encoding="utf-8")
        row.update(
            {
                "status": "launched",
                "pid": process.pid,
                "log": str(log_path),
                "launched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        rows.append(row)
        print(f"[launched] {launch_identity} pid={process.pid} vm={vm['name']}@{vm['ip']}", flush=True)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    write_json(
        args.root / f"formal_launch_{stamp}.json",
        {"created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "rows": rows},
    )
    return 0


def target_subdir(vm: Dict[str, Any]) -> str:
    return str(vm["target"])


if __name__ == "__main__":
    raise SystemExit(main())
