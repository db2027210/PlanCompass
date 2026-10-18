#!/usr/bin/env python3
"""Build sealed exact-physical EXPLAIN fingerprints for JOB round-1 candidates."""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import psycopg2


FORMAL_ACTIVE_PARAMS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
EXACT_PHYSICAL_KEYS = (
    "Node Type",
    "Parent Relationship",
    "Join Type",
    "Relation Name",
    "Alias",
    "Index Name",
    "Scan Direction",
    "Strategy",
    "Partial Mode",
    "Parallel Aware",
    "Async Capable",
    "Workers Planned",
    "Subplan Name",
)
APPROXIMATE_AUDIT_KEYS = (
    "Node Type",
    "Parent Relationship",
    "Join Type",
    "Strategy",
    "Partial Mode",
    "Parallel Aware",
    "Workers Planned",
)
COUNTER_NAMES = (
    "replay_counter.txt",
    "formal_search_replay_counter.txt",
    "formal_search_accepted_counter.txt",
    "formal_startup_baseline_counter.txt",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def run(
    command: Sequence[str],
    *,
    check: bool = True,
    env: Optional[Mapping[str, str]] = None,
    timeout: Optional[float] = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(command),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(env) if env is not None else None,
        timeout=timeout,
    )
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"command failed exit={completed.returncode}: {list(command)!r}\n"
            f"stdout={(completed.stdout or '')[-4000:]}\n"
            f"stderr={(completed.stderr or '')[-4000:]}"
        )
    return completed


