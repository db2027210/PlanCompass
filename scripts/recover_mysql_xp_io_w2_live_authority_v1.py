#!/usr/bin/env python3
"""Recreate absent XP/I/O w2 and publish successor live authority bindings."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_data_planes_v6 as reset
from scripts import provision_mysql_four_scene_benchmark_users_v5 as users


FREEZE = ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18/four_scene_effective_resource_successor.json"
WORKER_ID = "xp_io_w2"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError("JSON object required")
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-prepared", type=Path, required=True)
    parser.add_argument("--source-accounts", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    prepared_path = args.source_prepared.resolve()
    accounts_path = args.source_accounts.resolve()
    output = args.output_root.resolve()
    if output.exists() or output.is_symlink():
        raise RuntimeError("fresh recovery output required")
    output.mkdir(parents=True, mode=0o700)
    freeze = load(FREEZE)
    prepared = load(prepared_path)
    accounts = load(accounts_path)
    if (
        prepared.get("status") != "PREPARED"
        or prepared.get("freeze_sha256") != sha(FREEZE)
        or accounts.get("status") != "PASS"
        or accounts.get("prepared_binding_sha256") != sha(prepared_path)
        or len(prepared.get("workers") or []) != 8
        or len(accounts.get("workers") or []) != 8
    ):
        raise RuntimeError("predecessor live authority differs")
    frozen = {str(row["worker_id"]): dict(row) for row in freeze["workers"]}
    prior = {str(row["worker_id"]): dict(row) for row in prepared["workers"]}
    quarantine = (
        Path(str(freeze["storage"]["runtime_root"])) / "quarantine"
        / output.name / "prestart_recovery"
    )
    reset_root = output / "worker_reset"
    reset_root.mkdir()
    reset_row = reset._prepare_fresh_xp(
        freeze, frozen[WORKER_ID], prior[WORKER_ID], quarantine, reset_root,
        True, "formal_point", True,
        allow_absent_predecessor_after_interrupted_discard=True,
    )
    account_root = output / "accounts"
    account_root.mkdir()
    account_row = users._run_worker(reset_row, account_root, True, False)
    replacement = {**frozen[WORKER_ID], **reset_row, **account_row}
    prepared_successor = copy.deepcopy(prepared)
    prepared_successor.update({
        "schema_version": "mysql_xp_io_w2_ghost_task_live_authority_successor_v1",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "predecessor_prepared_path": str(prepared_path),
        "predecessor_prepared_sha256": sha(prepared_path),
        "technical_container_recovery": {
            "worker_id": WORKER_ID,
            "reason": "docker_task_already_exists_but_absent_from_ctr_task_list",
            "resource_envelope_changed": False,
            "recalibration_performed": False,
        },
        "workers": [
            replacement if str(row["worker_id"]) == WORKER_ID else row
            for row in prepared["workers"]
        ],
    })
    successor_prepared_path = output / "prepared_binding.json"
    atomic(successor_prepared_path, prepared_successor)
    accounts_successor = copy.deepcopy(accounts)
    accounts_successor.update({
        "schema_version": "mysql_xp_io_w2_ghost_task_accounts_successor_v1",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "predecessor_accounts_path": str(accounts_path),
        "predecessor_accounts_sha256": sha(accounts_path),
        "prepared_binding_path": str(successor_prepared_path),
        "prepared_binding_sha256": sha(successor_prepared_path),
        "workers": [
            replacement if str(row["worker_id"]) == WORKER_ID else row
            for row in accounts["workers"]
        ],
    })
    successor_accounts_path = output / "benchmark_accounts_binding.json"
    atomic(successor_accounts_path, accounts_successor)
    atomic(output / "recovery_manifest.json", {
        "schema_version": "mysql_xp_io_w2_ghost_task_recovery_manifest_v1",
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "worker_id": WORKER_ID,
        "prepared_binding": str(successor_prepared_path),
        "prepared_binding_sha256": sha(successor_prepared_path),
        "benchmark_accounts_binding": str(successor_accounts_path),
        "benchmark_accounts_binding_sha256": sha(successor_accounts_path),
        "new_container_id": replacement["container_id"],
        "recalibration_performed": False,
    })
    print(json.dumps({
        "status": "PASS", "prepared": str(successor_prepared_path),
        "accounts": str(successor_accounts_path),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
