#!/usr/bin/env python3
"""CPU fair v82 batched new-operator Phase A.

This script owns only PostgreSQL worker cpu_mem_w1 on port 56433. It maps the
predeclared shared seven-cost seed menu, globally collapses identical plan
signatures, and runs EXPLAIN ANALYZE only for intended structure transitions.
It never starts HyBench and never changes persistent PostgreSQL settings.
"""
from __future__ import annotations

import hashlib
import json
import os
import statistics
import subprocess
import time
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ROOT = Path("/home/anonymous/program/resource_adaptive")
RUN_DIR = Path("/tmp/hyxp_cpu_v82_newop_phase_a_20260715")
PLAN_DIR = RUN_DIR / "plans"
ANALYZE_DIR = RUN_DIR / "analyze"
EVENTS_PATH = RUN_DIR / "phase_a_events.jsonl"
MAP_PATH = RUN_DIR / "phase_a_map.json"
ANALYZE_PATH = RUN_DIR / "phase_a_analyze.json"
DECISION_PATH = RUN_DIR / "phase_a_eligibility_decision_v82_cpu_newop.json"
REPORT_DECISION_PATH = (
    ROOT / "report/bonect/phase_a_eligibility_decision_v82_cpu_newop.json"
)

HOST = "127.0.0.1"
PORT = 56433
USER = "postgres"
DB = "hybench_sf10x"
FALLBACK_DB_QPS = 183.02089028620412
SPEED_RATIO_MAX = 0.85

COST_KEYS = [
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
]

DEFAULTS: Dict[str, float] = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}


def point(**changes: float) -> Dict[str, float]:
    value = dict(DEFAULTS)
    value.update(changes)
    return value


# Exactly the 32 predeclared seed names from the gap authority. highrpc_ptc3
# carries the proven index-prefer relation (spc=25, rpc=1) and the requested
# ptc=3 combination; ctc10_spc1 is retained and later exact/signature-collapsed.
RAW_VECTORS: "OrderedDict[str, Dict[str, float]]" = OrderedDict(
    [
        ("default", point()),
        ("rpc8", point(random_page_cost=8.0)),
        ("rpc16", point(random_page_cost=16.0)),
        ("rpc1", point(random_page_cost=1.0)),
        ("citc02", point(cpu_index_tuple_cost=0.02)),
        ("citc05", point(cpu_index_tuple_cost=0.05)),
        ("citc10", point(cpu_index_tuple_cost=0.1)),
        ("coc01", point(cpu_operator_cost=0.01)),
        ("coc025", point(cpu_operator_cost=0.025)),
        ("coc05", point(cpu_operator_cost=0.05)),
        ("ctc02", point(cpu_tuple_cost=0.02)),
        ("ctc05", point(cpu_tuple_cost=0.05)),
        ("ctc1", point(cpu_tuple_cost=1.0)),
        ("ctc2", point(cpu_tuple_cost=2.0)),
        ("ctc5", point(cpu_tuple_cost=5.0)),
        ("ctc10", point(cpu_tuple_cost=10.0)),
        ("ptc1", point(parallel_tuple_cost=1.0)),
        ("ptc2", point(parallel_tuple_cost=2.0)),
        ("ptc3", point(parallel_tuple_cost=3.0)),
        ("psc5k", point(parallel_setup_cost=5000.0)),
        ("psc10k", point(parallel_setup_cost=10000.0)),
        (
            "rpc8_citc05",
            point(random_page_cost=8.0, cpu_index_tuple_cost=0.05),
        ),
        (
            "rpc16_citc05",
            point(random_page_cost=16.0, cpu_index_tuple_cost=0.05),
        ),
        (
            "rpc8_ptc3",
            point(random_page_cost=8.0, parallel_tuple_cost=3.0),
        ),
        (
            "citc05_coc025",
            point(cpu_index_tuple_cost=0.05, cpu_operator_cost=0.025),
        ),
        (
            "citc10_coc05",
            point(cpu_index_tuple_cost=0.1, cpu_operator_cost=0.05),
        ),
        (
            "ctc1_ptc3",
            point(cpu_tuple_cost=1.0, parallel_tuple_cost=3.0),
        ),
        (
            "ctc2_rpc8",
            point(cpu_tuple_cost=2.0, random_page_cost=8.0),
        ),
        (
            "ctc5_coc025",
            point(cpu_tuple_cost=5.0, cpu_operator_cost=0.025),
        ),
        ("ctc10_spc1", point(seq_page_cost=1.0, cpu_tuple_cost=10.0)),
        (
            "ptc3_psc5k",
            point(parallel_tuple_cost=3.0, parallel_setup_cost=5000.0),
        ),
        (
            "highrpc_ptc3",
            point(
                seq_page_cost=25.0,
                random_page_cost=1.0,
                parallel_tuple_cost=3.0,
            ),
        ),
    ]
)

