#!/usr/bin/env python3
"""Offline v38 preflight against the sealed 183-MiB XP candidate pool."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


PROJECT = Path("/home/anonymous/program/resource_adaptive")
BUNDLE = Path("/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v33_plan_quarantine")
SOURCE = PROJECT / "history/xp_final_algorithm_fresh10_v37_20260803"
TARGET = PROJECT / "history/xp_final_algorithm_fresh10_v38_20260803"
if str(PROJECT / "tools") not in sys.path:
    sys.path.insert(0, str(PROJECT / "tools"))

import xp_v38_phase_state_patch as v38


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    target_state = TARGET / "state.json"
    state_sha = sha256(target_state)
    source_state = load(SOURCE / "state.json")
    preflight = TARGET / "offline_preflight"
    assert sha256(
        SOURCE / "batches/cont003/explain_only/explain_only_generation.json"
    ) == "595a0c85922b849e393706b982965bef8ab1d5a89dd42d89f3a21f09f605ac4c"
    assert sha256(preflight / "manifest.json") == (
        "3c1558b685a35d38dc6dd514a75034f20d5b7ebf151976a504b080019860f2fe"
    )
    assert sha256(preflight / "selection.json") == (
        "7d5bebd163f32626d7a4992e2b8aecdcd6a74e0e82e8c157e73d9121dbb84c20"
    )
    manifest = load(preflight / "manifest.json")
    selection = load(preflight / "selection.json")
    gate = v38._selection_gate(
        selection,
        source_state.get("replayed_fingerprints") or [],
    )
    assert isinstance(gate, dict)
    assert manifest["status"] == "ready"
    assert manifest["counts"]["novel_non_corner_fingerprints"] >= 5
    assert manifest["counts"]["safe_probe_candidates"] >= 5
    assert manifest["counts"]["selected_top5"] == 5
    assert gate["passed"] is True
    assert gate["selection_stage"] == "discovery_bootstrap_probe5"
    assert gate["roles"] == ["maximin_uncertain_probe"] * 5
    assert selection["batch_mode"] == "diagnostic_validation"
    assert selection["formal_admission"]["passed"] is False
    for candidate in selection["top5"]:
        fingerprint = candidate["three_level_fingerprint"]
        payload = {
            "plan": fingerprint["plan_level"],
            "feature_ratio_q005": fingerprint["feature_ratio_level"],
            "resource_utilization_q005": fingerprint[
                "predicted_resource_level"
            ],
        }
        expected = "three-level-sha256:" + hashlib.sha256(
            json.dumps(
                payload, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        assert fingerprint["id"] == expected
        assert candidate["accepted"] is False if "accepted" in candidate else True
        assert candidate["used_for_gradient"] is False
        assert candidate["used_for_inner_incumbent_update"] is False
    assert not list((TARGET / "offline_preflight").rglob("workload.log"))
    assert load(preflight / "fallback_budget.json")[
        "maximum_fallback_batches"
    ] == 1
    assert sha256(target_state) == state_sha
    print(
        "PASS XP v38 actual pool: novel safe probe5 bootstrap, zero workload",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
