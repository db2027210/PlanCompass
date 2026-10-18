#!/usr/bin/env python3
"""Zero-call audit of the TPC-C v28 role-aware pre-third gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

try:
    from tpcc_autonomous_target10_controller_v28_role_aware import (
        build_role_aware_prethird_feasibility_gate,
    )
except ModuleNotFoundError:
    from tools.tpcc_autonomous_target10_controller_v28_role_aware import (
        build_role_aware_prethird_feasibility_gate,
    )


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller-state", type=Path, required=True)
    parser.add_argument("--formal-state", type=Path, required=True)
    args = parser.parse_args()

    state = load_json(args.controller_state.resolve())
    audits = state.get("audits") or []
    if not audits:
        raise RuntimeError("controller state has no completed audit")
    result = build_role_aware_prethird_feasibility_gate(
        audits[-1],
        args.formal_state.resolve(),
    )
    if result is None:
        raise RuntimeError("latest audit is not a clean two-observation boundary")
    print(
        json.dumps(
            {
                "contract": result["contract"],
                "passed": result["passed"],
                "feasible_ranks": result["feasible_ranks"],
                "candidate_bounds": result["candidate_bounds"],
                "probe_upper_bounds_affect_gate": result[
                    "probe_upper_bounds_affect_gate"
                ],
                "physical_workload_calls_added": result[
                    "physical_workload_calls_added"
                ],
                "formal_state_mutated": result["formal_state_mutated"],
                "manual_candidate_injection": result[
                    "manual_candidate_injection"
                ],
            },
            indent=2,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
