#!/usr/bin/env python3
"""Run frozen TPC-H v36 from accepted=0 with plan-preserving exploration.

The previous campaign is immutable training/audit input only.  No accepted,
incumbent, range, or gradient state is inherited.  The formal state starts
from the measured default-parameter baseline with the full configured ranges.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Dict


B = Path(
    "/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/"
    "pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/"
    "experience_sgd/attempt48_global_replay_cap5_20260726"
)
OLD_ROOT = B / "tpch_final_algorithm_fresh10_v58_20260731T2340"
NEW_ROOT = B / "tpch_final_algorithm_fresh0_v36_compensated_20260803"
LOOP_ROOT = NEW_ROOT / "target10_loop_v36"
ACCEPTED_COUNTER = NEW_ROOT / "formal_search_accepted_counter.txt"

V26 = Path("/tmp/tpch_fresh0_target10_loop_v36_base.py")
V27 = Path("/tmp/tpch_fresh10_target10_loop_v27.py")
V30 = Path("/tmp/tpch_fresh10_target10_loop_v30.py")
V32 = Path("/tmp/tpch_fresh10_target10_loop_v32_v6_hierarchical.py")
V33 = Path("/tmp/tpch_fresh10_target10_loop_v33_prethird_bound.py")
BUILDER = Path(
    "/tmp/build_tpch_autonomous_successor_top5_v7_incumbent_plan_probe.py"
)
EXPANDER = Path(
    "/tmp/tpch_exact_regime_neighbor_expand_v4_compensated_plan_preserving.py"
)

BASELINE_STATE = (
    OLD_ROOT
    / "target10_loop_v26/cycle_011/formal_run/formal_state.json"
)
SEED_SELECTION = OLD_ROOT / "target10_loop_v26/cycle_034/exact_repeat_top5.a.json"
SEED_RUN = OLD_ROOT / "target10_loop_v26/cycle_034/diagnostic_exact_repeat"

EXPECTED = {
    V26: "34107e91b4bcfd0b2fe8d2911a0855e1512fc881665f4db3c3cb67618bd055fb",
    V27: "d8e1672b4aceea5065f612170d57c1b16917a1dce9bf039fb3af274be4492589",
    V30: "384f2ce8f944525711d706c1e85493bfe61ed075d20d6794f61caed3dd67d7f8",
    V32: "54ac22227f190bfbbc1f2889927cba9500cbe1bd6f4347287d69c3647d6e98b5",
    V33: "e5c18e3ebe7b8152e12ada134b86f50b32edc610f86346030c37aba5ebd688a0",
    BUILDER: "27c8dfdcf16f2387c469d5d133c8dc9c38154419c1ac3575a5c57260f3273b8d",
    EXPANDER: "5d8e6da67ed4207b6a5b80d268bf695d4c2a4de4d925d685bb607a7d7fd155f5",
    BASELINE_STATE: "af8b17dc77b82fb60166102d091d112eae132c4a6f086fb5a560316385e544ee",
    SEED_SELECTION: "b000e1c8d8759e281956aadd83309927ffbc1b9183871a962f95d214aebb6824",
    SEED_RUN / "diagnostic_summary.json": (
        "9a2a8d5f961203c457211f811885ff6ca3abc6ac487c374ef07110bf956a7754"
    ),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def import_module(name: str, path: Path) -> Any:
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot import {name}: {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def preflight() -> None:
    for path, expected in EXPECTED.items():
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"TPC-H v36 SHA mismatch: {path}")
    baseline = load(BASELINE_STATE)
    summary = load(SEED_RUN / "diagnostic_summary.json")
    ranges = baseline.get("range_state")
    if not (
        int(baseline.get("accepted_count", -1)) == 0
        and baseline.get("status") == "complete"
        and baseline.get("baseline_restored") is True
        and int(baseline.get("technical_failure_count", -1)) == 0
        and isinstance(ranges, dict)
        and len(ranges) == 7
        and int(summary.get("physical_call_count", -1)) == 5
        and int(summary.get("valid_replay_count", -1)) == 5
        and int(summary.get("technical_failure_count", -1)) == 0
        and summary.get("baseline_restored") is True
    ):
        raise RuntimeError("TPC-H v36 fresh0 authority is not clean")
    if LOOP_ROOT.exists() or ACCEPTED_COUNTER.exists():
        raise RuntimeError("TPC-H v36 fresh0 output already exists")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    preflight()
    if args.preflight_only:
        print("TPC-H v36 fresh0 preflight passed", flush=True)
        return 0
    v26 = import_module("tpch_v36_v26", V26)
    v27 = import_module("tpch_v36_v27", V27)
    v30 = import_module("tpch_v36_v30", V30)
    v32 = import_module("tpch_v36_v32", V32)
    v33 = import_module("tpch_v36_v33", V33)

    v26.ROOT = NEW_ROOT
    v26.LOOP_ROOT = LOOP_ROOT
    v26.ACCEPTED_COUNTER = ACCEPTED_COUNTER
    v26.INITIAL_PREVIOUS_SELECTION = SEED_SELECTION
    v26.INITIAL_PREVIOUS_RUN = SEED_RUN
    v26.MAX_CYCLES = 100
    v26.SUCCESSOR_BUILDER = BUILDER
    v26.EXPECTED["successor_builder"] = EXPECTED[BUILDER]
    v26.ROBUST_BUILDER = v32.ROBUST_BUILDER
    v26.EXPECTED["robust_builder"] = v32.ROBUST_BUILDER_SHA256

    v32.SUCCESSOR_BUILDER = BUILDER
    v32.SUCCESSOR_BUILDER_SHA256 = EXPECTED[BUILDER]
    v32.EXPANDER = EXPANDER
    v32.EXPANDER_SHA256 = EXPECTED[EXPANDER]

    def fresh_authority(_common: Any, accepted_before: int) -> tuple[int, Path, bool]:
        if accepted_before != 0:
            raise RuntimeError("TPC-H v36 accepted counter did not start at zero")
        return 0, BASELINE_STATE, True

    original_import_common = v26.import_common

    def import_common_with_v36_hooks() -> Any:
        common = original_import_common()
        v32.install_algorithm_hooks(v26, v27, v30, common)
        v33.install_prethird_hook(v26, v27)
        return common

    v26.resolve_initial_authority = fresh_authority
    v26.import_common = import_common_with_v36_hooks

    NEW_ROOT.mkdir(mode=0o700, parents=True, exist_ok=False)
    ACCEPTED_COUNTER.write_text("0\n", encoding="utf-8")
    return int(v26.main())


if __name__ == "__main__":
    raise SystemExit(main())
