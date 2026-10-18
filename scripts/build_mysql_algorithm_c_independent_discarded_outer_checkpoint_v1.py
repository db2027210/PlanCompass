#!/usr/bin/env python3
"""Seal completed independent C outers after a later quiet-gate timeout.

The interrupted outer may contain passing base or fallback waves, but without
its scene decision the entire outer remains uncredited and is replayed from
its beginning.  Every discarded batch and the blocking gate are SHA-bound.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
from typing import Any, Mapping

from scripts import build_mysql_algorithm_c_independent_quiet_gate_checkpoint_v1 as predecessor


base = predecessor.base
verifier = predecessor.verifier


def _blocked_gate(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    value = base.alg.load(path)
    samples = list(value.get("samples") or [])
    if (
        value.get("status") != "BLOCKED_FAIL_CLOSED"
        or value.get("errors") != ["coordinated_quiet_window_timeout"]
        or float(value.get("required_clean_seconds") or 0) != 15.0
        or float(value.get("maximum_wait_seconds") or 0) != 60.0
        or float(value.get("observed_seconds") or 0) < 60.0
        or not samples
        or not any(row.get("persistent_d_state_pids") for row in samples)
        or any(
            set(row.get("errors") or [])
            - {"host_uninterruptible_sleep_present"}
            for row in samples
        )
        or any(
            (row.get("co_resident_pg_isolation") or {}).get("passed") is not True
            for row in samples
        )
    ):
        raise RuntimeError("discarded outer blocked gate differs")
    return value, samples


def verify_discarded_outer(
    run_root: Path, state: Mapping[str, Any], scene: str, completed: int,
) -> dict[str, Any]:
    del state
    outer = run_root / f"outer_{completed + 1:03d}"
    ranking = outer / "predicted_rankings.json"
    if (
        not outer.is_dir()
        or not ranking.is_file()
        or list(outer.glob("*.decision.json"))
    ):
        raise RuntimeError("discarded undecided outer differs")
    blocked = []
    passed_gates = []
    for path in sorted(outer.rglob("*quiet_gate_attempt_*.json")):
        value = base.alg.load(path)
        if value.get("status") == "BLOCKED_FAIL_CLOSED":
            _blocked_gate(path)
            blocked.append(path)
        elif value.get("status") == "PASS":
            passed_gates.append(predecessor.bound(path, "passed quiet gate"))
        else:
            raise RuntimeError(f"discarded outer gate status differs: {path}")
    if len(blocked) != 1:
        raise RuntimeError("discarded outer blocked gate cardinality differs")

    discarded_batches = []
    for path in sorted(outer.rglob("batch_result.json")):
        batch = base.alg.load(path)
        measurements = predecessor.mapping(
            batch.get("measurements"), "discarded batch measurements",
        )
        if (
            batch.get("status") != "PASS"
            or batch.get("failures") not in ([], None)
            or not measurements
            or any(
                row.get("scene") != scene
                or row.get("hard_gate_passed") is not True
                or row.get("coverage_complete") is not True
                for row in measurements.values()
            )
        ):
            raise RuntimeError(f"discarded completed batch differs: {path}")
        discarded_batches.append(predecessor.bound(path, "discarded batch"))
    if not discarded_batches:
        raise RuntimeError("discarded outer has no completed batches")
    ranking_value = base.alg.load(ranking)
    if (
        int(ranking_value.get("outer_attempt") or 0) != completed + 1
        or scene not in (ranking_value.get("active_decision_scenes") or [])
        or ranking_value.get("formal_evidence_eligible") is not True
    ):
        raise RuntimeError("discarded outer ranking differs")
    return {
        "predicted_rankings": predecessor.bound(ranking, "discarded rankings"),
        "blocked_gate": predecessor.bound(blocked[0], "blocked quiet gate"),
        "passed_quiet_gates": passed_gates,
        "discarded_completed_batches": discarded_batches,
        "discarded_entire_undecided_outer": True,
        "formal_state_credit_increment": 0,
        "fresh_quiet_gate_required_on_resume": True,
    }


def build(run_root: Path) -> dict[str, Any]:
    original = predecessor._verify_interrupted_outer
    predecessor._verify_interrupted_outer = verify_discarded_outer
    try:
        document = predecessor.build(run_root)
    finally:
        predecessor._verify_interrupted_outer = original
    document.pop("checkpoint_sha256", None)
    document["builder_source_path"] = str(Path(__file__).resolve())
    document["builder_source_sha256"] = base.alg.sha(Path(__file__).resolve())
    document["independent_scene_discarded_outer_boundary"] = document.pop(
        "independent_scene_quiet_gate_boundary"
    )
    payload = copy.deepcopy(document)
    document["checkpoint_sha256"] = base._canonical_sha256(payload)
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.output_dir.name) is None:
        parser.error("unsafe output directory")
    if args.output_dir.exists() or args.output_dir.is_symlink():
        raise RuntimeError("fresh checkpoint output required")
    document = build(args.run_root.resolve())
    args.output_dir.mkdir(parents=True, mode=0o700)
    path = args.output_dir / "prepared_binding.json"
    base.alg.atomic(path, document)
    verifier.verify_checkpoint(path)
    print(json.dumps({
        "status": "PREPARED",
        "checkpoint": str(path),
        "completed_outer_attempts": document["completed_outer_attempts"],
        "scene": document[
            "independent_scene_discarded_outer_boundary"
        ]["scene"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
