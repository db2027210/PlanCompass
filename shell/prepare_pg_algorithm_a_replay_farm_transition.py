#!/usr/bin/env python3
"""Stage an audited N-worker Algorithm A configuration without starting it."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Set


TRANSITION_CONTRACT = "algorithm_a_worker_cohort_transition_v1"
ALGORITHM_A_METHOD = "exhaustive_binary_factorial_algorithm_a_v2"
PARALLELISM_INFERRED_COMPARISON = "parallelism_inferred_comparison_v1"


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.replace(path)


def parse_cpuset(value: Any) -> Set[int]:
    result: Set[int] = set()
    for component in str(value or "").split(","):
        component = component.strip()
        if not component:
            continue
        if "-" in component:
            start_raw, end_raw = component.split("-", 1)
            start = int(start_raw)
            end = int(end_raw)
            if start > end:
                raise ValueError(f"invalid CPU interval: {component}")
            values = range(start, end + 1)
        else:
            values = (int(component),)
        for cpu in values:
            if not 0 <= cpu <= 255:
                raise ValueError(f"worker CPU outside allowed 0-255 range: {cpu}")
            if cpu in result:
                raise ValueError(f"duplicate CPU inside cpuset: {cpu}")
            result.add(cpu)
    if not result:
        raise ValueError("worker cpuset_cpus is empty")
    return result


def validate_workers(
    source_workers: Sequence[Mapping[str, Any]],
    workers: Sequence[Mapping[str, Any]],
) -> None:
    if not 2 <= len(workers) <= 16:
        raise ValueError("Algorithm A replay farm must contain 2..16 workers")
    unique_fields = ("name", "container", "port", "data_dir")
    for field in unique_fields:
        values = [str(worker.get(field) or "") for worker in workers]
        if any(not value for value in values) or len(values) != len(set(values)):
            raise ValueError(f"worker field must be non-empty and unique: {field}")
    for worker in workers:
        identity_text = " ".join(
            str(worker.get(key) or "") for key in ("name", "container", "data_dir")
        ).lower()
        if "mysql" in identity_text:
            raise ValueError("worker identity must not reference MySQL")
    by_name = {str(worker["name"]): worker for worker in workers}
    for source in source_workers:
        name = str(source.get("name") or "")
        if name not in by_name:
            raise ValueError(f"source worker removed from transition: {name}")
        for field in (
            "container",
            "port",
            "data_dir",
            "dbname",
            "cpuset_cpus",
            "cpus",
            "memory",
        ):
            if by_name[name].get(field) != source.get(field):
                raise ValueError(
                    f"source worker changed during expansion: {name}.{field}"
                )
    occupied: Dict[int, str] = {}
    for worker in workers:
        name = str(worker["name"])
        cpuset = parse_cpuset(worker.get("cpuset_cpus"))
        declared_cpus = float(worker.get("cpus") or 0.0)
        if not 0.0 < declared_cpus <= len(cpuset):
            raise ValueError(
                f"worker CPU quota exceeds its cpuset: {name} "
                f"quota={declared_cpus} cpuset={len(cpuset)}"
            )
        for cpu in cpuset:
            if cpu in occupied:
                raise ValueError(
                    f"worker CPU overlap: cpu={cpu} workers={occupied[cpu]},{name}"
                )
            occupied[cpu] = name


def wave_shape(candidate_count: int, parallelism: int) -> List[int]:
    if candidate_count < 1 or parallelism < 1:
        raise ValueError("candidate_count and parallelism must be positive")
    return [
        min(parallelism, candidate_count - start)
        for start in range(0, candidate_count, parallelism)
    ]


def build_transition_config(
    source: Mapping[str, Any],
    workers: Sequence[Mapping[str, Any]],
    *,
    transition_id: str,
    baseline_cohort_lanes: Sequence[str],
    global_restore_parallelism: int,
) -> Dict[str, Any]:
    config = copy.deepcopy(dict(source))
    algorithm_a = config.get("algorithm_a")
    algorithm_c = config.get("algorithm_c")
    binary = config.get("binary_plan_tuning")
    if not isinstance(algorithm_a, dict) or not isinstance(algorithm_c, dict):
        raise ValueError("Algorithm A/C configuration is incomplete")
    if algorithm_a.get("method") != ALGORITHM_A_METHOD:
        raise ValueError("source is not the formal exhaustive Algorithm A")
    if not isinstance(binary, dict):
        raise ValueError("binary_plan_tuning configuration is missing")
    validation = binary.get("validation")
    if not isinstance(validation, dict):
        raise ValueError("validation configuration is missing")
    docker_workers = validation.get("docker_workers")
    if not isinstance(docker_workers, dict):
        raise ValueError("Docker worker configuration is missing")
    source_workers = docker_workers.get("workers")
    if not isinstance(source_workers, list):
        raise ValueError("source Docker worker list is missing")
    validate_workers(source_workers, workers)

    parallelism = len(workers)
    candidate_count = int(algorithm_a.get("candidates_per_round") or 0)
    waves = wave_shape(candidate_count, parallelism)
    algorithm_a["real_replay_parallelism"] = parallelism
    algorithm_a["worker_cohort_transition"] = {
        "contract": TRANSITION_CONTRACT,
        "transition_id": transition_id,
        "preserve_accepted": True,
        "preserve_ranges": True,
        "preserve_canonical_comparison_scale": True,
        "fresh_baseline_per_worker": True,
        "incumbent_replay_required": True,
        "global_restore_parallelism": global_restore_parallelism,
    }
    algorithm_c["real_replay_parallelism"] = parallelism
    workload_contract = algorithm_c.get("workload_contract")
    if not isinstance(workload_contract, dict):
        raise ValueError("Algorithm C compatibility workload contract is missing")
    workload_contract["full_factorial_replay_parallelism"] = parallelism
    workload_contract["full_factorial_wave_count"] = len(waves)
    workload_contract["wave_shape"] = waves
    workload_contract["worker_cohort_transition"] = copy.deepcopy(
        algorithm_a["worker_cohort_transition"]
    )
    baseline_cohort = workload_contract.get("baseline_cohort")
    if not isinstance(baseline_cohort, dict):
        raise ValueError("baseline cohort contract is missing")
    baseline_cohort["lane_count"] = len(baseline_cohort_lanes)
    baseline_cohort["lanes"] = [str(value) for value in baseline_cohort_lanes]
    baseline_cohort["synchronized_repetitions"] = 2

    database_contract = workload_contract.get("database")
    if not isinstance(database_contract, dict):
        raise ValueError("workload database contract is missing")
    database_contract["workers"] = [
        {
            "name": str(worker["name"]),
            "host": str(worker.get("host") or "127.0.0.1"),
            "port": int(worker["port"]),
            "container": str(worker["container"]),
            "cpuset_cpus": str(worker["cpuset_cpus"]),
        }
        for worker in workers
    ]

    docker_workers["workers"] = copy.deepcopy(list(workers))
    target = str(algorithm_c.get("target_resource") or "").strip().lower()
    scene = str(algorithm_c.get("scene") or "").strip().lower()
    if not target:
        if scene.endswith("_memory"):
            target = "memory"
        elif scene.endswith("_cpu"):
            target = "cpu"
        elif scene.endswith("_io"):
            target = "io"
    if target in {"cpu", "memory"}:
        docker_workers["cpu_memory_parallelism"] = parallelism
    elif target == "io":
        docker_workers["io_parallelism"] = parallelism
    elif target == "none":
        docker_workers["none_parallelism"] = parallelism
    adaptive = docker_workers.get("same_ssd_adaptive_parallelism")
    if isinstance(adaptive, dict):
        adaptive["initial"] = parallelism
        adaptive["effective_worker_cap"] = parallelism
        adaptive["preferred"] = max(
            parallelism,
            int(adaptive.get("preferred") or parallelism),
        )
        adaptive["stable_cap"] = max(
            parallelism,
            int(adaptive.get("stable_cap") or parallelism),
        )
    parallel_baseline = validation.get("parallel_baseline")
    if not isinstance(parallel_baseline, dict):
        raise ValueError("parallel baseline configuration is missing")
    parallel_baseline["parallelism"] = parallelism

    experiment_meta = binary.get("experiment_meta")
    if isinstance(experiment_meta, dict):
        experiment_meta["replay_parallelism"] = parallelism
        experiment_meta["replay_wave_shape"] = waves
        experiment_meta["execution_domain_transition_id"] = transition_id
    return config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-config", required=True, type=Path)
    workers_source = parser.add_mutually_exclusive_group(required=True)
    workers_source.add_argument("--workers-json", type=Path)
    workers_source.add_argument(
        "--workers-config",
        type=Path,
        help="Read the worker array from binary_plan_tuning.validation.docker_workers.workers",
    )
    parser.add_argument("--output-config", required=True, type=Path)
    parser.add_argument("--transition-id", required=True)
    parser.add_argument("--baseline-cohort-lane", action="append", default=[])
    parser.add_argument(
        "--from-global-restore-parallelism", type=int, default=2
    )
    parser.add_argument("--global-restore-parallelism", type=int, default=2)
    parser.add_argument(
        "--comparison-mode",
        choices=("physical_remeasure", PARALLELISM_INFERRED_COMPARISON),
        default="physical_remeasure",
    )
    args = parser.parse_args()

    source = read_json(args.source_config)
    if args.workers_json is not None:
        workers = read_json(args.workers_json)
    else:
        worker_config = read_json(args.workers_config)
        workers = (
            worker_config.get("binary_plan_tuning", {})
            .get("validation", {})
            .get("docker_workers", {})
            .get("workers")
            if isinstance(worker_config, Mapping)
            else None
        )
    if not isinstance(source, Mapping) or not isinstance(workers, list):
        raise SystemExit("source config must be an object and workers JSON an array")
    if not args.baseline_cohort_lane:
        raise SystemExit("at least one --baseline-cohort-lane is required")
    if not 1 <= args.from_global_restore_parallelism <= 16:
        raise SystemExit("source global restore parallelism must be 1..16")
    if not 1 <= args.global_restore_parallelism <= 16:
        raise SystemExit("target global restore parallelism must be 1..16")
    config = build_transition_config(
        source,
        workers,
        transition_id=args.transition_id,
        baseline_cohort_lanes=args.baseline_cohort_lane,
        global_restore_parallelism=args.global_restore_parallelism,
    )
    write_json(args.output_config, config)
    draft_request = {
        "contract": TRANSITION_CONTRACT,
        "authorized": True,
        "transition_id": args.transition_id,
        "from_worker_keys": [
            str(worker["name"])
            for worker in source["binary_plan_tuning"]["validation"]
            ["docker_workers"]["workers"]
        ],
        "to_worker_keys": [str(worker["name"]) for worker in workers],
        "from_replay_parallelism": int(
            source["algorithm_a"]["real_replay_parallelism"]
        ),
        "to_replay_parallelism": len(workers),
        "from_global_restore_parallelism": (
            args.from_global_restore_parallelism
        ),
        "to_global_restore_parallelism": args.global_restore_parallelism,
        "preserve_accepted": True,
        "preserve_ranges": True,
        "preserve_canonical_comparison_scale": True,
        "comparison_mode": args.comparison_mode,
    }
    if args.comparison_mode == PARALLELISM_INFERRED_COMPARISON:
        source_keys = draft_request["from_worker_keys"]
        draft_request.update(
            {
                "user_authorized": True,
                "fresh_baseline_per_worker": False,
                "incumbent_replay_required": False,
                "physical_workload_invocations": 0,
                "valid_replay_count": 0,
                "accepted_increment": 0,
                "comparison_source": (
                    "prior_two_worker_canonical_physical_baseline"
                ),
                "inference_source_worker_map": {
                    str(worker["name"]): source_keys[index % len(source_keys)]
                    for index, worker in enumerate(workers)
                },
            }
        )
        transition = config["algorithm_a"]["worker_cohort_transition"]
        transition.update(
            {
                "comparison_mode": args.comparison_mode,
                "user_authorized": True,
                "fresh_baseline_per_worker": False,
                "incumbent_replay_required": False,
            }
        )
        config["algorithm_c"]["workload_contract"][
            "worker_cohort_transition"
        ] = copy.deepcopy(transition)
        write_json(args.output_config, config)
    draft_path = args.output_config.with_suffix(".transition_request.json")
    write_json(draft_path, draft_request)
    manifest = {
        "contract": TRANSITION_CONTRACT,
        "transition_id": args.transition_id,
        "source_config": str(args.source_config),
        "source_config_sha256": canonical_sha256(source),
        "output_config": str(args.output_config),
        "output_config_sha256": canonical_sha256(config),
        "draft_transition_request": str(draft_path),
        "draft_transition_request_sha256": canonical_sha256(draft_request),
        "worker_count": len(workers),
        "from_global_restore_parallelism": (
            args.from_global_restore_parallelism
        ),
        "to_global_restore_parallelism": args.global_restore_parallelism,
        "worker_cohort_sha256": canonical_sha256(workers),
        "no_workload_started": True,
        "mysql_untouched": True,
    }
    manifest_path = args.output_config.with_suffix(".manifest.json")
    write_json(manifest_path, manifest)
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
