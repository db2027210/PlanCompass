#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import binary_plan_resource_tuner as tuner


def load_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify CPU round2 near-miss anchor selection.")
    parser.add_argument(
        "--config",
        default="config/binary_plan_tuning_hybenchxp_cpu_nearmiss_resume_20260709.json",
    )
    parser.add_argument(
        "--jsonl",
        default=(
            "/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs/"
            "hyxp_cpu_formal_20260709_131733/cpu_binary_search_hybenchxp.jsonl"
        ),
    )
    parser.add_argument("--round", type=int, default=2)
    parser.add_argument("--expect-label", default="cpu_r02_c026_LLRRLLR")
    parser.add_argument("--expect-next-round", type=int, default=0)
    parser.add_argument("--expect-resume-id", default="")
    parser.add_argument("--expect-plan-probe-workers", type=int, default=-1)
    parser.add_argument(
        "--expect-run-plan-probe-for-full-bruteforce",
        choices=["true", "false", ""],
        default="",
    )
    args = parser.parse_args()

    cfg = tuner.load_config(args.config)
    rows = load_rows(Path(args.jsonl))
    out_dir = Path(args.jsonl).resolve().parent
    baseline = next((row for row in rows if row.get("event") == "baseline_validation"), None)
    if baseline is None:
        raise SystemExit("baseline_validation not found")
    worker_rows = [row for row in rows if row.get("event") == "baseline_worker_validation"]
    baseline_by_worker = tuner.baseline_map_from_validations(worker_rows)

    validations = [
        row
        for row in rows
        if row.get("event") == "candidate_validation" and tuner.safe_int(row.get("round"), 0) == args.round
    ]
    if not validations:
        raise SystemExit(f"no candidate_validation rows for round {args.round}")

    def baseline_resolver(validation: Dict[str, Any]) -> Dict[str, Any]:
        resolved, _, _ = tuner.resolve_worker_baseline(validation, baseline, baseline_by_worker)
        return resolved

    strict_anchor, _ = tuner.select_range_anchor(
        validations,
        baseline,
        target="cpu",
        cfg=cfg,
        baseline_resolver=baseline_resolver,
    )
    near_anchor, near_details = tuner.select_range_anchor(
        validations,
        baseline,
        target="cpu",
        cfg=cfg,
        baseline_resolver=baseline_resolver,
        allow_near_miss=True,
    )
    if strict_anchor is not None:
        raise SystemExit(f"strict anchor unexpectedly selected: {strict_anchor.get('label')}")
    if near_anchor is None:
        raise SystemExit("near-miss anchor not selected")
    if near_anchor.get("label") != args.expect_label:
        raise SystemExit(f"near-miss anchor mismatch: {near_anchor.get('label')} != {args.expect_label}")

    active = [name for name in tuner.load_active_params(out_dir, "") if name in tuner.COST_PARAMS]
    resume_state = tuner.replay_existing_stage_state(
        cfg=cfg,
        target="cpu",
        log_path=Path(args.jsonl),
        base_params=tuner.initial_params(cfg),
        initial_ranges=tuner.param_ranges(cfg),
        active=active,
        baseline_probe={"resource_avg": {}, "resource_error_profile": {}},
    )
    bpt = cfg.get("binary_plan_tuning") if isinstance(cfg.get("binary_plan_tuning"), dict) else {}
    search_cfg = bpt.get("binary_search") if isinstance(bpt.get("binary_search"), dict) else {}
    resume_override_id = (resume_state.get("resume_range_override") or {}).get("id")
    plan_probe_workers = tuner.safe_int(search_cfg.get("plan_probe_workers"), 0)
    run_plan_probe_for_full_bruteforce = tuner.to_bool(
        search_cfg.get("run_plan_probe_for_full_bruteforce"),
        False,
    )
    if args.expect_next_round > 0 and tuner.safe_int(resume_state.get("next_round"), 0) != args.expect_next_round:
        raise SystemExit(
            f"resume_next_round mismatch: {resume_state.get('next_round')} != {args.expect_next_round}"
        )
    if args.expect_resume_id and resume_override_id != args.expect_resume_id:
        raise SystemExit(f"resume_override_id mismatch: {resume_override_id} != {args.expect_resume_id}")
    if args.expect_plan_probe_workers >= 0 and plan_probe_workers != args.expect_plan_probe_workers:
        raise SystemExit(f"plan_probe_workers mismatch: {plan_probe_workers} != {args.expect_plan_probe_workers}")
    if args.expect_run_plan_probe_for_full_bruteforce:
        expected_probe = args.expect_run_plan_probe_for_full_bruteforce == "true"
        if run_plan_probe_for_full_bruteforce != expected_probe:
            raise SystemExit(
                "run_plan_probe_for_full_bruteforce mismatch: "
                f"{run_plan_probe_for_full_bruteforce} != {expected_probe}"
            )
    output = {
        "strict_anchor": None,
        "near_miss_anchor": near_anchor.get("label"),
        "resume_next_round": resume_state.get("next_round"),
        "resume_override_id": resume_override_id,
        "resume_ranges": {name: list(resume_state["ranges"][name]) for name in active},
        "plan_probe_workers": plan_probe_workers,
        "run_plan_probe_for_full_bruteforce": run_plan_probe_for_full_bruteforce,
        "db_qps": near_details.get("db_qps") if near_details else None,
        "target_value": near_details.get("target_value") if near_details else None,
        "target_sweet_spot_distance": near_details.get("target_sweet_spot_distance") if near_details else None,
        "target_sweet_spot_distance_unit": near_details.get("target_sweet_spot_distance_unit") if near_details else None,
        "db_qps_delta_pct_vs_baseline": (
            near_details.get("db_qps_delta_pct_vs_baseline") if near_details else None
        ),
        "target_delta_pct_vs_baseline": (
            near_details.get("target_delta_pct_vs_baseline") if near_details else None
        ),
        "other_up_resources": near_details.get("other_up_resources") if near_details else [],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
