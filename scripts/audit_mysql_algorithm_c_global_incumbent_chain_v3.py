#!/usr/bin/env python3
"""Audit Algorithm C incumbent chains using runtime semantic-batch eligibility."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping

from scripts import audit_mysql_algorithm_c_global_incumbent_chain_v1 as predecessor


SCHEMA = "mysql_algorithm_c_global_incumbent_chain_audit_v3"


def decision_transition(path: Path) -> dict[str, Any]:
    decision = predecessor.load(path)
    scene = str(decision.get("scene") or "")
    outer = int(decision.get("outer_attempt") or 0)
    rows = [
        predecessor.mapping(row, "decision evaluation")
        for row in decision.get("evaluations") or []
    ]
    if scene not in predecessor.SCENES or outer < 1:
        raise RuntimeError(f"decision identity differs: {path}")
    if decision.get("accepted") is None and not rows:
        return {
            "path": str(path), "scene": scene, "outer_attempt": outer,
            "candidate_space_exhausted": True, "accepted": None,
            "reference_db_qps": None, "selected_db_qps": None,
            "selected_rank": None,
        }
    if not rows:
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

    selected_kind = decision.get("selected_semantic_batch_kind")
    if selected_kind is not None:
        selected_kind = str(selected_kind)
        for row in rows:
            row_kind = str(row.get("semantic_batch_kind") or "base_top5")
            tagged = row.get("semantic_state_eligible") is True
            if tagged != (row_kind == selected_kind):
                raise RuntimeError(
                    f"semantic state eligibility differs from selected batch: {path}"
                )

    references: list[float] = []
    eligible: list[dict[str, Any]] = []
    for row in rows:
        semantic = predecessor.mapping(row.get("outer_decision"), "row outer decision")
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
        state_eligible = (
            row.get("semantic_state_eligible", True) is True
            if selected_kind is not None
            else True
        )
        if (
            state_eligible
            and semantic.get("accepted") is True
            and row.get("effective_parameter_movement_admissible") is not False
        ):
            eligible.append({"row": row, "decision": semantic})

    reference_qps = references[0]
    if any(not predecessor.close(value, reference_qps) for value in references[1:]):
        raise RuntimeError(f"one outer used multiple incumbent references: {path}")
    eligible.sort(
        key=predecessor.selection_policy.accepted_selection_key,
        reverse=True,
    )
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
        "selected_semantic_batch_kind": selected_kind,
        "semantic_state_eligibility_enforced": selected_kind is not None,
    }


def _with_transition(call):
    old = predecessor.decision_transition
    predecessor.decision_transition = decision_transition
    try:
        value = call()
    finally:
        predecessor.decision_transition = old
    value["schema_version"] = SCHEMA
    value["semantic_state_eligibility_enforced"] = True
    return value


def audit_paths(*args, **kwargs):
    return _with_transition(lambda: predecessor.audit_paths(*args, **kwargs))


def audit_checkpoint(path: Path):
    return _with_transition(lambda: predecessor.audit_checkpoint(path))


def audit_run(run_root: Path):
    return _with_transition(lambda: predecessor.audit_run(run_root))


atomic = predecessor.atomic

