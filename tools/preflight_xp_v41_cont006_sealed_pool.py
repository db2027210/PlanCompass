#!/usr/bin/env python3
"""Read-only preflight for XP v41's sealed cont006 pool resume."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


PROJECT = Path("/home/anonymous/program/resource_adaptive")
TOOLS = PROJECT / "tools"
ROOT = PROJECT / "history/xp_final_algorithm_fresh10_v38_20260803"
EXPECTED_SOURCE_STATE_SHA256 = (
    "163200e9dcf0a95c27cf2e73aed066229980c85d574c7bc9b2367c1bdf075e75"
)
FORMAL_FIELDS = (
    "accepted_count",
    "accepted_fingerprints",
    "incumbent",
    "incumbent_score",
    "incumbent_params",
    "parameter_ranges",
    "range_state",
    "gradient_state",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--post-recovery", action="store_true")
    args = parser.parse_args()
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    import xp_v39_exact_point_pending_trust_patch as pending
    import xp_v41_excluded_ids_sealed_pool_resume_patch as v41

    state_path = ROOT / "state.json"
    pool_path = ROOT / "batches/cont006/explain_only/explain_only_generation.json"
    evidence = [
        state_path,
        pool_path,
        ROOT / "batches/cont006/score_metadata_normalization/selection.json",
        ROOT / "batches/cont006/score_metadata_normalization/audit.json",
        ROOT / "batches/cont006/formal_acceptance_infeasible_reroute.json",
        ROOT / "batches/cont006/accepted_incumbent.json",
    ]
    before = {str(path): sha256(path) for path in evidence}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if args.post_recovery:
        recovery = state.get("xp_v41_cont006_sealed_pool_recovery") or {}
        if (
            state.get("status") != "offline_reexploration"
            or int(state.get("next_batch_index", -1)) != 6
            or (state.get("active_phase") or {}).get("batch_id") != "cont006"
            or int((state.get("active_phase") or {}).get("physical_workload_calls", -1)) != 0
            or recovery.get("contract") != "xp_v41_cont006_sealed_pool_zero_call_recovery_v1"
            or int(recovery.get("workload_calls_added", -1)) != 0
            or int(recovery.get("explain_calls_repeated", -1)) != 0
        ):
            raise RuntimeError("XP v41 post-recovery state mismatch")
    elif sha256(state_path) != EXPECTED_SOURCE_STATE_SHA256:
        raise RuntimeError("XP v41 source state SHA mismatch")
    pool = v41.validate_sealed_pool(pool_path)

    rounds = [path for path in (pool_path.parent / "subset_logs").iterdir() if path.is_dir()]
    if len(rounds) != 1:
        raise RuntimeError("XP v41 requires exactly one sealed subset-log round")
    logs = sorted(rounds[0].glob("*.jsonl"))
    orders = []
    for path in logs:
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines:
            raise RuntimeError(f"XP v41 empty subset log: {path.name}")
        final = json.loads(lines[-1])
        if (
            final.get("event") != "experience_table_subset_complete"
            or final.get("status") != "success"
        ):
            raise RuntimeError(f"XP v41 incomplete subset log: {path.name}")
        orders.append(int(final.get("canonical_subset_order", -1)))
    if len(logs) != 63 or sorted(orders) != list(range(1, 64)):
        raise RuntimeError("XP v41 subset completion is not exact 63-of-63")

    cont006 = ROOT / "batches/cont006"
    if any(
        (cont006 / relative).exists()
        for relative in ("diagnostic_bundle", "selection.json", "serial_replay", "replay_summary.json")
    ):
        raise RuntimeError("XP v41 preflight found post-pool or workload artifacts")
    if list(cont006.rglob("workload.log")):
        raise RuntimeError("XP v41 preflight found workload evidence")
    source = v41.corrected_install_bundle_source(pending)
    if (
        v41.BROKEN_LOOP in source
        or source.count(v41.FIXED_LOOP) != 1
        or "excluded fingerprints must be non-empty string IDs" not in source
    ):
        raise RuntimeError("XP v41 type-safe source transformation mismatch")
    replayed = set(state.get("replayed_fingerprints") or [])
    rankings = list(pool.get("all_candidate_rankings") or []) + list(
        pool.get("candidate_rankings") or []
    )
    identifiers = {
        str((item.get("three_level_fingerprint") or {}).get("id") or "")
        for item in rankings
    }
    novel = identifiers - replayed - {""}
    if len(novel) < 5:
        raise RuntimeError("XP v41 sealed pool has fewer than five novel fingerprints")
    if any(sha256(path) != before[str(path)] for path in evidence):
        raise RuntimeError("XP v41 preflight mutated sealed evidence")
    print(
        json.dumps(
            {
                "contract": "xp_v41_cont006_sealed_pool_zero_call_preflight_v1",
                "status": "ready",
                "mode": "post_recovery" if args.post_recovery else "source",
                "accepted_count": int(state["accepted_count"]),
                "accepted_target": int(state["accepted_target"]),
                "pool_sha256": sha256(pool_path),
                "pool_bytes": pool_path.stat().st_size,
                "subset_success": "63/63",
                "predicted_safe_novel_fingerprints": int(
                    pool["predicted_safe_novel_fingerprint_count"]
                ),
                "novel_fingerprints_before_calibration": len(novel),
                "excluded_ids_type_contract": "list[str]",
                "workload_calls": 0,
                "explain_calls_repeated": 0,
                "manual_candidate_injection": False,
                "formal_state_sha256": digest(
                    {key: state.get(key) for key in FORMAL_FIELDS}
                ),
                "state_unchanged": True,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
