#!/usr/bin/env python3
"""v120 Algorithm C entry using the September interval-control successor.

This entry requires a fresh search. It does not reinterpret any v119
checkpoint, accepted round, or candidate measurement under the new policy.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
import sys
from types import ModuleType
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_outer_rejection_cycle_v2 as rejection_cycle
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v29 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v45 as old_runtime
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v46 as runtime


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_interval_control_20260920_v120/"
    "algorithm_c_interval_contract.json"
)
PARENT_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260907_v119/"
    "algorithm_c_pg_latest_outer_contract.json"
)


def validate_contract() -> dict[str, Any]:
    contract = runtime.alg.load(CONTRACT)
    parent = runtime.alg.load(PARENT_CONTRACT)
    flow = dict(contract.get("outer_flow") or {})
    if (
        contract.get("release_version") != "v120-actual-interval-two-valid-misses"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or flow.get("semantic_batch") != "one_complete_valid_top5"
        or flow.get("first_valid_miss") != "expand_next_candidate_batch"
        or flow.get("second_valid_miss") != "bisect_unchanged_incumbent"
        or flow.get("empty_or_technical_batch_miss_credit") != 0
        or flow.get("qps_used_for_inner_scoring") is not False
        or flow.get("terminal_normalized_width") != 0.01
        or flow.get("fixed_replay_or_wall_stop") is not False
        or flow.get("fixed_outer_round_stop") is not False
        or flow.get("post_search_stability_confirmation") is not False
        or contract.get("parent_contract", {}).get("sha256")
        != runtime.alg.sha(PARENT_CONTRACT)
        or parent.get("formal_scope") != "mysql_only"
        or parent.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("Algorithm C v120 interval contract differs")
    for label, document in (("parent", parent), ("v120", contract)):
        for name, row in dict(document.get("source_bindings") or {}).items():
            path = Path(str(row.get("path") or ""))
            if not path.is_absolute():
                path = ROOT / path
            if path.is_symlink() or not path.is_file() or runtime.alg.sha(path) != row.get("sha256"):
                raise RuntimeError(
                    f"Algorithm C v120 {label} source differs: {name}: {path}"
                )
    return contract


@contextlib.contextmanager
def bind_runtime() -> Iterator[None]:
    """Rebind the existing launcher chain while preserving sealed modules."""
    rebound: list[tuple[ModuleType, str, Any]] = []
    for module in tuple(sys.modules.values()):
        if not isinstance(module, ModuleType):
            continue
        name = str(getattr(module, "__name__", ""))
        if not name.startswith("scripts.run_mysql_algorithm_c_four_scene_client_primary_v"):
            continue
        for attribute in ("runtime", "latest_runtime"):
            if getattr(module, attribute, None) is old_runtime:
                rebound.append((module, attribute, old_runtime))
                setattr(module, attribute, runtime)
    old_cycle = runtime.rejection_cycle
    old_flags = (runtime.INDEPENDENT_SCENE_EXECUTION, runtime.NO_LEASE_RUNTIME)
    runtime.rejection_cycle = rejection_cycle
    runtime.INDEPENDENT_SCENE_EXECUTION = True
    runtime.NO_LEASE_RUNTIME = True
    try:
        yield
    finally:
        runtime.rejection_cycle = old_cycle
        runtime.INDEPENDENT_SCENE_EXECUTION, runtime.NO_LEASE_RUNTIME = old_flags
        for module, attribute, previous in reversed(rebound):
            setattr(module, attribute, previous)


def main() -> int:
    validate_contract()
    if "--fresh-search" not in sys.argv[1:]:
        raise RuntimeError("v120 requires --fresh-search; old checkpoints use a different policy")
    old_validate = predecessor.validate_contract
    old_contract = predecessor.CONTRACT
    predecessor.validate_contract = validate_contract
    predecessor.CONTRACT = CONTRACT
    try:
        with bind_runtime():
            return int(predecessor.main())
    finally:
        predecessor.validate_contract = old_validate
        predecessor.CONTRACT = old_contract


if __name__ == "__main__":
    raise SystemExit(main())
