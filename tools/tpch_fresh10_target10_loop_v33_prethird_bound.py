#!/usr/bin/env python3
"""Resume TPC-H with a bounded pre-third-observation feasibility gate.

The formal acceptance statistic is the median after a third diagnostic and a
fourth formal observation.  The utility score is bounded above by 5.0.  After
two clean exact-point observations, this controller skips the third diagnostic
when even two future scores of 5.0 cannot raise any candidate's four-point
median above the formal incumbent.  The transition is zero-workload and
state-neutral; otherwise the sealed v32 three-observation path is unchanged.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import math
from pathlib import Path
from typing import Any, Dict, Mapping


V26_PATH = Path("/tmp/tpch_fresh10_target10_loop_v26.py")
V26_SHA256 = "73cfcdd2eb60d199c5b2650eb51f2b19bd4b4d845b7f6fcd9714ef2c48757976"
V27_PATH = Path("/tmp/tpch_fresh10_target10_loop_v27.py")
V27_SHA256 = "d8e1672b4aceea5065f612170d57c1b16917a1dce9bf039fb3af274be4492589"
V30_PATH = Path("/tmp/tpch_fresh10_target10_loop_v30.py")
V30_SHA256 = "384f2ce8f944525711d706c1e85493bfe61ed075d20d6794f61caed3dd67d7f8"
V32_PATH = Path("/tmp/tpch_fresh10_target10_loop_v32_feasibility.py")
V32_SHA256 = "f5276a04d2a16dbf3c37c37c1b79d3d93842633a480f488a2c7c50c30c7b85d7"
MAXIMUM_UTILITY_SCORE = 5.0


def import_module(name: str, path: Path) -> Any:
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot load {name}: {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def finite(value: Any, label: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise RuntimeError(f"{label} must be finite")
    return result


def fingerprint(item: Mapping[str, Any]) -> str:
    value = item.get("three_level_fingerprint") or {}
    identifier = str(value.get("id") if isinstance(value, Mapping) else value)
    if not identifier.startswith("three-level-sha256:"):
        raise RuntimeError("candidate/result lacks a three-level fingerprint")
    return identifier


def prethird_feasibility(
    v26: Any,
    repeat_selection: Path,
    repeat_run: Path,
    resume_state: Path,
) -> Dict[str, Any]:
    selection = v26.load(repeat_selection)
    summary = v26.load(repeat_run / "diagnostic_summary.json")
    state = v26.load(resume_state)
    candidates = selection.get("top5") or []
    successful = summary.get("successful") or []
    if not (
        len(candidates) == v26.CAP
        and len(successful) == v26.CAP
        and int(summary.get("physical_call_count", -1)) == v26.CAP
        and int(summary.get("valid_replay_count", -1)) == v26.CAP
        and int(summary.get("technical_failure_count", -1)) == 0
        and summary.get("baseline_restored") is True
        and summary.get("accepted_update_applied") is False
        and summary.get("incumbent_update_applied") is False
        and summary.get("range_update_applied") is False
        and summary.get("gradient_update_applied") is False
        and summary.get("selection_sha256") == v26.sha256(repeat_selection)
        and state.get("status") == "complete"
        and state.get("baseline_restored") is True
    ):
        raise RuntimeError("pre-third gate requires a clean exact repeat")
    by_rank = {int(item["rank"]): item for item in successful}
    if set(by_rank) != set(range(1, v26.CAP + 1)):
        raise RuntimeError("pre-third repeat results do not cover ranks 1..5")

    incumbent = finite(state["incumbent_score"], "incumbent_score")
    rows = []
    complete_two_observation_binding = True
    for candidate in candidates:
        rank = int(candidate["rank"])
        result = by_rank[rank]
        confidence = candidate.get("confidence") or {}
        measurement_count = int(confidence.get("measurement_count") or 0)
        if (
            measurement_count != 1
            or fingerprint(candidate) != fingerprint(result)
            or result.get("technical_failure") is not False
            or result.get("valid_replay") is not True
        ):
            complete_two_observation_binding = False
            continue
        first_score = finite(
            confidence.get("score_median", candidate.get("candidate_score")),
            "first_score",
        )
        second_score = finite(result["actual_score"], "second_score")
        # With existing a<=b and two future observations bounded by M, the
        # largest possible median of [a,b,M,M] is (b+M)/2.
        upper_bound = (max(first_score, second_score) + MAXIMUM_UTILITY_SCORE) / 2.0
        rows.append(
            {
                "rank": rank,
                "selection_role": str(candidate.get("selection_role")),
                "first_score": first_score,
                "second_score": second_score,
                "maximum_four_observation_median": upper_bound,
                "can_still_beat_incumbent": upper_bound > incumbent,
            }
        )
    # Unknown or accumulated histories take the conservative path and retain
    # the third observation.  The gate skips only a fully proven five-point
    # two-observation binding.
    passed = not complete_two_observation_binding or any(
        row["can_still_beat_incumbent"] for row in rows
    )
    return {
        "contract": "tpch_bounded_four_observation_median_prethird_v1",
        "passed": passed,
        "complete_two_observation_binding": complete_two_observation_binding,
        "incumbent_score": incumbent,
        "maximum_utility_score": MAXIMUM_UTILITY_SCORE,
        "candidate_bounds": rows,
        "feasible_ranks": [
            row["rank"] for row in rows if row["can_still_beat_incumbent"]
        ],
        "physical_workload_calls": 0,
        "formal_state_mutated": False,
        "manual_candidate_injection": False,
        "thresholds_relaxed": False,
        "selection_sha256": v26.sha256(repeat_selection),
        "repeat_summary_sha256": v26.sha256(
            repeat_run / "diagnostic_summary.json"
        ),
        "resume_state_sha256": v26.sha256(resume_state),
    }


def install_prethird_hook(v26: Any, v27: Any) -> None:
    original_build_robust_repeat = v26.build_robust_repeat
    original_insufficient = v27.insufficient_stable_failure
    original_reexploration_audit = v27.reexploration_audit

    def build_robust_repeat_with_bound(
        common: Any,
        cycle_root: Path,
        probe: Path,
        probe_run: Path,
        repeat: Path,
        repeat_run: Path,
        accepted: int,
        environment: Mapping[str, str],
    ) -> Path:
        _ = probe
        _ = probe_run
        _ = accepted
        gate = prethird_feasibility(
            v26,
            repeat,
            repeat_run,
            Path(str(v26.load(v26.LOOP_ROOT / "controller_state.json")["resume_state"])),
        )
        gate_path = cycle_root / "prethird_acceptance_feasibility.json"
        v27.atomic_json(gate_path, gate)
        if gate["passed"] is not True:
            raise RuntimeError(
                "bounded four-observation median cannot beat incumbent"
            )
        return original_build_robust_repeat(
            common,
            cycle_root,
            probe,
            probe_run,
            repeat,
            repeat_run,
            accepted,
            environment,
        )

    def prethird_or_insufficient(cycle_root: Path) -> bool:
        gate_path = cycle_root / "prethird_acceptance_feasibility.json"
        if gate_path.is_file():
            gate = v26.load(gate_path)
            forbidden = (
                cycle_root / "robust_top5.a.json",
                cycle_root / "robust_top5.b.json",
                cycle_root / "diagnostic_robust_repeat",
                cycle_root / "formal_package",
                cycle_root / "formal_run",
            )
            if (
                gate.get("contract")
                == "tpch_bounded_four_observation_median_prethird_v1"
                and gate.get("passed") is False
                and int(gate.get("physical_workload_calls", -1)) == 0
                and gate.get("formal_state_mutated") is False
                and not any(path.exists() for path in forbidden)
            ):
                return True
        return original_insufficient(cycle_root)

    def prethird_or_reexploration_audit(
        module: Any,
        cycle: int,
        accepted: int,
        cycle_root: Path,
        probe: Path,
        repeat: Path,
    ) -> Dict[str, Any]:
        gate_path = cycle_root / "prethird_acceptance_feasibility.json"
        if gate_path.is_file() and module.load(gate_path).get("passed") is False:
            gate = module.load(gate_path)
            return {
                "cycle": cycle,
                "status": "reexplore_prethird_acceptance_impossible",
                "accepted_before": accepted,
                "accepted_after": accepted,
                "probe_selection_sha256": module.sha256(probe),
                "repeat_selection_sha256": module.sha256(repeat),
                "probe_physical_calls": module.CAP,
                "probe_valid_replays": module.CAP,
                "repeat_physical_calls": module.CAP,
                "repeat_valid_replays": module.CAP,
                "technical_failures": 0,
                "prethird_gate_sha256": module.sha256(gate_path),
                "prethird_maximum_utility_score": gate[
                    "maximum_utility_score"
                ],
                "robust_third_physical_calls": 0,
                "formal_started": False,
                "formal_state_mutated": False,
                "manual_candidate_injection": False,
                "thresholds_relaxed": False,
            }
        return original_reexploration_audit(
            module,
            cycle,
            accepted,
            cycle_root,
            probe,
            repeat,
        )

    v26.build_robust_repeat = build_robust_repeat_with_bound
    v27.insufficient_stable_failure = prethird_or_insufficient
    v27.reexploration_audit = prethird_or_reexploration_audit


def recover_boundary(
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
        raise RuntimeError("TPC-H v33 recovery state SHA mismatch")
    state = v26.load(state_path)
    accepted = v26.accepted_counter()
    cycles = state.get("cycles") or []
    last_cycle = cycles[-1] if cycles else {}
    resume_state = Path(str(state.get("resume_state") or ""))
    partial = v26.LOOP_ROOT / f"cycle_{expected_next_cycle:03d}"
    expected_files = {
        "config.a.json",
        "config.b.json",
        "dynamic_expansion_v32/audit.json",
        "dynamic_expansion_v32/expand_a.log",
        "dynamic_expansion_v32/expand_b.log",
        "dynamic_expansion_v32/expanded_candidates.a.json",
        "dynamic_expansion_v32/expanded_candidates.b.json",
        "probe_builder_a.log",
    }
    observed_files = {
        str(path.relative_to(partial))
        for path in partial.rglob("*")
        if path.is_file()
    }
    failure_log = (partial / "probe_builder_a.log").read_text(
        encoding="utf-8",
        errors="replace",
    )
    if not (
        state.get("schema_version") == "tpch_fresh10_target10_loop_v32"
        and int(state.get("controller_version", -1)) == 32
        and state.get("status") == "preparing_cycle"
        and int(state.get("accepted_count", -1)) == accepted
        and accepted < v26.TARGET
        and int(state.get("next_cycle", -1)) == expected_next_cycle
        and int(last_cycle.get("cycle", -1)) == expected_next_cycle - 1
        and int(last_cycle.get("accepted_after", -1)) == accepted
        and resume_state.is_file()
        and observed_files == expected_files
        and (partial / "config.a.json").read_bytes()
        == (partial / "config.b.json").read_bytes()
        and (
            partial / "dynamic_expansion_v32/expanded_candidates.a.json"
        ).read_bytes()
        == (
            partial / "dynamic_expansion_v32/expanded_candidates.b.json"
        ).read_bytes()
        and "No such file or directory" in failure_log
        and not (partial / "diagnostic_probe").exists()
    ):
        raise RuntimeError("TPC-H v33 zero-call recovery boundary mismatch")

    previous_cycle = v26.LOOP_ROOT / f"cycle_{expected_next_cycle - 1:03d}"
    entry_gate = v26.load(previous_cycle / "entry_gate.json")
    postrobust = entry_gate.get("postrobust_reseal") or {}
    previous_selection = Path(str(postrobust.get("resealed_selection") or ""))
    previous_run = Path(str(postrobust.get("resealed_summary") or "")).parent
    for path, expected in (
        (previous_selection, str(postrobust.get("resealed_selection_sha256"))),
        (
            previous_run / "diagnostic_summary.json",
            str(postrobust.get("resealed_summary_sha256")),
        ),
        (
            previous_run / "diagnostic_state.json",
            str(postrobust.get("resealed_state_sha256")),
        ),
    ):
        v26.verify(path, expected)
    v30.validate_resealed_summary(v26, previous_selection, previous_run)
    v27.clean_diagnostic(v26, common, previous_run, accepted)

    backup = v26.LOOP_ROOT / (
        f"controller_state.pre_v33_cycle{expected_next_cycle:03d}.json"
    )
    if backup.exists():
        raise RuntimeError("TPC-H v33 recovery backup already exists")
    v27.atomic_json(backup, state)
    if v26.sha256(backup) != expected_state_sha256:
        raise RuntimeError("TPC-H v33 recovery backup SHA mismatch")

    quarantine = v26.LOOP_ROOT / (
        f"quarantine_cycle_{expected_next_cycle:03d}_v32_zero_call_"
        "prethird_handover"
    )
    if quarantine.exists():
        raise RuntimeError("TPC-H v33 zero-call quarantine already exists")
    partial.replace(quarantine)
    v27.atomic_json(
        quarantine / "v33_zero_call_quarantine_audit.json",
        {
            "contract": "tpch_v32_prethird_handover_zero_call_v1",
            "source_state_sha256": expected_state_sha256,
            "next_cycle": expected_next_cycle,
            "observed_files": sorted(observed_files),
            "physical_workload_calls": 0,
            "valid_replays": 0,
            "technical_failures": 0,
            "formal_state_mutated": False,
        },
    )

    recovered = copy.deepcopy(state)
    recovered.update(
        {
            "schema_version": "tpch_fresh10_target10_loop_v33",
            "controller_version": 33,
            "status": "resumed_with_bounded_prethird_feasibility",
            "next_cycle": expected_next_cycle,
            "v33_recovery": {
                "contract": "exact_zero_call_cycle_boundary_state_sha_v1",
                "source_state_sha256": expected_state_sha256,
                "next_cycle": expected_next_cycle,
                "accepted_count": accepted,
                "physical_workload_calls": 0,
                "formal_state_mutated": False,
                "manual_candidate_injection": False,
            },
        }
    )
    v27.atomic_json(state_path, recovered)
    v32.install_algorithm_hooks(v26, v27, v30, common)
    install_prethird_hook(v26, v27)
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

    v27 = import_module("tpch_target10_v27_for_v33", V27_PATH)
    v30 = import_module("tpch_target10_v30_for_v33", V30_PATH)
    v32 = import_module("tpch_target10_v32_for_v33", V32_PATH)
    original_verify_dependencies = v27.verify_dependencies

    def verify_dependencies(v26: Any) -> None:
        v26.SUCCESSOR_BUILDER = v32.SUCCESSOR_BUILDER
        v26.EXPECTED["successor_builder"] = v32.SUCCESSOR_BUILDER_SHA256
        v26.ROBUST_BUILDER = v32.ROBUST_BUILDER
        v26.EXPECTED["robust_builder"] = v32.ROBUST_BUILDER_SHA256
        original_verify_dependencies(v26)
        for path, expected in (
            (V26_PATH, V26_SHA256),
            (V27_PATH, V27_SHA256),
            (V30_PATH, V30_SHA256),
            (V32_PATH, V32_SHA256),
            (v32.SUCCESSOR_BUILDER, v32.SUCCESSOR_BUILDER_SHA256),
            (v32.EXPANDER, v32.EXPANDER_SHA256),
            (v32.ROBUST_BUILDER, v32.ROBUST_BUILDER_SHA256),
            (v32.RESEALER, v32.RESEALER_SHA256),
            (v32.SELECTOR, v32.SELECTOR_SHA256),
        ):
            v26.verify(path, expected)

    def recover(
        v26: Any,
        common: Any,
    ) -> tuple[Dict[str, Any], int, Path, Path, Path, int]:
        return recover_boundary(
            v26,
            v27,
            v30,
            v32,
            common,
            args.expected_state_sha256,
            args.expected_next_cycle,
        )

    v27.verify_dependencies = verify_dependencies
    v27.recover_cycle004 = recover
    return int(v27.main())


if __name__ == "__main__":
    raise SystemExit(main())
