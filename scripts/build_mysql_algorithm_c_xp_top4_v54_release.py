#!/usr/bin/env python3
"""Build v54 with serialized Docker-Socket inventory snapshots."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_c_20260825_v2"
    / "algorithm_c_async_contract.json"
)
OUTPUT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_c_20260825_v3"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_v54.py"
LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_c_async_v54.py"
PREFLIGHT = ROOT / "scripts/preflight_mysql_four_scene_v5.py"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def binding(path: Path) -> dict[str, str]:
    return {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}


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


def main() -> int:
    if OUTPUT.exists() and any(OUTPUT.iterdir()):
        raise RuntimeError(f"refusing to overwrite frozen release: {OUTPUT}")
    contract = load(PREDECESSOR)
    contract["release_version"] = "v54"
    contract["released_at"] = "2026-08-25T12:48:00+08:00"
    contract["docker_unix_socket_inventory_successor"] = {
        "inventory_transport": "docker_unix_socket_http_api",
        "docker_cli_inventory_used": False,
        "snapshot_lock_scope": "docker_list_and_inspect_snapshot_only",
        "snapshot_lock_is_management_only": True,
        "database_or_workload_resource_lease": False,
        "cross_scene_completion_dependency": False,
        "round_barrier": False,
        "database_inspect_failure_fails_closed": True,
        "intended_container_inspect_failure_fails_closed": True,
        "unrelated_non_database_inspect_failure_blocks": False,
        "process_inventory_unchanged": True,
        "algorithm_or_parameter_flow_changed": False,
        "trigger_observation": "concurrent_docker_ps_cli_requests_timed_out",
    }
    bindings = copy.deepcopy(contract["source_bindings"])
    bindings["predecessor_controller_v53"] = binding(
        ROOT / "scripts/run_mysql_algorithm_c_async_scene_v53.py"
    )
    bindings["controller"] = binding(CONTROLLER)
    bindings["launcher"] = binding(LAUNCHER)
    bindings["docker_unix_socket_inventory_successor"] = binding(PREFLIGHT)
    contract["source_bindings"] = bindings
    atomic(OUTPUT / "algorithm_c_async_contract.json", contract)
    print(OUTPUT / "algorithm_c_async_contract.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
