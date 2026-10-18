#!/usr/bin/env python3
"""Run XP Top-4 Algorithm C with process-proven inactive restart fallback."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import preflight_mysql_four_scene_v6 as preflight_v6
from scripts import run_mysql_algorithm_c_async_scene_v54 as v54


runtime = v54.runtime
alg = v54.alg
ASYNC_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_c_20260825_v4"
    / "algorithm_c_async_contract.json"
)


def validate_contract() -> dict[str, Any]:
    v54.validate_contract()
    if not ASYNC_CONTRACT.is_file() or ASYNC_CONTRACT.is_symlink():
        raise RuntimeError(f"sealed asynchronous C v55 contract absent: {ASYNC_CONTRACT}")
    contract = alg.load(ASYNC_CONTRACT)
    policy = contract.get("inactive_restarting_external_database_policy") or {}
    if (
        contract.get("release_version") != "v55"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or policy.get("intended_mysql_inspect_failure_fails_closed") is not True
        or policy.get("external_database_summary_state_required") != "restarting"
        or policy.get("live_cgroup_process_scan_count") != 2
        or policy.get("live_cgroup_process_required_count") != 0
        or policy.get("runtime_process_watch_required") is not True
        or policy.get("algorithm_or_parameter_flow_changed") is not False
    ):
        raise RuntimeError("v55 Algorithm C inactive-restart policy differs")
    for binding in (contract.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("v55 source binding is not an object")
        path = ROOT / str(binding["path"])
        if (
            not path.is_file()
            or path.is_symlink()
            or alg.sha(path) != str(binding["sha256"])
        ):
            raise RuntimeError(f"v55 Algorithm C binding differs: {path}")
    return contract


def install() -> None:
    v54.install()
    alg.r0mod.PREFLIGHT_SOURCE = Path(preflight_v6.__file__).resolve()

    original_state_document = (
        v54.v53.v52.v51.v50.v49.v28.ORIGINAL_V27_STATE_DOCUMENT
    )

    def restart_fallback_state_document(**kwargs: Any) -> dict[str, Any]:
        document = copy.deepcopy(original_state_document(**kwargs))
        document["inactive_restarting_external_database_successor"] = {
            "release_version": "v55",
            "contract_path": str(ASYNC_CONTRACT),
            "contract_sha256": alg.sha(ASYNC_CONTRACT),
            "preflight_source_path": str(Path(preflight_v6.__file__).resolve()),
            "preflight_source_sha256": alg.sha(Path(preflight_v6.__file__).resolve()),
            "inspect_failure_ignored": False,
            "replacement_proof": "two_direct_proc_cgroup_scans_no_live_process",
            "runtime_process_watch_required": True,
            "cross_scene_completion_dependency": False,
            "algorithm_or_parameter_flow_changed": False,
        }
        return document

    v54.v53.v52.v51.v50.v49.v28.ORIGINAL_V27_STATE_DOCUMENT = (
        restart_fallback_state_document
    )


def main() -> int:
    validate_contract()
    install()
    return v54.v53.v52.v51.v50.v49.v45.v34.main()


if __name__ == "__main__":
    raise SystemExit(main())
