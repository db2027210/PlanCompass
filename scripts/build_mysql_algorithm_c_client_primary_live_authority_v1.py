#!/usr/bin/env python3
"""Build the no-recalibration fresh-R0 authority for client-primary C."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = ROOT / (
    "config/formal/frozen_mysql_four_scene_two_worker_20260903_v20/"
    "four_scene_effective_resource_successor.json"
)
CHECKPOINT = HISTORY / (
    "v1185_to_c_client_primary_bootstrap_20260903/"
    "checkpoint/prepared_binding.json"
)
SCENES = (
    "job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io",
)


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    )
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--authority-root", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run id")
    authority = args.authority_root.expanduser().resolve()
    if HISTORY.resolve() not in authority.parents or authority.is_symlink():
        parser.error("authority root must be a regular history directory")
    preflight_path = authority / "transition_preflight.json"
    prepared_path = authority / "prepared_binding.json"
    accounts_path = authority / "benchmark_accounts_binding.json"
    preflight = load(preflight_path)
    prepared = load(prepared_path)
    accounts = load(accounts_path)
    checkpoint = load(CHECKPOINT)
    if (
        checkpoint.get("status") != "PREPARED"
        or checkpoint.get("freeze_sha256") != sha(FREEZE)
        or preflight.get("status") != "PASS"
        or preflight.get("formal_launch_authorized") is not True
        or float(preflight.get("observed_seconds") or 0) < 60.0
        or preflight.get("freeze_sha256") != sha(FREEZE)
        or prepared.get("status") != "PREPARED"
        or prepared.get("freeze_sha256") != sha(FREEZE)
        or prepared.get("preflight_sha256") != sha(preflight_path)
        or prepared.get("workload_started") is not False
        or prepared.get("fresh_r0_complete") is not False
        or prepared.get("formal_outer_started") is not False
        or len(prepared.get("workers") or []) != 8
        or accounts.get("status") != "PASS"
        or accounts.get("freeze_sha256") != sha(FREEZE)
        or accounts.get("prepared_binding_sha256") != sha(prepared_path)
        or accounts.get("passwords_emitted") is not False
        or accounts.get("workload_started") is not False
        or len(accounts.get("workers") or []) != 8
    ):
        raise RuntimeError("client-primary C live authority lineage differs")
    output = HISTORY / args.run_id
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh live-authority output required: {output}")
    output.mkdir(parents=True, mode=0o700)
    value = {
        "schema_version": "mysql_algorithm_c_client_primary_fresh_r0_authority_v1",
        "status": "PASS", "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE), "freeze_sha256": sha(FREEZE),
        "checkpoint_path": str(CHECKPOINT), "checkpoint_sha256": sha(CHECKPOINT),
        "preflight_path": str(preflight_path),
        "preflight_sha256": sha(preflight_path),
        "prepared_binding_path": str(prepared_path),
        "prepared_binding_sha256": sha(prepared_path),
        "benchmark_accounts_path": str(accounts_path),
        "benchmark_accounts_sha256": sha(accounts_path),
        "fresh_r0_complete": True,
        "formal_outer_launch_authorized": True,
        "formal_outer_started": False,
        "accepted_rounds": {scene: 0 for scene in SCENES},
        "target_bottleneck_gate": {
            "passed": True,
            "fresh_incumbent_measurement_owned_by_controller": True,
        },
        "failures": [], "client_isolation_failures": [],
        "observed_seconds": float(preflight["observed_seconds"]),
        "recalibration_performed": False,
        "algorithm_a_measurement_credit_reused": False,
        "algorithm_b_measurement_credit_reused": False,
        "accepted_state_credit_increment": 0,
        "source_authority_path": str(authority),
        "source_authority_result_sha256": sha(authority / "authority_result.json"),
        "xp_outer_primary_metric": "native_hybench_xp_client_xap_qps",
        "xp_server_questions_qps_role": "audit_only",
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
    }
    path = output / "fresh_r0_binding.json"
    atomic(path, value)
    print(json.dumps({
        "status": "PASS", "fresh_r0": str(path), "sha256": sha(path),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
