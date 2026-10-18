from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "handover_tpch_v33_to_v34_after_cycle14.py"


def _load_module() -> Any:
    specification = importlib.util.spec_from_file_location(
        "tpch_v34_handover_boundary", MODULE_PATH
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _state(status: str) -> dict[str, Any]:
    return {
        "schema_version": "tpch_fresh10_target10_loop_v33",
        "controller_version": 33,
        "status": status,
        "accepted_count": 0,
        "next_cycle": 15,
        "cycles": [
            {
                "cycle": 14,
                "status": "reexplore_prethird_acceptance_impossible",
                "accepted_before": 0,
                "accepted_after": 0,
                "formal_started": False,
                "formal_state_mutated": False,
            }
        ],
    }


def test_accepts_transient_and_durable_controller_statuses() -> None:
    module = _load_module()
    assert module.boundary_matches(
        _state("reexplore_prethird_acceptance_impossible"), 14, 0
    )
    assert module.boundary_matches(_state("preparing_cycle"), 14, 0)


def test_rejects_a_partial_or_advanced_cycle() -> None:
    module = _load_module()
    partial = _state("preparing_cycle")
    partial["cycles"][-1]["formal_started"] = True
    assert not module.boundary_matches(partial, 14, 0)

    advanced = _state("preparing_cycle")
    advanced["next_cycle"] = 16
    assert not module.boundary_matches(advanced, 14, 0)


if __name__ == "__main__":
    test_accepts_transient_and_durable_controller_statuses()
    test_rejects_a_partial_or_advanced_cycle()
    print("TPC-H v34 handover boundary regression passed", flush=True)
