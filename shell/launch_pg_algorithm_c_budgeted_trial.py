#!/usr/bin/env python3
"""Preflight and supervise only the four explicitly configured C pilot lanes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = "/home/anonymous/miniconda3/envs/resource_adaptive/bin/python"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from algorithm_c_budget_policy import CONTRACT
from shell.run_pg_algorithm_c_cohort_support_supervisor import support_workload_environment


def command(args, timeout=60):
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"command failed: {args[0:2]}: {result.stderr[-1000:]}")
    return result.stdout


def cpus(value):
    result = set()
    for token in str(value).split(","):
        if not token:
            continue
        parts = token.split("-")
        result.update(range(int(parts[0]), int(parts[-1]) + 1))
    return result


def inspect_peer_snapshot(container_ids):
    """Tolerate peers removed between docker ps and inspect, not other errors."""
    if not container_ids:
        return [], []
    result = subprocess.run(["docker", "inspect", *container_ids], text=True,
                            capture_output=True, timeout=60)
    missing = []
    for line in result.stderr.splitlines():
        prefix = "Error: No such object: "
        if not line.startswith(prefix) or line[len(prefix):] not in container_ids:
            raise RuntimeError(f"peer inspection failed: {result.stderr[-1000:]}")
        missing.append(line[len(prefix):])
    if result.returncode and not missing:
        raise RuntimeError("peer inspection failed without a missing-container diagnostic")
    return json.loads(result.stdout), missing


def preflight(root, *, resume_worker_startup=False):
    manifest = json.loads((root / "budgeted_launch_manifest.json").read_text())
    if manifest.get("contract") != CONTRACT:
        raise ValueError("unexpected campaign contract")
    errors, workers, configs = [], [], {}
    peer_overlap_mode = manifest.get("peer_overlap_mode", "hard_stop")
    if peer_overlap_mode not in {"hard_stop", "record_only"}:
        errors.append("invalid_peer_overlap_mode")
    for relative, expected in manifest["source_sha256"].items():
        source = ROOT / relative
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            errors.append(f"source_changed:{relative}")
    for scene, entry in manifest["scenes"].items():
        cfg = json.loads(Path(entry["config"]).read_text())
        configs[scene] = cfg
        workers.extend(cfg["binary_plan_tuning"]["validation"]["docker_workers"]["workers"])
        if (root / scene / "algorithm_c_state.json").exists():
            errors.append(f"fresh_state_required:{scene}")
    names = [w["container"] for w in workers]
    previous_path = root / "launch_preflight.json"
    if resume_worker_startup:
        previous = json.loads(previous_path.read_text()) if previous_path.is_file() else {}
        if previous.get("passed") is not True or set(previous.get("worker_names", [])) != set(names):
            errors.append("startup_resume_requires_previously_passed_exact_worker_receipt")
    if len(names) != 8 or len(set(names)) != 8:
        errors.append("four_scenes_require_eight_distinct_workers")
    inspected = json.loads(command(["docker", "inspect", *names]))
    by_name = {x["Name"].lstrip("/"): x for x in inspected}
    topology = {}
    for line in command(["lscpu", "-p=CPU,CORE,SOCKET"]).splitlines():
        if line and not line.startswith("#"):
            cpu, core, socket = line.split(",")
            topology[int(cpu)] = (int(socket), int(core))
    our_cpus = set()
    for w in workers:
        actual = by_name[w["container"]]
        expected_cpus = cpus(w["cpuset_cpus"])
        our_cpus.update(expected_cpus)
        if cpus(actual["HostConfig"]["CpusetCpus"]) != expected_cpus:
            errors.append(f"worker_cpuset_mismatch:{w['container']}")
        mounts = {x["Destination"]: x["Source"] for x in actual["Mounts"]}
        if mounts.get(w["container_data_dir"]) != w["data_dir"] or not w["data_dir"].startswith("/disk1/"):
            errors.append(f"worker_disk1_mount_mismatch:{w['container']}")
        if actual["State"]["Running"] and not resume_worker_startup:
            errors.append(f"worker_already_running_ownership_check_required:{w['container']}")
    for entry in manifest["scenes"].values():
        our_cpus.update(cpus(entry["controller_cpus"]))
    our_cores = {topology[c] for c in our_cpus}
    other_ids = command(["docker", "ps", "-q"]).split()
    peers, disappeared_peers = inspect_peer_snapshot(other_ids)
    overlaps = []
    for peer in peers:
        name = peer["Name"].lstrip("/")
        bound = cpus(peer["HostConfig"]["CpusetCpus"])
        shared = sorted(our_cores & {topology[c] for c in bound if c in topology})
        if shared and name not in names:
            overlaps.append({"container": name, "shared_physical_cores": shared})
    # Idle containers are not equivalent to active experiments. Record physical
    # overlap and its observed CPU load; this is not continuous isolation proof.
    if overlaps and peer_overlap_mode == "hard_stop":
        samples = command(["docker", "stats", "--no-stream", "--format", "{{json .}}",
                           *[x["container"] for x in overlaps]], timeout=90)
        statistics = {x["Name"]: x for x in map(json.loads, samples.splitlines())}
        for row in overlaps:
            sample = statistics.get(row["container"], {})
            usage = float(str(sample.get("CPUPerc", "100%")).rstrip("%"))
            row["cpu_percent_snapshot"] = usage
            if usage >= 2.0:
                errors.append(f"active_physical_core_overlap:{row['container']}:{usage}%")
    receipt = {"passed": not errors, "errors": errors, "worker_names": names,
               "peer_overlap_mode": peer_overlap_mode,
               "peers_removed_during_snapshot": disappeared_peers,
               "overlapping_peer_containers": overlaps, "idle_overlap_is_not_continuous_isolation_proof": True,
               "checked_at_epoch_s": time.time(), "trial_only_not_publication_comparison": True}
    (root / "launch_preflight.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return manifest, configs, receipt


def launch(root, manifest, configs, receipt):
    names = receipt["worker_names"]
    # These stopped, explicitly configured workers belong to the authorized PG trial.
    def start_worker(name):
        running = command(["docker", "inspect", "--format", "{{.State.Running}}", name]).strip()
        if running != "true":
            command(["docker", "start", name], timeout=240)
        return name
    with ThreadPoolExecutor(max_workers=4) as executor:
        started_workers = list(executor.map(start_worker, names))
    (root / "worker_startup_receipt.json").write_text(json.dumps({
        "worker_names": started_workers, "checked_at_epoch_s": time.time(),
        "reused_running_workers_without_recreation": True}, indent=2) + "\n")
    for scene, cfg in configs.items():
        template = cfg["binary_plan_tuning"]["validation"]["hybench_xp"]["template_db"]
        for worker in cfg["binary_plan_tuning"]["validation"]["docker_workers"]["workers"]:
            name = worker["container"]
            port = str(worker["port"])
            deadline = time.monotonic() + 180
            while True:
                try:
                    ready = subprocess.run(["docker", "exec", name, "pg_isready", "-p", port,
                                            "-U", "postgres", "-t", "5"],
                                           capture_output=True, timeout=60)
                    is_ready = ready.returncode == 0
                except subprocess.TimeoutExpired:
                    is_ready = False
                if is_ready:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"worker_not_ready:{name}")
                time.sleep(2)
            escaped = template.replace("'", "''")
            result = command(["docker", "exec", "-u", "postgres", name, "psql", "-p", port,
                              "-X", "-At", "-d", "postgres",
                              "-c", f"SELECT count(*) FROM pg_database WHERE datname='{escaped}'"])
            if result.strip() != "1":
                raise RuntimeError(f"required_template_missing:{name}:{template}")
    prefix = root.name.replace("-", "_")
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONPATH=str(ROOT),
               BONECT_SHARED_SSD_COORDINATION_ENABLED="0", BONECT_SHARED_SSD_WORKLOAD_LOCK_MODE="none",
               BONECT_ALGORITHM_C_BASELINE_COHORT_ID=f"{prefix}_baseline",
               BONECT_ALGORITHM_C_BASELINE_COHORT_SIZE="4", BONECT_ALGORITHM_C_BASELINE_COHORT_TIMEOUT_S="1800",
               BONECT_ALGORITHM_C_CANDIDATE_COHORT_ID=f"{prefix}_candidate",
               BONECT_ALGORITHM_C_CANDIDATE_COHORT_SIZE="4", BONECT_ALGORITHM_C_CANDIDATE_COHORT_TIMEOUT_S="1800",
               PG_ALGORITHM_C_GLOBAL_RESTORE_SLOT_TIMEOUT_S="21600")
    processes, handles = {}, []
    for scene, entry in manifest["scenes"].items():
        scene_env = {**env, **support_workload_environment(scene, root)}
        target = {"job_memory": "memory", "tpch_memory": "memory", "xp_cpu": "cpu", "xp_io": "io"}[scene]
        output = (root / scene / "controller.log").open("a")
        handles.append(output)
        args = ["taskset", "-c", entry["controller_cpus"], PYTHON, "-B", "-u",
                str(ROOT / "shell/run_pg_algorithm_c_budgeted_scene.py"), "--config", entry["config"],
                "--output-dir", str(root / scene), "--target", target]
        processes[scene] = subprocess.Popen(args, cwd=ROOT, env=scene_env, stdout=output, stderr=subprocess.STDOUT)
    output = (root / "cohort_support/supervisor.log").open("a")
    handles.append(output)
    adaptive_wall_guards = [
        float(
            ((entry.get("policy") or {}).get("adaptive_search") or {}).get(
                "operational_safety_max_active_wall_s"
            )
            or 0.0
        )
        for entry in manifest["scenes"].values()
    ]
    support_terminal_timeout_s = max(86400.0, max(adaptive_wall_guards, default=0.0) + 3600.0)
    support = subprocess.Popen([PYTHON, "-B", "-u", str(ROOT / "shell/run_pg_algorithm_c_budgeted_support.py"),
        "--campaign-root", str(root), "--cohort-id", f"{prefix}_candidate", "--cohort-size", "4",
        "--timeout-s", "1800", "--terminal-wait-timeout-s", str(support_terminal_timeout_s)], cwd=ROOT, env=env,
        stdout=output, stderr=subprocess.STDOUT)
    status = {"phase": "launched", "started_at_epoch_s": time.time(),
              "pids": {k: v.pid for k, v in processes.items()}, "support_pid": support.pid, "deadline_signals": []}
    def write_status():
        (root / "launcher_status.json").write_text(json.dumps(status, indent=2) + "\n")
    write_status()
    print(json.dumps(status), flush=True)
    def stop(_signal, _frame):
        for p in processes.values():
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    failure_signaled = False
    while any(p.poll() is None for p in processes.values()):
        failures = {s: p.returncode for s, p in processes.items() if p.poll() not in (None, 0)}
        if support.poll() not in (None, 0):
            failures["cohort_support"] = support.returncode
        if failures and not failure_signaled:
            status["failures"] = failures
            stop(None, None)
            failure_signaled = True
            write_status()
        for scene, p in processes.items():
            policy = manifest["scenes"][scene]["policy"]
            budget = policy.get("budget")
            maximum = float(budget["max_wall_s"]) if isinstance(budget, dict) else None
            if maximum is not None and p.poll() is None and scene not in status["deadline_signals"]:
                if time.time() - status["started_at_epoch_s"] >= maximum - 300:
                    p.send_signal(signal.SIGTERM)
                    status["deadline_signals"].append(scene)
                    write_status()
        time.sleep(10)
    status["returncodes"] = {s: p.returncode for s, p in processes.items()}
    status["phase"] = "finished" if all(p.returncode == 0 for p in processes.values()) else "failed"
    status["finished_at_epoch_s"] = time.time()
    write_status()
    if support.poll() is None:
        support.terminate()
    for handle in handles:
        handle.close()
    return 0 if status["phase"] == "finished" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-root", required=True, type=Path)
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--resume-worker-startup", action="store_true")
    args = parser.parse_args()
    root = args.campaign_root.resolve()
    manifest, configs, receipt = preflight(root, resume_worker_startup=args.resume_worker_startup)
    print(json.dumps(receipt, indent=2), flush=True)
    if not receipt["passed"]:
        raise SystemExit(2)
    if args.launch:
        raise SystemExit(launch(root, manifest, configs, receipt))
