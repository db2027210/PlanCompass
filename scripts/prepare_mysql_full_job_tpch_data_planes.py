#!/usr/bin/env python3
"""Prepare, but do not run, the frozen full JOB/TPC-H MySQL data planes."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from same_ssd_concurrency import choose_same_ssd_parallelism


FREEZE_DIR = ROOT / "config/formal/frozen_mysql_three_workloads_20260806_v1"
CONCURRENCY_FREEZE_DIR = (
    ROOT / "config/formal/frozen_mysql_three_workloads_20260809_v2"
)
CONCURRENCY_SOURCE = (
    ROOT.parent / "resource_adaptive/docs/"
    "hybench_xp_single_ssd_concurrency_summary_20260809.md"
)
LOCAL_SOURCE = (
    ROOT / "history/mysql_gradient_debug/readiness_mysql_early_stop_v1/"
    "source-v34-731fe47e778aa0e7889a3e66a02e0de32c429f1eb8ee03f8f215b7e87940d65c/"
    "mysql_algorithm_c_local_services.py"
)
IDLE_PREFLIGHT = "ra_mysql_job_preflight_cef7fc919584"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current_replay_parallelism_decisions(
    concurrency_policy: dict[str, Any], *, pending_candidates: int = 5,
) -> dict[str, dict[str, Any]]:
    """Evaluate the frozen SSD policy against the currently frozen worker pool."""
    policy = concurrency_policy["same_physical_ssd_replay"]
    decisions: dict[str, dict[str, Any]] = {}
    for name, row in concurrency_policy["current_readback"].items():
        available = int(row["independent_replay_workers"])
        decision = choose_same_ssd_parallelism(
            current=int(row["effective_replay_parallelism"]),
            available_workers=available,
            pending_candidates=pending_candidates,
            mean_util_pct=None,
            peak_util_pct=None,
            preferred=int(policy["preferred_parallelism"]),
            stable_cap=int(policy["stable_cap"]),
            allow_stable_cap=bool(policy["allow_stable_cap"]),
            daily_mean_limit_pct=float(policy["scale_up"]["mean_util_pct_lt"]),
            daily_peak_limit_pct=float(policy["scale_up"]["peak_util_pct_lt"]),
            warning_mean_pct=float(policy["scale_down"]["mean_util_pct_gte"]),
            warning_peak_pct=float(policy["scale_down"]["peak_util_pct_gte"]),
            tiers=policy["tiers"],
        )
        decision["adaptive_enabled"] = bool(row["adaptive_same_ssd_enabled"])
        decision["activation_reason"] = row["reason"]
        if decision["effective"] != int(row["effective_replay_parallelism"]):
            raise RuntimeError(f"current {name} replay concurrency decision differs")
        decisions[name] = decision
    return decisions


def load_local_services():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location("three_workload_local_services", LOCAL_SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError("local service source cannot be imported")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def docker_inspect(name: str) -> dict[str, Any]:
    completed = subprocess.run(
        ["docker", "inspect", name], text=True, capture_output=True, check=True, timeout=20,
    )
    rows = json.loads(completed.stdout)
    if not isinstance(rows, list) or len(rows) != 1:
        raise RuntimeError(f"Docker identity is not unique: {name}")
    return rows[0]


def verify_freeze() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    manifest_path = FREEZE_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("formal_scope") != "mysql_only"
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("status") != "SEALED_PRELAUNCH_GATES_INCOMPLETE"
    ):
        raise RuntimeError("three-workload prelaunch manifest is not fail-closed")
    for name, expected in manifest["artifacts"].items():
        if name in {"mysql_workload_adapter.py", "mysql_benchmark_command.py"}:
            path = ROOT / name
        elif name == "xp_runtime_manifest.json":
            continue
        else:
            path = FREEZE_DIR / name
        if sha(path) != expected:
            raise RuntimeError(f"frozen artifact bytes differ: {name}")
    envelope = json.loads(
        (FREEZE_DIR / "job_tpch_resource_envelope.json").read_text(encoding="utf-8")
    )
    if envelope.get("execution_authorized") is not False:
        raise RuntimeError("resource envelope unexpectedly grants execution")
    xp_envelope = json.loads(
        (FREEZE_DIR / "xp_cpu_resource_envelope.json").read_text(encoding="utf-8")
    )
    if (
        xp_envelope.get("execution_authorized") is not False
        or xp_envelope.get("target_bottleneck") != "cpu"
        or xp_envelope.get("workload_freeze_sha256")
        != manifest["artifacts"]["workload_freeze.json"]
    ):
        raise RuntimeError("XP CPU resource envelope is not the frozen fail-closed intent")
    concurrency_manifest = json.loads(
        (CONCURRENCY_FREEZE_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    concurrency_policy_path = CONCURRENCY_FREEZE_DIR / "disk_concurrency_policy.json"
    concurrency_policy = json.loads(concurrency_policy_path.read_text(encoding="utf-8"))
    if (
        concurrency_manifest.get("formal_launch_authorized") is not False
        or concurrency_manifest.get("launch_performed") is not False
        or concurrency_policy.get("execution_authorized") is not False
    ):
        raise RuntimeError("disk concurrency successor unexpectedly grants execution")
    if concurrency_manifest.get("predecessor", {}).get("sha256") != sha(manifest_path):
        raise RuntimeError("disk concurrency successor predecessor identity differs")
    successor = concurrency_manifest.get("successor_artifacts") or {}
    if successor.get("disk_concurrency_policy.json") != sha(concurrency_policy_path):
        raise RuntimeError("disk concurrency policy bytes differ")
    if successor.get("same_ssd_concurrency.py") != sha(ROOT / "same_ssd_concurrency.py"):
        raise RuntimeError("same-SSD concurrency implementation bytes differ")
    source = concurrency_policy.get("policy_source") or {}
    if source.get("sha256") != sha(CONCURRENCY_SOURCE):
        raise RuntimeError("same-SSD study bytes differ")
    current = concurrency_policy.get("current_readback") or {}
    for name in ("job", "tpch", "hybench_xp"):
        row = current.get(name) or {}
        if (
            row.get("independent_replay_workers") != 1
            or row.get("effective_replay_parallelism") != 1
            or row.get("adaptive_same_ssd_enabled") is not False
        ):
            raise RuntimeError(f"current {name} replay concurrency is not fail-closed")
    if (
        concurrency_policy["current_readback"]["job"].get("host_port")
        != envelope["job"]["host_port"]
        or concurrency_policy["current_readback"]["tpch"].get("host_port")
        != envelope["tpch"]["host_port"]
        or concurrency_policy["current_readback"]["hybench_xp"].get("scenario")
        != "hybench_xp_cpu"
        or concurrency_policy["current_readback"]["hybench_xp"].get("target_resource")
        != "cpu"
    ):
        raise RuntimeError("disk concurrency successor endpoint/scenario binding differs")
    envelope["xp"] = {
        "host_port": concurrency_policy["current_readback"]["hybench_xp"][
            "host_port"
        ],
        "intended": xp_envelope["intended"],
    }
    current_replay_parallelism_decisions(concurrency_policy)
    return manifest, envelope, concurrency_policy


def stop_verified_idle_preflight() -> dict[str, Any]:
    row = docker_inspect(IDLE_PREFLIGHT)
    state = row.get("State") or {}
    host = row.get("HostConfig") or {}
    if state.get("Running") is False and state.get("Status") == "exited":
        return {
            "container": IDLE_PREFLIGHT,
            "container_id": row["Id"],
            "previous_pid": 0,
            "previous_started_at": state.get("StartedAt"),
            "action": "already_stopped_not_removed",
            "recovery": f"docker start {IDLE_PREFLIGHT}",
        }
    if (
        state.get("Running") is not True
        or host.get("CpusetCpus") != "176-183,432-439"
        or int(state.get("Pid") or 0) <= 1
    ):
        raise RuntimeError("known idle JOB preflight identity/state differs")
    pid = int(state["Pid"])
    cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
        "utf-8", errors="replace"
    )
    if "mysqld" not in cmdline:
        raise RuntimeError("known idle JOB preflight PID is not mysqld")
    workload = subprocess.run(
        ["pgrep", "-af", "mysql_benchmark_command|formal_job_multiquery|benchbase.*tpch"],
        text=True, capture_output=True, check=False, timeout=10,
    ).stdout
    workload_lines = [line for line in workload.splitlines() if "pgrep -af" not in line]
    if workload_lines:
        raise RuntimeError("a JOB/TPC-H workload is live; refusing to stop the preflight")
    subprocess.run(["docker", "stop", "--time", "120", IDLE_PREFLIGHT], check=True, timeout=150)
    after = docker_inspect(IDLE_PREFLIGHT)
    if (after.get("State") or {}).get("Running") is not False:
        raise RuntimeError("idle JOB preflight did not stop cleanly")
    return {
        "container": IDLE_PREFLIGHT,
        "container_id": row["Id"],
        "previous_pid": pid,
        "previous_started_at": state.get("StartedAt"),
        "action": "stopped_not_removed",
        "recovery": f"docker start {IDLE_PREFLIGHT}",
    }


def build_campaign(name: str, run_id: str, envelope: dict[str, Any], services: Any):
    output = ROOT / "history/mysql_gradient_debug/mysql_three_workload_20260806_preparation" / run_id
    intended = envelope[name]["intended"]
    base = services.LOCAL_LANES[name]
    lane_updates: dict[str, Any] = {}
    if name == "xp":
        lane_updates.update(
            scenario="hybench_xp_cpu",
            target_resource="cpu",
        )
    lane = replace(
        base,
        cpu_cores=float(intended["cpu_quota_cores"]),
        cpuset=str(intended["cpuset_cpus"]),
        mems=str(intended["cpuset_mems"]),
        memory_bytes=int(intended["memory_bytes"]),
        pids=int(intended["pids_max"]),
        read_bps=int(intended["io_read_bps"]),
        write_bps=int(intended["io_write_bps"]),
        remote_port=int(envelope[name]["host_port"]),
        buffer_pool_bytes=int(intended["innodb_buffer_pool_bytes"]),
        **lane_updates,
    )
    profile = services.LOCAL_STORAGE_PROFILES.get(name)
    scenario_output = output / name
    scenario_output.mkdir(parents=True, mode=0o700)
    class PermissionSafePreparationCampaign(services.LocalCampaign):
        def remote(self, script: str, timeout: int = 300):
            needle = (
                f'test -z "$(find {self.lane.template} '
                '-xdev -perm /222 -print -quit)"'
            )
            replacement = (
                'test -z "$(docker run --rm --network none '
                f'-v {self.lane.template}:/tree:ro --entrypoint /bin/sh '
                f'{services.campaign_module.IMAGE} '
                "-c 'find /tree -xdev -perm /222 -print -quit')\""
            )
            # LocalCampaign.remote() inserts the host-side permission check
            # immediately before it calls its module-level run().  Intercept
            # that final generated script so root-owned MySQL template files
            # are inspected inside a read-only helper container.  The helper
            # changes no template bytes or permissions.
            original_run = services.run

            def permission_safe_run(*args: Any, **kwargs: Any):
                payload = kwargs.get("input")
                if isinstance(payload, str):
                    if needle in payload:
                        payload = payload.replace(needle, replacement, 1)
                    datadir_readback = (
                        f'test "$(findmnt -n -o SOURCE -T {self.remote_data})" = '
                        f'{self.storage_profile.device}'
                        if self.storage_profile is not None
                        else ""
                    )
                    if datadir_readback and datadir_readback in payload:
                        payload = payload.replace(
                            datadir_readback,
                            f"mkdir -p {self.remote_data}\n{datadir_readback}",
                            1,
                        )
                    kwargs["input"] = payload
                return original_run(*args, **kwargs)

            services.run = permission_safe_run
            try:
                return super().remote(script, timeout=timeout)
            finally:
                services.run = original_run

    campaign = PermissionSafePreparationCampaign(
        lane, scenario_output, f"{run_id}_{name}",
        storage_profile=profile,
        storage_manifest_sha256=(sha(Path(profile.manifest)) if profile else ""),
        tpch_terminals=5,
    )
    return campaign


def worker(name: str, run_id: str) -> int:
    _, envelope, _ = verify_freeze()
    services = load_local_services()
    campaign = build_campaign(name, run_id, envelope, services)
    campaign.prepare_remote()
    row = docker_inspect(campaign.container)
    print(json.dumps({
        "scenario": name, "container": campaign.container,
        "container_id": row["Id"], "pid": row["State"]["Pid"],
    }))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retire-verified-idle-preflight", action="store_true")
    parser.add_argument("--worker", choices=("job", "tpch", "xp"))
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if args.worker:
        if not args.run_id or args.retire_verified_idle_preflight:
            parser.error("worker mode requires only --worker and --run-id")
        return worker(args.worker, args.run_id)
    if not args.retire_verified_idle_preflight or args.run_id:
        parser.error("parent mode requires --retire-verified-idle-preflight")
    manifest, envelope, concurrency_policy = verify_freeze()
    stopped = stop_verified_idle_preflight()
    token = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    freeze_prefix = manifest["artifacts"]["workload_freeze.json"][:12]
    run_id = f"full3w_{token}_{freeze_prefix}"
    output = ROOT / "history/mysql_gradient_debug/mysql_three_workload_20260806_preparation" / run_id
    output.mkdir(parents=True, mode=0o700)

    processes = {}
    for name in ("job", "tpch"):
        log_out = (output / f"{name}_worker.stdout.log").open("w", encoding="utf-8")
        log_err = (output / f"{name}_worker.stderr.log").open("w", encoding="utf-8")
        command = [
            "taskset", "-c", envelope[name]["intended"]["cpuset_cpus"],
            sys.executable, str(Path(__file__).resolve()),
            "--worker", name, "--run-id", run_id,
        ]
        processes[name] = (subprocess.Popen(command, stdout=log_out, stderr=log_err), log_out, log_err)
    failures = []
    for name, (process, log_out, log_err) in processes.items():
        returncode = process.wait()
        log_out.close()
        log_err.close()
        if returncode:
            failures.append({"scenario": name, "returncode": returncode})
    if failures:
        failure = {
            "status": "PREPARATION_FAILED_CLOSED", "failures": failures,
            "formal_launch_authorized": False, "workload_started": False,
        }
        (output / "failure.json").write_text(
            json.dumps(failure, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        raise RuntimeError(f"prepared workers failed closed: {failures}")

    prepared = {}
    services = load_local_services()
    for name in ("job", "tpch"):
        campaign = build_campaign(name, run_id, envelope, services)
        row = docker_inspect(campaign.container)
        state = row["State"]
        prepared[name] = {
            "container": campaign.container,
            "container_id": row["Id"],
            "pid": state["Pid"],
            "started_at": state["StartedAt"],
            "host_port": envelope[name]["host_port"],
            "runtime_root": campaign.remote_root,
            "datadir": campaign.remote_data,
            "private_defaults_file": str((output / name / "private/mysql.cnf").resolve()),
            "campaign_runtime": str((output / name / "campaign_runtime.json").resolve()),
        }
    replay_decisions = current_replay_parallelism_decisions(concurrency_policy)
    binding = {
        "schema_version": "mysql_full_job_tpch_prepared_data_planes_v2",
        "status": "PREPARED_READBACK_AND_DYNAMIC_GATE_REQUIRED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "workload_freeze_sha256": manifest["artifacts"]["workload_freeze.json"],
        "resource_envelope_sha256": manifest["artifacts"]["job_tpch_resource_envelope.json"],
        "disk_concurrency_policy_sha256": sha(
            CONCURRENCY_FREEZE_DIR / "disk_concurrency_policy.json"
        ),
        "disk_concurrency": {
            "inter_scene_parallelism": concurrency_policy["inter_scene"][
                "maximum_parallel_scenes"
            ],
            "job_replay_parallelism": concurrency_policy["current_readback"]["job"][
                "effective_replay_parallelism"
            ],
            "tpch_replay_parallelism": concurrency_policy["current_readback"]["tpch"][
                "effective_replay_parallelism"
            ],
            "same_mysql_instance_parallel_candidate_replay_prohibited": (
                concurrency_policy["same_physical_ssd_replay"][
                    "same_mysql_instance_parallel_candidate_replay_prohibited"
                ]
            ),
            "current_policy_decisions": replay_decisions,
        },
        "adapter_sha256": manifest["artifacts"]["mysql_workload_adapter.py"],
        "command_sha256": manifest["artifacts"]["mysql_benchmark_command.py"],
        "local_service_source": str(LOCAL_SOURCE),
        "local_service_source_sha256": sha(LOCAL_SOURCE),
        "stopped_idle_preflight": stopped,
        "prepared": prepared,
        "data_planes_parallel_preparation": True,
        "formal_launch_authorized": False,
        "workload_started": False,
        "accepted_rounds": {"job": 0, "tpch": 0, "hybench_xp": 0},
    }
    binding_path = output / "prepared_binding.json"
    binding_path.write_text(json.dumps(binding, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "prepared_binding.json.sha256").write_text(
        f"{sha(binding_path)}  prepared_binding.json\n", encoding="utf-8"
    )
    print(json.dumps({"status": binding["status"], "output": str(output), "prepared": prepared}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
