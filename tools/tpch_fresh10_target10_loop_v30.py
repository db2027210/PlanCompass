#!/usr/bin/env python3
"""Resume TPC-H with post-robust resealing and explicit v28 dependency binding.

The controller is a fail-closed wrapper around the sealed v27 state machine.
It resumes only an exact state SHA at an exact cycle boundary, keeps v28's
per-cycle EXPLAIN expansion, and inserts a deterministic zero-call reseal after
the third diagnostic observation and before the formal-entry gate.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple


V27_PATH = Path("/tmp/tpch_fresh10_target10_loop_v27.py")
V27_SHA256 = "d8e1672b4aceea5065f612170d57c1b16917a1dce9bf039fb3af274be4492589"
V28_PATH = Path("/tmp/tpch_fresh10_target10_loop_v28.py")
V28_SHA256 = "c30eeb216998782c0f4d7d0e66dd5ab05458b3dcaad16b6a08167eea5b6a2096"
V26_PATH = Path("/tmp/tpch_fresh10_target10_loop_v26.py")
V26_SHA256 = "73cfcdd2eb60d199c5b2650eb51f2b19bd4b4d845b7f6fcd9714ef2c48757976"
RESEALER = Path("/tmp/build_tpch_three_observation_postrobust_reseal_v1.py")
RESEALER_SHA256 = "bfebe552e86aec4fd55e75296e64a6d8b45f8ebdee4cebc87832fb44b453d47c"


def import_module(name: str, path: Path) -> Any:
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot load {name}: {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def validate_resealed_summary(v26: Any, selection: Path, run_root: Path) -> None:
    summary_path = run_root / "diagnostic_summary.json"
    state_path = run_root / "diagnostic_state.json"
    summary = v26.load(summary_path)
    state = v26.load(state_path)
    successful = summary.get("successful") or []
    if not (
        summary.get("schema_version")
        == "tpch_postrobust_resealed_diagnostic_summary_v1"
        and summary.get("diagnostic_only") is True
        and int(summary.get("physical_call_count", -1)) == 5
        and int(summary.get("valid_replay_count", -1)) == 5
        and int(summary.get("technical_failure_count", -1)) == 0
        and summary.get("baseline_restored") is True
        and summary.get("accepted_update_applied") is False
        and summary.get("incumbent_update_applied") is False
        and summary.get("range_update_applied") is False
        and summary.get("gradient_update_applied") is False
        and summary.get("selection_sha256") == v26.sha256(selection)
        and len(successful) == 5
        and {int(item["rank"]) for item in successful} == {1, 2, 3, 4, 5}
        and (summary.get("postrobust_reseal") or {}).get("new_physical_calls") == 0
        and (summary.get("postrobust_reseal") or {}).get("state_neutral") is True
        and state.get("schema_version")
        == "tpch_postrobust_resealed_diagnostic_state_v1"
        and state.get("selection_sha256") == v26.sha256(selection)
        and state.get("status") == "complete"
        and state.get("diagnostic_only") is True
        and int(state.get("physical_call_count", -1)) == 5
        and int(state.get("valid_replay_count", -1)) == 5
        and int(state.get("technical_failure_count", -1)) == 0
        and state.get("baseline_restored") is True
        and (state.get("postrobust_reseal") or {}).get("new_physical_calls") == 0
        and (state.get("postrobust_reseal") or {}).get("state_neutral") is True
    ):
        raise RuntimeError("invalid post-robust resealed diagnostic summary")


def install_algorithm_hooks(
    v26: Any,
    v27: Any,
    v28: Any,
    common: Any,
) -> None:
    original_build_probe = v26.build_probe
    original_analyze_gate = v26.analyze_gate
    original_promote_formal = v26.promote_formal
    resealed: Dict[str, Tuple[Path, Path]] = {}

    def translated(
        selection: Path,
        run_root: Path,
    ) -> Tuple[Path, Path]:
        return resealed.get(str(selection.resolve()), (selection, run_root))

    def build_probe_with_dynamic_expansion(
        common_module: Any,
        cycle_root: Path,
        previous_selection: Path,
        previous_run: Path,
        resume_state: Path,
        accepted_count: int,
        environment: Mapping[str, str],
    ) -> Path:
        effective_selection, effective_run = translated(
            previous_selection,
            previous_run,
        )
        expansion_path = v28.dynamic_expansion(
            v26,
            common_module,
            cycle_root,
            effective_selection,
            environment,
        )
        v26.EXPANSION = expansion_path
        return original_build_probe(
            common_module,
            cycle_root,
            effective_selection,
            effective_run,
            resume_state,
            accepted_count,
            environment,
        )

    def analyze_gate_after_postrobust_reseal(
        common_module: Any,
        cycle_root: Path,
        selection: Path,
        validation_run: Path,
        environment: Mapping[str, str],
    ) -> Path:
        output_root = cycle_root / "postrobust_reseal_v30"
        output_root.mkdir(mode=0o700)
        first_root = output_root / "a"
        second_root = output_root / "b"
        first_root.mkdir(mode=0o700)
        second_root.mkdir(mode=0o700)
        first_selection = first_root / "selection.json"
        second_selection = second_root / "selection.json"
        first_summary = first_root / "diagnostic_summary.json"
        second_summary = second_root / "diagnostic_summary.json"
        first_state = first_root / "diagnostic_state.json"
        second_state = second_root / "diagnostic_state.json"
        source_summary = validation_run / "diagnostic_summary.json"
        base = [
            str(v26.PYTHON),
            "-u",
            str(RESEALER),
            "--selection",
            str(selection),
            "--third-observation-summary",
            str(source_summary),
            "--maximum-trusted-score-span",
            "0.01",
            "--adaptive-score-span-cap",
            "0.05",
            "--maximum-trusted-resource-factor",
            "1.5",
        ]
        v26.run(
            common_module,
            [
                *base,
                "--output",
                str(first_selection),
                "--output-summary",
                str(first_summary),
                "--output-state",
                str(first_state),
            ],
            output_root / "reseal_a.log",
            environment,
        )
        v26.run(
            common_module,
            [
                *base,
                "--output",
                str(second_selection),
                "--output-summary",
                str(second_summary),
                "--output-state",
                str(second_state),
            ],
            output_root / "reseal_b.log",
            environment,
        )
        if (
            first_selection.read_bytes() != second_selection.read_bytes()
            or first_summary.read_bytes() != second_summary.read_bytes()
            or first_state.read_bytes() != second_state.read_bytes()
        ):
            raise RuntimeError("post-robust reseal is not deterministic")
        common_module.validate_selection(
            v26.load(first_selection),
            ["trusted"] * 3 + ["probe"] * 2,
        )
        validate_resealed_summary(v26, first_selection, first_root)
        analysis_path = original_analyze_gate(
            common_module,
            cycle_root,
            first_selection,
            first_root,
            environment,
        )
        analysis = v26.load(analysis_path)
        analysis["postrobust_reseal"] = {
            "contract": "tpch_three_observation_postrobust_reseal_v1",
            "source_selection": str(selection),
            "source_selection_sha256": v26.sha256(selection),
            "source_summary": str(source_summary),
            "source_summary_sha256": v26.sha256(source_summary),
            "resealed_selection": str(first_selection),
            "resealed_selection_sha256": v26.sha256(first_selection),
            "resealed_summary": str(first_summary),
            "resealed_summary_sha256": v26.sha256(first_summary),
            "resealed_state": str(first_state),
            "resealed_state_sha256": v26.sha256(first_state),
            "deterministic_double_generation": True,
            "physical_workload_calls": 0,
            "formal_state_mutated": False,
        }
        v27.atomic_json(analysis_path, analysis)
        resealed[str(selection.resolve())] = (first_selection, first_root)
        return analysis_path

    def promote_resealed_formal(
        common_module: Any,
        cycle_root: Path,
        selection: Path,
        validation_run: Path,
        analysis_path: Path,
        accepted: int,
        config: Path,
    ) -> tuple[Path, Path, Path]:
        effective_selection, effective_run = translated(selection, validation_run)
        return original_promote_formal(
            common_module,
            cycle_root,
            effective_selection,
            effective_run,
            analysis_path,
            accepted,
            config,
        )

    v26.build_probe = build_probe_with_dynamic_expansion
    v26.analyze_gate = analyze_gate_after_postrobust_reseal
    v26.promote_formal = promote_resealed_formal


def recover_boundary(
    v26: Any,
    v27: Any,
    v28: Any,
    common: Any,
    expected_state_sha256: str,
    expected_next_cycle: int,
) -> tuple[Dict[str, Any], int, Path, Path, Path, int]:
    for path, expected in (
        (V27_PATH, V27_SHA256),
        (V28_PATH, V28_SHA256),
        (V26_PATH, V26_SHA256),
        (RESEALER, RESEALER_SHA256),
    ):
        v26.verify(path, expected)

    state_path = v26.LOOP_ROOT / "controller_state.json"
    if v26.sha256(state_path) != expected_state_sha256:
        raise RuntimeError("TPC-H v30 recovery state SHA mismatch")
    state = v26.load(state_path)
    accepted = v26.accepted_counter()
    cycles = state.get("cycles") or []
    last_cycle = cycles[-1] if cycles else {}
    cycle_number = expected_next_cycle - 1
    cycle_root = v26.LOOP_ROOT / f"cycle_{cycle_number:03d}"
    next_cycle_root = v26.LOOP_ROOT / f"cycle_{expected_next_cycle:03d}"
    resume_state = Path(str(state.get("resume_state") or ""))
    if next_cycle_root.exists():
        # v29 imported v28.dynamic_expansion without binding v28's module-level
        # ``v27`` dependency.  It failed after deterministic A/B expansion and
        # before creating a diagnostic runner.  Quarantine only this exact
        # zero-call footprint; anything else fails closed.
        expected_relative_files = {
            "config.a.json",
            "config.b.json",
            "dynamic_expansion_v28/expand_a.log",
            "dynamic_expansion_v28/expand_b.log",
            "dynamic_expansion_v28/expanded_candidates.a.json",
            "dynamic_expansion_v28/expanded_candidates.b.json",
        }
        observed_relative_files = {
            str(path.relative_to(next_cycle_root))
            for path in next_cycle_root.rglob("*")
            if path.is_file()
        }
        if (
            state.get("schema_version")
            != "tpch_fresh10_target10_loop_v29"
            or int(state.get("controller_version", -1)) != 29
            or (state.get("v29_recovery") or {}).get(
                "physical_workload_calls"
            )
            != 0
            or observed_relative_files != expected_relative_files
            or (next_cycle_root / "config.a.json").read_bytes()
            != (next_cycle_root / "config.b.json").read_bytes()
            or (
                next_cycle_root
                / "dynamic_expansion_v28/expanded_candidates.a.json"
            ).read_bytes()
            != (
                next_cycle_root
                / "dynamic_expansion_v28/expanded_candidates.b.json"
            ).read_bytes()
        ):
            raise RuntimeError(
                "TPC-H v30 found a non-zero-call or unexpected partial cycle"
            )
        quarantine = v26.LOOP_ROOT / (
            f"quarantine_cycle_{expected_next_cycle:03d}_v29_zero_call_"
            "dependency_binding_failure"
        )
        if quarantine.exists():
            raise RuntimeError("TPC-H v30 zero-call quarantine already exists")
        next_cycle_root.replace(quarantine)
        v27.atomic_json(
            quarantine / "v29_zero_call_quarantine_audit.json",
            {
                "contract": (
                    "tpch_v29_zero_call_dependency_binding_failure_"
                    "quarantine_v1"
                ),
                "source_state_sha256": expected_state_sha256,
                "next_cycle": expected_next_cycle,
                "observed_files": sorted(observed_relative_files),
                "deterministic_config_ab": True,
                "deterministic_expansion_ab": True,
                "physical_workload_calls": 0,
                "valid_replays": 0,
                "technical_failures": 0,
                "formal_state_mutated": False,
            },
        )

    if not (
        state.get("status") != "target_reached"
        and int(state.get("accepted_count", -1)) == accepted
        and accepted < v26.TARGET
        and int(state.get("next_cycle", -1)) == expected_next_cycle
        and int(last_cycle.get("cycle", -1)) == cycle_number
        and int(last_cycle.get("accepted_after", -1)) == accepted
        and resume_state.is_file()
        and cycle_root.is_dir()
        and not next_cycle_root.exists()
    ):
        raise RuntimeError("TPC-H v30 recovery boundary mismatch")

    entry_gate_path = cycle_root / "entry_gate.json"
    previous_selection = cycle_root / "robust_top5.a.json"
    previous_run = cycle_root / "diagnostic_robust_repeat"
    if entry_gate_path.is_file():
        postrobust = (v26.load(entry_gate_path).get("postrobust_reseal") or {})
        if postrobust:
            previous_selection = Path(str(postrobust["resealed_selection"]))
            previous_run = Path(str(postrobust["resealed_summary"])).parent
            v26.verify(
                previous_selection,
                str(postrobust["resealed_selection_sha256"]),
            )
            v26.verify(
                previous_run / "diagnostic_summary.json",
                str(postrobust["resealed_summary_sha256"]),
            )
            v26.verify(
                previous_run / "diagnostic_state.json",
                str(postrobust["resealed_state_sha256"]),
            )
            validate_resealed_summary(v26, previous_selection, previous_run)
    if not previous_selection.is_file():
        raise RuntimeError("TPC-H v30 previous robust selection is missing")
    v27.clean_diagnostic(v26, common, previous_run, accepted)

    backup = v26.LOOP_ROOT / (
        f"controller_state.pre_v30_cycle{expected_next_cycle:03d}.json"
    )
    if backup.exists():
        raise RuntimeError("TPC-H v30 recovery backup already exists")
    v27.atomic_json(backup, state)
    if v26.sha256(backup) != expected_state_sha256:
        raise RuntimeError("TPC-H v30 recovery backup SHA mismatch")

    recovered = copy.deepcopy(state)
    recovered["schema_version"] = "tpch_fresh10_target10_loop_v30"
    recovered["controller_version"] = 30
    recovered["status"] = "resumed_with_three_observation_postrobust_reseal"
    recovered["next_cycle"] = expected_next_cycle
    recovered["v30_recovery"] = {
        "contract": "exact_cycle_boundary_state_sha_v1",
        "source_state_sha256": expected_state_sha256,
        "next_cycle": expected_next_cycle,
        "accepted_count": accepted,
        "physical_workload_calls": 0,
        "formal_state_mutated": False,
    }
    v27.atomic_json(state_path, recovered)
    install_algorithm_hooks(v26, v27, v28, common)
    return (
        recovered,
        accepted,
        resume_state,
        previous_selection,
        previous_run,
        expected_next_cycle,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-state-sha256", required=True)
    parser.add_argument("--expected-next-cycle", type=int, required=True)
    args = parser.parse_args()
    if len(args.expected_state_sha256) != 64 or args.expected_next_cycle <= 0:
        raise ValueError("invalid recovery boundary arguments")

    v27 = import_module("tpch_target10_v27_for_v30", V27_PATH)
    v28 = import_module("tpch_target10_v28_for_v30", V28_PATH)
    # v28.dynamic_expansion was written with a module-global v27 reference.
    # Importing it as a library does not execute v28.main(), so bind the exact
    # verified module explicitly before installing the hook.
    v28.v27 = v27

    def recover(v26: Any, common: Any) -> tuple[Dict[str, Any], int, Path, Path, Path, int]:
        return recover_boundary(
            v26,
            v27,
            v28,
            common,
            args.expected_state_sha256,
            args.expected_next_cycle,
        )

    v27.recover_cycle004 = recover
    return int(v27.main())


if __name__ == "__main__":
    raise SystemExit(main())
