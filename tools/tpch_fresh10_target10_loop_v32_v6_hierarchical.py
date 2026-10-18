#!/usr/bin/env python3
"""Resume TPC-H with hierarchical probe prediction and a formal feasibility gate.

This controller resumes only the exact zero-workload cycle-12 boundary left by
v31.  Candidate discovery is calibrated from the formal incumbent resource,
uses the formal range state, and remains EXPLAIN-only.  Three diagnostic
observations are still mandatory.  A passing rank gate may enter formal replay
only when at least one trusted candidate has both a robust lower bound above
the incumbent and a median gain above the measured noise margin.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import math
from pathlib import Path
import statistics
from typing import Any, Dict, Mapping, Tuple


V26_PATH = Path("/tmp/tpch_fresh10_target10_loop_v26.py")
V26_SHA256 = "73cfcdd2eb60d199c5b2650eb51f2b19bd4b4d845b7f6fcd9714ef2c48757976"
V27_PATH = Path("/tmp/tpch_fresh10_target10_loop_v27.py")
V27_SHA256 = "d8e1672b4aceea5065f612170d57c1b16917a1dce9bf039fb3af274be4492589"
V28_PATH = Path("/tmp/tpch_fresh10_target10_loop_v28.py")
V28_SHA256 = "c30eeb216998782c0f4d7d0e66dd5ab05458b3dcaad16b6a08167eea5b6a2096"
V30_PATH = Path("/tmp/tpch_fresh10_target10_loop_v30.py")
V30_SHA256 = "384f2ce8f944525711d706c1e85493bfe61ed075d20d6794f61caed3dd67d7f8"
SUCCESSOR_BUILDER = Path(
    "/tmp/build_tpch_autonomous_successor_top5_v6_hierarchical_probe.py"
)
SUCCESSOR_BUILDER_SHA256 = (
    "33a36e16056a47bec9d6fcbedf26d7ca31bf076940ee0775a68582121f729790"
)
EXPANDER = Path("/tmp/tpch_exact_regime_neighbor_expand_v3_incumbent_ladder.py")
EXPANDER_SHA256 = (
    "e6815809288c13c8a7ecd8773354d979e3eecba4a752c17756072724ec2ee25e"
)
ROBUST_BUILDER = Path("/tmp/build_tpch_two_observation_robust_repeat_v3.py")
ROBUST_BUILDER_SHA256 = (
    "b124ef363035606c55bcc16a801c220b50725b18d3cbe8e98fe816fdf83195b5"
)
RESEALER = Path("/tmp/build_tpch_three_observation_postrobust_reseal_v2.py")
RESEALER_SHA256 = (
    "78da3a52078bd3613ad2b33fc6c02bb8b7bff28f7133ecec5f7348a04ca57c08"
)
SELECTOR = Path("/tmp/tpch_round19_plan_regime_select.py")
SELECTOR_SHA256 = (
    "68820feeed713f059925862e39709b046f15c510f01b2709b86ac6a57ccd88ab"
)
HISTORY_RELATIVE = (
    "gradient_debug_round39_latest_epoch_exact_v2_20260731T0725_package/"
    "expanded_analysis.json"
)
HISTORY_SHA256 = (
    "9152445d78867ab371a0673c6a4037fa7ce486e440c4510ee349b047ecd3b641"
)
CAPTURE_RELATIVE = (
    "gradient_debug_round37_corrected3p2_diagnosticfix_v1_20260731T0525/"
    "query_capture_exact.json"
)
CAPTURE_SHA256 = (
    "8021855b122cfc27c3f709abbdaa620d72c69b6ac77238d26abb8a4d575ad66d"
)


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


def validate_expansion(
    v26: Any,
    path: Path,
    resume_state: Path,
) -> Dict[str, Any]:
    payload = v26.load(path)
    candidates = payload.get("ranked_candidates")
    calibration = payload.get("formal_incumbent_calibration") or {}
    if not (
        payload.get("diagnostic_only") is True
        and payload.get("explain_only") is True
        and int(payload.get("new_workload_calls", -1)) == 0
        and payload.get("accepted_update_allowed") is False
        and payload.get("incumbent_update_allowed") is False
        and payload.get("range_update_allowed") is False
        and payload.get("gradient_update_allowed") is False
        and payload.get("qps_used_for_inner_scoring") is False
        and isinstance(candidates, list)
        and len(candidates) >= 5
        and calibration.get("resume_state_sha256") == v26.sha256(resume_state)
        and calibration.get("historical_candidate_initial_resource_used") is False
        and int(payload.get("incumbent_coordinate_neighbor_count", -1)) >= 5
    ):
        raise RuntimeError(f"invalid incumbent-bound EXPLAIN expansion: {path}")
    return payload


def dynamic_expansion(
    v26: Any,
    v27: Any,
    common: Any,
    cycle_root: Path,
    evidence: Path,
    resume_state: Path,
    environment: Mapping[str, str],
) -> Path:
    output_root = cycle_root / "dynamic_expansion_v32"
    output_root.mkdir(mode=0o700)
    first = output_root / "expanded_candidates.a.json"
    second = output_root / "expanded_candidates.b.json"
    history = v26.B / HISTORY_RELATIVE
    capture = v26.B / CAPTURE_RELATIVE
    config = cycle_root / "config.a.json"
    for path, expected in (
        (EXPANDER, EXPANDER_SHA256),
        (SELECTOR, SELECTOR_SHA256),
        (history, HISTORY_SHA256),
        (capture, CAPTURE_SHA256),
        (resume_state, v26.sha256(resume_state)),
    ):
        v26.verify(path, expected)
    base = [
        str(v26.PYTHON),
        "-u",
        str(EXPANDER),
        "--history",
        str(history),
        "--evidence-top5",
        str(evidence),
        "--config",
        str(config),
        "--capture",
        str(capture),
        "--selector",
        str(SELECTOR),
        "--code-root",
        str(v26.B / "code"),
        "--resume-state",
        str(resume_state),
        "--allow-live-epoch-transition",
        "--output",
    ]
    v26.run(
        common,
        [*base, str(first)],
        output_root / "expand_a.log",
        environment,
    )
    v26.run(
        common,
        [*base, str(second)],
        output_root / "expand_b.log",
        environment,
    )
    if first.read_bytes() != second.read_bytes():
        raise RuntimeError("incumbent-bound EXPLAIN expansion is not deterministic")
    payload = validate_expansion(v26, first, resume_state)
    state = v26.load(resume_state)
    incumbent = finite(state["incumbent_score"], "incumbent_score")
    neighbors = [
        item
        for item in payload["ranked_candidates"]
        if item.get("candidate_origin")
        == "formal_incumbent_coordinate_score_ladder_probe"
    ]
    above = [
        item
        for item in neighbors
        if finite(item["candidate_score"], "candidate_score") > incumbent
    ]
    v27.atomic_json(
        output_root / "audit.json",
        {
            "contract": "tpch_incumbent_bound_dynamic_expansion_v32",
            "status": "complete",
            "source_evidence_sha256": v26.sha256(evidence),
            "resume_state_sha256": v26.sha256(resume_state),
            "expansion_sha256": v26.sha256(first),
            "deterministic_double_generation": True,
            "ranked_candidate_count": len(payload["ranked_candidates"]),
            "incumbent_coordinate_neighbor_count": len(neighbors),
            "incumbent_coordinate_above_incumbent_count": len(above),
            "incumbent_score": incumbent,
            "maximum_incumbent_neighbor_score": max(
                (finite(item["candidate_score"], "candidate_score") for item in neighbors),
                default=None,
            ),
            "physical_workload_calls": 0,
            "formal_state_mutated": False,
            "manual_candidate_injection": False,
            "thresholds_relaxed": False,
        },
    )
    return first


def acceptance_feasibility(
    v26: Any,
    selection: Path,
    resume_state: Path,
) -> Dict[str, Any]:
    payload = v26.load(selection)
    state = v26.load(resume_state)
    trusted = [
        item
        for item in payload.get("top5") or []
        if str(item.get("selection_role")) == "trusted"
    ]
    if len(trusted) != 3:
        raise RuntimeError("acceptance feasibility requires exactly three trusted")
    incumbent = finite(state["incumbent_score"], "incumbent_score")
    spans = [
        max(
            0.0,
            finite(
                (item.get("confidence") or {}).get(
                    "robust_two_of_three_score_span", 0.0
                ),
                "robust_two_of_three_score_span",
            ),
        )
        for item in trusted
    ]
    noise_margin = max(0.002, float(statistics.median(spans)))
    rows = []
    for item in trusted:
        confidence = item.get("confidence") or {}
        score = finite(item["candidate_score"], "candidate_score")
        lower = finite(confidence.get("score_lower", score), "score_lower")
        upper = finite(confidence.get("score_upper", score), "score_upper")
        feasible = lower > incumbent and score > incumbent + noise_margin
        rows.append(
            {
                "rank": int(item["rank"]),
                "candidate_score": score,
                "score_lower": lower,
                "score_upper": upper,
                "gain_over_incumbent": score - incumbent,
                "robustly_acceptance_feasible": feasible,
            }
        )
    feasible_ranks = [
        row["rank"] for row in rows if row["robustly_acceptance_feasible"]
    ]
    return {
        "contract": "tpch_trusted_robust_acceptance_feasibility_v1",
        "passed": bool(feasible_ranks),
        "incumbent_score": incumbent,
        "noise_margin": noise_margin,
        "trusted": rows,
        "feasible_trusted_ranks": feasible_ranks,
        "physical_workload_calls": 0,
        "formal_state_mutated": False,
        "manual_candidate_injection": False,
        "thresholds_relaxed": False,
        "resume_state_sha256": v26.sha256(resume_state),
        "selection_sha256": v26.sha256(selection),
    }


def install_algorithm_hooks(
    v26: Any,
    v27: Any,
    v30: Any,
    common: Any,
) -> None:
    original_build_probe = v26.build_probe
    original_analyze_gate = v26.analyze_gate
    original_promote_formal = v26.promote_formal
    resealed: Dict[str, Tuple[Path, Path]] = {}
    cycle_resume_states: Dict[str, Path] = {}

    def translated(selection: Path, run_root: Path) -> Tuple[Path, Path]:
        return resealed.get(str(selection.resolve()), (selection, run_root))

    def build_probe_with_incumbent_expansion(
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
        expansion_path = dynamic_expansion(
            v26,
            v27,
            common_module,
            cycle_root,
            effective_selection,
            resume_state,
            environment,
        )
        cycle_resume_states[str(cycle_root.resolve())] = resume_state.resolve()
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

    def analyze_gate_after_reseal_and_feasibility(
        common_module: Any,
        cycle_root: Path,
        selection: Path,
        validation_run: Path,
        environment: Mapping[str, str],
    ) -> Path:
        output_root = cycle_root / "postrobust_reseal_v32"
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
        for root, resealed_selection, summary, state, label in (
            (first_root, first_selection, first_summary, first_state, "a"),
            (second_root, second_selection, second_summary, second_state, "b"),
        ):
            v26.run(
                common_module,
                [
                    *base,
                    "--output",
                    str(resealed_selection),
                    "--output-summary",
                    str(summary),
                    "--output-state",
                    str(state),
                ],
                output_root / f"reseal_{label}.log",
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
        v30.validate_resealed_summary(v26, first_selection, first_root)
        analysis_path = original_analyze_gate(
            common_module,
            cycle_root,
            first_selection,
            first_root,
            environment,
        )
        analysis = v26.load(analysis_path)
        resume_state = cycle_resume_states.get(str(cycle_root.resolve()))
        if resume_state is None:
            raise RuntimeError("cycle resume state was not bound before gate")
        feasibility = acceptance_feasibility(
            v26,
            first_selection,
            resume_state,
        )
        feasibility_path = cycle_root / "acceptance_feasibility.json"
        v27.atomic_json(feasibility_path, feasibility)
        rank_gate_passed = analysis.get("passed") is True
        checks = analysis.setdefault("checks", {})
        checks["trusted_acceptance_feasible"] = feasibility["passed"]
        contract_checks = analysis.setdefault("contract_check_names", [])
        if "trusted_acceptance_feasible" not in contract_checks:
            contract_checks.append("trusted_acceptance_feasible")
        analysis["rank_gate_passed_before_acceptance_feasibility"] = (
            rank_gate_passed
        )
        analysis["passed"] = rank_gate_passed and feasibility["passed"] is True
        analysis["acceptance_feasibility"] = {
            "path": str(feasibility_path),
            "sha256": v26.sha256(feasibility_path),
            **feasibility,
        }
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
        analysis = v26.load(analysis_path)
        if not (
            analysis.get("passed") is True
            and (analysis.get("acceptance_feasibility") or {}).get("passed")
            is True
        ):
            raise RuntimeError("formal promotion lacks acceptance feasibility")
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

    v26.build_probe = build_probe_with_incumbent_expansion
    v26.analyze_gate = analyze_gate_after_reseal_and_feasibility
    v26.promote_formal = promote_resealed_formal


def recover_boundary(
    v26: Any,
    v27: Any,
    v30: Any,
    common: Any,
    expected_state_sha256: str,
    expected_next_cycle: int,
) -> tuple[Dict[str, Any], int, Path, Path, Path, int]:
    state_path = v26.LOOP_ROOT / "controller_state.json"
    if v26.sha256(state_path) != expected_state_sha256:
        raise RuntimeError("TPC-H v32 recovery state SHA mismatch")
    state = v26.load(state_path)
    accepted = v26.accepted_counter()
    cycles = state.get("cycles") or []
    last_cycle = cycles[-1] if cycles else {}
    resume_state = Path(str(state.get("resume_state") or ""))
    next_cycle_root = v26.LOOP_ROOT / f"cycle_{expected_next_cycle:03d}"
    expected_files = {
        "config.a.json",
        "config.b.json",
        "dynamic_expansion_v28/audit.json",
        "dynamic_expansion_v28/expand_a.log",
        "dynamic_expansion_v28/expand_b.log",
        "dynamic_expansion_v28/expanded_candidates.a.json",
        "dynamic_expansion_v28/expanded_candidates.b.json",
        "probe_builder_a.log",
    }
    observed_files = {
        str(path.relative_to(next_cycle_root))
        for path in next_cycle_root.rglob("*")
        if path.is_file()
    }
    failure_log = (next_cycle_root / "probe_builder_a.log").read_text(
        encoding="utf-8",
        errors="replace",
    )
    if not (
        state.get("schema_version") == "tpch_fresh10_target10_loop_v31"
        and int(state.get("controller_version", -1)) == 31
        and state.get("status") == "preparing_cycle"
        and int(state.get("accepted_count", -1)) == accepted
        and accepted < v26.TARGET
        and int(state.get("next_cycle", -1)) == expected_next_cycle
        and int(last_cycle.get("cycle", -1)) == expected_next_cycle - 1
        and int(last_cycle.get("accepted_after", -1)) == accepted
        and resume_state.is_file()
        and v26.load(resume_state).get("status") == "complete"
        and int(v26.load(resume_state).get("accepted_count", -1)) == accepted
        and observed_files == expected_files
        and (next_cycle_root / "config.a.json").read_bytes()
        == (next_cycle_root / "config.b.json").read_bytes()
        and (
            next_cycle_root / "dynamic_expansion_v28/expanded_candidates.a.json"
        ).read_bytes()
        == (
            next_cycle_root / "dynamic_expansion_v28/expanded_candidates.b.json"
        ).read_bytes()
        and "No such file or directory" in failure_log
        and not (next_cycle_root / "diagnostic_probe").exists()
    ):
        raise RuntimeError("TPC-H v32 zero-call recovery boundary mismatch")

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
        f"controller_state.pre_v32_cycle{expected_next_cycle:03d}.json"
    )
    if backup.exists():
        raise RuntimeError("TPC-H v32 recovery backup already exists")
    v27.atomic_json(backup, state)
    if v26.sha256(backup) != expected_state_sha256:
        raise RuntimeError("TPC-H v32 recovery backup SHA mismatch")

    quarantine = v26.LOOP_ROOT / (
        f"quarantine_cycle_{expected_next_cycle:03d}_v31_zero_call_held_successor"
    )
    if quarantine.exists():
        raise RuntimeError("TPC-H v32 zero-call quarantine already exists")
    next_cycle_root.replace(quarantine)
    v27.atomic_json(
        quarantine / "v32_zero_call_quarantine_audit.json",
        {
            "contract": "tpch_v31_held_successor_zero_call_quarantine_v1",
            "source_state_sha256": expected_state_sha256,
            "next_cycle": expected_next_cycle,
            "observed_files": sorted(observed_files),
            "deterministic_config_ab": True,
            "deterministic_expansion_ab": True,
            "physical_workload_calls": 0,
            "valid_replays": 0,
            "technical_failures": 0,
            "formal_state_mutated": False,
        },
    )

    recovered = copy.deepcopy(state)
    recovered.update(
        {
            "schema_version": "tpch_fresh10_target10_loop_v32",
            "controller_version": 32,
            "status": "resumed_with_incumbent_bound_acceptance_feasibility",
            "next_cycle": expected_next_cycle,
            "v32_recovery": {
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
    install_algorithm_hooks(v26, v27, v30, common)
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

    v27 = import_module("tpch_target10_v27_for_v32", V27_PATH)
    v30 = import_module("tpch_target10_v30_for_v32", V30_PATH)
    original_verify_dependencies = v27.verify_dependencies

    def verify_dependencies(v26: Any) -> None:
        v26.SUCCESSOR_BUILDER = SUCCESSOR_BUILDER
        v26.EXPECTED["successor_builder"] = SUCCESSOR_BUILDER_SHA256
        v26.ROBUST_BUILDER = ROBUST_BUILDER
        v26.EXPECTED["robust_builder"] = ROBUST_BUILDER_SHA256
        original_verify_dependencies(v26)
        for path, expected in (
            (V26_PATH, V26_SHA256),
            (V27_PATH, V27_SHA256),
            (V28_PATH, V28_SHA256),
            (V30_PATH, V30_SHA256),
            (SUCCESSOR_BUILDER, SUCCESSOR_BUILDER_SHA256),
            (EXPANDER, EXPANDER_SHA256),
            (ROBUST_BUILDER, ROBUST_BUILDER_SHA256),
            (RESEALER, RESEALER_SHA256),
            (SELECTOR, SELECTOR_SHA256),
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
            common,
            args.expected_state_sha256,
            args.expected_next_cycle,
        )

    v27.verify_dependencies = verify_dependencies
    v27.recover_cycle004 = recover
    return int(v27.main())


if __name__ == "__main__":
    raise SystemExit(main())