SHAPES: "OrderedDict[str, Dict[str, Any]]" = OrderedDict(
    [
        (
            "c01_stock_merge_semi",
            {
                "family": "C01",
                "rank": 1,
                "primary": True,
                "sql_path": "shell/hybenchxp_cpu_v82_c01_stock_merge_semi.sql",
            },
        ),
        (
            "c01_age_nl_semi",
            {
                "family": "C01",
                "rank": 1,
                "primary": True,
                "sql_path": "shell/hybenchxp_cpu_v82_c01_age_nl_semi.sql",
            },
        ),
        (
            "c06_bounded_topn",
            {
                "family": "C06",
                "rank": 2,
                "primary": True,
                "sql_path": "shell/hybenchxp_cpu_v82_c06_bounded_topn.sql",
            },
        ),
        (
            "c03_groupagg_nonmj",
            {
                "family": "C03",
                "rank": 3,
                "primary": True,
                "sql_path": "shell/hybenchxp_cpu_v82_c03_groupagg_nonmj.sql",
            },
        ),
        (
            "c02_windowagg",
            {
                "family": "C02",
                "rank": 4,
                "primary": True,
                "sql_path": "shell/hybenchxp_cpu_v82_c02_windowagg.sql",
            },
        ),
        (
            "co_role_hashagg",
            {
                "family": "CO_ROLE",
                "rank": 99,
                "primary": False,
                "sql_path": "shell/hybenchxp_cpu_v82_corole_hashagg.sql",
            },
        ),
    ]
)


def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S %z")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def emit(event: str, **fields: Any) -> None:
    record = {"event": event, "timestamp": now_text(), **fields}
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with EVENTS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    details = " ".join(f"{key}={value}" for key, value in fields.items() if key in {"vector", "shape", "status", "ratio"})
    print(f"[v82] {event} {details}".rstrip(), flush=True)


def load_sql(relative_path: str) -> str:
    lines: List[str] = []
    for line in (ROOT / relative_path).read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("--") or not line.strip():
            continue
        lines.append(line)
    return "\n".join(lines).strip().rstrip(";")


def validate_vectors() -> None:
    if len(RAW_VECTORS) != 32:
        raise RuntimeError(f"expected 32 raw vectors, got {len(RAW_VECTORS)}")
    for name, params in RAW_VECTORS.items():
        if set(params) != set(COST_KEYS):
            raise RuntimeError(f"{name} does not contain exactly seven cost keys")
        if any(float(value) <= 0 for value in params.values()):
            raise RuntimeError(f"{name} contains a non-positive cost value")


