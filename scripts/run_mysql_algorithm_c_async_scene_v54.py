#!/usr/bin/env python3
"""Run XP Top-4 Algorithm C with Docker-Socket inventory."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import preflight_mysql_four_scene_v5 as preflight_v5
from scripts import run_mysql_algorithm_c_async_scene_v53 as v53


runtime = v53.runtime
alg = runtime.alg
ASYNC_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_c_20260825_v3"
    / "algorithm_c_async_contract.json"
)


def validate_contract() -> dict[str, Any]:
    if not ASYNC_CONTRACT.is_file() or ASYNC_CONTRACT.is_symlink():
        raise RuntimeError(f"sealed asynchronous C v54 contract absent: {ASYNC_CONTRACT}")
    contract = alg.load(ASYNC_CONTRACT)
    inventory = contract.get("docker_unix_socket_inventory_successor") or {}
    if (
        contract.get("release_version") != "v54"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or contract.get("post_search_stability_confirmation") is not False
        or inventory.get("inventory_transport") != "docker_unix_socket_http_api"
        or inventory.get("snapshot_lock_scope")
        != "docker_list_and_inspect_snapshot_only"
        or inventory.get("cross_scene_completion_dependency") is not False
        or inventory.get("database_inspect_failure_fails_closed") is not True
        or inventory.get("intended_container_inspect_failure_fails_closed") is not True
        or inventory.get("process_inventory_unchanged") is not True
        or inventory.get("algorithm_or_parameter_flow_changed") is not False
    ):
        raise RuntimeError("v54 Algorithm C Docker inventory contract differs")
    successor = contract.get("xp_top4_parameter_successor") or {}
    if (
        successor.get("active_parameters_by_scene") != v53.v52.EXPECTED_ACTIVE
        or successor.get("candidate_subset_count_by_scene")
        != {"hybench_xp_cpu": 15, "hybench_xp_io": 15}
        or successor.get("real_top_k_limit") != 5
    ):
        raise RuntimeError("v54 XP Top-4 binding differs")
    for binding in (contract.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("v54 source binding is not an object")
        path = ROOT / str(binding["path"])
        if (
            not path.is_file()
            or path.is_symlink()
            or alg.sha(path) != str(binding["sha256"])
        ):
            raise RuntimeError(f"v54 Algorithm C binding differs: {path}")
    return contract


def install() -> None:
    v53.validate_contract()
    v53.install()
    alg.r0mod.PREFLIGHT_SOURCE = Path(preflight_v5.__file__).resolve()

    original_state_document = v53.v52.v51.v50.v49.v28.ORIGINAL_V27_STATE_DOCUMENT

    def inventory_state_document(**kwargs: Any) -> dict[str, Any]:
        document = copy.deepcopy(original_state_document(**kwargs))
        document["docker_unix_socket_inventory_successor"] = {
            "release_version": "v54",
            "contract_path": str(ASYNC_CONTRACT),
            "contract_sha256": alg.sha(ASYNC_CONTRACT),
            "preflight_source_path": str(Path(preflight_v5.__file__).resolve()),
            "preflight_source_sha256": alg.sha(Path(preflight_v5.__file__).resolve()),
            "inventory_transport": "docker_unix_socket_http_api",
            "snapshot_lock_scope": "docker_list_and_inspect_snapshot_only",
            "cross_scene_completion_dependency": False,
            "process_inventory_unchanged": True,
            "algorithm_or_parameter_flow_changed": False,
        }
        return document

    v53.v52.v51.v50.v49.v28.ORIGINAL_V27_STATE_DOCUMENT = inventory_state_document


def main() -> int:
    validate_contract()
    install()
    return v53.v52.v51.v50.v49.v45.v34.main()


if __name__ == "__main__":
    raise SystemExit(main())
