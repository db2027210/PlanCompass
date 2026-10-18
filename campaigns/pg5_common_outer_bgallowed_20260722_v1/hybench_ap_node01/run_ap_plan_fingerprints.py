#!/usr/bin/env python3
"""Build audited AP1--AP13 EXPLAIN(JSON) fingerprints for AP6 candidates."""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple


CONTAINER = "p24_ap6_plan_fingerprint_pg5hya201v1"
IMAGE = "sha256:6c2f8e0ad2949b4e100456f138b1184fe58cb2010cd7a9721008abb87f501458"
FORMAL_ACTIVE_PARAMS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
EXACT_KEYS = (
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
APPROXIMATE_KEYS = (
    "Node Type",
    "Parent Relationship",
    "Join Type",
    "Strategy",
    "Partial Mode",
    "Parallel Aware",
    "Workers Planned",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def run(
    command: List[str],
    *,
    check: bool = True,
    input_text: Optional[str] = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=check,
        text=True,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def docker_exists() -> bool:
    return run(["docker", "inspect", CONTAINER], check=False).returncode == 0


def restore_and_start_container(lane: Path, output: Path) -> Tuple[Path, str]:
    if docker_exists():
        raise RuntimeError(f"owned plan fingerprint container already exists: {CONTAINER}")
    source = lane / "package/templates/postgresql/hybench/pgdata"
    if not (source / "PG_VERSION").is_file():
        raise RuntimeError(f"HyBench template is unavailable: {source}")
    work_root = output / "runtime"
    pgdata = work_root / "pgdata"
    work_root.mkdir(parents=True, exist_ok=False)
    pgdata.mkdir()
    run(["cp", "-a", "--reflink=auto", str(source) + "/.", str(pgdata)])
    # The immutable template is owned by the lane user.  The PostgreSQL image
    # runs the server as uid 999, so normalize only this fresh owned copy.
    run([
        "docker", "run", "--rm", "--user", "0",
        "--volume", f"{pgdata}:/data", IMAGE,
        "sh", "-c", "chown -R 999:999 /data && chmod -R u+rwX /data",
    ])
    command = [
        "docker", "run", "--detach", "--name", CONTAINER,
        "--label", "ra.owner=ap6_plan_fingerprint_v1",
        "--label", f"ra.output={output}",
        "--volume", f"{pgdata}:/var/lib/postgresql/data",
        "--shm-size", "1073741824",
        "--health-cmd", "pg_isready -U postgres -d hybench_sf10x",
        "--health-interval", "2s", "--health-timeout", "3s", "--health-retries", "60",
        "--cpus", "5.5", "--cpuset-cpus", "16-23", "--cpuset-mems", "0",
        "--memory", "223155000", "--memory-swap", "223155000",
        "--pids-limit", "512", "--network", "bridge",
        IMAGE, "postgres",
        "-c", "port=5432",
        "-c", "listen_addresses=*",
        "-c", "default_transaction_isolation=read committed",
        "-c", "effective_cache_size=5GB",
        "-c", "fsync=on",
        "-c", "full_page_writes=on",
        "-c", "jit=off",
        "-c", "maintenance_work_mem=64MB",
        "-c", "max_connections=64",
        "-c", "max_parallel_workers=8",
        "-c", "max_parallel_workers_per_gather=2",
        "-c", "max_worker_processes=16",
        "-c", "parallel_leader_participation=on",
        "-c", "shared_buffers=4864MB",
        "-c", "synchronous_commit=on",
        "-c", "track_io_timing=on",
        "-c", "work_mem=4MB",
        "-c", "cpu_index_tuple_cost=0.005",
    ]
    container_id = run(command).stdout.strip()
    if not container_id:
        raise RuntimeError("docker run returned an empty container id")
    for _ in range(120):
        health = run(
            ["docker", "inspect", "-f", "{{.State.Health.Status}}", CONTAINER],
            check=False,
        )
        if health.returncode == 0 and health.stdout.strip() == "healthy":
            return work_root, container_id
        time.sleep(1.0)
    raise RuntimeError("plan fingerprint PostgreSQL container did not become healthy")


def cleanup_container(work_root: Optional[Path], expected_id: Optional[str]) -> Dict[str, Any]:
    evidence = {
        "container": CONTAINER,
        "expected_container_id": expected_id,
        "owned_container_removed": False,
        "runtime_removed": False,
    }
    if docker_exists():
        current_id = run(["docker", "inspect", "-f", "{{.Id}}", CONTAINER]).stdout.strip()
        if expected_id is None:
            owner = run([
                "docker", "inspect", "-f", "{{index .Config.Labels \"ra.owner\"}}", CONTAINER,
            ]).stdout.strip()
            owner_output = run([
                "docker", "inspect", "-f", "{{index .Config.Labels \"ra.output\"}}", CONTAINER,
            ]).stdout.strip()
            expected_output = str(work_root.parent) if work_root is not None else ""
            if owner != "ap6_plan_fingerprint_v1" or owner_output != expected_output:
                raise RuntimeError(
                    "refusing to remove an unverified plan fingerprint container: "
                    f"owner={owner!r} output={owner_output!r}"
                )
            expected_id = current_id
        if current_id != expected_id:
            raise RuntimeError(
                f"refusing to remove unexpected container id: {current_id} != {expected_id}"
            )
        run(["docker", "rm", "-f", CONTAINER])
        evidence["owned_container_removed"] = True
    else:
        evidence["owned_container_removed"] = True
    if work_root is not None and work_root.is_dir():
        if work_root.name != "runtime":
            raise RuntimeError(f"refusing to remove unexpected runtime path: {work_root}")
        run([
            "docker", "run", "--rm", "--user", "0",
            "--volume", f"{work_root}:/data", IMAGE,
            "sh", "-c", "chown -R 1008:1008 /data && chmod -R u+rwX /data",
        ])
        shutil.rmtree(work_root)
        evidence["runtime_removed"] = True
    return evidence


def parse_ap_queries(statement_path: Path) -> Dict[int, str]:
    source = statement_path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"^\[AP-(\d+)\]\s*\n\s*sql\s*=\s*(?:\"\"\"(.*?)\"\"\"|\"([^\"\n]*)\")",
        re.MULTILINE | re.DOTALL,
    )
    queries: Dict[int, str] = {}
    for match in pattern.finditer(source):
        query_id = int(match.group(1))
        if 1 <= query_id <= 13:
            queries[query_id] = (match.group(2) or match.group(3) or "").strip()
    if sorted(queries) != list(range(1, 14)):
        raise RuntimeError(f"AP1--AP13 SQL templates are incomplete: {sorted(queries)}")
    return queries


def psql(sql: str) -> str:
    completed = run([
        "docker", "exec", "-i", CONTAINER,
        "psql", "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1",
        "-U", "postgres", "-d", "hybench_sf10x",
    ], input_text=sql)
    return completed.stdout


def sql_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return format(value, ".17g")
    return "'" + str(value).replace("'", "''") + "'"


def query_fixture() -> Dict[str, Any]:
    row = psql(
        "SELECT "
        "(SELECT min(custid) FROM customer), "
        "(SELECT min(companyid) FROM company), "
        "(SELECT min(sourceid) FROM transfer), "
        "(SELECT min(category) FROM company WHERE category IS NOT NULL);\n"
    ).strip()
    fields = row.split("|")
    if len(fields) != 4 or any(not field for field in fields):
        raise RuntimeError(f"invalid AP plan parameter fixture: {row!r}")
    return {
        "customer_id": int(fields[0]),
        "company_id": int(fields[1]),
        "transfer_source_id": int(fields[2]),
        "company_category": fields[3],
    }


def bind_query(query_id: int, sql: str, fixture: Dict[str, Any]) -> str:
    replacements: Dict[int, List[Any]] = {
        1: [fixture["transfer_source_id"]] * 4,
        2: [fixture["customer_id"]],
        3: [fixture["customer_id"]] * 2,
        4: [fixture["customer_id"]] * 2,
        5: [fixture["company_id"]],
        6: [],
        7: [fixture["company_id"]] * 4,
        8: [],
        9: [],
        10: [],
        11: [],
        12: [fixture["company_category"]],
        13: [fixture["transfer_source_id"]],
    }
    values = replacements[query_id]
    parts = sql.split("?")
    if len(parts) - 1 != len(values):
        raise RuntimeError(
            f"AP{query_id} placeholder mismatch: sql={len(parts) - 1} fixture={len(values)}"
        )
    result = parts[0]
    for value, suffix in zip(values, parts[1:]):
        result += sql_literal(value) + suffix
    return result.rstrip().rstrip(";")


def structural_plan(node: Dict[str, Any], keys: Iterable[str]) -> Dict[str, Any]:
    result = {key: copy.deepcopy(node[key]) for key in keys if key in node}
    children = node.get("Plans")
    if isinstance(children, list):
        result["Plans"] = [structural_plan(child, keys) for child in children]
    return result


def walk_plan(node: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    yield node
    for child in node.get("Plans") or []:
        yield from walk_plan(child)


def feature_summary(plans: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    operators: Counter[str] = Counter()
    joins: Counter[str] = Counter()
    scans: Counter[str] = Counter()
    relations = set()
    indexes = set()
    parallel_queries: List[int] = []
    node_count_by_query: Dict[str, int] = {}
    root_type_by_query: Dict[str, str] = {}
    for query_id, plan in plans.items():
        nodes = list(walk_plan(plan))
        node_count_by_query[str(query_id)] = len(nodes)
        root_type_by_query[str(query_id)] = str(plan.get("Node Type"))
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
            if node.get("Parallel Aware") is True or int(node.get("Workers Planned") or 0) > 0:
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
    candidate: Dict[str, Any],
    bound_queries: Dict[int, str],
) -> Dict[str, Any]:
    rank = int(candidate["rank"])
    params = candidate.get("optimized_params") or {}
    if tuple(sorted(params)) != tuple(sorted(FORMAL_ACTIVE_PARAMS)):
        raise RuntimeError(
            f"rank {rank} params do not match formal AP6: {sorted(params)}"
        )
    script_lines = ["\\set QUIET 1"]
    for name in FORMAL_ACTIVE_PARAMS:
        value = float(params[name])
        script_lines.append(f"SET {name} = {format(value, '.17g')};")
    script_lines.append("SET cpu_index_tuple_cost = 0.005;")
    script_lines.extend(["\\set QUIET 0", "\\pset format unaligned", "\\pset tuples_only on"])
    for query_id in range(1, 14):
        script_lines.append(f"\\echo __AP_{query_id}_BEGIN__")
        script_lines.append(
            "EXPLAIN (FORMAT JSON, COSTS TRUE, VERBOSE FALSE, SETTINGS FALSE) "
            + bound_queries[query_id]
            + ";"
        )
        script_lines.append(f"\\echo __AP_{query_id}_END__")
    stdout = psql("\n".join(script_lines) + "\n")
    plans: Dict[int, Dict[str, Any]] = {}
    raw_explain_json: Dict[str, Any] = {}
    for query_id in range(1, 14):
        begin = f"__AP_{query_id}_BEGIN__"
        end = f"__AP_{query_id}_END__"
        if begin not in stdout or end not in stdout:
            raise RuntimeError(f"rank {rank} AP{query_id} EXPLAIN markers are missing")
        payload = stdout.split(begin, 1)[1].split(end, 1)[0].strip()
        parsed = json.loads(payload)
        if not isinstance(parsed, list) or len(parsed) != 1 or "Plan" not in parsed[0]:
            raise RuntimeError(f"rank {rank} AP{query_id} EXPLAIN JSON is invalid")
        raw_explain_json[str(query_id)] = parsed
        plans[query_id] = parsed[0]["Plan"]
    exact_bundle = {
        str(query_id): structural_plan(plans[query_id], EXACT_KEYS)
        for query_id in range(1, 14)
    }
    approximate_bundle = {
        str(query_id): structural_plan(plans[query_id], APPROXIMATE_KEYS)
        for query_id in range(1, 14)
    }
    exact_sha = canonical_sha(exact_bundle)
    approximate_sha = canonical_sha(approximate_bundle)
    return {
        "schema_version": 1,
        "status": "complete",
        "rank": rank,
        "subset_id": candidate.get("subset_id"),
        "optimized_params": copy.deepcopy(params),
        "fixed_excluded_parameter": {"cpu_index_tuple_cost": 0.005},
        "query_ids": list(range(1, 14)),
        "workload_plan_fingerprint": f"ap13-exact-sha256:{exact_sha}",
        "plan_similarity_cluster_id": f"ap13-exact-sha256:{exact_sha}",
        "plan_similarity_cluster_semantics": (
            "exact_AP1_AP13_physical_structure_bundle_equality_v1"
        ),
        "exact_structural_sha256": exact_sha,
        "approximate_structural_sha256": approximate_sha,
        "approximate_structural_sha256_role": "audit_only_never_suppresses_real_replay",
        "raw_explain_json_by_query": raw_explain_json,
        "exact_structural_plans": exact_bundle,
        "approximate_structural_plans": approximate_bundle,
        "near_plan_features": feature_summary(plans),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lane-root", type=Path, required=True)
    parser.add_argument("--ranking-json", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args()
    lane = args.lane_root.resolve()
    ranking_path = args.ranking_json.resolve()
    output = args.output_root.resolve()
    if args.max_workers <= 0:
        raise ValueError("--max-workers must be positive")
    output.mkdir(parents=True, exist_ok=False)
    progress_path = output / "plan_fingerprint_progress.json"
    statement_path = lane / "package/assets/hybench/stmt_postgres.toml"
    ranking = json.loads(ranking_path.read_text(encoding="utf-8"))
    candidates = ranking.get("candidate_rankings") or []
    if (
        ranking.get("subset_count") != 63
        or ranking.get("completed_count") != 63
        or ranking.get("failed_subset_count") != 0
        or len(candidates) != 63
    ):
        raise RuntimeError("ranking is not the complete formal AP6 63-subset result")
    queries = parse_ap_queries(statement_path)
    work_root: Optional[Path] = output / "runtime"
    container_id: Optional[str] = None
    completed: Dict[int, Dict[str, Any]] = {}
    failures: Dict[int, Dict[str, Any]] = {}
    cleanup: Dict[str, Any] = {}
    write_json(progress_path, {
        "schema_version": 1,
        "status": "starting_container",
        "pid": os.getpid(),
        "candidate_count": len(candidates),
        "completed_count": 0,
        "failed_count": 0,
        "max_workers": min(args.max_workers, len(candidates)),
        "ranking_path": str(ranking_path),
        "ranking_sha256": sha256(ranking_path),
        "statement_path": str(statement_path),
        "statement_sha256": sha256(statement_path),
        "started_at_epoch": time.time(),
    })
    try:
        work_root, container_id = restore_and_start_container(lane, output)
        fixture = query_fixture()
        bound_queries = {
            query_id: bind_query(query_id, queries[query_id], fixture)
            for query_id in range(1, 14)
        }
        fixture_artifact = {
            "schema_version": 1,
            "source": "deterministic_existing_database_values",
            "database": "hybench_sf10x",
            "query_ids": list(range(1, 14)),
            "fixture": fixture,
            "template_sql_sha256_by_query": {
                str(query_id): hashlib.sha256(queries[query_id].encode("utf-8")).hexdigest()
                for query_id in range(1, 14)
            },
            "bound_sql_sha256_by_query": {
                str(query_id): hashlib.sha256(bound_queries[query_id].encode("utf-8")).hexdigest()
                for query_id in range(1, 14)
            },
            "statement_path": str(statement_path),
            "statement_sha256": sha256(statement_path),
        }
        fixture_artifact["fixture_contract_sha256"] = canonical_sha(fixture_artifact)
        write_json(output / "ap13_plan_query_fixture.json", fixture_artifact)
        write_json(progress_path, {
            "schema_version": 1,
            "status": "running",
            "pid": os.getpid(),
            "candidate_count": len(candidates),
            "completed_count": 0,
            "failed_count": 0,
            "max_workers": min(args.max_workers, len(candidates)),
            "container": CONTAINER,
            "container_id": container_id,
            "ranking_sha256": sha256(ranking_path),
            "fixture_contract_sha256": fixture_artifact["fixture_contract_sha256"],
            "updated_at_epoch": time.time(),
        })
        with ThreadPoolExecutor(max_workers=min(args.max_workers, len(candidates))) as executor:
            futures = {
                executor.submit(explain_candidate, candidate, bound_queries): candidate
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
                        "status": "failed_plan_fingerprint_fail_open_unique_cluster",
                        "rank": rank,
                        "subset_id": candidate.get("subset_id"),
                        "error": f"{type(exc).__name__}: {exc}",
                        "plan_similarity_cluster_id_omitted_for_fail_open": True,
                    }
                    failures[rank] = artifact
                else:
                    completed[rank] = artifact
                write_json(output / f"rank_{rank:02d}_plan_fingerprint.json", artifact)
                write_json(progress_path, {
                    "schema_version": 1,
                    "status": "running",
                    "pid": os.getpid(),
                    "candidate_count": len(candidates),
                    "completed_count": len(completed),
                    "failed_count": len(failures),
                    "max_workers": min(args.max_workers, len(candidates)),
                    "container_id": container_id,
                    "updated_at_epoch": time.time(),
                })
    finally:
        cleanup = cleanup_container(work_root, container_id)

    annotated_candidates: List[Dict[str, Any]] = []
    for candidate in candidates:
        rank = int(candidate["rank"])
        annotated = copy.deepcopy(candidate)
        if rank in completed:
            plan = completed[rank]
            annotated["workload_plan_fingerprint"] = plan["workload_plan_fingerprint"]
            annotated["plan_similarity_cluster_id"] = plan["plan_similarity_cluster_id"]
            annotated["plan_fingerprint_artifact"] = str(
                output / f"rank_{rank:02d}_plan_fingerprint.json"
            )
            annotated["near_plan_features"] = copy.deepcopy(plan["near_plan_features"])
        else:
            annotated["plan_fingerprint_error"] = failures[rank]["error"]
            annotated["plan_fingerprint_missing_policy"] = "fail_open_unique_cluster"
        annotated_candidates.append(annotated)
    cluster_counts = Counter(
        candidate.get("plan_similarity_cluster_id")
        for candidate in annotated_candidates
        if candidate.get("plan_similarity_cluster_id")
    )
    aggregate = copy.deepcopy(ranking)
    aggregate.update({
        "schema_version": 1,
        "plan_fingerprint_status": "complete_with_fail_open_missing" if failures else "complete",
        "source_ranking_path": str(ranking_path),
        "source_ranking_sha256": sha256(ranking_path),
        "candidate_rankings": annotated_candidates,
        "plan_fingerprint_candidate_count": len(candidates),
        "plan_fingerprint_complete_count": len(completed),
        "plan_fingerprint_failed_count": len(failures),
        "plan_similarity_cluster_count": len(cluster_counts) + len(failures),
        "missing_plan_policy": "fail_open_unique_cluster",
        "cleanup": cleanup,
        "completed_at_epoch": time.time(),
    })
    aggregate_path = output / "subset_ranking_with_plan_fingerprints.json"
    write_json(aggregate_path, aggregate)
    aggregate_sha = sha256(aggregate_path)
    write_json(progress_path, {
        "schema_version": 1,
        "status": "complete_with_fail_open_missing" if failures else "complete",
        "pid": os.getpid(),
        "candidate_count": len(candidates),
        "completed_count": len(completed),
        "failed_count": len(failures),
        "plan_similarity_cluster_count": len(cluster_counts) + len(failures),
        "ranking_with_plan_fingerprints": str(aggregate_path),
        "ranking_with_plan_fingerprints_sha256": aggregate_sha,
        "cleanup": cleanup,
        "completed_at_epoch": time.time(),
    })
    print(json.dumps({
        "status": "complete_with_fail_open_missing" if failures else "complete",
        "candidate_count": len(candidates),
        "completed_count": len(completed),
        "failed_count": len(failures),
        "plan_similarity_cluster_count": len(cluster_counts) + len(failures),
        "ranking_sha256": aggregate_sha,
    }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
