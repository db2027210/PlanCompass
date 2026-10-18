#!/usr/bin/env python3
"""Run XP Top-4 C with the zero-safe rolling isolation gate."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v4 as gate_v4
from scripts import preflight_mysql_four_scene_v7 as preflight_v7
from scripts import run_mysql_algorithm_c_async_scene_v58 as v58


alg = v58.alg
ASYNC_CONTRACT = v58.ASYNC_CONTRACT


def validate_contract() -> dict[str, Any]:
    contract = alg.load(ASYNC_CONTRACT)
    zero = contract.get("zero_value_telemetry_release") or {}
    if (
        contract.get("release_version") != "v59"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or zero.get("swap_zero_is_valid") is not True
        or zero.get("memory_full_psi_zero_is_valid") is not True
        or zero.get("missing_values_remain_fail_closed") is not True
        or zero.get("algorithm_or_candidate_flow_changed") is not False
    ):
        raise RuntimeError("v59 zero-value telemetry contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("v59 source binding is not an object")
        path = ROOT / str(binding["path"])
        if (
            not path.is_file()
            or path.is_symlink()
            or alg.sha(path) != str(binding["sha256"])
        ):
            raise RuntimeError(f"v59 source binding differs: {path}")
    return contract


def main() -> int:
    validate_contract()
    v58.v57.v56.install()
    v58.v57.v56.runtime.coordination.rolling_quiet_gate = gate_v4.rolling_quiet_gate
    alg.r0mod.PREFLIGHT_SOURCE = Path(preflight_v7.__file__).resolve()
    return v58.v57.v56.v55.v54.v53.v52.v51.v50.v49.v45.v34.main()


if __name__ == "__main__":
    raise SystemExit(main())