def psql_json(params: Dict[str, float], sql: str, *, analyze: bool) -> Dict[str, Any]:
    set_sql = ";\n".join(f"SET LOCAL {key} = {params[key]}" for key in COST_KEYS)
    options = "ANALYZE, BUFFERS, WAL, TIMING OFF, SUMMARY TRUE, FORMAT JSON" if analyze else "COSTS TRUE, SETTINGS TRUE, FORMAT JSON"
    payload = f"BEGIN;\n{set_sql};\nEXPLAIN ({options})\n{sql};\nROLLBACK;\n"
    env = os.environ.copy()
    env["PGPASSWORD"] = "postgres"
    cmd = [
        "psql",
        "-X",
        "-q",
        "-A",
        "-t",
        "-v",
        "ON_ERROR_STOP=1",
        "-h",
        HOST,
        "-p",
        str(PORT),
        "-U",
        USER,
        "-d",
        DB,
        "-c",
        payload,
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[:1200] or f"psql rc={result.returncode}")
    parsed = json.loads(result.stdout.strip())
    if not isinstance(parsed, list) or not parsed:
        raise RuntimeError("EXPLAIN JSON did not return a nonempty array")
    return parsed[0]


def answer_digest(params: Dict[str, float], sql: str) -> Dict[str, Any]:
    set_sql = ";\n".join(f"SET LOCAL {key} = {params[key]}" for key in COST_KEYS)
    digest_sql = (
        "SELECT count(*), "
        "coalesce(md5(string_agg(row_to_json(v82_q)::text, E'\\n' "
        "ORDER BY row_to_json(v82_q)::text)), md5('')) "
        f"FROM ({sql}) AS v82_q"
    )
    payload = f"BEGIN;\n{set_sql};\n{digest_sql};\nROLLBACK;\n"
    env = os.environ.copy()
    env["PGPASSWORD"] = "postgres"
    cmd = [
        "psql",
        "-X",
        "-q",
        "-A",
        "-t",
        "-F",
        "|",
        "-v",
        "ON_ERROR_STOP=1",
        "-h",
        HOST,
        "-p",
        str(PORT),
        "-U",
        USER,
        "-d",
        DB,
        "-c",
        payload,
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[:1200] or f"digest psql rc={result.returncode}")
    output = result.stdout.strip().splitlines()
    if not output or "|" not in output[-1]:
        raise RuntimeError(f"unexpected digest output: {result.stdout[:300]}")
    count_text, digest = output[-1].split("|", 1)
    return {"row_count": int(count_text), "sha256": digest.strip()}


