#!/usr/bin/env python3
"""Resume TPC-H after fingerprint exhaustion with dense EXPLAIN expansion."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
from pathlib import Path
from typing import Any, Dict


V33_PATH = Path("/tmp/tpch_fresh10_target10_loop_v33_prethird_bound.py")
V33_SHA256 = "e5c18e3ebe7b8152e12ada134b86f50b32edc610f86346030c37aba5ebd688a0"
V32_PATH = Path("/tmp/tpch_fresh10_target10_loop_v32_v6_hierarchical.py")
V32_SHA256 = "54ac22227f190bfbbc1f2889927cba9500cbe1bd6f4347287d69c3647d6e98b5"
BUILDER_PATH = Path(
    "/tmp/build_tpch_autonomous_successor_top5_v6_hierarchical_probe.py"
)
BUILDER_SHA256 = "33a36e16056a47bec9d6fcbedf26d7ca31bf076940ee0775a68582121f729790"
EXPANDER_PATH = Path(
    "/tmp/tpch_exact_regime_neighbor_expand_v3_incumbent_ladder.py"
)
EXPANDER_SHA256 = "e6815809288c13c8a7ecd8773354d979e3eecba4a752c17756072724ec2ee25e"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def import_module(name: str, path: Path) -> Any:
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot import {name}: {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def inventory(root: Path) -> list[Dict[str, Any]]:
    return [
        {
            "path": str(path.relative_to(root)),
            "size": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def recover_boundary_v35(
    v26: Any,
    v27: Any,
    v30: Any,
    v32: Any,
    common: Any,
    expected_state_sha256: str,
    expected_next_cycle: int,
) -> tuple[Dict[str, Any], int, Path, Path, Path, int]:
    state_path = v26.LOOP_ROOT / "controller_state.json"
    if v26.sha256(state_path) != expected_state_sha256:
        raise RuntimeError("TPC-H v35 recovery state SHA mismatch")
    state = v26.load(state_path)
    accepted = v26.accepted_counter()
    cycles = state.get("cycles") or []
    last_cycle = cycles[-1] if cycles else {}
    completed_cycle = expected_next_cycle - 1
    completed_root = v26.LOOP_ROOT / f"cycle_{completed_cycle:03d}"
    repeat = completed_root / "exact_repeat_top5.a.json"
    repeat_run = completed_root / "diagnostic_exact_repeat"
    probe_run = completed_root / "diagnostic_probe"
    gate_path = completed_root / "prethird_acceptance_feasibility.json"
    resume_state = Path(str(state.get("resume_state") or ""))
    partial = v26.LOOP_ROOT / f"cycle_{expected_next_cycle:03d}"

    if not (
        state.get("schema_version") == "tpch_fresh10_target10_loop_v33"
        and int(state.get("controller_version", -1)) == 34
        and state.get("status") == "preparing_cycle"
        and int(state.get("accepted_count", -1)) == accepted == 0
        and int(state.get("historical_accepted_excluded", -1)) == 1
        and int(state.get("next_cycle", -1)) == expected_next_cycle
        and int(last_cycle.get("cycle", -1)) == completed_cycle
        and last_cycle.get("status") == "reexplore_prethird_acceptance_impossible"
        and last_cycle.get("formal_started") is False
        and last_cycle.get("formal_state_mutated") is False
        and resume_state.is_file()
        and v26.load(resume_state).get("status") == "complete"
        and v26.load(resume_state).get("baseline_restored") is True
        and int(v26.load(resume_state).get("accepted_count", -1)) == accepted
        and gate_path.is_file()
        and partial.is_dir()
    ):
        raise RuntimeError("TPC-H v35 clean cycle boundary mismatch")

    forbidden = (
        partial / "diagnostic_probe",
        partial / "diagnostic_exact_repeat",
        partial / "diagnostic_robust_repeat",
        partial / "formal_package",
        partial / "formal_run",
    )
    if any(path.exists() for path in forbidden):
        raise RuntimeError("TPC-H v35 partial cycle crossed workload boundary")
    observed = {
        str(path.relative_to(partial))
        for path in partial.rglob("*")
        if path.is_file()
    }
    expected = {
        "config.a.json",
        "config.b.json",
        "dynamic_expansion_v32/audit.json",
        "dynamic_expansion_v32/expand_a.log",
        "dynamic_expansion_v32/expand_b.log",
        "dynamic_expansion_v32/expanded_candidates.a.json",
        "dynamic_expansion_v32/expanded_candidates.b.json",
        "probe_builder_a.log",
    }
    failure_log = (partial / "probe_builder_a.log").read_text(
        encoding="utf-8", errors="replace"
    )
    if not (
        observed == expected
        and (partial / "config.a.json").read_bytes()
        == (partial / "config.b.json").read_bytes()
        and (partial / "dynamic_expansion_v32/expanded_candidates.a.json").read_bytes()
        == (partial / "dynamic_expansion_v32/expanded_candidates.b.json").read_bytes()
        and "recovery_probes=3" in failure_log
        and "cannot form either" in failure_log
    ):
        raise RuntimeError("TPC-H v35 offline failure evidence mismatch")

    v27.clean_diagnostic(v26, common, probe_run, accepted)
    v27.clean_diagnostic(v26, common, repeat_run, accepted)
    backup = state_path.with_name(
        f"controller_state.pre_v35.{expected_state_sha256[:12]}.json"
    )
    quarantine = v26.LOOP_ROOT / (
        f"quarantine_cycle_{expected_next_cycle:03d}_v35_dense_"
        f"{expected_state_sha256[:12]}"
    )
    if backup.exists() or quarantine.exists():
        raise RuntimeError("TPC-H v35 backup or quarantine already exists")
    v27.atomic_json(backup, state)
    if v26.sha256(backup) != expected_state_sha256:
        raise RuntimeError("TPC-H v35 backup SHA mismatch")
    partial.rename(quarantine)

    recovered = copy.deepcopy(state)
    recovered["controller_version"] = 35
    recovered["status"] = "resumed_with_dense_hierarchical_explain_expansion"
    recovered.setdefault("handover_audits", []).append(
        {
            "contract": "tpch_v34_to_v35_dense_explain_zero_call_v1",
            "completed_cycle": completed_cycle,
            "next_cycle": expected_next_cycle,
            "controller_state_sha256": expected_state_sha256,
            "resume_state_sha256": v26.sha256(resume_state),
            "partial_offline_inventory": inventory(quarantine),
            "partial_quarantine": str(quarantine),
            "builder_sha256": BUILDER_SHA256,
            "expander_sha256": EXPANDER_SHA256,
            "single_and_paired_coordinate_expansion": True,
            "maximum_relative_step": 0.40,
            "physical_workload_calls_added": 0,
            "formal_state_mutated": False,
            "manual_candidate_injection": False,
            "thresholds_relaxed": False,
        }
    )
    v27.atomic_json(state_path, recovered)
    v32.install_algorithm_hooks(v26, v27, v30, common)
    v33 = import_module("tpch_target10_v33_hook_for_v35", V33_PATH)
    v33.install_prethird_hook(v26, v27)
    return (
        recovered,
        accepted,
        resume_state,
        repeat,
        repeat_run,
        expected_next_cycle,
    )


def main() -> int:
    for path, expected in (
        (V33_PATH, V33_SHA256),
        (V32_PATH, V32_SHA256),
        (BUILDER_PATH, BUILDER_SHA256),
        (EXPANDER_PATH, EXPANDER_SHA256),
    ):
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"TPC-H v35 dependency SHA mismatch: {path}")
    v33 = import_module("tpch_target10_v33_for_v35", V33_PATH)
    v33.V32_PATH = V32_PATH
    v33.V32_SHA256 = V32_SHA256
    v33.recover_boundary = recover_boundary_v35
    return int(v33.main())


if __name__ == "__main__":
    raise SystemExit(main())
