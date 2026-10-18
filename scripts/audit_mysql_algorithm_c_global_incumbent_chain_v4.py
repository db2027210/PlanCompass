#!/usr/bin/env python3
"""Audit C chains while recognizing the sealed outer-1 lineage anchor."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from scripts import audit_mysql_algorithm_c_global_incumbent_chain_v1 as core
from scripts import audit_mysql_algorithm_c_global_incumbent_chain_v3 as predecessor


SCHEMA = "mysql_algorithm_c_global_incumbent_chain_audit_v4"
ANCHOR_SCHEMA = "mysql_algorithm_c_outer1_checkpoint_v1"


def checkpoint_decision_paths(
    checkpoint_path: Path,
    *,
    visited_checkpoints: set[Path] | None = None,
    visited_decisions: set[Path] | None = None,
) -> list[Path]:
    """Return bound decisions, stopping at the sealed pre-evidence anchor."""
    root_call = visited_checkpoints is None
    checkpoint_path = checkpoint_path.resolve()
    checkpoints = visited_checkpoints if visited_checkpoints is not None else set()
    decisions = visited_decisions if visited_decisions is not None else set()
    if checkpoint_path in checkpoints:
        raise RuntimeError("Algorithm C checkpoint lineage cycle detected")
    checkpoints.add(checkpoint_path)
    checkpoint = core.load(checkpoint_path)
    raw_evidence = checkpoint.get("evidence")
    if not isinstance(raw_evidence, Mapping):
        if (
            checkpoint.get("schema_version") != ANCHOR_SCHEMA
            or checkpoint.get("status") != "PREPARED"
            or checkpoint.get("formal_scope") != "mysql_only"
            or checkpoint.get("cross_engine_comparison_prohibited") is not True
            or int(checkpoint.get("completed_outer_attempts") or 0) != 1
        ):
            raise RuntimeError("checkpoint evidence object required")
        raw_paths = core.mapping(checkpoint.get("decision_paths"), "anchor decisions")
        raw_shas = core.mapping(checkpoint.get("decision_sha256"), "anchor SHAs")
        for scene in core.SCENES:
            path = Path(str(raw_paths.get(scene) or "")).resolve()
            if (
                not path.is_file()
                or path.is_symlink()
                or core.alg.sha(path) != str(raw_shas.get(scene) or "")
            ):
                raise RuntimeError(f"outer-1 anchor decision differs: {scene}")
        # This predecessor predates explicit last-accepted-QPS references.
        # Its decisions are SHA-verified above and form the immutable anchor;
        # the auditable transition chain begins with outer 2's explicit
        # reference to the resulting incumbent.
        return []
    evidence = dict(raw_evidence)
    ordered: list[Path] = []
    source = evidence.get("source_checkpoint")
    if isinstance(source, Mapping) and not core.source_is_fresh_search(evidence):
        source_path = core.bound_path(source, "source checkpoint")
        ordered.extend(checkpoint_decision_paths(
            source_path,
            visited_checkpoints=checkpoints,
            visited_decisions=decisions,
        ))
    outers = core.mapping(evidence.get("outers") or {}, "checkpoint outer evidence")
    for outer_key in sorted(outers, key=lambda item: int(item)):
        outer = core.mapping(outers[outer_key], f"outer {outer_key} evidence")
        raw_decisions = core.mapping(outer.get("decisions") or {}, "outer decisions")
        for scene in core.SCENES:
            raw = raw_decisions.get(scene)
            if not isinstance(raw, Mapping):
                continue
            path = core.bound_path(raw, f"outer {outer_key} {scene} decision")
            if path not in decisions:
                decisions.add(path)
                ordered.append(path)
    if not root_call:
        return ordered
    reference_capable_seen: set[str] = set()
    auditable: list[Path] = []
    for path in ordered:
        decision = core.load(path)
        scene = str(decision.get("scene") or "")
        rows = decision.get("evaluations") or []
        explicit = False
        for raw in rows:
            if not isinstance(raw, Mapping):
                continue
            semantic = raw.get("outer_decision")
            if isinstance(semantic, Mapping) and (
                semantic.get("last_accepted_db_qps") is not None
                or semantic.get("qps_reference_db_qps") is not None
            ):
                explicit = True
                break
        if (
            not rows
            and decision.get("accepted") is False
            and decision.get("strict_qps_comparison_proved") is True
            and decision.get("incumbent_db_qps") is not None
        ):
            explicit = True
        if explicit:
            reference_capable_seen.add(scene)
        if explicit or scene in reference_capable_seen:
            auditable.append(path)
    return auditable


def _with_lineage(call):
    old = core.checkpoint_decision_paths
    core.checkpoint_decision_paths = checkpoint_decision_paths
    try:
        value = call()
    finally:
        core.checkpoint_decision_paths = old
    value["schema_version"] = SCHEMA
    value["sealed_outer1_lineage_anchor_supported"] = True
    return value


def audit_paths(*args, **kwargs):
    return _with_lineage(lambda: predecessor.audit_paths(*args, **kwargs))


def audit_checkpoint(path: Path):
    return _with_lineage(lambda: predecessor.audit_checkpoint(path))


def audit_run(run_root: Path):
    return _with_lineage(lambda: predecessor.audit_run(run_root))


atomic = predecessor.atomic
