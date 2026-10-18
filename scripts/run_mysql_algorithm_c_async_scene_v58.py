#!/usr/bin/env python3
"""Run XP Top-4 C with exact full-parallel sibling-MySQL R0 admission."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import preflight_mysql_four_scene_v7 as preflight_v7
from scripts import run_mysql_algorithm_c_async_scene_v57 as v57


alg = v57.alg
ASYNC_CONTRACT = v57.ASYNC_CONTRACT


def validate_contract() -> dict[str, Any]:
    contract = alg.load(ASYNC_CONTRACT)
    sibling = contract.get("full_parallel_sibling_mysql_point_gate") or {}
    if (
        contract.get("release_version") != "v58"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or sibling.get("exact_authority_bound") is not True
        or sibling.get("cpu_overlap_required") != 0
        or sibling.get("same_disk_allowed_with_cpu_disjointness") is not True
        or sibling.get("cross_scene_completion_dependency") is not False
        or sibling.get("sibling_container_mutation_performed") is not False
        or sibling.get("candidate_or_outer_flow_changed") is not False
    ):
        raise RuntimeError("v58 full-parallel sibling contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("v58 source binding is not an object")
        path = ROOT / str(binding["path"])
        if (
            not path.is_file()
            or path.is_symlink()
            or alg.sha(path) != str(binding["sha256"])
        ):
            raise RuntimeError(f"v58 source binding differs: {path}")
    return contract


def main() -> int:
    validate_contract()
    v57.v56.install()
    alg.r0mod.PREFLIGHT_SOURCE = Path(preflight_v7.__file__).resolve()
    return v57.v56.v55.v54.v53.v52.v51.v50.v49.v45.v34.main()


if __name__ == "__main__":
    raise SystemExit(main())
