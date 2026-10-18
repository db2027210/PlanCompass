#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "config/formal/frozen_mysql_algorithm_c_four_scene_20260727_v43"
MANIFEST = BUNDLE / "manifest.json"

REGISTERED = [
    "shell/freeze_mysql_four_scene_bundle.py",
    "shell/launch_mysql_algorithm_c_four_scenes.py",
    "shell/monitor_mysql_algorithm_c_four_scenes.py",
    "host02_job_xp_mutex.py",
    "shell/manual_mysql_sgd_r0_campaign.py",
    "shell/formal_job_multiquery_workload.py",
    "formal_job_continuous_session.py",
    "shell/job_memory_r0_calibration.py",
    "shell/capture_tpcc_formal_binding_trace_node01.py",
    "tests/test_manual_mysql_sgd_capture_mode.py",
    "tests/test_formal_job_continuous_session.py",
    "tests/test_mysql_algorithm_c_production_services.py",
    "tests/test_capture_tpcc_formal_binding_trace_node01.py",
    "tests/test_formal_mysql_binding_trace.py",
    "tests/test_mysql_algorithm_c_four_scene_launcher.py",
    "shell/test_host02_job_xp_mutex.py",
    "run_xp_r0_with_candidate_jar.py",
    "formal_stability.py",
    "formal_mysql_sgd_orchestration.py",
    "mysql_algorithm_c_production_services.py",
    "common_outer_loop.py",
    "mysql_parameter_optimizer.py",
    "config/formal/unified_resource_awareness_20260721.json",
    "config/formal/unified_resource_awareness_20260721_v3.json",
    "preflight/frozen_formal_config_20260727_v10/measurement_protocol.json",
    "config/formal/mysql_sgd_production/job_memory.algorithm_c.optimizer.json",
    "config/formal/mysql_sgd_production/hybench_xp_io.algorithm_c.optimizer.json",
    "config/formal/mysql_sgd_production/tpch_memory.algorithm_c.optimizer.json",
    "config/formal/mysql_sgd_production/tpcc_cpu.algorithm_c.optimizer.json",
    "config/formal/mysql_sgd_production/profiles/job_memory.json",
    "config/formal/mysql_sgd_production/profiles/hybench_xp_io.json",
    "config/formal/mysql_sgd_production/profiles/tpch_memory.json",
    "config/formal/mysql_sgd_production/profiles/tpcc_cpu.json",
    "config/formal/mysql_sgd_production/xp_frozen_launch_spec.json",
    "config/formal/frozen_mysql_deployment_schedule_20260723_v4_mysql_only/experience_table_sgd_method_manifest.json",
    "config/formal/frozen_mysql_deployment_schedule_20260723_v4_mysql_only/readiness_manifest.json",
    "formal_benchbase_src_20260723/target/benchbase-2023-SNAPSHOT-formal-binding-trace-candidate.jar",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_contract() -> None:
    for relative in (
        "config/formal/unified_resource_awareness_20260721.json",
        "config/formal/unified_resource_awareness_20260721_v3.json",
        "preflight/frozen_formal_config_20260727_v10/measurement_protocol.json",
    ):
        stability = load_json(ROOT / relative)["stability"]
        keys = ("cpu_cv_max", "memory_cv_max", "io_cv_max")
        if any(float(stability[key]) != 0.10 for key in keys):
            raise ValueError(f"non-uniform resource CV contract: {relative}")
    runner = (ROOT / "shell/manual_mysql_sgd_r0_campaign.py").read_text(encoding="utf-8")
    for token in ("--capture-tpch-bindings", "INVALID_R0_UNSTABLE", "algorithm_c_authorized=False"):
        if token not in runner:
            raise ValueError(f"shared runner lacks required token: {token}")
    xp = load_json(ROOT / "config/formal/mysql_sgd_production/xp_frozen_launch_spec.json")
    command = xp["command"]
    for token in ("/envs/py313/bin/python", "--scenario xp", "--profile-json"):
        if token not in command:
            raise ValueError(f"XP launch spec lacks required token: {token}")
    if " --config " in command or command.startswith("python3 "):
        raise ValueError("XP launch spec uses forbidden interpreter or argument")


def launch_specs() -> dict:
    python = "/home/anonymous/miniconda3/envs/py313/bin/python"
    root = "/home/anonymous/program/resource_adaptive_mysql"
    return {
        "job_memory": {
            "host": "compute00",
            "command": f"{python} {root}/shell/manual_mysql_sgd_r0_campaign.py --scenario job --run-id {{fresh_utc_run_id}} --profile-json {root}/config/formal/mysql_sgd_production/profiles/job_memory.json --measurement-seconds 600 --minimum-measurement-seconds 600 --require-r0-profile-gate",
            "stage": "fresh_r0",
        },
        "hybench_xp_io": {
            "host": "node02_via_compute00",
            "command": load_json(ROOT / "config/formal/mysql_sgd_production/xp_frozen_launch_spec.json")["command"],
            "stage": "fresh_r0",
        },
        "tpch_memory": {
            "host": "node01_via_compute00",
            "command": f"{python} {root}/shell/manual_mysql_sgd_r0_campaign.py --scenario tpch --run-id {{fresh_utc_run_id}} --profile-json {root}/config/formal/mysql_sgd_production/profiles/tpch_memory.json --measurement-seconds 600 --minimum-measurement-seconds 600 --require-r0-profile-gate",
            "stage": "fresh_r0",
        },
        "tpcc_cpu": {
            "host": "node01_via_compute00",
            "command": f"{python} {root}/shell/manual_mysql_sgd_r0_campaign.py --scenario tpcc --run-id {{fresh_utc_run_id}} --profile-json {root}/config/formal/mysql_sgd_production/profiles/tpcc_cpu.json --measurement-seconds 600 --minimum-measurement-seconds 600 --require-r0-profile-gate",
            "stage": "fresh_r0",
        },
    }


def freeze() -> None:
    if BUNDLE.exists():
        raise FileExistsError(f"bundle already exists: {BUNDLE}")
    validate_contract()
    entries = []
    for relative in REGISTERED:
        path = ROOT / relative
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError(f"invalid registered input: {path}")
        entries.append({"path": relative, "sha256": sha256(path)})
    BUNDLE.mkdir(parents=True)
    payload = {
        "schema_version": "mysql_algorithm_c_four_scene_bundle_v17",
        "bundle_id": BUNDLE.name,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "immutable": True,
        "formal_scope": "mysql_only",
        "environment_mutation_after_freeze": False,
        "method_contract": {
            "global_real_replay_top_k_hard_max": 5,
            "target_accepted_shrinks": 10,
            "qps_role": "outer_acceptance_only",
            "acceptance": "qps_strictly_up_and_target_resource_strictly_down_and_each_non_target_strictly_below_0.8",
            "resource_cv_max": 0.10,
            "warmup_seconds": 60,
            "measurement_seconds": {"minimum": 180, "maximum": 600, "step": 30},
        },
        "preparation_launch_authorized": True,
        "algorithm_c_outer_launch_authorized": False,
        "outer_authorization_condition": "each scenario has a valid stable R0 and required binding/EXPLAIN evidence",
        "post_start_gate": {"wait_seconds": 30, "require_pid_live": True, "require_log_or_sample_growth": True},
        "launch_specs": launch_specs(),
        "registered_files": entries,
        "verification": {"focused_tests": "<actual> passed", "python": "3.13"},
    }
    MANIFEST.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    digest = sha256(MANIFEST)
    (BUNDLE / "manifest.json.sha256").write_text(f"{digest}  manifest.json\n", encoding="ascii")
    for path in BUNDLE.iterdir():
        path.chmod(0o444)
    BUNDLE.chmod(0o555)
    print(f"bundle={BUNDLE} registered={len(entries)} manifest_sha256={digest}")


def verify() -> None:
    payload = load_json(MANIFEST)
    for entry in payload["registered_files"]:
        actual = sha256(ROOT / entry["path"])
        if actual != entry["sha256"]:
            raise ValueError(f"bundle drift: {entry['path']}")
    expected = (BUNDLE / "manifest.json.sha256").read_text(encoding="ascii").split()[0]
    if sha256(MANIFEST) != expected:
        raise ValueError("manifest checksum mismatch")
    print(f"PASS bundle={payload['bundle_id']} registered={len(payload['registered_files'])}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    verify() if args.verify else freeze()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
