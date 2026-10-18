#!/usr/bin/env python3
"""Seal and launch the v40 JOB-to-XP monitor after JOB expands to four lanes."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
CAMPAIGN = "v40_to_a_last_accepted_20260822_attempt002"
EXPANSION_RESULT = LOG_ROOT / f"{CAMPAIGN}.job_expand_v2.handoff.json"
SOURCE_FREEZE = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_dynamic_slots_20260823_v2"
    / "job_four_worker_resource_successor.json"
)
CONTRACT_DIR = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_to_xp_slots_20260823_v2"
)
CONTRACT_PATH = CONTRACT_DIR / "transition_contract.json"
MONITOR = ROOT / "scripts/monitor_mysql_algorithm_a_job_to_xp_slots_v2.py"
MONITOR_STATE = LOG_ROOT / f"{CAMPAIGN}.job_to_xp_v2.monitor.json"
MONITOR_UNIT = "mysql-alga-v40-job-to-xp-monitor-v2.service"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
MANAGEMENT_CPUSET = "240-241,496-497"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular JSON required: {path}")
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
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def wait_for_expansion(timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if EXPANSION_RESULT.is_file():
            value = load(EXPANSION_RESULT)
            if value.get("status") == "LAUNCHED":
                launch = value.get("launch") or {}
                state_path = Path(str(launch.get("state") or ""))
                if state_path.is_file():
                    state = load(state_path)
                    if (
                        state.get("algorithm") == "A"
                        and state.get("async_scene") == "job_memory"
                        and state.get("status") == "RUNNING"
                        and int(state.get("physical_replay_parallelism") or 0) == 4
                    ):
                        return value
            if value.get("status") == "FAILED_CLOSED":
                raise RuntimeError(f"JOB expansion failed closed: {value.get('error')}")
        time.sleep(10)
    raise TimeoutError("JOB four-worker expansion wait timed out")


def seal_contract(expansion: Mapping[str, Any]) -> dict[str, Any]:
    if not SOURCE_FREEZE.is_file():
        raise RuntimeError("four-worker JOB resource successor is absent")
    source_sha = sha(SOURCE_FREEZE)
    if expansion.get("successor_freeze_sha256") != source_sha:
        raise RuntimeError("JOB expansion resource successor differs")
    bindings = {
        "slot_planner": "scripts/mysql_algorithm_a_job_to_xp_slots_v1.py",
        "provisioner_adapter": "scripts/provision_mysql_algorithm_a_job_to_xp_workers_v1.py",
        "resume_adapter": "scripts/run_mysql_algorithm_a_job_to_xp_resume_v1.py",
        "resume_v4_selector": "scripts/run_mysql_algorithm_a_job_to_xp_resume_v2.py",
        "scene_executor": "scripts/execute_mysql_algorithm_a_job_to_xp_scene_v2.py",
        "monitor": "scripts/monitor_mysql_algorithm_a_job_to_xp_slots_v2.py",
        "physical_xp_provisioner": "scripts/provision_mysql_algorithm_a_xp_slot_workers_v1.py",
        "live_xp_authority_helper": "scripts/execute_mysql_algorithm_a_xp_slot_transition_v1.py",
        "algorithm_a_controller": "scripts/run_mysql_algorithm_a_async_scene_v3.py",
    }
    value = {
        "schema_version": "mysql_algorithm_a_job_to_xp_slot_contract_v1",
        "status": "FROZEN", "release_version": "v2",
        "created_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm": "A", "campaign_id": CAMPAIGN,
        "contract": "mysql_algorithm_a_job_four_slots_to_xp_2_to_4_v1",
        "trigger_requires_job_complete_10": True,
        "tpch_already_complete_10": True,
        "job_nonconvergence_does_not_release_slots": True,
        "xp_parallelism": {"before": 2, "after": 4},
        "released_slot_mapping": {
            "job_w1": "xp_cpu_w3", "job_w2": "xp_cpu_w4",
            "job_w3": "xp_io_w3", "job_w4": "xp_io_w4",
        },
        "per_scene_independent_transition": True,
        "xp_cpu_does_not_wait_for_xp_io": True,
        "xp_io_does_not_wait_for_xp_cpu": True,
        "round_boundary_required": True,
        "partial_next_round_archived_and_not_credited": True,
        "mixed_worker_cohort_round_prohibited": True,
        "accepted_ranges_incumbent_history_preserved": True,
        "canonical_baseline_preserved": True,
        "fresh_baseline_repetitions_per_worker": 2,
        "incumbent_replay_required": True,
        "post_convergence_stability_required": False,
        "native_xp_workload_unchanged": True,
        "native_xp_constraints": {
            "iq_selection": "IQ1-IQ7_uniform_random",
            "at_percentages": "35/25/15/15/7/3",
            "xap_xtp_ratio_unchanged": True,
            "workload_mix_override_prohibited": True,
        },
        "source_resource_freeze": {
            "path": str(SOURCE_FREEZE.relative_to(ROOT)),
            "sha256": source_sha,
        },
        "predecessor_algorithm_a_contract": {
            "path": "config/formal/frozen_mysql_algorithm_a_async_20260822_v4/algorithm_a_contract.json",
            "sha256": sha(ROOT / "config/formal/frozen_mysql_algorithm_a_async_20260822_v4/algorithm_a_contract.json"),
        },
        "source_bindings": {
            name: {"path": path, "sha256": sha(ROOT / path)}
            for name, path in bindings.items()
        },
        "job_expansion_result": {
            "path": str(EXPANSION_RESULT),
            "sha256": sha(EXPANSION_RESULT),
        },
    }
    if CONTRACT_PATH.is_file():
        prior = load(CONTRACT_PATH)
        if (
            prior.get("status") != "FROZEN"
            or (prior.get("source_resource_freeze") or {}).get("sha256") != source_sha
            or prior.get("source_bindings") != value["source_bindings"]
        ):
            raise RuntimeError("existing JOB-to-XP frozen contract differs")
        return prior
    atomic(CONTRACT_PATH, value)
    return value


def launch_monitor() -> dict[str, Any]:
    stdout = LOG_ROOT / f"{CAMPAIGN}.job_to_xp_v2.monitor.stdout.log"
    stderr = LOG_ROOT / f"{CAMPAIGN}.job_to_xp_v2.monitor.stderr.log"
    command = [
        "systemd-run", "--user", f"--unit={MONITOR_UNIT.removesuffix('.service')}",
        "--description=MySQL Algorithm A v40 JOB-to-XP slot monitor",
        f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,
        str(PYTHON), str(MONITOR), "--output", str(MONITOR_STATE),
        "--poll-seconds", "30",
    ]
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"JOB-to-XP monitor launch failed: {completed.stderr}")
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if MONITOR_STATE.is_file():
            return {
                "unit": MONITOR_UNIT, "state": str(MONITOR_STATE),
                "stdout": str(stdout), "stderr": str(stderr),
            }
        time.sleep(1)
    raise TimeoutError("JOB-to-XP monitor startup timed out")


def run(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    if output.is_file() and load(output).get("status") == "LAUNCHED":
        return 0
    document: dict[str, Any] = {
        "schema_version": "mysql_algorithm_a_job_to_xp_activation_v1",
        "status": "WAITING_JOB_EXPANSION", "created_at": now(),
        "updated_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "campaign_id": CAMPAIGN,
    }
    atomic(output, document)
    expansion = wait_for_expansion(args.timeout_seconds)
    document.update({
        "status": "SEALING_CONTRACT", "updated_at": now(),
        "job_expansion_result": str(EXPANSION_RESULT),
        "job_expansion_result_sha256": sha(EXPANSION_RESULT),
    })
    atomic(output, document)
    contract = seal_contract(expansion)
    launched = launch_monitor()
    document.update({
        "status": "LAUNCHED", "updated_at": now(),
        "contract_path": str(CONTRACT_PATH),
        "contract_sha256": sha(CONTRACT_PATH),
        "contract_release": contract["release_version"],
        "monitor": launched,
    })
    atomic(output, document)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=172800.0)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
