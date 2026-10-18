#!/usr/bin/env python3
"""Recover TPC-H at a clean pre-third boundary with hierarchical probes.

The live v33 controller is stopped only after it has atomically recorded a
complete ``reexplore_prethird_acceptance_impossible`` cycle and before the next
cycle launches a workload.  Any partially generated next-cycle files are
offline-only and moved to a timestamp-free SHA-bound quarantine directory.
The formal state and accepted counter remain unchanged.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any, Dict, Mapping


V33_PATH = Path("/tmp/tpch_fresh10_target10_loop_v33_prethird_bound.py")
V33_SHA256 = "e5c18e3ebe7b8152e12ada134b86f50b32edc610f86346030c37aba5ebd688a0"
V32_V6_PATH = Path("/tmp/tpch_fresh10_target10_loop_v32_v6_hierarchical.py")
V32_V6_SHA256 = "825004bd315ff2f6756707afb141ddae755c92baec1108291d4d5e57ed9993bf"
BUILDER_V6_PATH = Path(
    "/tmp/build_tpch_autonomous_successor_top5_v6_hierarchical_probe.py"
)
BUILDER_V6_SHA256 = (
    "33a36e16056a47bec9d6fcbedf26d7ca31bf076940ee0775a68582121f729790"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def import_module(name: str, path: Path) -> Any:
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot load {name}: {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def offline_inventory(root: Path) -> list[Dict[str, Any]]:
    return [
        {
            "path": str(path.relative_to(root)),
            "size": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def recover_boundary_v34(
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
        raise RuntimeError("TPC-H v34 recovery state SHA mismatch")
    state = v26.load(state_path)
    accepted = v26.accepted_counter()
    cycles = state.get("cycles") or []
    last_cycle = cycles[-1] if cycles else {}
    completed_cycle = expected_next_cycle - 1
    completed_root = v26.LOOP_ROOT / f"cycle_{completed_cycle:03d}"
    probe = completed_root / "probe_top5.a.json"
    probe_run = completed_root / "diagnostic_probe"
    repeat = completed_root / "exact_repeat_top5.a.json"
    repeat_run = completed_root / "diagnostic_exact_repeat"
    gate_path = completed_root / "prethird_acceptance_feasibility.json"
    resume_state = Path(str(state.get("resume_state") or ""))

    if not (
        state.get("schema_version") == "tpch_fresh10_target10_loop_v33"
        and int(state.get("controller_version", -1)) == 33
        # The durable boundary is represented by the final cycle audit.  v33
        # may already have advanced the controller-level status to
        # ``preparing_cycle`` before the handover watcher observes it.
        and state.get("status")
        in {"reexplore_prethird_acceptance_impossible", "preparing_cycle"}
        and int(state.get("accepted_count", -1)) == accepted
        and accepted < v26.TARGET
        and int(state.get("next_cycle", -1)) == expected_next_cycle
        and int(last_cycle.get("cycle", -1)) == completed_cycle
        and last_cycle.get("status")
        == "reexplore_prethird_acceptance_impossible"
        and int(last_cycle.get("accepted_before", -1)) == accepted
        and int(last_cycle.get("accepted_after", -1)) == accepted
        and last_cycle.get("formal_started") is False
        and last_cycle.get("formal_state_mutated") is False
        and resume_state.is_file()
        and v26.load(resume_state).get("status") == "complete"
        and v26.load(resume_state).get("baseline_restored") is True
        and int(v26.load(resume_state).get("accepted_count", -1)) == accepted
        and gate_path.is_file()
    ):
        raise RuntimeError("TPC-H v34 clean pre-third boundary mismatch")
    gate = v26.load(gate_path)
    if not (
        gate.get("contract")
        == "tpch_bounded_four_observation_median_prethird_v1"
        and gate.get("passed") is False
        and int(gate.get("physical_workload_calls", -1)) == 0
        and gate.get("formal_state_mutated") is False
        and gate.get("manual_candidate_injection") is False
    ):
        raise RuntimeError("TPC-H v34 pre-third gate mismatch")
    v27.clean_diagnostic(v26, common, probe_run, accepted)
    v27.clean_diagnostic(v26, common, repeat_run, accepted)
    forbidden_completed = (
        completed_root / "diagnostic_robust_repeat",
        completed_root / "formal_package",
        completed_root / "formal_run",
    )
    if any(path.exists() for path in forbidden_completed):
        raise RuntimeError("TPC-H v34 completed cycle has forbidden work")

    partial = v26.LOOP_ROOT / f"cycle_{expected_next_cycle:03d}"
    inventory: list[Dict[str, Any]] = []
    quarantine: Path | None = None
    if partial.exists():
        forbidden_partial = (
            partial / "diagnostic_probe",
            partial / "diagnostic_exact_repeat",
            partial / "diagnostic_robust_repeat",
            partial / "formal_package",
            partial / "formal_run",
        )
        if any(path.exists() for path in forbidden_partial):
            raise RuntimeError("TPC-H v34 partial cycle crossed workload boundary")
        inventory = offline_inventory(partial)
        quarantine = v26.LOOP_ROOT / (
            f"quarantine_cycle_{expected_next_cycle:03d}_v34_offline_"
            f"{expected_state_sha256[:12]}"
        )
        if quarantine.exists():
            raise RuntimeError("TPC-H v34 quarantine already exists")
        partial.rename(quarantine)

    backup = state_path.with_name(
        f"controller_state.pre_v34.{expected_state_sha256[:12]}.json"
    )
    if backup.exists():
        raise RuntimeError("TPC-H v34 recovery backup already exists")
    v27.atomic_json(backup, state)
    if v26.sha256(backup) != expected_state_sha256:
        raise RuntimeError("TPC-H v34 recovery backup SHA mismatch")

    recovered = copy.deepcopy(state)
    recovered["controller_version"] = 34
    recovered["status"] = "resumed_with_hierarchical_probe_prior"
    recovered.setdefault("handover_audits", []).append(
        {
            "contract": "tpch_v33_to_v34_clean_prethird_boundary_v1",
            "completed_cycle": completed_cycle,
            "next_cycle": expected_next_cycle,
            "controller_state_sha256": expected_state_sha256,
            "resume_state_sha256": v26.sha256(resume_state),
            "prethird_gate_sha256": v26.sha256(gate_path),
            "builder_v6_sha256": BUILDER_V6_SHA256,
            "partial_offline_inventory": inventory,
            "partial_quarantine": str(quarantine) if quarantine else None,
            "physical_workload_calls_added": 0,
            "formal_state_mutated": False,
            "manual_candidate_injection": False,
            "thresholds_relaxed": False,
        }
    )
    v27.atomic_json(state_path, recovered)
    v32.install_algorithm_hooks(v26, v27, v30, common)
    v33 = import_module("tpch_target10_v33_hook_for_v34", V33_PATH)
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
        (V32_V6_PATH, V32_V6_SHA256),
        (BUILDER_V6_PATH, BUILDER_V6_SHA256),
    ):
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"TPC-H v34 dependency SHA mismatch: {path}")
    v33 = import_module("tpch_target10_v33_for_v34", V33_PATH)
    v33.V32_PATH = V32_V6_PATH
    v33.V32_SHA256 = V32_V6_SHA256
    v33.recover_boundary = recover_boundary_v34
    return int(v33.main())


if __name__ == "__main__":
    raise SystemExit(main())
