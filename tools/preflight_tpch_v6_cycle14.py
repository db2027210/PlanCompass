#!/usr/bin/env python3
"""Zero-workload deterministic preflight of TPC-H v6 on cycle14 inputs."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from typing import Any


V26 = Path("/tmp/tpch_fresh10_target10_loop_v26.py")
V26_SHA = "73cfcdd2eb60d199c5b2650eb51f2b19bd4b4d845b7f6fcd9714ef2c48757976"
BUILDER = Path("/tmp/build_tpch_autonomous_successor_top5_v6_hierarchical_probe.py")
BUILDER_SHA = "33a36e16056a47bec9d6fcbedf26d7ca31bf076940ee0775a68582121f729790"
ROOT = Path(
    "/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/"
    "pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/"
    "experience_sgd/attempt48_global_replay_cap5_20260726/"
    "tpch_final_algorithm_fresh10_v58_20260731T2340"
)
LOOP = ROOT / "target10_loop_v26"
SELECTION = LOOP / "cycle_013/exact_repeat_top5.a.json"
SUMMARY = LOOP / "cycle_013/diagnostic_exact_repeat/diagnostic_summary.json"
EXPANSION = LOOP / "cycle_014/dynamic_expansion_v32/expanded_candidates.a.json"
RESUME = LOOP / "cycle_011/formal_run/formal_state.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def import_module(name: str, path: Path) -> Any:
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot load {name}: {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def main() -> int:
    if sha256(V26) != V26_SHA or sha256(BUILDER) != BUILDER_SHA:
        raise RuntimeError("preflight dependency SHA mismatch")
    v26 = import_module("tpch_v26_for_v6_preflight", V26)
    builder = import_module("tpch_builder_v6_preflight", BUILDER)
    exclusions = tuple(
        path
        for path in v26.selection_paths(v26.ROOT)
        if path.resolve() != SELECTION.resolve()
    )
    resume_before = sha256(RESUME)
    with tempfile.TemporaryDirectory(prefix="tpch_v6_cycle14_preflight_") as temporary:
        temporary_root = Path(temporary)
        first = temporary_root / "a.json"
        second = temporary_root / "b.json"
        output_a = builder.build(
            SELECTION,
            SUMMARY,
            EXPANSION,
            first,
            exclusions,
            0.002,
            RESUME,
        )
        output_b = builder.build(
            SELECTION,
            SUMMARY,
            EXPANSION,
            second,
            exclusions,
            0.002,
            RESUME,
        )
        if first.read_bytes() != second.read_bytes() or output_a != output_b:
            raise RuntimeError("v6 preflight is not deterministic")
        top5 = output_a.get("top5") or []
        roles = [str(item.get("selection_role")) for item in top5]
        if roles not in (["probe"] * 5, ["trusted"] * 3 + ["probe"] * 2):
            raise RuntimeError(f"v6 preflight roles are invalid: {roles}")
        unknown = [item for item in top5 if item.get("selection_role") == "probe"]
        if any(
            (item.get("confidence") or {}).get("exact_plan_regime") != 0.0
            or int((item.get("confidence") or {}).get("measurement_count", -1)) != 0
            or not isinstance(item.get("probe_acquisition_score"), (int, float))
            for item in unknown
        ):
            raise RuntimeError("v6 unknown probe confidence hygiene failed")
        audit = {
            "contract": "tpch_v6_cycle14_zero_workload_preflight_v1",
            "deterministic_double_generation": True,
            "selection_sha256": sha256(SELECTION),
            "summary_sha256": sha256(SUMMARY),
            "expansion_sha256": sha256(EXPANSION),
            "resume_state_sha256": resume_before,
            "additional_exclusion_count": len(exclusions),
            "roles": roles,
            "top5": [
                {
                    "rank": int(item["rank"]),
                    "selection_role": str(item["selection_role"]),
                    "candidate_score": float(item["candidate_score"]),
                    "probe_acquisition_score": item.get("probe_acquisition_score"),
                    "predicted_resource": item["predicted_resource"],
                    "three_level_fingerprint": item["three_level_fingerprint"]["id"],
                }
                for item in top5
            ],
            "physical_workload_calls": 0,
            "formal_state_mutated": False,
            "manual_candidate_injection": False,
        }
    if sha256(RESUME) != resume_before:
        raise RuntimeError("formal state changed during v6 preflight")
    print(json.dumps(audit, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
