#!/usr/bin/env python3
"""Train an auditable direction/ranking artifact from extracted real replays."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from real_replay_direction_ranker import (  # noqa: E402
    DirectionRankerConfig,
    ReplayRankingSample,
    train_real_replay_direction_ranker,
)


def _read_json(path: Path) -> Dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"dataset must be a JSON object: {path}")
    return document


def _record_samples(
    document: Mapping[str, Any],
    *,
    workload: str,
    target_resource: str,
    scenario: Optional[str],
    require_stable: bool,
) -> List[ReplayRankingSample]:
    samples: List[ReplayRankingSample] = []
    records = document.get("records")
    if not isinstance(records, list):
        raise ValueError("dataset records must be a list")
    for record in records:
        if not isinstance(record, Mapping):
            continue
        if str(record.get("workload") or "") != workload:
            continue
        if str(record.get("target_resource") or "") != target_resource:
            continue
        record_scenario = str(record.get("scenario") or "unknown")
        if scenario is not None and record_scenario != scenario:
            continue
        if require_stable and record.get("stable_measurement") is not True:
            continue
        parameter_log_delta = record.get("parameter_log_delta")
        if not isinstance(parameter_log_delta, Mapping):
            continue
        resource_profile = record.get("resource_profile")
        if not isinstance(resource_profile, Mapping):
            resource_profile = {}
        scenario_features: Dict[str, Any] = {
            "workload": workload,
            "target_resource": target_resource,
            "scenario": record_scenario,
            "changed_parameter_count": int(
                record.get("changed_parameter_count") or len(parameter_log_delta)
            ),
        }
        for resource_name, value in resource_profile.items():
            scenario_features[f"capability_{resource_name}"] = value
        candidate_plan_signature = str(
            record.get("candidate_plan_signature") or ""
        ).strip()
        plan_features = {
            "plan_signature_available": bool(candidate_plan_signature),
            "candidate_plan_signature": (
                candidate_plan_signature or None
            ),
        }
        samples.append(
            ReplayRankingSample(
                sample_id=str(record.get("sample_id") or ""),
                scope_id=str(record.get("scope_id") or ""),
                parameter_log_delta={
                    str(key): float(value)
                    for key, value in parameter_log_delta.items()
                },
                db_qps_delta_pct=float(record.get("db_qps_delta_pct")),
                target_util_delta_pp=float(record.get("target_util_delta_pp")),
                scenario_features=scenario_features,
                plan_features=plan_features,
            )
        )
    return samples


def build_artifact(
    dataset_path: Path,
    *,
    workload: str,
    target_resource: str,
    scenario: Optional[str],
    active_params: Sequence[str],
    require_stable: bool = True,
    config: Optional[DirectionRankerConfig] = None,
) -> Dict[str, Any]:
    dataset_bytes = dataset_path.read_bytes()
    document = json.loads(dataset_bytes.decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError("dataset must be a JSON object")
    samples = _record_samples(
        document,
        workload=workload,
        target_resource=target_resource,
        scenario=scenario,
        require_stable=require_stable,
    )
    result = train_real_replay_direction_ranker(samples, config=config)
    capability_profiles = []
    seen_profiles = set()
    for sample in samples:
        profile = {
            key[len("capability_") :]: float(value)
            for key, value in sample.scenario_features.items()
            if key.startswith("capability_") and isinstance(value, (int, float))
        }
        token = json.dumps(profile, sort_keys=True, separators=(",", ":"))
        if profile and token not in seen_profiles:
            seen_profiles.add(token)
            capability_profiles.append(profile)
    artifact = result.to_dict()
    artifact["training_contract"] = {
        "dataset_path": str(dataset_path.resolve()),
        "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "dataset_artifact_type": document.get("artifact_type"),
        "dataset_schema_version": document.get("schema_version"),
        "workload": workload,
        "target_resource": target_resource,
        "scenario": scenario,
        "active_params": [str(name) for name in active_params],
        "capability_profiles": capability_profiles,
        "require_stable_measurement": require_stable,
        "group_key": "scope_id (one outer incumbent and its context)",
        "candidate_features": (
            "parameter log delta + bound scenario/capability features + "
            "pre-replay plan features"
        ),
        "ranking_target": "db_qps_up AND target_bottleneck_utilization_down",
        "online_experience_table_mutation_allowed": False,
    }
    artifact["filtered_sample_count"] = len(samples)
    return artifact


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--workload", required=True)
    parser.add_argument("--target-resource", choices=["cpu", "memory", "io"], required=True)
    parser.add_argument("--scenario")
    parser.add_argument("--active-param", action="append", required=True)
    parser.add_argument("--allow-unstable-history", action="store_true")
    parser.add_argument("--min-positive-per-task", type=int, default=3)
    parser.add_argument("--max-group-folds", type=int, default=5)
    parser.add_argument("--min-joint-pairwise-auc", type=float, default=0.90)
    parser.add_argument(
        "--max-joint-pairwise-inversion-upper",
        type=float,
        default=0.10,
    )
    parser.add_argument(
        "--min-joint-top1-success-wilson-lower",
        type=float,
        default=0.50,
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    artifact = build_artifact(
        Path(args.dataset),
        workload=str(args.workload),
        target_resource=str(args.target_resource),
        scenario=str(args.scenario) if args.scenario else None,
        active_params=args.active_param,
        require_stable=not args.allow_unstable_history,
        config=DirectionRankerConfig(
            min_positive_per_task=args.min_positive_per_task,
            max_group_folds=args.max_group_folds,
            min_joint_pairwise_auc=args.min_joint_pairwise_auc,
            max_joint_pairwise_inversion_upper=(
                args.max_joint_pairwise_inversion_upper
            ),
            min_joint_top1_success_wilson_lower=(
                args.min_joint_top1_success_wilson_lower
            ),
        ),
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "ready": artifact["ready"],
                "reason": artifact["reason"],
                "filtered_sample_count": artifact["filtered_sample_count"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0 if artifact["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
