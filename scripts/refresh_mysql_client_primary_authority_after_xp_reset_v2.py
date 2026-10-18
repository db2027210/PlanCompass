#!/usr/bin/env python3
"""Refresh v20 identities and bind blkio authority to current container IDs."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import prepare_mysql_four_scene_v4 as prepare
from scripts import refresh_mysql_client_primary_authority_after_xp_reset_v1 as predecessor


def main() -> int:
    code = predecessor.main()
    if code != 0:
        return code
    args = sys.argv
    run_id = args[args.index("--run-id") + 1]
    output = predecessor.HISTORY / run_id / "authority"
    freeze = predecessor.load(predecessor.FREEZE)
    frozen = {str(row["worker_id"]): dict(row) for row in freeze["workers"]}
    prepared_path = output / "prepared_binding.json"
    accounts_path = output / "benchmark_accounts_binding.json"
    result_path = output / "authority_result.json"
    prepared = predecessor.load(prepared_path)
    accounts = predecessor.load(accounts_path)
    prepared_rows = {
        str(row["worker_id"]): dict(row) for row in prepared["workers"]
    }
    account_rows = {
        str(row["worker_id"]): dict(row) for row in accounts["workers"]
    }
    repaired: list[str] = []
    for worker_id in sorted(frozen):
        current = predecessor.inspect(str(frozen[worker_id]["container"]))
        authority = dict(prepared_rows[worker_id].get("blkio_authority") or {})
        expected = (
            Path("/sys/fs/cgroup/blkio/docker-limited") / str(current["Id"])
        )
        if authority.get("cgroup_path") != str(expected):
            authority = prepare._enforce_blkio(
                current, freeze, frozen[worker_id],
            )
            repaired.append(worker_id)
        for suffix in (
            "tasks", "blkio.throttle.read_bps_device",
            "blkio.throttle.write_bps_device",
        ):
            if not (expected / suffix).is_file():
                raise RuntimeError(f"{worker_id} blkio authority file absent: {suffix}")
        task_ids = (expected / "tasks").read_text(encoding="ascii").split()
        if not task_ids:
            raise RuntimeError(f"{worker_id} blkio authority has no tasks")
        prepared_rows[worker_id]["blkio_authority"] = authority
        prepared_rows[worker_id]["blkio_identity_refresh_v2"] = True

    prepared["schema_version"] = "mysql_client_primary_identity_refresh_prepared_v2"
    prepared["workers"] = [prepared_rows[key] for key in sorted(prepared_rows)]
    prepared["blkio_rebound_worker_ids"] = repaired
    predecessor.atomic(prepared_path, prepared)
    for worker_id in account_rows:
        account_rows[worker_id]["blkio_identity_refresh_v2"] = True
    accounts["schema_version"] = "mysql_client_primary_identity_refresh_accounts_v2"
    accounts["prepared_binding_sha256"] = predecessor.sha(prepared_path)
    accounts["workers"] = [account_rows[key] for key in sorted(account_rows)]
    predecessor.atomic(accounts_path, accounts)
    result = predecessor.load(result_path)
    result.update({
        "schema_version": "mysql_client_primary_identity_refresh_authority_v2",
        "status": "PASS",
        "prepared": {
            "path": str(prepared_path), "sha256": predecessor.sha(prepared_path),
        },
        "accounts": {
            "path": str(accounts_path), "sha256": predecessor.sha(accounts_path),
        },
        "blkio_rebound_worker_ids": repaired,
        "blkio_resource_envelope_changed": False,
        "container_mutation_performed": False,
        "cgroup_throttle_reasserted_for_current_container_identity": bool(repaired),
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
    })
    predecessor.atomic(result_path, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