def load_round1_ranking(history_path: Path) -> Dict[str, Any]:
    matches = []
    for line_number, line in enumerate(
        history_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        event = json.loads(line)
        if (
            event.get("event") == "experience_table_all_subset_ranking"
            and int(event.get("round_index", -1)) == 1
        ):
            matches.append((line_number, event))
    if not matches:
        raise RuntimeError("history lacks a round-1 all-subset ranking event")
    line_number, ranking = matches[-1]
    candidates = ranking.get("candidate_rankings") or []
    if (
        int(ranking.get("subset_count", -1)) != 127
        or int(ranking.get("successful_subset_count", -1)) != 127
        or int(ranking.get("failed_subset_count", -1)) != 0
        or not isinstance(candidates, list)
        or not candidates
    ):
        raise RuntimeError("round-1 ranking is not the complete JOB 127-subset result")
    result = copy.deepcopy(ranking)
    result["source_history_line_number"] = line_number
    return result


def load_fixed_queries(lane: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    manifest_path = lane / "manifest/workload_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    query_sequence = list(manifest.get("query_sequence") or [])
    query_weights = list(manifest.get("query_weights") or [])
    expected_hashes = manifest.get("query_sha256") or {}
    if (
        manifest.get("formal_name") != "JOB-8-mixed-26a-weighted"
        or len(query_sequence) != 8
        or len(query_weights) != len(query_sequence)
    ):
        raise RuntimeError("unexpected frozen JOB workload manifest")
    queries: List[Dict[str, Any]] = []
    for position, (filename, weight) in enumerate(
        zip(query_sequence, query_weights),
        start=1,
    ):
        path = lane / "assets/job_sql" / str(filename)
        actual_sha = sha256(path)
        if actual_sha != expected_hashes.get(filename):
            raise RuntimeError(f"frozen JOB SQL hash mismatch: {filename}")
        sql = path.read_text(encoding="utf-8").strip().rstrip(";")
        if not sql:
            raise RuntimeError(f"frozen JOB SQL is empty: {filename}")
        queries.append(
            {
                "position": position,
                "query_id": Path(filename).stem,
                "filename": filename,
                "weight": int(weight),
                "sql_sha256": actual_sha,
                "sql": sql,
            }
        )
    contract = {
        "schema_version": 1,
        "formal_name": manifest["formal_name"],
        "database": manifest["database"],
        "query_sequence": query_sequence,
        "query_weights": query_weights,
        "query_sha256": {item["filename"]: item["sql_sha256"] for item in queries},
        "workload_manifest_path": str(manifest_path),
        "workload_manifest_sha256": sha256(manifest_path),
        "exact_cluster_semantics": (
            "equality_of_ordered_JOB8_canonical_physical_plan_bundle_v1"
        ),
        "cost_cardinality_runtime_fields_in_exact_cluster": False,
        "approximate_features_role": "audit_only_never_clusters_or_suppresses",
    }
    contract["fixed_sql_contract_sha256"] = canonical_sha(contract)
    return queries, contract


def structural_plan(node: Mapping[str, Any], keys: Iterable[str]) -> Dict[str, Any]:
    result = {key: copy.deepcopy(node[key]) for key in keys if key in node}
    children = node.get("Plans")
    if isinstance(children, list):
        result["Plans"] = [structural_plan(child, keys) for child in children]
    return result


def walk_plan(node: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    yield node
    for child in node.get("Plans") or []:
        yield from walk_plan(child)


def feature_summary(plans: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    operators: Counter[str] = Counter()
    joins: Counter[str] = Counter()
    scans: Counter[str] = Counter()
    relations = set()
    indexes = set()
    parallel_queries: List[str] = []
    node_count_by_query: Dict[str, int] = {}
    root_type_by_query: Dict[str, str] = {}
    for query_id, plan in plans.items():
        nodes = list(walk_plan(plan))
        node_count_by_query[query_id] = len(nodes)
        root_type_by_query[query_id] = str(plan.get("Node Type") or "unknown")
        query_parallel = False
        for node in nodes:
            node_type = str(node.get("Node Type") or "unknown")
            operators[node_type] += 1
            if "Join" in node_type or node.get("Join Type") is not None:
                joins[str(node.get("Join Type") or node_type)] += 1
            if "Scan" in node_type:
                scans[node_type] += 1
            if node.get("Relation Name"):
                relations.add(str(node["Relation Name"]))
            if node.get("Index Name"):
                indexes.add(str(node["Index Name"]))
            if node.get("Parallel Aware") is True or int(
                node.get("Workers Planned") or 0
            ) > 0:
                query_parallel = True
        if query_parallel:
            parallel_queries.append(query_id)
    return {
        "operator_histogram": dict(sorted(operators.items())),
        "join_histogram": dict(sorted(joins.items())),
        "scan_histogram": dict(sorted(scans.items())),
        "relations": sorted(relations),
        "indexes": sorted(indexes),
        "parallel_query_ids": parallel_queries,
        "node_count_by_query": node_count_by_query,
        "root_type_by_query": root_type_by_query,
    }


def explain_candidate(
    candidate: Mapping[str, Any],
    queries: Sequence[Mapping[str, Any]],
    fixed_sql_contract_sha256: str,
) -> Dict[str, Any]:
    rank = int(candidate["rank"])
    params = candidate.get("optimized_params") or {}
    if tuple(sorted(params)) != tuple(sorted(FORMAL_ACTIVE_PARAMS)):
        raise RuntimeError(
            f"rank {rank} params do not match formal JOB7: {sorted(params)}"
        )
    raw_explain: Dict[str, Any] = {}
    plans: Dict[str, Mapping[str, Any]] = {}
    with psycopg2.connect(
        host="127.0.0.1",
        port=64309,
        user="postgres",
        password="CHANGE_ME",
        dbname="imdb_job",
        application_name=f"job-plan-fingerprint-rank-{rank}",
    ) as connection:
        connection.autocommit = False
        with connection.cursor() as cursor:
            for name in FORMAL_ACTIVE_PARAMS:
                cursor.execute(f"SET LOCAL {name} = %s", (format(float(params[name]), ".17g"),))
            for query in queries:
                query_id = str(query["query_id"])
                cursor.execute(
                    "EXPLAIN (FORMAT JSON, COSTS TRUE, VERBOSE FALSE, "
                    "SETTINGS FALSE, SUMMARY FALSE) " + str(query["sql"])
                )
                parsed = cursor.fetchone()[0]
                if (
                    not isinstance(parsed, list)
                    or len(parsed) != 1
                    or not isinstance(parsed[0], dict)
                    or "Plan" not in parsed[0]
                ):
                    raise RuntimeError(f"rank {rank} query {query_id} EXPLAIN is invalid")
                raw_explain[query_id] = parsed
                plans[query_id] = parsed[0]["Plan"]
        connection.rollback()
    exact_bundle = [
        {
            "position": int(query["position"]),
            "query_id": str(query["query_id"]),
            "sql_sha256": str(query["sql_sha256"]),
            "physical_plan": structural_plan(
                plans[str(query["query_id"])],
                EXACT_PHYSICAL_KEYS,
            ),
        }
        for query in queries
    ]
    approximate_bundle = [
        {
            "position": int(query["position"]),
            "query_id": str(query["query_id"]),
            "sql_sha256": str(query["sql_sha256"]),
            "approximate_plan": structural_plan(
                plans[str(query["query_id"])],
                APPROXIMATE_AUDIT_KEYS,
            ),
        }
        for query in queries
    ]
    exact_sha = canonical_sha(exact_bundle)
    approximate_sha = canonical_sha(approximate_bundle)
    return {
        "schema_version": 1,
        "status": "complete",
        "rank": rank,
        "subset_id": candidate.get("subset_id"),
        "optimized_params": copy.deepcopy(params),
        "fixed_sql_contract_sha256": fixed_sql_contract_sha256,
        "workload_plan_fingerprint": f"job8-exact-sha256:{exact_sha}",
        "plan_similarity_cluster_id": f"job8-exact-sha256:{exact_sha}",
        "plan_similarity_cluster_semantics": (
            "exact_ordered_JOB8_physical_structure_bundle_equality_v1"
        ),
        "exact_physical_bundle_sha256": exact_sha,
        "approximate_structural_sha256": approximate_sha,
        "approximate_structural_sha256_role": (
            "audit_only_never_clusters_or_suppresses_real_replay"
        ),
        "raw_explain_json_by_query": raw_explain,
        "exact_physical_plan_bundle": exact_bundle,
        "approximate_plan_bundle": approximate_bundle,
        "near_plan_features": feature_summary(plans),
    }


def lane_state(root: Path) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for filename in COUNTER_NAMES:
        path = root / filename
        result[filename] = {
            "value": int(path.read_text(encoding="utf-8").strip()),
            "sha256": sha256(path),
        }
    marker = root / "pause_before_next_formal_claim.json"
    result["pause_marker"] = {
        "path": str(marker),
        "sha256": sha256(marker),
        "value": json.loads(marker.read_text(encoding="utf-8")),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lane-root", type=Path, required=True)
    parser.add_argument("--attempt-root", type=Path, required=True)
    parser.add_argument("--history-jsonl", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args()
    lane = args.lane_root.resolve()
    attempt = args.attempt_root.resolve()
    history_path = args.history_jsonl.resolve()
    output = args.output_root.resolve()
    if args.max_workers <= 0:
        raise ValueError("--max-workers must be positive")
    output.mkdir(parents=True, exist_ok=False)
    ranking = load_round1_ranking(history_path)
    candidates = list(ranking.get("candidate_rankings") or [])
    queries, fixed_sql_contract = load_fixed_queries(lane)
    source_ranking_path = output / "source_round1_ranking_event.json"
    fixed_sql_contract_path = output / "fixed_sql_contract.json"
    write_json(source_ranking_path, ranking)
    write_json(fixed_sql_contract_path, fixed_sql_contract)
    progress_path = output / "plan_fingerprint_progress.json"
    lock_path = lane / "lease/job_lane.lock"
    setup_script = lane / "launch_outer/setup_job_outer_control.sh"
    cleanup_script = lane / "launch_outer/cleanup_job_outer_control.sh"
    control_root = output / "runtime_control"
    control_evidence = output / "control_evidence"
    completed: Dict[int, Dict[str, Any]] = {}
    failures: Dict[int, Dict[str, Any]] = {}
    cleanup: Dict[str, Any] = {}
    state_before = lane_state(attempt)
    env = os.environ.copy()
    env["RA_PG_CONTROL_ROOT"] = str(control_root)
    env["RA_PG_OUTPUT_ROOT"] = str(control_evidence)
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("JOB lane lock is busy; refusing plan capture") from exc
        if run(["docker", "inspect", "ra_pg5_job_node02_v1_outercontrol"], check=False).returncode == 0:
            raise RuntimeError("JOB outer control container already exists")
        write_json(
            progress_path,
            {
                "schema_version": 1,
                "status": "starting_isolated_control",
                "candidate_count": len(candidates),
                "completed_count": 0,
                "failed_count": 0,
                "history_path": str(history_path),
                "history_sha256": sha256(history_path),
                "source_ranking_sha256": sha256(source_ranking_path),
                "fixed_sql_contract_sha256": fixed_sql_contract[
                    "fixed_sql_contract_sha256"
                ],
                "lane_state_before": state_before,
                "started_at_epoch": time.time(),
            },
        )
        container_id: Optional[str] = None
        try:
            run([str(setup_script)], env=env, timeout=1800)
            container_id = run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.Id}}",
                    "ra_pg5_job_node02_v1_outercontrol",
                ]
            ).stdout.strip()
            with ThreadPoolExecutor(
                max_workers=min(args.max_workers, len(candidates))
            ) as executor:
                futures = {
                    executor.submit(
                        explain_candidate,
                        candidate,
                        queries,
                        fixed_sql_contract["fixed_sql_contract_sha256"],
                    ): candidate
                    for candidate in candidates
                }
                for future in as_completed(futures):
                    candidate = futures[future]
                    rank = int(candidate["rank"])
                    try:
                        artifact = future.result()
                    except Exception as exc:
                        artifact = {
                            "schema_version": 1,
                            "status": (
                                "failed_plan_fingerprint_fail_open_unique_cluster"
                            ),
                            "rank": rank,
                            "subset_id": candidate.get("subset_id"),
                            "error": f"{type(exc).__name__}: {exc}",
                            "plan_similarity_cluster_id_omitted_for_fail_open": True,
                        }
                        failures[rank] = artifact
                    else:
                        completed[rank] = artifact
                    write_json(
                        output / f"rank_{rank:03d}_plan_fingerprint.json",
                        artifact,
                    )
                    progress_path.unlink()
                    write_json(
                        progress_path,
                        {
                            "schema_version": 1,
                            "status": "running",
                            "candidate_count": len(candidates),
                            "completed_count": len(completed),
                            "failed_count": len(failures),
                            "container_id": container_id,
                            "updated_at_epoch": time.time(),
                        },
                    )
        finally:
            inspect = run(
                ["docker", "inspect", "ra_pg5_job_node02_v1_outercontrol"],
                check=False,
            )
            if inspect.returncode == 0:
                current = json.loads(inspect.stdout)[0]
                current_id = str(current.get("Id") or "")
                labels = (current.get("Config") or {}).get("Labels") or {}
                if (
                    container_id is not None
                    and current_id != container_id
                ) or labels.get("resource_adaptive.role") != "outer_plan_control":
                    raise RuntimeError("refusing to clean an unexpected control container")
            run([str(cleanup_script)], env=env, timeout=1800)
            cleanup = {
                "owned_container_removed": run(
                    ["docker", "inspect", "ra_pg5_job_node02_v1_outercontrol"],
                    check=False,
                ).returncode
                != 0,
                "owned_runtime_removed": not control_root.exists(),
            }
        state_after = lane_state(attempt)
        if state_after != state_before:
            raise RuntimeError("formal JOB counters or pause marker changed during EXPLAIN")

    annotated_candidates: List[Dict[str, Any]] = []
    for candidate in candidates:
        rank = int(candidate["rank"])
        annotated = copy.deepcopy(candidate)
        if rank in completed:
            plan = completed[rank]
            annotated["workload_plan_fingerprint"] = plan[
                "workload_plan_fingerprint"
            ]
            annotated["plan_similarity_cluster_id"] = plan[
                "plan_similarity_cluster_id"
            ]
            annotated["plan_fingerprint_artifact"] = str(
                output / f"rank_{rank:03d}_plan_fingerprint.json"
            )
            annotated["near_plan_features"] = copy.deepcopy(
                plan["near_plan_features"]
            )
        else:
            annotated["plan_fingerprint_error"] = failures[rank]["error"]
            annotated["plan_fingerprint_missing_policy"] = (
                "fail_open_unique_cluster"
            )
        annotated_candidates.append(annotated)
    cluster_counts = Counter(
        item.get("plan_similarity_cluster_id")
        for item in annotated_candidates
        if item.get("plan_similarity_cluster_id")
    )
    aggregate = copy.deepcopy(ranking)
    aggregate.update(
        {
            "schema_version": 1,
            "plan_fingerprint_status": (
                "complete_with_fail_open_missing" if failures else "complete"
            ),
            "source_history_path": str(history_path),
            "source_history_sha256": sha256(history_path),
            "source_ranking_path": str(source_ranking_path),
            "source_ranking_sha256": sha256(source_ranking_path),
            "fixed_sql_contract_path": str(fixed_sql_contract_path),
            "fixed_sql_contract_sha256": fixed_sql_contract[
                "fixed_sql_contract_sha256"
            ],
            "candidate_rankings": annotated_candidates,
            "plan_fingerprint_candidate_count": len(candidates),
            "plan_fingerprint_complete_count": len(completed),
            "plan_fingerprint_failed_count": len(failures),
            "plan_similarity_cluster_count": len(cluster_counts) + len(failures),
            "plan_cluster_basis": "exact_physical_bundle_only",
            "approximate_features_role": "audit_only",
            "missing_plan_policy": "fail_open_unique_cluster",
            "lane_state_before": state_before,
            "lane_state_after": state_after,
            "formal_replay_accounting_consumed": False,
            "cleanup": cleanup,
            "completed_at_epoch": time.time(),
        }
    )
    aggregate_path = output / "round1_ranking_with_plan_fingerprints.json"
    write_json(aggregate_path, aggregate)
    progress_path.unlink()
    write_json(
        progress_path,
        {
            "schema_version": 1,
            "status": "complete_with_fail_open_missing" if failures else "complete",
            "candidate_count": len(candidates),
            "completed_count": len(completed),
            "failed_count": len(failures),
            "plan_similarity_cluster_count": len(cluster_counts) + len(failures),
            "ranking_with_plan_fingerprints": str(aggregate_path),
            "ranking_with_plan_fingerprints_sha256": sha256(aggregate_path),
            "cleanup": cleanup,
            "completed_at_epoch": time.time(),
        },
    )
    print(
        json.dumps(
            {
                "status": "complete_with_fail_open_missing" if failures else "complete",
                "candidate_count": len(candidates),
                "completed_count": len(completed),
                "failed_count": len(failures),
                "plan_similarity_cluster_count": len(cluster_counts) + len(failures),
                "ranking_path": str(aggregate_path),
                "ranking_sha256": sha256(aggregate_path),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
