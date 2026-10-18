#!/usr/bin/env python3
"""Audit Algorithm C's selected incumbent chain across resume checkpoints.

Candidate-level ``outer_decision.accepted`` means that a row is eligible.  It
does not by itself identify the row selected to replace the incumbent.  This
auditor reproduces the runtime selection key and verifies the stronger state
transition invariant:

* every outer compares against the exact incumbent selected by the preceding
  accepted outer (or keeps the same incumbent after a rejection); and
* the final persisted incumbent equals the end of that transition chain.

A fresh-search boundary intentionally cuts older checkpoint history because
the runtime resets all optimization state at that boundary.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))

from scripts import mysql_algorithm_b_pg_outer_policy_v31 as selection_policy
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg


SCHEMA = "mysql_algorithm_c_global_incumbent_chain_audit_v1"
SCENES = tuple(alg.SCENES)


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def load(path: Path) -> dict[str, Any]:
    path = path.resolve()
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular JSON evidence required: {path}")
    return mapping(json.loads(path.read_text(encoding="utf-8")), str(path))


def bound_path(raw: Any, label: str) -> Path:
    binding = mapping(raw, label)
    path = Path(str(binding.get("path") or "")).resolve()
    if (
        not path.is_file()
        or path.is_symlink()
        or alg.sha(path) != str(binding.get("sha256") or "")
    ):
        raise RuntimeError(f"{label} binding differs: {path}")
    return path


def close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12)


def source_is_fresh_search(evidence: Mapping[str, Any]) -> bool:
    raw = evidence.get("source_state")
    if not isinstance(raw, Mapping):
        return False
    state = load(bound_path(raw, "source state"))
    return state.get("fresh_search") is True


def checkpoint_decision_paths(
    checkpoint_path: Path,
    *,
    visited_checkpoints: set[Path] | None = None,
    visited_decisions: set[Path] | None = None,
) -> list[Path]:
    """Return SHA-bound decisions in semantic execution order."""
    checkpoint_path = checkpoint_path.resolve()
    checkpoints = visited_checkpoints if visited_checkpoints is not None else set()
    decisions = visited_decisions if visited_decisions is not None else set()
    if checkpoint_path in checkpoints:
        raise RuntimeError("Algorithm C checkpoint lineage cycle detected")
    checkpoints.add(checkpoint_path)
    checkpoint = load(checkpoint_path)
    evidence = mapping(checkpoint.get("evidence"), "checkpoint evidence")
    ordered: list[Path] = []
    source = evidence.get("source_checkpoint")
    if isinstance(source, Mapping) and not source_is_fresh_search(evidence):
        source_path = bound_path(source, "source checkpoint")
        ordered.extend(checkpoint_decision_paths(
            source_path,
            visited_checkpoints=checkpoints,
            visited_decisions=decisions,
        ))
    outers = mapping(evidence.get("outers") or {}, "checkpoint outer evidence")
    for outer_key in sorted(outers, key=lambda item: int(item)):
        outer = mapping(outers[outer_key], f"outer {outer_key} evidence")
        for scene in SCENES:
            raw = mapping(outer.get("decisions") or {}, "outer decisions").get(scene)
            if not isinstance(raw, Mapping):
                continue
            path = bound_path(raw, f"outer {outer_key} {scene} decision")
            if path not in decisions:
                decisions.add(path)
                ordered.append(path)
    return ordered


def local_run_decision_paths(run_root: Path, state: Mapping[str, Any]) -> list[Path]:
    scene = str(state.get("async_scene") or "")
    if scene not in SCENES:
        raise RuntimeError("independent Algorithm C scene differs")
    completed = int(state.get("outer_attempt") or 0)
    paths: list[Path] = []
    for outer in range(1, completed + 1):
        path = run_root / f"outer_{outer:03d}" / f"{scene}.decision.json"
        if path.is_file() and not path.is_symlink():
            paths.append(path.resolve())
    return paths


def decision_transition(path: Path) -> dict[str, Any]:
    decision = load(path)
    scene = str(decision.get("scene") or "")
    outer = int(decision.get("outer_attempt") or 0)
    rows = [mapping(row, "decision evaluation") for row in decision.get("evaluations") or []]
    if scene not in SCENES or outer < 1:
        raise RuntimeError(f"decision identity differs: {path}")
    if decision.get("accepted") is None and not rows:
        return {
            "path": str(path), "scene": scene, "outer_attempt": outer,
            "candidate_space_exhausted": True, "accepted": None,
            "reference_db_qps": None, "selected_db_qps": None,
            "selected_rank": None,
        }
    if not rows:
        # A small number of sealed technical-recovery decisions summarize a
        # rejected outer without copying the full evaluations.  Their bound
        # incumbent is sufficient to prove that rejection preserved state.
        if (
            decision.get("accepted") is False
            and decision.get("strict_qps_comparison_proved") is True
            and isinstance(decision.get("incumbent_db_qps"), (int, float))
            and float(decision["incumbent_db_qps"]) > 0.0
        ):
            return {
                "path": str(path), "scene": scene, "outer_attempt": outer,
                "candidate_space_exhausted": False, "accepted": False,
                "reference_db_qps": float(decision["incumbent_db_qps"]),
                "selected_db_qps": None, "selected_rank": None,
                "recovered_rejected_outer": True,
            }
        raise RuntimeError(f"decision evaluations absent: {path}")
    references: list[float] = []
    eligible: list[dict[str, Any]] = []
    for row in rows:
        semantic = mapping(row.get("outer_decision"), "row outer decision")
        reference = semantic.get("last_accepted_db_qps")
        if reference is None:
            reference = semantic.get("qps_reference_db_qps")
        try:
            reference_qps = float(reference)
        except (TypeError, ValueError) as error:
            raise RuntimeError(f"decision reference absent: {path}") from error
        if not math.isfinite(reference_qps) or reference_qps <= 0:
            raise RuntimeError(f"decision reference invalid: {path}")
        references.append(reference_qps)
        if (
            semantic.get("accepted") is True
            and row.get("effective_parameter_movement_admissible") is not False
        ):
            eligible.append({"row": row, "decision": semantic})
    reference_qps = references[0]
    if any(not close(value, reference_qps) for value in references[1:]):
        raise RuntimeError(f"one outer used multiple incumbent references: {path}")
    eligible.sort(key=selection_policy.accepted_selection_key, reverse=True)
    accepted = decision.get("accepted") is True
    if accepted != bool(eligible):
        raise RuntimeError(f"selected acceptance evidence differs: {path}")
    chosen = eligible[0]["row"] if eligible else None
    selected_qps = None if chosen is None else float(chosen["db_qps"])
    if selected_qps is not None and not selected_qps > reference_qps:
        raise RuntimeError(f"accepted incumbent did not strictly improve QPS: {path}")
    return {
        "path": str(path), "scene": scene, "outer_attempt": outer,
        "candidate_space_exhausted": False, "accepted": accepted,
        "reference_db_qps": reference_qps,
        "selected_db_qps": selected_qps,
        "selected_rank": None if chosen is None else int(chosen.get("rank") or 0),
    }


def audit_paths(
    decision_paths: Sequence[Path],
    *,
    final_incumbents: Mapping[str, Any],
    subject: str,
) -> dict[str, Any]:
    expected: dict[str, float] = {}
    transitions: dict[str, list[dict[str, Any]]] = {scene: [] for scene in SCENES}
    failures: list[str] = []
    for path in decision_paths:
        transition = decision_transition(path)
        scene = transition["scene"]
        reference = transition["reference_db_qps"]
        if reference is None:
            continue
        if scene in expected and not close(float(reference), expected[scene]):
            failures.append(
                f"{scene} outer {transition['outer_attempt']} reference "
                f"{reference:.15g} does not continue incumbent {expected[scene]:.15g}"
            )
        elif scene not in expected:
            expected[scene] = float(reference)
        selected = transition["selected_db_qps"]
        if selected is not None:
            if not float(selected) > expected[scene]:
                failures.append(
                    f"{scene} outer {transition['outer_attempt']} selected QPS "
                    f"{selected:.15g} is not above incumbent {expected[scene]:.15g}"
                )
            expected[scene] = float(selected)
        transitions[scene].append(transition)
    for scene, expected_qps in expected.items():
        incumbent = mapping(final_incumbents.get(scene), f"{scene} final incumbent")
        final_qps = float(incumbent.get("db_qps") or 0)
        if not close(final_qps, expected_qps):
            failures.append(
                f"{scene} final incumbent {final_qps:.15g} differs from selected "
                f"chain {expected_qps:.15g}"
            )
    compact = {scene: rows for scene, rows in transitions.items() if rows}
    return {
        "schema_version": SCHEMA,
        "status": "PASS" if not failures else "FAIL_CLOSED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "subject": subject,
        "comparison": "next_reference_equals_previous_selected_incumbent",
        "strict_qps_progress_required": True,
        "candidate_level_accepted_is_not_selected_incumbent": True,
        "failures": failures,
        "transitions": compact,
        "final_expected_qps": expected,
    }


def audit_checkpoint(path: Path) -> dict[str, Any]:
    checkpoint = load(path)
    reconstructed = mapping(
        checkpoint.get("reconstructed_state"), "checkpoint reconstructed state",
    )
    decisions = checkpoint_decision_paths(path)
    return audit_paths(
        decisions,
        final_incumbents=mapping(reconstructed.get("incumbents"), "incumbents"),
        subject=str(path.resolve()),
    )


def audit_run(run_root: Path) -> dict[str, Any]:
    run_root = run_root.resolve()
    state = load(run_root / "run_state.json")
    paths: list[Path] = []
    if state.get("fresh_search") is not True:
        checkpoint_path = Path(str(state.get("checkpoint_path") or "")).resolve()
        paths.extend(checkpoint_decision_paths(checkpoint_path))
    seen = set(paths)
    for path in local_run_decision_paths(run_root, state):
        if path not in seen:
            paths.append(path)
            seen.add(path)
    return audit_paths(
        paths,
        final_incumbents=mapping(state.get("incumbents"), "run incumbents"),
        subject=str(run_root),
    )


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{alg.sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--checkpoint", type=Path)
    group.add_argument("--run-root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = (
        audit_checkpoint(args.checkpoint)
        if args.checkpoint is not None
        else audit_run(args.run_root)
    )
    if args.output is not None:
        atomic(args.output.resolve(), result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
