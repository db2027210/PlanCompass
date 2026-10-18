#!/usr/bin/env python3
"""XP4 C runtime with adapter compatibility and scene-local Top4 tables."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from scripts import run_mysql_four_scene_algorithm_c_xp4_runtime_v1 as runtime_v1
from scripts import run_mysql_four_scene_algorithm_c_xp4_runtime_v4 as runtime_v4


# The async scene controller binds these public runtime attributes before
# calling main().  Keep the same module interface as every XP4 predecessor.
alg = runtime_v1.alg
OPTIMIZATION_CONTRACT = runtime_v4.OPTIMIZATION_CONTRACT
WAVE_COORDINATION_CONTRACT = runtime_v4.WAVE_COORDINATION_CONTRACT


TOP4_ACTIVE_PARAMETERS = {
    "hybench_xp_cpu": (
        "row_evaluate_cost",
        "key_compare_cost",
        "disk_temptable_row_cost",
        "io_block_read_cost",
    ),
    "hybench_xp_io": (
        "io_block_read_cost",
        "disk_temptable_row_cost",
        "disk_temptable_create_cost",
        "memory_temptable_row_cost",
    ),
}

_ORIGINAL_LOAD = runtime_v1.alg.load
_INSTALLED = False


def _top4_table_load(path: Path | str) -> dict[str, Any]:
    """Project only operator-table parameter metadata onto the frozen Top4.

    The CPU and I/O scenes intentionally share one physics table, so the
    process-local async scene selected by the runtime is the disambiguating
    authority.  All coefficients and candidate-ranking code remain unchanged.
    """
    document = _ORIGINAL_LOAD(path)
    if not (
        runtime_v1.INDEPENDENT_SCENE_EXECUTION
        and len(runtime_v1.SCENES) == 1
        and runtime_v1.SCENES[0] in TOP4_ACTIVE_PARAMETERS
    ):
        return document
    scene = runtime_v1.SCENES[0]
    table_path = (runtime_v1.alg.TABLE_ROOT / runtime_v1.alg.TABLES[scene]).resolve()
    if Path(path).resolve() != table_path:
        return document
    result = copy.deepcopy(document)
    parameter_space = result.get("parameter_space") or {}
    ranges = parameter_space.get("ranges") or {}
    active = TOP4_ACTIVE_PARAMETERS[scene]
    if any(name not in ranges for name in active):
        raise RuntimeError(f"{scene} Top4 operator-table range differs")
    parameter_space["active_params"] = list(active)
    parameter_space["ranges"] = {
        name: copy.deepcopy(ranges[name]) for name in active
    }
    result["parameter_space"] = parameter_space
    return result


def install() -> None:
    global _INSTALLED
    runtime_v4.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    runtime_v4.WAVE_COORDINATION_CONTRACT = WAVE_COORDINATION_CONTRACT
    runtime_v4.install()
    if not _INSTALLED:
        runtime_v1.alg.load = _top4_table_load
        _INSTALLED = True


def main() -> int:
    install()
    return runtime_v1.main()


if __name__ == "__main__":
    raise SystemExit(main())
