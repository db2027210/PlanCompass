#!/usr/bin/env python3
"""Resume TPC-H after a zero-call fixed-expansion score plateau.

The controller reuses the sealed v27 state machine, replaces its cycle004
recovery hook with an exact cycle005 recovery, and regenerates deterministic
EXPLAIN-only neighbors before every later acquisition cycle.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from typing import Any, Dict, Mapping


V27_PATH = Path("/tmp/tpch_fresh10_target10_loop_v27.py")
V27_SHA256 = "d8e1672b4aceea5065f612170d57c1b16917a1dce9bf039fb3af274be4492589"
RECOVERY_STATE_SHA256 = (
    "58f0b2df6a74b26fefe8041c6e9213a9f21f14e07acac46eec7a586808c03df5"
)
FAILED_PROBE_LOG_SHA256 = (
    "b1f5ef1550fabde64ac1dffb7f70007fc7b4206a1580867d61363ba43c316af5"
)
PREBUILT_EXPANSION_SHA256 = (
    "04212aeec13abd3a99acd3f1c192412ccb24f20653d99a9104d6f43c7f4ae657"
)
PREBUILT_AUDIT_SHA256 = (
    "ae601fc5d5f1c139e84fdcd48463d0e0b51eadc5ed7fb4407feefe29735f8210"
)
EXPANDER = Path("/tmp/tpch_exact_regime_neighbor_expand_v2.py")
EXPANDER_SHA256 = "a4fb3fd997cc92d7c7c217086627d30c72caf3d2a7f74347ad28be7b0ca54857"
SELECTOR = Path("/tmp/tpch_round19_plan_regime_select.py")
SELECTOR_SHA256 = "68820feeed713f059925862e39709b046f15c510f01b2709b86ac6a57ccd88ab"
HISTORY_RELATIVE = (
    "gradient_debug_round39_latest_epoch_exact_v2_20260731T0725_package/"
    "expanded_analysis.json"
)
HISTORY_SHA256 = "9152445d78867ab371a0673c6a4037fa7ce486e440c4510ee349b047ecd3b641"
CAPTURE_RELATIVE = (
    "gradient_debug_round37_corrected3p2_diagnosticfix_v1_20260731T0525/"
    "query_capture_exact.json"
)
CAPTURE_SHA256 = "8021855b122cfc27c3f709abbdaa620d72c69b6ac77238d26abb8a4d575ad66d"


def import_v27() -> Any:
    specification = importlib.util.spec_from_file_location("tpch_target10_v27", V27_PATH)
    if specification is None or specification.loader is None:
        raise RuntimeError("cannot load sealed TPC-H v27 controller")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def validate_expansion(v26: Any, path: Path) -> Dict[str, Any]:
    payload = v26.load(path)
    candidates = payload.get("ranked_candidates")
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
    ):
        raise RuntimeError(f"invalid zero-call dynamic expansion: {path}")
    return payload


def dynamic_expansion(
    v26: Any,
    common: Any,
    cycle_root: Path,
    evidence: Path,
    environment: Mapping[str, str],
) -> Path:
    output_root = cycle_root / "dynamic_expansion_v28"
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
        raise RuntimeError("dynamic EXPLAIN expansion is not deterministic")
    payload = validate_expansion(v26, first)
    neighbors = [
        item
        for item in payload["ranked_candidates"]
        if item.get("candidate_origin")
        in {
            "live_plan_epoch_transition_explain_probe",
            "validated_exact_regime_explain_neighbor",
        }
    ]
    scores = [float(item["candidate_score"]) for item in neighbors]
    audit = {
        "contract": "tpch_per_cycle_dynamic_explain_expansion_v28",
        "status": "complete",
        "source_evidence_sha256": v26.sha256(evidence),
        "expansion_sha256": v26.sha256(first),
        "deterministic_double_generation": True,
        "ranked_candidate_count": len(payload["ranked_candidates"]),
        "new_neighbor_count": len(neighbors),
        "new_neighbor_score_span": max(scores) - min(scores) if scores else 0.0,
        "physical_workload_calls": 0,
        "formal_state_mutated": False,
        "thresholds_relaxed": False,
    }
    v27.atomic_json(output_root / "audit.json", audit)
    return first


def recover_cycle005(
    v26: Any,
    common: Any,
) -> tuple[Dict[str, Any], int, Path, Path, Path, int]:
    state_path = v26.LOOP_ROOT / "controller_state.json"
    if v26.sha256(state_path) != RECOVERY_STATE_SHA256:
        raise RuntimeError("TPC-H v28 recovery state SHA mismatch")
    state = v26.load(state_path)
    cycle4 = v26.LOOP_ROOT / "cycle_004"
    cycle5 = v26.LOOP_ROOT / "cycle_005"
    failed_log = cycle5 / "probe_builder_a.log"
    prebuilt_root = cycle5 / "dynamic_expansion_v28"
    prebuilt = prebuilt_root / "expanded_candidates.a.json"
    prebuilt_b = prebuilt_root / "expanded_candidates.b.json"
    prebuilt_audit = prebuilt_root / "audit.json"
    for path, expected in (
        (V27_PATH, V27_SHA256),
        (failed_log, FAILED_PROBE_LOG_SHA256),
        (prebuilt, PREBUILT_EXPANSION_SHA256),
        (prebuilt_audit, PREBUILT_AUDIT_SHA256),
    ):
        v26.verify(path, expected)
    if prebuilt.read_bytes() != prebuilt_b.read_bytes():
        raise RuntimeError("prebuilt cycle005 expansion A/B mismatch")
    expansion = validate_expansion(v26, prebuilt)
    scores = [
        float(item["candidate_score"])
        for item in expansion["ranked_candidates"]
        if item.get("candidate_origin") == "live_plan_epoch_transition_explain_probe"
    ]
    last_cycle = (state.get("cycles") or [{}])[-1]
    accepted = v26.accepted_counter()
    expected_resume = v26.ROOT / "formal_batch002" / "formal_state.json"
    if not (
        state.get("schema_version") == "tpch_fresh10_target10_loop_v27"
        and state.get("status") == "preparing_cycle"
        and int(state.get("accepted_count", -1)) == 0
        and int(state.get("next_cycle", -1)) == 5
        and len(state.get("cycles") or []) == 1
        and int(last_cycle.get("cycle", -1)) == 4
        and last_cycle.get("formal_started") is False
        and last_cycle.get("thresholds_relaxed") is False
        and Path(str(state.get("resume_state"))) == expected_resume
        and accepted == 0
        and len(scores) >= 5
        and max(scores) - min(scores) > 0.002
        and not (cycle5 / "probe_top5.a.json").exists()
        and not (cycle5 / "diagnostic_probe").exists()
    ):
        raise RuntimeError("TPC-H v28 recovery boundary mismatch")
    v27.clean_diagnostic(v26, common, cycle4 / "diagnostic_probe", accepted)
    v27.clean_diagnostic(v26, common, cycle4 / "diagnostic_exact_repeat", accepted)

    backup = v26.LOOP_ROOT / "controller_state.pre_v28_cycle005.json"
    if backup.exists():
        raise RuntimeError("TPC-H v28 recovery backup already exists")
    v27.atomic_json(backup, state)
    if v26.sha256(backup) != RECOVERY_STATE_SHA256:
        raise RuntimeError("TPC-H v28 recovery backup SHA mismatch")

    recovered = copy.deepcopy(state)
    recovered["schema_version"] = "tpch_fresh10_target10_loop_v28"
    recovered["controller_version"] = 28
    recovered["status"] = "reexploring_after_fixed_expansion_score_plateau"
    recovered["cycles"].append(
        {
            "cycle": 5,
            "status": "zero_call_fixed_expansion_score_plateau_recovered",
            "accepted_before": accepted,
            "accepted_after": accepted,
            "probe_builder_log_sha256": v26.sha256(failed_log),
            "dynamic_expansion_sha256": v26.sha256(prebuilt),
            "dynamic_expansion_candidate_count": len(expansion["ranked_candidates"]),
            "dynamic_expansion_new_neighbor_count": len(scores),
            "dynamic_expansion_new_neighbor_score_span": max(scores) - min(scores),
            "physical_workload_calls": 0,
            "technical_failures": 0,
            "formal_started": False,
            "thresholds_relaxed": False,
        }
    )
    recovered["next_cycle"] = 6
    v27.atomic_json(state_path, recovered)

    original_build_probe = v26.build_probe
    first_cycle = True

    def build_probe_with_dynamic_expansion(
        common_module: Any,
        cycle_root: Path,
        previous_selection: Path,
        previous_run: Path,
        resume_state: Path,
        accepted_count: int,
        environment: Mapping[str, str],
    ) -> Path:
        nonlocal first_cycle
        if first_cycle:
            expansion_path = prebuilt
            first_cycle = False
        else:
            expansion_path = dynamic_expansion(
                v26,
                common_module,
                cycle_root,
                previous_selection,
                environment,
            )
        v26.EXPANSION = expansion_path
        return original_build_probe(
            common_module,
            cycle_root,
            previous_selection,
            previous_run,
            resume_state,
            accepted_count,
            environment,
        )

    v26.build_probe = build_probe_with_dynamic_expansion
    return (
        recovered,
        accepted,
        expected_resume,
        cycle4 / "exact_repeat_top5.a.json",
        cycle4 / "diagnostic_exact_repeat",
        6,
    )


def main() -> int:
    global v27
    v27 = import_v27()
    v27.recover_cycle004 = recover_cycle005
    return int(v27.main())


if __name__ == "__main__":
    raise SystemExit(main())
