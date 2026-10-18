#!/usr/bin/env python3
"""Generate and validate XP cont003 selection without running a workload."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--bootstrap-selection", type=Path)
    parser.add_argument("--bootstrap-replay", type=Path)
    parser.add_argument("--query-snapshot", type=Path)
    args = parser.parse_args()
    if bool(args.bootstrap_selection) != bool(args.bootstrap_replay):
        raise ValueError(
            "bootstrap selection and replay must be provided together"
        )
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)

    bundle = Path("/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v6")
    sys.path.insert(0, str(bundle))
    from xp_unattended_adapters import FormalPaths, generate_selection
    from xp_unattended_continuation import normalize_selection

    experience = Path(
        "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
        "hybench_xp_compute00_59630/experience_sgd"
    )
    attempt = experience / "attempt39_fresh_from_accepted3_20260726"
    formal = attempt / "formal10_cont16b_accepted4_skip_initial_v1_20260729T2245"
    canonical = attempt / "formal10_canonical_after_unreserved_call_20260729T1448"
    accepted4 = attempt / "formal10_cont1_accepted4_cap5_feedback_v1_20260729T1729"
    repeat = (
        attempt
        / "gradient_debug_round17n_xp_exact_plan_repeat_v1_20260731T0345"
    )
    controller = Path(
        "/home/anonymous/program/resource_adaptive/history/"
        "xp_autonomous_formal_target10_v1_20260731"
    )
    paths = FormalPaths(
        root=bundle,
        state=controller / "state.json",
        batch_root=controller / "batches",
        formal_manifest=formal / "formal_manifest.json",
        authority_counter=formal / "state/formal_search_accepted_counter.txt",
        authority_history=formal / "optimizer_history_2026-07-29_225003.jsonl",
        canonical_state=canonical / "canonical_state.json",
        canonical_manifest=canonical / "manifest.json",
        acceptance_manifest=accepted4 / "formal_manifest.json",
        query_snapshot=(
            args.query_snapshot.resolve()
            if args.query_snapshot
            else bundle
            / "authority_refresh_accepted4_v4_20260731/query_snapshot_v3.json"
        ),
        replay_audit=(
            args.bootstrap_replay.resolve()
            if args.bootstrap_replay
            else repeat
            / "top5_strict_serial_diagnostic_v1/replay_summary.json"
        ),
        rank1_evidence=(
            args.bootstrap_selection.resolve()
            if args.bootstrap_selection
            else repeat
            / "top5_strict_serial_diagnostic_v1/rank_01/result.json"
        ),
        restore_script=(
            experience / "restore_with_physical_io_reapply_attempt25.sh"
        ),
    )
    state = json.loads(paths.state.read_text(encoding="utf-8"))
    selection = generate_selection(
        paths, "cont003_preflight_v14", state, output
    )
    status, normalized = normalize_selection(
        selection,
        replayed_fingerprints=state["replayed_fingerprints"],
    )
    if status != "ready" or normalized is None:
        raise RuntimeError(f"XP preflight selection is not ready: {status}")
    print(
        json.dumps(
            {
                "status": status,
                "workload_calls": 0,
                "batch_mode": normalized["batch_mode"],
                "roles": [
                    item["selection_role"] for item in normalized["top5"]
                ],
                "formal_admission": normalized["formal_admission"],
                "fingerprints": [
                    item["three_level_fingerprint"]["id"]
                    for item in normalized["top5"]
                ],
                "predicted_scores": [
                    item["candidate_score"] for item in normalized["top5"]
                ],
                "formal_lcb_scores": [
                    item["formal_lcb_score"] for item in normalized["top5"]
                ],
                "required_fields_present": all(
                    all(
                        item.get(field) is not None
                        for field in (
                            "feature_ratio",
                            "predicted_resource",
                            "utilization",
                            "dScore/dU",
                            "T",
                            "confidence",
                            "g_j",
                            "eta",
                            "delta_param",
                            "candidate_score",
                        )
                    )
                    for item in normalized["top5"]
                ),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
