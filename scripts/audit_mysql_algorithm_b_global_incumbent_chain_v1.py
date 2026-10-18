#!/usr/bin/env python3
"""Audit Algorithm B's incumbent chain across technical continuations."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "mysql_algorithm_b_global_incumbent_chain_audit_v1"


def load(path: Path) -> dict[str, Any]:
    path = path.resolve()
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular JSON evidence required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12)


def source_root(state: Mapping[str, Any]) -> Path | None:
    for key in ("completed_boundary_continuation", "technical_continuation"):
        row = state.get(key)
        if isinstance(row, Mapping) and row.get("source_run_root"):
            return Path(str(row["source_run_root"])).resolve()
    return None


def lineage(final_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    newest_first: list[tuple[Path, dict[str, Any]]] = []
    visited: set[Path] = set()
    root: Path | None = final_root.resolve()
    while root is not None:
        if root in visited:
            raise RuntimeError(f"continuation cycle detected: {root}")
        visited.add(root)
        state_path = root / "run_state.json"
        if not state_path.is_file():
            break
        state = load(state_path)
        newest_first.append((root, state))
        root = source_root(state)
    return list(reversed(newest_first))


def scene_of(state: Mapping[str, Any]) -> str:
    incumbents = state.get("incumbents") or {}
    if not isinstance(incumbents, Mapping) or len(incumbents) != 1:
        raise RuntimeError("single-scene Algorithm B state required")
    return str(next(iter(incumbents)))


def selected_row(decision: Mapping[str, Any]) -> dict[str, Any]:
    rows = [
        dict(row) for row in (decision.get("evaluations") or [])
        if isinstance(row, Mapping) and row.get("selected_for_outer_decision") is True
    ]
    if len(rows) != 1:
        raise RuntimeError(
            f"outer {decision.get('outer_attempt')} selected-row cardinality differs"
        )
    return rows[0]


def audit(state_path: Path) -> dict[str, Any]:
    state_path = state_path.resolve()
    final_root = state_path.parent
    final_state = load(state_path)
    scene = scene_of(final_state)
    decisions: dict[int, tuple[Path, dict[str, Any]]] = {}
    technical_baselines: list[dict[str, Any]] = []
    roots = lineage(final_root)
    for root, state in roots:
        baseline = ((state.get("fresh_baseline") or {}).get("incumbents") or {}).get(scene)
        if isinstance(baseline, Mapping) and baseline.get("db_qps") is not None:
            technical_baselines.append({
                "run_id": state.get("run_id"),
                "db_qps": float(baseline["db_qps"]),
                "measured_in_current_campaign": bool(
                    (state.get("fresh_baseline") or {}).get("measured_in_current_campaign")
                ),
            })
        for path in sorted(root.glob(f"outer_*/{scene}.decision.json")):
            decision = load(path)
            outer = int(decision.get("outer_attempt") or 0)
            if outer > 0:
                decisions[outer] = (path.resolve(), decision)
    failures: list[str] = []
    transitions: list[dict[str, Any]] = []
    expected: float | None = None
    accepted_count = 0
    for outer in sorted(decisions):
        path, decision = decisions[outer]
        row = selected_row(decision)
        semantic = row.get("outer_decision") or {}
        if not isinstance(semantic, Mapping):
            raise RuntimeError(f"outer decision object required: {path}")
        reference = float(semantic.get("incumbent_db_qps") or 0.0)
        candidate = float(row.get("db_qps") or 0.0)
        accepted = decision.get("accepted") is True
        if not math.isfinite(reference) or reference <= 0.0:
            failures.append(f"outer {outer} incumbent reference invalid: {reference}")
        if expected is None:
            expected = reference
        elif not close(reference, expected):
            failures.append(
                f"outer {outer} reference {reference:.15g} does not continue "
                f"incumbent {expected:.15g}"
            )
        if accepted:
            accepted_count += 1
            if not candidate > reference:
                failures.append(
                    f"outer {outer} accepted QPS {candidate:.15g} is not above "
                    f"reference {reference:.15g}"
                )
            expected = candidate
        transitions.append({
            "outer_attempt": outer,
            "decision_path": str(path),
            "accepted": accepted,
            "reference_db_qps": reference,
            "candidate_db_qps": candidate,
            "incumbent_after_db_qps": expected,
            "selected_rank": int(row.get("rank") or 0),
        })
    final_qps = float((final_state.get("incumbents") or {})[scene]["db_qps"])
    if expected is None:
        failures.append("no Algorithm B decisions found in continuation lineage")
    elif not close(final_qps, expected):
        failures.append(
            f"final incumbent {final_qps:.15g} differs from chain {expected:.15g}"
        )
    persisted_accepted = int((final_state.get("accepted_rounds") or {}).get(scene) or 0)
    persisted_rejected = int(
        (final_state.get("rejected_outer_attempts") or {}).get(scene) or 0
    )
    final_outer = int(final_state.get("outer_attempt") or 0)
    audited_rejected = sum(not row["accepted"] for row in transitions)
    present_outers = {int(row["outer_attempt"]) for row in transitions}
    missing_outers = [
        outer for outer in range(1, final_outer + 1) if outer not in present_outers
    ]
    if accepted_count != persisted_accepted:
        failures.append(
            f"accepted transition count {accepted_count} differs from persisted "
            f"count {persisted_accepted}"
        )
    if persisted_accepted + persisted_rejected != final_outer:
        failures.append(
            "persisted accepted/rejected counts do not cover final outer attempt"
        )
    if audited_rejected + len(missing_outers) != persisted_rejected:
        failures.append(
            f"audited rejections {audited_rejected} plus missing outers "
            f"{len(missing_outers)} differ from persisted rejections "
            f"{persisted_rejected}"
        )
    by_outer = {int(row["outer_attempt"]): row for row in transitions}
    full_transitions: list[dict[str, Any]] = []
    carried = None if not transitions else float(transitions[0]["reference_db_qps"])
    for outer in range(1, final_outer + 1):
        if outer in by_outer:
            row = by_outer[outer]
            carried = float(row["incumbent_after_db_qps"])
            full_transitions.append(row)
        else:
            full_transitions.append({
                "outer_attempt": outer,
                "decision_path": None,
                "accepted": False,
                "reference_db_qps": carried,
                "candidate_db_qps": None,
                "incumbent_after_db_qps": carried,
                "selected_rank": None,
                "technical_rejection_without_local_decision": True,
            })
    initial_reference = None if not transitions else transitions[0]["reference_db_qps"]
    final_technical_baseline = (
        None if not technical_baselines else technical_baselines[-1]["db_qps"]
    )
    return {
        "schema_version": SCHEMA,
        "status": "PASS" if not failures else "FAIL_CLOSED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm": "B",
        "scene": scene,
        "final_state_path": str(state_path),
        "continuation_roots": [str(root) for root, _ in roots],
        "curve_semantics": "initial_outer_reference_then_selected_incumbent_after_each_outer",
        "technical_rebaseline_points_are_not_curve_incumbents": True,
        "initial_reference_db_qps": initial_reference,
        "final_technical_baseline_db_qps": final_technical_baseline,
        "technical_baseline_differs_from_curve_origin": (
            initial_reference is not None
            and final_technical_baseline is not None
            and not close(float(initial_reference), float(final_technical_baseline))
        ),
        "accepted_rounds": accepted_count,
        "rejected_outer_attempts": persisted_rejected,
        "missing_outer_attempts": missing_outers,
        "missing_outer_attempts_filled_as_persisted_rejections": bool(
            missing_outers
        ),
        "final_incumbent_db_qps": final_qps,
        "failures": failures,
        "technical_baselines": technical_baselines,
        "transitions": full_transitions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.state)
    text = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