def walk_plan(node: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    yield node
    for child in node.get("Plans", []) or []:
        yield from walk_plan(child)


STRUCTURAL_KEYS = [
    "Node Type",
    "Parent Relationship",
    "Join Type",
    "Strategy",
    "Partial Mode",
    "Relation Name",
    "Index Name",
    "Scan Direction",
    "Parallel Aware",
    "Workers Planned",
    "Sort Key",
    "Group Key",
    "Presorted Key",
    "Hash Cond",
    "Merge Cond",
]


def structural_tree(node: Dict[str, Any]) -> Dict[str, Any]:
    result = {key: node[key] for key in STRUCTURAL_KEYS if key in node}
    children = [structural_tree(child) for child in node.get("Plans", []) or []]
    if children:
        result["Plans"] = children
    return result


def fingerprint(node: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    tree = structural_tree(node)
    payload = json.dumps(tree, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest(), tree


def node_label(node: Dict[str, Any]) -> str:
    label = str(node.get("Node Type", ""))
    join_type = node.get("Join Type")
    if join_type:
        label = f"{label} [{join_type}]"
    if node.get("Parallel Aware"):
        label = f"Parallel {label}"
    return label


MEMORY_NODE_WEIGHTS = {
    "HashAggregate": 3.0,
    "Hash": 1.0,
    "Hash Join": 2.0,
    "Sort": 2.0,
    "Incremental Sort": 1.5,
    "Materialize": 3.0,
    "Memoize": 2.0,
}


def summarize_plan(response: Dict[str, Any]) -> Dict[str, Any]:
    root = response["Plan"]
    nodes = list(walk_plan(root))
    fp, tree = fingerprint(root)
    node_types = [str(node.get("Node Type", "")) for node in nodes]
    labels = [node_label(node) for node in nodes]
    memory_score = sum(MEMORY_NODE_WEIGHTS.get(node_type, 0.0) for node_type in node_types)
    seq_count = sum(1 for node_type in node_types if node_type in {"Seq Scan", "Parallel Seq Scan"})
    index_count = sum(
        1
        for node_type in node_types
        if node_type in {"Index Scan", "Index Only Scan", "Bitmap Heap Scan", "Bitmap Index Scan"}
    )
    return {
        "fingerprint": fp,
        "tree": tree,
        "node_types": node_types,
        "node_labels": labels,
        "node_counts": dict(Counter(node_types)),
        "workers_planned_max": max((int(node.get("Workers Planned", 0)) for node in nodes), default=0),
        "parallel": any(bool(node.get("Parallel Aware")) for node in nodes)
        or any(node_type in {"Gather", "Gather Merge"} for node_type in node_types),
        "memory_structure_score": memory_score,
        "seq_scan_count": seq_count,
        "index_or_bitmap_count": index_count,
        "total_cost": root.get("Total Cost"),
        "plan_rows": root.get("Plan Rows"),
    }


def has_join(summary: Dict[str, Any], node_type: str, join_type: str) -> bool:
    for node in summary["tree_nodes"]:
        if node.get("Node Type") == node_type and node.get("Join Type") == join_type:
            return True
    return False


def flat_tree_nodes(tree: Dict[str, Any]) -> List[Dict[str, Any]]:
    nodes = [tree]
    for child in tree.get("Plans", []) or []:
        nodes.extend(flat_tree_nodes(child))
    return nodes


def finish_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    summary["tree_nodes"] = flat_tree_nodes(summary["tree"])
    return summary


def intended_transition(
    shape: str,
    base: Dict[str, Any],
    candidate: Dict[str, Any],
) -> Dict[str, Any]:
    family = SHAPES[shape]["family"]
    changed = base["fingerprint"] != candidate["fingerprint"]
    ok = False
    reason = "no_intended_transition"
    if family == "C01":
        base_semi = any(
            node.get("Join Type") == "Semi"
            and node.get("Node Type") in {"Nested Loop", "Hash Join"}
            for node in base["tree_nodes"]
        )
        cand_merge_semi = has_join(candidate, "Merge Join", "Semi")
        ok = changed and base_semi and cand_merge_semi
        reason = "semi_to_merge_semi" if ok else "missing_merge_semi_transition"
    elif family == "C06":
        base_full_sort = "Sort" in base["node_types"] and "Incremental Sort" not in base["node_types"]
        cand_incremental = "Incremental Sort" in candidate["node_types"]
        cand_ordered_limit = (
            "Limit" in candidate["node_types"]
            and "Sort" not in candidate["node_types"]
            and candidate["index_or_bitmap_count"] > 0
        )
        ok = changed and base_full_sort and (cand_incremental or cand_ordered_limit)
        reason = "full_sort_to_incremental_or_ordered_limit" if ok else "missing_topn_transition"
    elif family == "C03":
        base_required = (
            "Gather Merge" in base["node_types"]
            and "HashAggregate" in base["node_types"]
            and "GroupAggregate" in base["node_types"]
        )
        candidate_serial_group = (
            "GroupAggregate" in candidate["node_types"]
            and "Gather" not in candidate["node_types"]
            and "Gather Merge" not in candidate["node_types"]
        )
        ok = changed and base_required and candidate_serial_group
        reason = "gather_merge_partialha_to_serial_groupagg" if ok else "missing_nonparallel_groupagg_transition"
    elif family == "C02":
        base_required = "WindowAgg" in base["node_types"] and "Sort" in base["node_types"]
        cand_reduced_sort = (
            "WindowAgg" in candidate["node_types"]
            and (
                "Sort" not in candidate["node_types"]
                or "Incremental Sort" in candidate["node_types"]
            )
        )
        ok = changed and base_required and cand_reduced_sort
        reason = "window_sort_reduced" if ok else "windowagg_sort_invariant"
    return {"ok": ok, "reason": reason, "fingerprint_changed": changed}


def closed_collision(shape: str, candidate: Dict[str, Any]) -> Dict[str, Any]:
    family = SHAPES[shape]["family"]
    nodes = candidate["tree_nodes"]
    has_inner_merge = any(
        node.get("Node Type") == "Merge Join" and node.get("Join Type") not in {"Semi", "Anti"}
        for node in nodes
    )
    if family == "C03" and any(node.get("Node Type") == "Merge Join" for node in nodes):
        return {"collision": True, "reason": "closed_ser_MJ_family"}
    if family != "C01" and has_inner_merge:
        return {"collision": True, "reason": "closed_inner_mergejoin_family"}
    lowseq_like = (
        not candidate["parallel"]
        and "Nested Loop" in candidate["node_types"]
        and "HashAggregate" in candidate["node_types"]
        and candidate["index_or_bitmap_count"] > 0
        and family not in {"C01", "C06"}
    )
    if lowseq_like:
        return {"collision": True, "reason": "closed_lowseq_nl_index_bitmap_family"}
    if family == "C06" and "Incremental Sort" in candidate["node_types"] and "GroupAggregate" in candidate["node_types"]:
        return {"collision": True, "reason": "closed_memory_incsort_groupagg_family"}
    return {"collision": False, "reason": "distinct_from_closed_families"}


def co_role_gate(base: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    gained_memory_nodes = sorted(
        node_type
        for node_type, weight in MEMORY_NODE_WEIGHTS.items()
        if weight > 0
        and candidate["node_counts"].get(node_type, 0) > base["node_counts"].get(node_type, 0)
    )
    memory_up = candidate["memory_structure_score"] > base["memory_structure_score"] and bool(gained_memory_nodes)
    io_up = candidate["seq_scan_count"] > base["seq_scan_count"]
    return {
        "ok": memory_up or io_up,
        "memory_up_structure": memory_up,
        "io_up_structure": io_up,
        "memory_score_base": base["memory_structure_score"],
        "memory_score_candidate": candidate["memory_structure_score"],
        "seq_scan_count_base": base["seq_scan_count"],
        "seq_scan_count_candidate": candidate["seq_scan_count"],
        "gained_memory_nodes": gained_memory_nodes,
        "note": "structural co-role gate only; runtime Memory/IO is not claimed",
    }


def execution_metrics(response: Dict[str, Any]) -> Dict[str, Any]:
    plan = response["Plan"]
    return {
        "execution_ms": response.get("Execution Time"),
        "planning_ms": response.get("Planning Time"),
        "actual_rows": plan.get("Actual Rows"),
        "shared_hit_blocks": plan.get("Shared Hit Blocks", 0),
        "shared_read_blocks": plan.get("Shared Read Blocks", 0),
        "temp_read_blocks": plan.get("Temp Read Blocks", 0),
        "temp_written_blocks": plan.get("Temp Written Blocks", 0),
    }


def timed_pair(
    shape: str,
    vector_name: str,
    vector: Dict[str, float],
    sql: str,
) -> Dict[str, Any]:
    pair_dir = ANALYZE_DIR / f"{shape}__{vector_name}"
    pair_dir.mkdir(parents=True, exist_ok=True)
    emit("analyze_pair_start", shape=shape, vector=vector_name, status="running")

    # Equal warmups, followed by three alternating measured pairs.
    psql_json(DEFAULTS, sql, analyze=True)
    psql_json(vector, sql, analyze=True)
    base_runs: List[Dict[str, Any]] = []
    candidate_runs: List[Dict[str, Any]] = []
    orders = [("base", "candidate"), ("candidate", "base"), ("base", "candidate")]
    for pair_index, order in enumerate(orders, 1):
        for role in order:
            params = DEFAULTS if role == "base" else vector
            response = psql_json(params, sql, analyze=True)
            metrics = execution_metrics(response)
            record = {
                "pair_index": pair_index,
                "order": list(order),
                "role": role,
                **metrics,
            }
            if role == "base":
                base_runs.append(record)
            else:
                candidate_runs.append(record)
            write_json(pair_dir / f"pair{pair_index}_{role}.json", response)

    base_values = [float(run["execution_ms"]) for run in base_runs]
    candidate_values = [float(run["execution_ms"]) for run in candidate_runs]
    base_median = statistics.median(base_values)
    candidate_median = statistics.median(candidate_values)
    ratio = candidate_median / base_median if base_median > 0 else None
    base_digest = answer_digest(DEFAULTS, sql)
    candidate_digest = answer_digest(vector, sql)
    invariant = base_digest == candidate_digest
    result = {
        "shape": shape,
        "family": SHAPES[shape]["family"],
        "vector_name": vector_name,
        "vector": vector,
        "base_runs": base_runs,
        "candidate_runs": candidate_runs,
        "base_median_ms": base_median,
        "candidate_median_ms": candidate_median,
        "elapsed_ratio": ratio,
        "speedup_pct": (1.0 - ratio) * 100.0 if ratio is not None else None,
        "speed_gate_ok": ratio is not None and ratio <= SPEED_RATIO_MAX,
        "answer_digest_base": base_digest,
        "answer_digest_candidate": candidate_digest,
        "answer_invariant_ok": invariant,
    }
    write_json(pair_dir / "pair_summary.json", result)
    emit("analyze_pair_stop", shape=shape, vector=vector_name, status="ok", ratio=ratio)
    return result


def public_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in summary.items() if key != "tree_nodes"}


def main() -> None:
    started = time.time()
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    ANALYZE_DIR.mkdir(parents=True, exist_ok=True)
    if EVENTS_PATH.exists():
        EVENTS_PATH.unlink()
    validate_vectors()
    sqls = {name: load_sql(meta["sql_path"]) for name, meta in SHAPES.items()}
    emit(
        "phase_a_start",
        status="running",
        raw_vector_count=len(RAW_VECTORS),
        shape_count=len(SHAPES),
        worker=f"{HOST}:{PORT}",
    )

    mapped: Dict[str, Any] = OrderedDict()
    for vector_name, vector in RAW_VECTORS.items():
        per_shape: Dict[str, Any] = OrderedDict()
        for shape, sql in sqls.items():
            response = psql_json(vector, sql, analyze=False)
            summary = finish_summary(summarize_plan(response))
            per_shape[shape] = summary
            write_json(PLAN_DIR / f"{vector_name}__{shape}.json", response)
            emit("plan_mapped", vector=vector_name, shape=shape, status="ok")
        composite = [per_shape[shape]["fingerprint"] for shape in SHAPES]
        composite_payload = json.dumps(composite, separators=(",", ":"))
        global_signature = hashlib.sha256(composite_payload.encode("utf-8")).hexdigest()
        mapped[vector_name] = {
            "vector": vector,
            "global_signature": global_signature,
            "per_shape": per_shape,
        }

    signature_groups: "OrderedDict[str, List[str]]" = OrderedDict()
    for vector_name, record in mapped.items():
        signature_groups.setdefault(record["global_signature"], []).append(vector_name)
    kept_vectors = [members[0] for members in signature_groups.values()]
    if len(kept_vectors) > 32:
        raise RuntimeError(f"signature-distinct vector count {len(kept_vectors)} exceeds 32")

    default_shapes = mapped["default"]["per_shape"]
    structure_candidates: List[Dict[str, Any]] = []
    for vector_name in kept_vectors:
        if vector_name == "default":
            continue
        record = mapped[vector_name]
        co_role = co_role_gate(
            default_shapes["co_role_hashagg"],
            record["per_shape"]["co_role_hashagg"],
        )
        for shape, meta in SHAPES.items():
            if not meta["primary"]:
                continue
            base = default_shapes[shape]
            candidate = record["per_shape"][shape]
            transition = intended_transition(shape, base, candidate)
            collision = closed_collision(shape, candidate)
            structure_candidates.append(
                {
                    "family": meta["family"],
                    "rank": meta["rank"],
                    "shape": shape,
                    "vector_name": vector_name,
                    "vector": record["vector"],
                    "base_fingerprint": base["fingerprint"],
                    "candidate_fingerprint": candidate["fingerprint"],
                    "base_nodes": base["node_labels"],
                    "candidate_nodes": candidate["node_labels"],
                    "transition": transition,
                    "closed_collision": collision,
                    "co_role": co_role,
                }
            )

    analyze_queue: List[Dict[str, Any]] = []
    dedup_keys = set()
    for candidate in sorted(
        structure_candidates,
        key=lambda item: (item["rank"], item["shape"], item["vector_name"]),
    ):
        if not candidate["transition"]["ok"] or candidate["closed_collision"]["collision"]:
            continue
        key = (
            candidate["shape"],
            candidate["candidate_fingerprint"],
            mapped[candidate["vector_name"]]["per_shape"]["co_role_hashagg"]["fingerprint"],
        )
        if key in dedup_keys:
            candidate["analyze_collapsed"] = True
            continue
        dedup_keys.add(key)
        analyze_queue.append(candidate)

    map_output = {
        "controller": "cpu_fair_v82_new_operator_batch",
        "executor": "Codex",
        "timestamp": now_text(),
        "worker": f"{HOST}:{PORT}",
        "cost_keys": COST_KEYS,
        "raw_vector_count": len(RAW_VECTORS),
        "raw_vectors": RAW_VECTORS,
        "signature_distinct_vector_count": len(kept_vectors),
        "kept_vectors": kept_vectors,
        "signature_groups": list(signature_groups.values()),
        "shape_metadata": SHAPES,
        "mapped": {
            vector_name: {
                "vector": record["vector"],
                "global_signature": record["global_signature"],
                "per_shape": {
                    shape: public_summary(summary)
                    for shape, summary in record["per_shape"].items()
                },
            }
            for vector_name, record in mapped.items()
        },
        "structure_candidates": structure_candidates,
        "analyze_queue": analyze_queue,
    }
    write_json(MAP_PATH, map_output)
    emit(
        "map_complete",
        status="ok",
        signature_distinct_vector_count=len(kept_vectors),
        intended_transition_count=sum(
            1 for item in structure_candidates if item["transition"]["ok"]
        ),
        analyze_pair_count=len(analyze_queue),
    )

    analyze_results: List[Dict[str, Any]] = []
    for candidate in analyze_queue:
        shape = candidate["shape"]
        vector_name = candidate["vector_name"]
        try:
            result = timed_pair(
                shape,
                vector_name,
                candidate["vector"],
                sqls[shape],
            )
            result["transition"] = candidate["transition"]
            result["closed_collision"] = candidate["closed_collision"]
            result["co_role"] = candidate["co_role"]
            result["base_fingerprint"] = candidate["base_fingerprint"]
            result["candidate_fingerprint"] = candidate["candidate_fingerprint"]
            analyze_results.append(result)
        except Exception as exc:
            emit(
                "analyze_pair_stop",
                shape=shape,
                vector=vector_name,
                status="failed",
                error=str(exc),
            )
            analyze_results.append(
                {
                    "shape": shape,
                    "family": candidate["family"],
                    "vector_name": vector_name,
                    "vector": candidate["vector"],
                    "transition": candidate["transition"],
                    "closed_collision": candidate["closed_collision"],
                    "co_role": candidate["co_role"],
                    "status": "failed",
                    "error": str(exc),
                    "speed_gate_ok": False,
                    "answer_invariant_ok": False,
                }
            )

    write_json(ANALYZE_PATH, analyze_results)
    eligible_set: List[Dict[str, Any]] = []
    for result in analyze_results:
        gates = {
            "intended_transition": bool(result.get("transition", {}).get("ok")),
            "not_closed_family": not bool(result.get("closed_collision", {}).get("collision")),
            "elapsed_ratio_le_0_85": bool(result.get("speed_gate_ok")),
            "same_vector_non_target_co_role": bool(result.get("co_role", {}).get("ok")),
            "answer_invariant": bool(result.get("answer_invariant_ok")),
        }
        result["eligibility_gates"] = gates
        result["eligible"] = all(gates.values())
        if result["eligible"]:
            eligible_set.append(result)
    eligible_set.sort(key=lambda item: (float(item["elapsed_ratio"]), int(SHAPES[item["shape"]]["rank"])))

    family_summary: Dict[str, Any] = {}
    for family in ("C01", "C06", "C03", "C02"):
        family_structures = [item for item in structure_candidates if item["family"] == family]
        family_analyze = [item for item in analyze_results if item["family"] == family]
        family_summary[family] = {
            "mapped_structure_candidate_count": len(family_structures),
            "intended_transition_count": sum(
                1 for item in family_structures if item["transition"]["ok"]
            ),
            "analyze_pair_count": len(family_analyze),
            "speed_gate_pass_count": sum(
                1 for item in family_analyze if item.get("speed_gate_ok")
            ),
            "eligible_count": sum(1 for item in family_analyze if item.get("eligible")),
            "best_elapsed_ratio": min(
                (
                    float(item["elapsed_ratio"])
                    for item in family_analyze
                    if item.get("elapsed_ratio") is not None
                ),
                default=None,
            ),
        }

    runtime_state = (
        "pending_main_agent_authorization"
        if eligible_set
        else "denied_phase_a_empty"
    )
    conclusion = (
        "phase_a_eligible_pending_main_agent"
        if eligible_set
        else "phase_a_empty; no_high_probability_operator_remaining"
    )
    decision = {
        "controller": "cpu_fair_v82_new_operator_batch",
        "executor": "Codex",
        "timestamp": now_text(),
        "worker": f"{HOST}:{PORT}",
        "db": DB,
        "phase": "Phase A eligibility",
        "capability_fixed": {"cpu_cores": 11.1, "memory_gib": 8.8},
        "cost_keys": COST_KEYS,
        "raw_vector_count": len(RAW_VECTORS),
        "signature_distinct_vector_count": len(kept_vectors),
        "signature_distinct_vectors": kept_vectors,
        "family_summary": family_summary,
        "analyze_results": analyze_results,
        "eligible": bool(eligible_set),
        "eligible_set": eligible_set,
        "runtime_recommended": bool(eligible_set),
        "runtime_authorization_state": runtime_state,
        "runtime_launched": False,
        "hybench_launched": False,
        "conclusion_code": conclusion,
        "fallback_absolute_db_qps": FALLBACK_DB_QPS,
        "fallback_preserved": True,
        "strict_gates_weakened": False,
        "hard_bounds": {
            "raw_seed_count_eq_32": len(RAW_VECTORS) == 32,
            "signature_distinct_vectors_le_32": len(kept_vectors) <= 32,
            "exactly_seven_cost_keys": all(
                set(vector) == set(COST_KEYS) for vector in RAW_VECTORS.values()
            ),
            "plan_probe_parallelism": 1,
            "persistent_postgres_settings_changed": False,
            "hybench_not_started": True,
            "core_not_modified": True,
        },
        "artifacts": {
            "run_dir": str(RUN_DIR),
            "events_jsonl": str(EVENTS_PATH),
            "map_json": str(MAP_PATH),
            "analyze_json": str(ANALYZE_PATH),
            "decision_json": str(DECISION_PATH),
            "plans_dir": str(PLAN_DIR),
            "analyze_dir": str(ANALYZE_DIR),
        },
        "elapsed_s": time.time() - started,
    }
    write_json(DECISION_PATH, decision)
    write_json(REPORT_DECISION_PATH, decision)
    emit(
        "phase_a_stop",
        status="ok",
        eligible_count=len(eligible_set),
        conclusion_code=conclusion,
        elapsed_s=decision["elapsed_s"],
    )
    print(
        f"[v82] decision={REPORT_DECISION_PATH} eligible={len(eligible_set)} "
        f"runtime_state={runtime_state}",
        flush=True,
    )


if __name__ == "__main__":
    main()
