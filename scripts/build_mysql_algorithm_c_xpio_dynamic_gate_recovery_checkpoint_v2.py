#!/usr/bin/env python3
"""Seal XP/I/O outer 35 after the undecided outer-36 gate failure."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_dynamic_gate_recovery_checkpoint_v1 as base
from scripts import build_mysql_algorithm_c_actual_width_checkpoint_v5 as actual_width


SCENE = "hybench_xp_io"
ERROR = "RuntimeError: dynamic non-interference gate failed closed"


def verify_failed_gate(path: Path) -> dict[str, Any]:
    value = base.load(path)
    samples = list(value.get("samples") or [])
    blockers = [
        row
        for sample in samples
        for row in list(sample.get("blocking_external_workloads") or [])
    ]
    if (
        value.get("status") != "BLOCKED_FAIL_CLOSED"
        or "dynamic_noninterference_window_timeout"
        not in set(value.get("errors") or [])
        or float(value.get("required_clean_seconds") or 0) != 15.0
        or float(value.get("maximum_wait_seconds") or 0) != 60.0
        or float(value.get("observed_seconds") or 0) < 60.0
        or not samples
        or not blockers
        or not any(
            row.get("command_class") == "database_client_or_benchmark"
            and row.get("target_cpu_overlap")
            and int(row.get("cpu_ticks_250ms") or 0) > 0
            for row in blockers
        )
        or any(
            base.mapping(sample.get("target_local_resource_proof"), "target proof").get(
                "passed"
            )
            is not True
            for sample in samples
        )
    ):
        raise RuntimeError("XP/I/O dynamic-gate failure evidence differs")
    return value


def verify_discarded_outer(
    run_root: Path, state: Mapping[str, Any], completed: int
) -> dict[str, Any]:
    outer_number = completed + 1
    outer = run_root / f"outer_{outer_number:03d}"
    ranking_path = outer / "predicted_rankings.json"
    gate_paths = sorted(outer.glob("*_dynamic_noninterference_gate.json"))
    if (
        not outer.is_dir()
        or not ranking_path.is_file()
        or list(outer.glob("*.decision.json"))
        or len(gate_paths) != 4
    ):
        raise RuntimeError("discarded XP/I/O outer differs")

    ranking = base.load(ranking_path)
    if (
        int(ranking.get("outer_attempt") or 0) != outer_number
        or SCENE not in list(ranking.get("active_decision_scenes") or [])
        or ranking.get("formal_evidence_eligible") is not True
    ):
        raise RuntimeError("discarded XP/I/O ranking differs")

    passed_gates: list[dict[str, str]] = []
    blocked_gates: list[Path] = []
    for gate_path in gate_paths:
        gate = base.load(gate_path)
        if gate.get("status") == "PASS":
            passed_gates.append(base.bound(gate_path, "passed dynamic gate"))
        elif gate.get("status") == "BLOCKED_FAIL_CLOSED":
            verify_failed_gate(gate_path)
            blocked_gates.append(gate_path)
        else:
            raise RuntimeError(f"unexpected dynamic gate status: {gate_path}")
    if len(passed_gates) != 3 or len(blocked_gates) != 1:
        raise RuntimeError("XP/I/O dynamic gate cardinality differs")
    if "lane_paired_incumbent_revalidation" not in blocked_gates[0].name:
        raise RuntimeError("XP/I/O blocked gate is not incumbent revalidation")

    completed_batches: list[dict[str, str]] = []
    for batch_index in (1, 2, 3):
        batch_path = outer / f"top5_batch_{batch_index}" / "batch_result.json"
        batch = base.load(batch_path)
        measurements = base.mapping(batch.get("measurements"), "batch measurements")
        expected = 2 if batch_index < 3 else 1
        if (
            batch.get("status") != "PASS"
            or batch.get("failures") not in ([], None)
            or len(measurements) != expected
            or any(
                base.mapping(row, "measurement").get("scene") != SCENE
                or base.mapping(row, "measurement").get("hard_gate_passed") is not True
                or base.mapping(row, "measurement").get("coverage_complete") is not True
                for row in measurements.values()
            )
        ):
            raise RuntimeError(f"discarded completed batch differs: {batch_path}")
        completed_batches.append(base.bound(batch_path, "discarded completed batch"))

    dedup = base.mapping(
        base.mapping(state.get("candidate_replay_dedup"), "candidate replay state").get(
            SCENE
        ),
        "scene candidate replay state",
    )
    if int(dedup.get("retained_for_next_outer") or 0) <= 0:
        raise RuntimeError("discarded outer transient candidate state is absent")

    return {
        "scene": SCENE,
        "completed_outer_attempts": completed,
        "discarded_incomplete_outer": outer_number,
        "predicted_rankings": base.bound(ranking_path, "discarded rankings"),
        "passed_dynamic_gates": passed_gates,
        "blocked_dynamic_gate": base.bound(blocked_gates[0], "blocked dynamic gate"),
        "discarded_completed_batches": completed_batches,
        "failure_stage": "lane_paired_incumbent_revalidation",
        "discarded_entire_undecided_outer": True,
        "formal_state_credit_increment": 0,
        "transient_candidate_replay_state_cleared": True,
        "fresh_dynamic_gate_required_on_resume": True,
        "completed_rounds_preserved": True,
    }


def build(run_root: Path) -> dict[str, Any]:
    run_root = run_root.resolve()
    state_path = run_root / "run_state.json"
    state = base.load(state_path)
    completed = int(state.get("outer_attempt") or 0)
    if (
        state.get("algorithm") != "C"
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error") != ERROR
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("formal_evidence_eligible") is not True
        or state.get("accepted_state_credit_prohibited") is not False
        or list(state.get("active_scenes") or []) != [SCENE]
        or completed != 35
    ):
        raise RuntimeError("XP/I/O dynamic-gate recovery source differs")

    source_checkpoint_path = Path(str(state.get("checkpoint_path") or "")).resolve()
    source_checkpoint = actual_width.verify_checkpoint(source_checkpoint_path)
    prior_completed = int(source_checkpoint.get("completed_outer_attempts") or 0)
    if prior_completed >= completed:
        raise RuntimeError("source checkpoint has no newly completed boundary")

    reconstructed = copy.deepcopy(
        base.mapping(source_checkpoint.get("reconstructed_state"), "source state")
    )
    for field in base.PERSISTENT_FIELDS:
        source = state.get(field)
        if field == "previously_accepted_parameter_signatures" and not source:
            continue
        source_map = base.mapping(source, f"source {field}")
        if SCENE not in source_map:
            raise RuntimeError(f"source field lacks scene: {field}")
        reconstructed.setdefault(field, {})[SCENE] = copy.deepcopy(source_map[SCENE])
    active_by_scene = base.mapping(state.get("active_by_scene") or {}, "active by scene")
    if active_by_scene:
        reconstructed.setdefault("active_by_scene", {})[SCENE] = copy.deepcopy(
            active_by_scene[SCENE]
        )
    else:
        reconstructed.setdefault("active_by_scene", {})[SCENE] = list(
            base.mapping(state.get("ranges_by_scene"), "ranges")[SCENE]
        )
    reconstructed.setdefault("replayed_param_keys_by_scene", {})[SCENE] = []
    reconstructed["post_target_stability_confirmation_counts"] = {}
    reconstructed["outer_attempt"] = completed

    baseline_binding = base.mapping(
        state.get("formal_incumbent_baseline"), "formal incumbent baseline"
    )
    baseline_path = Path(str(baseline_binding.get("path") or "")).resolve()
    if (
        baseline_binding.get("status") != "PASS"
        or not baseline_path.is_file()
        or baseline_path.is_symlink()
        or base.sha(baseline_path) != baseline_binding.get("sha256")
    ):
        raise RuntimeError("formal incumbent baseline differs")

    evidence: dict[str, Any] = {
        "source_checkpoint": base.bound(source_checkpoint_path, "source checkpoint"),
        "baseline": base.bound(baseline_path, "formal baseline"),
        "source_state": base.bound(state_path, "failed source state"),
        "outers": {},
    }
    accepted_delta = 0
    rejected_delta = 0
    for outer_number in range(prior_completed + 1, completed + 1):
        outer = run_root / f"outer_{outer_number:03d}"
        decision_path = outer / f"{SCENE}.decision.json"
        decision = base.load(decision_path)
        if (
            decision.get("scene") != SCENE
            or int(decision.get("outer_attempt") or 0) != outer_number
            or decision.get("formal_evidence_eligible") is not True
            or not isinstance(decision.get("accepted"), bool)
        ):
            raise RuntimeError(f"completed decision differs: {decision_path}")
        accepted_delta += int(decision["accepted"])
        rejected_delta += int(not decision["accepted"])
        evidence["outers"][str(outer_number)] = {
            "predicted_rankings": base.bound(
                outer / "predicted_rankings.json", "completed rankings"
            ),
            "companion_audit": base.bound(
                outer / "companion_audit.json", "completed companion audit"
            ),
            "batches": [
                base.bound(
                    outer / f"top5_batch_{batch_index}" / "batch_result.json",
                    "completed batch",
                )
                for batch_index in (1, 2, 3)
            ],
            "decisions": {SCENE: base.bound(decision_path, "completed decision")},
        }

    base_state = base.mapping(source_checkpoint["reconstructed_state"], "base state")
    if (
        int(reconstructed["accepted_rounds"][SCENE])
        != int(base_state["accepted_rounds"][SCENE]) + accepted_delta
        or int(reconstructed["rejected_outer_attempts"][SCENE])
        != int(base_state["rejected_outer_attempts"][SCENE]) + rejected_delta
    ):
        raise RuntimeError("completed decision counters differ")

    interruption = verify_discarded_outer(run_root, state, completed)
    document: dict[str, Any] = {
        "schema_version": base.SCHEMA,
        "status": "PREPARED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "source_run_root": str(run_root),
        "completed_outer_attempts": completed,
        "workload_started": False,
        "fresh_r0_complete": False,
        "formal_outer_started": False,
        "freeze_path": state["freeze_path"],
        "freeze_sha256": state["freeze_sha256"],
        "resource_model_contract_path": state["resource_model_contract_path"],
        "resource_model_contract_sha256": state["resource_model_contract_sha256"],
        "reconstructed_state": reconstructed,
        "evidence": evidence,
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": base.sha(Path(__file__).resolve()),
        "independent_scene_boundary": {
            "scene": SCENE,
            "completed_outer_attempts": completed,
            "discarded_incomplete_outer": completed + 1,
            "completed_rounds_preserved": True,
            "range_lineage_preserved_from_failed_state": True,
        },
        "independent_scene_dynamic_gate_boundary": interruption,
        "recovery_semantics": {
            "candidate_set_or_workload_changed": False,
            "resource_envelope_changed": False,
            "completed_rounds_replayed": False,
            "partial_outer_measurements_formal_credit": 0,
            "partial_outer_replayed_from_beginning": True,
            "dynamic_noninterference_gate_relaxed": False,
        },
    }
    document["checkpoint_sha256"] = base.canonical(document)
    return document


def verify_checkpoint(path: Path) -> dict[str, Any]:
    value = base.standard.verify_checkpoint(path)
    if (
        value.get("builder_source_path") != str(Path(__file__).resolve())
        or value.get("builder_source_sha256") != base.sha(Path(__file__).resolve())
        or value.get("independent_scene_dynamic_gate_boundary", {}).get("scene")
        != SCENE
        or build(Path(str(value.get("source_run_root") or ""))) != value
    ):
        raise RuntimeError("XP/I/O dynamic-gate recovery checkpoint differs")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.output_dir.name) is None:
        parser.error("unsafe output directory")
    if args.output_dir.exists() or args.output_dir.is_symlink():
        raise RuntimeError("fresh checkpoint output required")
    document = build(args.run_root)
    args.output_dir.mkdir(parents=True, mode=0o700)
    path = args.output_dir / "prepared_binding.json"
    base.standard.predecessor.predecessor.alg.atomic(path, document)
    path.with_name(path.name + ".sha256").write_text(
        f"{base.sha(path)}  {path.name}\n", encoding="ascii"
    )
    verify_checkpoint(path)
    print(json.dumps({
        "status": "PREPARED",
        "checkpoint": str(path),
        "completed_outer_attempts": document["completed_outer_attempts"],
        "discarded_incomplete_outer": document["completed_outer_attempts"] + 1,
        "scene": SCENE,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
