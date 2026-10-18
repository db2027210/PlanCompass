#!/usr/bin/env python3
"""Memory fair v86 single-batch Phase A controller.

Maps M01/M03/M04/M02 over one shared 32-seed seven-cost grid, globally
deduplicates the four-shape fingerprint profile, and analyzes only structural
changes. It never starts HyBench or the binary-plan tuner.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import statistics
import subprocess
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


REPO = Path("/home/anonymous/program/resource_adaptive")
CONFIG_PATH = REPO / "config/binary_plan_tuning_hybenchxp_memory_fair_v86_phase_a_20260715.json"
RUN_DIR = Path(
    "/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs/"
    "hyxp_memory_fair_v86_newop_batch_20260715"
)
REPORT_DIR = REPO / "report/bonect"
STATUS_PATH = REPO / ".agents/grok-hybenchxp-memory-v86-status.md"
DECISION_PATH = REPORT_DIR / "phase_a_eligibility_decision_v86.json"
DATED_DECISION_PATH = REPORT_DIR / "hybenchxp_memory_fair_v86_phase_a_eligibility_20260715.json"
CENSUS_PATH = REPORT_DIR / "hybenchxp_memory_fair_v86_plan_family_census_20260715.json"
REPORT_PATH = REPORT_DIR / "hybenchxp_memory_fair_v86_phase_a_eligibility_20260715.md"
TZ = timezone(timedelta(hours=8))

SEVEN = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)


def now_iso() -> str:
    return datetime.now(TZ).isoformat()


def log(message: str) -> None:
    print(f"[{datetime.now(TZ).strftime('%H:%M:%S')}] {message}", flush=True)


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def strip_sql(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("--")
    ).strip().rstrip(";")


def command_output(args: Sequence[str], timeout: int = 30) -> str:
    result = subprocess.run(
        list(args), capture_output=True, text=True, check=False, timeout=timeout
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(args)}: "
            f"{result.stderr.strip()[:1000]}"
        )
    return result.stdout.strip()


def validate_config(config: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    if tuple(config.get("candidate_keys") or ()) != SEVEN:
        raise RuntimeError("candidate_keys must be exactly the seven planner cost keys")
    default = config.get("default_params") or {}
    if set(default) != set(SEVEN):
        raise RuntimeError("default_params must contain exactly seven keys")
    vectors: Dict[str, Dict[str, float]] = {}
    overrides = config.get("vector_overrides") or {}
    if len(overrides) != 32:
        raise RuntimeError(f"expected 32 shared seeds, got {len(overrides)}")
    for name, delta in overrides.items():
        if not set(delta).issubset(SEVEN):
            raise RuntimeError(f"{name} contains a non-cost key: {sorted(set(delta) - set(SEVEN))}")
        vector = {key: float(default[key]) for key in SEVEN}
        vector.update({key: float(value) for key, value in delta.items()})
        if set(vector) != set(SEVEN):
            raise RuntimeError(f"{name} is not an exact seven-key vector")
        vectors[name] = vector
    return vectors


def preflight(config: Dict[str, Any]) -> Dict[str, Any]:
    container = str(config["container"])
    port = int(config["port"])
    inspect = command_output(
        [
            "docker",
            "inspect",
            container,
            "--format",
            "{{.State.Status}}|{{.HostConfig.Memory}}|{{.HostConfig.ShmSize}}|{{.HostConfig.CpusetCpus}}|"
            "{{json .NetworkSettings.Ports}}|{{json .Mounts}}",
        ]
    )
    parts = inspect.split("|", 5)
    if len(parts) != 6 or parts[0] != "running":
        raise RuntimeError(f"v86 worker is not running: {inspect}")
    memory_bytes = int(parts[1])
    shm_bytes = int(parts[2])
    expected_bytes = int(float(config["capability_gib"]) * (1024**3))
    if abs(memory_bytes - expected_bytes) > 1:
        raise RuntimeError(f"worker memory {memory_bytes} != 6.4 GiB ({expected_bytes})")
    expected_shm = int(config["worker_shm_size_bytes"])
    if shm_bytes != expected_shm:
        raise RuntimeError(f"worker shm {shm_bytes} != required {expected_shm}")
    listeners = command_output(["ss", "-ltnp"])
    pool_lines = [
        line
        for line in listeners.splitlines()
        if re.search(r":(?:5743[3-9]|57440)\b", line)
    ]
    own_lines = [line for line in pool_lines if re.search(fr":{port}\b", line)]
    if len(pool_lines) != 1 or len(own_lines) != 1:
        raise RuntimeError(f"Memory pool ownership conflict: {pool_lines}")
    pgrep = subprocess.run(
        ["pgrep", "-af", "memory_v85|same_vector_composition|grok.*memory"],
        capture_output=True,
        text=True,
        check=False,
    )
    foreign = [
        line
        for line in pgrep.stdout.splitlines()
        if "hybenchxp_memory_v86_newop_batch_probe.py" not in line
        and "pgrep -af" not in line
    ]
    if foreign:
        raise RuntimeError(f"live v85/Grok residue detected: {foreign}")
    ready = command_output(
        [
            "psql",
            "-h",
            str(config["host"]),
            "-p",
            str(port),
            "-U",
            "postgres",
            "-d",
            str(config["database"]),
            "-XAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            "SELECT current_database() || '|' || current_setting('server_version_num');",
        ]
    )
    return {
        "timestamp": now_iso(),
        "container": container,
        "port": port,
        "memory_bytes": memory_bytes,
        "memory_gib": memory_bytes / (1024**3),
        "shm_bytes": shm_bytes,
        "cpuset": parts[3],
        "port_listener": own_lines[0],
        "foreign_memory_processes": foreign,
        "database_ready": ready,
        "cpu_pool_untouched": True,
        "external_untouched": True,
    }


def fixed_set_sql(config: Dict[str, Any], params: Dict[str, float]) -> str:
    statements = [f"SET {key} = {params[key]};" for key in SEVEN]
    for key, value in (config.get("fixed_settings") or {}).items():
        if key in SEVEN:
            raise RuntimeError(f"fixed setting overlaps candidate key: {key}")
        if isinstance(value, str) and value.lower() in {"on", "off"}:
            rendered = value
        elif isinstance(value, str):
            rendered = "'" + value.replace("'", "''") + "'"
        else:
            rendered = str(value)
        statements.append(f"SET {key} = {rendered};")
    statements.append("SET statement_timeout = '14min';")
    return "\n".join(statements)


def psql_input(config: Dict[str, Any], body: str, timeout: int) -> str:
    env = {**os.environ, "PGPASSWORD": "postgres"}
    result = subprocess.run(
        [
            "psql",
            "-h",
            str(config["host"]),
            "-p",
            str(config["port"]),
            "-U",
            "postgres",
            "-d",
            str(config["database"]),
            "-XqAt",
            "-v",
            "ON_ERROR_STOP=1",
        ],
        input=body,
        text=True,
        capture_output=True,
        env=env,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"psql failed: {result.stderr.strip()[:1200]}")
    return result.stdout.strip()


def explain(
    config: Dict[str, Any],
    sql_body: str,
    params: Dict[str, float],
    *,
    analyze: bool,
) -> Dict[str, Any]:
    options = (
        "ANALYZE, BUFFERS, SUMMARY, VERBOSE, FORMAT JSON"
        if analyze
        else "VERBOSE, FORMAT JSON"
    )
    body = (
        fixed_set_sql(config, params)
        + f"\nEXPLAIN ({options})\n{sql_body};\n"
    )
    raw = psql_input(config, body, timeout=900 if analyze else 180)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        match = re.search(r"(\[.*\])", raw, re.S)
        if not match:
            raise RuntimeError(f"no EXPLAIN JSON: {raw[:500]}") from exc
        payload = json.loads(match.group(1))
    return payload[0]


def plan_signature(plan: Dict[str, Any]) -> str:
    labels: List[str] = []

    def walk(node: Dict[str, Any]) -> None:
        label = str(node.get("Node Type") or "?")
        for key in ("Strategy", "Join Type", "Command", "Partial Mode"):
            value = node.get(key)
            if value and value != "Simple":
                label += f":{value}"
        relation = node.get("Relation Name")
        if relation:
            label += f":{relation}"
        index_name = node.get("Index Name")
        if index_name:
            label += f":index={index_name}"
        for key in (
            "Index Cond",
            "Recheck Cond",
            "Filter",
            "Hash Cond",
            "Merge Cond",
            "Join Filter",
            "Sort Key",
            "Presorted Key",
            "Group Key",
        ):
            value = node.get(key)
            if value:
                label += f":{key}={json.dumps(value, sort_keys=True)}"
        workers = node.get("Workers Planned")
        if workers is not None:
            label += f":W{workers}"
        parent = node.get("Parent Relationship")
        subplan = node.get("Subplan Name")
        if parent in {"InitPlan", "SubPlan"} or subplan:
            label += f"[{parent or ''}:{subplan or ''}]"
        labels.append(label)
        for child in node.get("Plans") or []:
            walk(child)

    walk(plan)
    return " > ".join(labels)


EXECUTION_SEMANTIC_KEYS = (
    "Node Type",
    "Parent Relationship",
    "Subplan Name",
    "Parallel Aware",
    "Async Capable",
    "Join Type",
    "Inner Unique",
    "Strategy",
    "Partial Mode",
    "Operation",
    "Command",
    "Relation Name",
    "Schema",
    "Alias",
    "Index Name",
    "Scan Direction",
    "CTE Name",
    "Function Name",
    "Table Function Name",
    "Index Cond",
    "Recheck Cond",
    "Filter",
    "Hash Cond",
    "Merge Cond",
    "Join Filter",
    "TID Cond",
    "Sort Key",
    "Presorted Key",
    "Group Key",
    "Grouping Sets",
    "Output",
    "Cache Key",
    "Cache Mode",
    "Workers Planned",
    "Planned Partitions",
)


def execution_semantics(node: Dict[str, Any]) -> Dict[str, Any]:
    """Canonical execution-affecting plan fields, excluding numeric costs."""
    result: Dict[str, Any] = {
        key: node[key] for key in EXECUTION_SEMANTIC_KEYS if key in node
    }
    result["Plans"] = [execution_semantics(child) for child in node.get("Plans") or []]
    return result


def node_own_memory_mib(node: Dict[str, Any]) -> Tuple[float, str]:
    values: List[Tuple[float, str]] = []
    for key in ("Peak Memory Usage", "Memory Usage", "Sort Space Used"):
        value = node.get(key)
        if value is not None:
            values.append((float(value) / 1024.0, key))
    if not values:
        return 0.0, "none"
    return max(values, key=lambda item: item[0])


def subtree_peak_mib(node: Dict[str, Any]) -> float:
    own, _ = node_own_memory_mib(node)
    return max([own] + [subtree_peak_mib(child) for child in node.get("Plans") or []])


def plan_meta(explain_obj: Dict[str, Any]) -> Dict[str, Any]:
    plan = explain_obj["Plan"]
    counts: Counter[str] = Counter()
    strategies: Counter[str] = Counter()
    memory_nodes: List[Dict[str, Any]] = []
    cte_retained: List[Dict[str, Any]] = []
    max_instance_mib = 0.0
    max_instance_basis = "none"
    shared_hit = 0
    shared_read = 0
    temp_read = 0
    temp_written = 0

    def walk(node: Dict[str, Any], path: str) -> None:
        nonlocal max_instance_mib, max_instance_basis
        nonlocal shared_hit, shared_read, temp_read, temp_written
        node_type = str(node.get("Node Type") or "?")
        counts[node_type] += 1
        strategy = node.get("Strategy")
        if strategy:
            strategies[f"{node_type}:{strategy}"] += 1
        if node.get("Parent Relationship") == "InitPlan":
            counts["__InitPlan__"] += 1
        own_mib, own_key = node_own_memory_mib(node)
        worker_mib = 0.0
        worker_details: List[float] = []
        for worker in node.get("Workers") or []:
            worker_peak, _ = node_own_memory_mib(worker)
            if worker_peak > 0:
                worker_details.append(worker_peak)
                worker_mib += worker_peak
        instance_mib = own_mib + worker_mib
        if instance_mib > 0:
            memory_nodes.append(
                {
                    "path": path,
                    "node_type": node_type,
                    "leader_mib": own_mib,
                    "leader_metric": own_key,
                    "worker_mib": worker_details,
                    "justified_concurrent_instance_mib": instance_mib,
                }
            )
        if instance_mib > max_instance_mib:
            max_instance_mib = instance_mib
            max_instance_basis = f"node_instance:{path}"
        subplan_name = str(node.get("Subplan Name") or "")
        if node.get("Parent Relationship") == "InitPlan" and subplan_name.startswith("CTE"):
            cte_retained.append(
                {"subplan": subplan_name, "subtree_peak_mib": subtree_peak_mib(node)}
            )
        shared_hit += int(node.get("Shared Hit Blocks") or 0)
        shared_read += int(node.get("Shared Read Blocks") or 0)
        temp_read += int(node.get("Temp Read Blocks") or 0)
        temp_written += int(node.get("Temp Written Blocks") or 0)
        for index, child in enumerate(node.get("Plans") or []):
            walk(child, f"{path}/{index}:{child.get('Node Type', '?')}")

    walk(plan, str(plan.get("Node Type") or "?"))
    cte_sum = sum(item["subtree_peak_mib"] for item in cte_retained)
    if cte_sum > max_instance_mib:
        effective_peak = cte_sum
        basis = "retained_cte_initplans_sum"
    else:
        effective_peak = max_instance_mib
        basis = max_instance_basis
    signature = plan_signature(plan)
    semantics = execution_semantics(plan)
    semantics_json = json.dumps(
        semantics, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    result: Dict[str, Any] = {
        "signature": signature,
        "signature_hash": hashlib.sha1(signature.encode("utf-8")).hexdigest()[:12],
        "execution_semantics": semantics,
        "execution_fingerprint_hash": hashlib.sha256(
            semantics_json.encode("utf-8")
        ).hexdigest()[:16],
        "counts": dict(sorted(counts.items())),
        "strategies": dict(sorted(strategies.items())),
        "memory_nodes": memory_nodes,
        "max_instance_mib": max_instance_mib,
        "retained_cte_mib": cte_sum,
        "retained_cte_detail": cte_retained,
        "effective_peak_mib": effective_peak,
        "effective_peak_basis": basis,
        "shared_hit_blocks": shared_hit,
        "shared_read_blocks": shared_read,
        "temp_read_blocks": temp_read,
        "temp_written_blocks": temp_written,
        "total_cost": float(plan.get("Total Cost") or 0.0),
    }
    if plan.get("Actual Total Time") is not None:
        result["actual_ms"] = float(plan["Actual Total Time"])
        result["actual_rows"] = float(plan.get("Actual Rows") or 0.0)
    return result


def intended_transition(shape_id: str, base: Dict[str, Any], cand: Dict[str, Any]) -> Tuple[bool, str]:
    bc = base.get("counts") or {}
    cc = cand.get("counts") or {}
    bs = base.get("strategies") or {}
    cs = cand.get("strategies") or {}
    if shape_id == "M01":
        if bc.get("WindowAgg", 0) < 1 or cc.get("WindowAgg", 0) < 1:
            return False, "WindowAgg missing on one side"
        base_blocking = bc.get("Sort", 0) + bc.get("Materialize", 0)
        cand_blocking = cc.get("Sort", 0) + cc.get("Materialize", 0)
        ordered_scan = cc.get("Index Scan", 0) + cc.get("Index Only Scan", 0)
        if cand_blocking < base_blocking:
            return True, f"WindowAgg blocking nodes {base_blocking}->{cand_blocking}"
        if ordered_scan > bc.get("Index Scan", 0) + bc.get("Index Only Scan", 0) and cand_blocking == 0:
            return True, "WindowAgg moved to an ordered scan without Sort"
        return False, f"no WindowAgg materialization collapse ({base_blocking}->{cand_blocking})"
    if shape_id == "M03":
        base_hash = bs.get("Aggregate:Hashed", 0) + bs.get("SetOp:Hashed", 0) + bc.get("HashSetOp", 0)
        cand_sorted = cc.get("Unique", 0) + cs.get("SetOp:Sorted", 0)
        if base_hash > 0 and cand_sorted > 0:
            return True, f"UNION hash dedup {base_hash}->sorted/Unique {cand_sorted}"
        return False, f"UNION hash-to-sorted transition absent ({base_hash}->{cand_sorted})"
    if shape_id == "M04":
        base_multi = bc.get("CTE Scan", 0) + bc.get("Materialize", 0) + bc.get("__InitPlan__", 0)
        cand_multi = cc.get("CTE Scan", 0) + cc.get("Materialize", 0) + cc.get("__InitPlan__", 0)
        if base_multi > 0 and cand_multi < base_multi:
            return True, f"CTE/InitPlan materialization nodes {base_multi}->{cand_multi}"
        return False, f"no CTE/InitPlan collapse ({base_multi}->{cand_multi})"
    if shape_id == "M02":
        base_hash = bs.get("Aggregate:Hashed", 0)
        cand_unique = cc.get("Unique", 0)
        if base_hash > 0 and cand_unique > 0 and cand_hash_count(cand) < cand_hash_count(base):
            return True, f"HashAggregate DISTINCT {base_hash}->Unique {cand_unique}"
        return False, f"HashAggregate-to-Unique transition absent ({base_hash}->{cand_unique})"
    return False, "unknown shape"


def cand_hash_count(meta: Dict[str, Any]) -> int:
    return int((meta.get("strategies") or {}).get("Aggregate:Hashed", 0))


def new_indexonly_cliff(base: Dict[str, Any], cand: Dict[str, Any]) -> bool:
    bc = int((base.get("counts") or {}).get("Index Only Scan", 0))
    cc = int((cand.get("counts") or {}).get("Index Only Scan", 0))
    return cc > bc


def answer_hash(
    config: Dict[str, Any], sql_body: str, params: Dict[str, float]
) -> Dict[str, Any]:
    raw = psql_input(config, fixed_set_sql(config, params) + f"\n{sql_body};\n", timeout=900)
    return {
        "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "bytes": len(raw.encode("utf-8")),
        "preview": raw[:200],
    }


def analyze_candidate(
    config: Dict[str, Any],
    shape: Dict[str, Any],
    sql_body: str,
    vector_name: str,
    default: Dict[str, float],
    candidate: Dict[str, float],
    default_meta: Dict[str, Any],
    execution_fingerprint_hash: str,
    aliases: List[Dict[str, Any]],
) -> Dict[str, Any]:
    cand_samples: List[Dict[str, Any]] = []
    cand_meta = plan_meta(explain(config, sql_body, candidate, analyze=True))
    cand_samples.append(cand_meta)
    first_speed = (
        (default_meta["actual_ms"] - cand_meta["actual_ms"])
        / default_meta["actual_ms"]
        * 100.0
    )
    near_low, near_high = config["gates"]["near_elapsed_band_pct"]
    sample_count = 1
    if float(near_low) <= first_speed <= float(near_high):
        sample_count = int(config["gates"]["near_elapsed_samples"])
    while len(cand_samples) < sample_count:
        cand_samples.append(
            plan_meta(explain(config, sql_body, candidate, analyze=True))
        )
    base_ms = float(default_meta["actual_ms"])
    cand_ms = statistics.median(float(item["actual_ms"]) for item in cand_samples)
    base_peak = float(default_meta["effective_peak_mib"])
    cand_peak = statistics.median(float(item["effective_peak_mib"]) for item in cand_samples)
    speedup = (base_ms - cand_ms) / base_ms * 100.0
    elapsed_ratio = cand_ms / base_ms
    peak_drop = base_peak - cand_peak
    transition_ok, transition_reason = intended_transition(shape["id"], default_meta, cand_meta)
    indexonly_cliff = new_indexonly_cliff(default_meta, cand_meta)
    rejects: List[str] = []
    if not transition_ok:
        rejects.append("intended_memory_node_transition_failed")
    if peak_drop < float(config["gates"]["min_peak_or_justified_concurrent_drop_mib"]):
        rejects.append("peak_or_justified_concurrent_drop<328MiB")
    if speedup < float(config["gates"]["min_elapsed_speedup_pct"]):
        rejects.append("elapsed_speedup<15%")
    if indexonly_cliff:
        rejects.append("new_IndexOnly_cliff")
    answer: Dict[str, Any] = {"checked": False, "equal": None}
    if not rejects:
        try:
            base_answer = answer_hash(config, sql_body, default)
            cand_answer = answer_hash(config, sql_body, candidate)
            answer = {
                "checked": True,
                "equal": base_answer["sha256"] == cand_answer["sha256"],
                "base": base_answer,
                "candidate": cand_answer,
            }
            if not answer["equal"]:
                rejects.append("answer_invariant_failed")
        except Exception as exc:  # noqa: BLE001
            answer = {"checked": True, "equal": False, "error": str(exc)[:1200]}
            rejects.append("answer_invariant_error")
    return {
        "shape_id": shape["id"],
        "rank": shape["rank"],
        "sql_path": shape["path"],
        "transition": shape["transition"],
        "vector": vector_name,
        "vector_params": {key: candidate[key] for key in SEVEN},
        "aliases": aliases,
        "execution_fingerprint_hash": execution_fingerprint_hash,
        "fixed_settings": config["fixed_settings"],
        "default_sample_count": 1,
        "candidate_sample_count": sample_count,
        "base_ms_samples": [default_meta["actual_ms"]],
        "cand_ms_samples": [item["actual_ms"] for item in cand_samples],
        "base_ms_median": base_ms,
        "cand_ms_median": cand_ms,
        "elapsed_ratio": elapsed_ratio,
        "elapsed_speedup_pct": speedup,
        "base_effective_peak_mib": base_peak,
        "cand_effective_peak_mib": cand_peak,
        "peak_or_justified_concurrent_drop_mib": peak_drop,
        "base_peak_basis": default_meta["effective_peak_basis"],
        "cand_peak_basis": cand_meta["effective_peak_basis"],
        "intended_transition_ok": transition_ok,
        "intended_transition_reason": transition_reason,
        "indexonly_cliff": indexonly_cliff,
        "answer_invariant": answer,
        "base_meta": default_meta,
        "cand_meta": cand_meta,
        "censored": False,
        "error": None,
        "reject": rejects,
        "eligible": not rejects,
    }


def is_timeout_error(error: str) -> bool:
    lowered = error.lower()
    return "statement timeout" in lowered or "timeoutexpired" in lowered


def analyze_error_record(
    config: Dict[str, Any],
    shape: Dict[str, Any],
    representative: str,
    vectors: Dict[str, Dict[str, float]],
    execution_fingerprint_hash: str,
    aliases: List[Dict[str, Any]],
    error: str,
    *,
    default_failed: bool = False,
) -> Dict[str, Any]:
    timeout = is_timeout_error(error)
    reason = "default_analyze_error" if default_failed else "candidate_analyze_error"
    if timeout:
        reason = "default_analyze_timeout" if default_failed else "candidate_analyze_timeout"
    return {
        "shape_id": shape["id"],
        "rank": shape["rank"],
        "sql_path": shape["path"],
        "transition": shape["transition"],
        "vector": representative,
        "vector_params": {key: vectors[representative][key] for key in SEVEN},
        "aliases": aliases,
        "execution_fingerprint_hash": execution_fingerprint_hash,
        "fixed_settings": config["fixed_settings"],
        "default_sample_count": 0 if default_failed else 1,
        "candidate_sample_count": 0,
        "base_ms_samples": [],
        "cand_ms_samples": [],
        "base_ms_median": None,
        "cand_ms_median": None,
        "elapsed_ratio": None,
        "elapsed_speedup_pct": None,
        "base_effective_peak_mib": None,
        "cand_effective_peak_mib": None,
        "peak_or_justified_concurrent_drop_mib": None,
        "intended_transition_ok": False,
        "intended_transition_reason": reason,
        "indexonly_cliff": None,
        "answer_invariant": {"checked": False, "equal": None},
        "base_meta": None,
        "cand_meta": None,
        "censored": timeout,
        "error": error[:1200],
        "reject": [reason],
        "eligible": False,
    }


def family_summary(
    shapes: Sequence[Dict[str, Any]],
    defaults: Dict[str, Dict[str, Any]],
    analyzed: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    for shape in shapes:
        shape_id = shape["id"]
        pairs = [item for item in analyzed if item.get("shape_id") == shape_id]
        measured = [
            item
            for item in pairs
            if item.get("error") is None
            and item.get("peak_or_justified_concurrent_drop_mib") is not None
            and item.get("elapsed_speedup_pct") is not None
        ]
        best_drop = (
            max(measured, key=lambda item: item["peak_or_justified_concurrent_drop_mib"])
            if measured
            else None
        )
        best_speed = (
            max(measured, key=lambda item: item["elapsed_speedup_pct"])
            if measured
            else None
        )
        default_record = defaults.get(shape_id) or {}
        summary[shape_id] = {
            "rank": shape["rank"],
            "default_status": default_record.get("status", "not_run"),
            "default_actual_ms": (default_record.get("meta") or {}).get("actual_ms"),
            "default_effective_peak_mib": (default_record.get("meta") or {}).get(
                "effective_peak_mib"
            ),
            "deduped_candidate_fingerprints": len(pairs),
            "measured_candidate_fingerprints": len(measured),
            "eligible_count": sum(bool(item.get("eligible")) for item in pairs),
            "timeouts": [
                {"vector": item.get("vector"), "error": item.get("error")}
                for item in pairs
                if item.get("censored")
            ],
            "errors": [
                {"vector": item.get("vector"), "error": item.get("error")}
                for item in pairs
                if item.get("error") and not item.get("censored")
            ],
            "best_peak_drop": None
            if best_drop is None
            else {
                "vector": best_drop["vector"],
                "aliases": best_drop.get("aliases") or [],
                "peak_drop_mib": best_drop["peak_or_justified_concurrent_drop_mib"],
                "elapsed_ratio": best_drop["elapsed_ratio"],
                "elapsed_speedup_pct": best_drop["elapsed_speedup_pct"],
                "eligible": best_drop["eligible"],
            },
            "best_speed": None
            if best_speed is None
            else {
                "vector": best_speed["vector"],
                "aliases": best_speed.get("aliases") or [],
                "peak_drop_mib": best_speed["peak_or_justified_concurrent_drop_mib"],
                "elapsed_ratio": best_speed["elapsed_ratio"],
                "elapsed_speedup_pct": best_speed["elapsed_speedup_pct"],
                "eligible": best_speed["eligible"],
            },
        }
    return summary


def update_status(
    phase: str,
    detail: str,
    *,
    eligible_count: Optional[int] = None,
    runtime_recommended: Optional[bool] = None,
) -> None:
    rows = [
        "# HyBenchXP Memory fair v86 status",
        "",
        f"Updated: {now_iso()}",
        "Executor: **Codex** (legacy filename retained only for tracker compatibility)",
        "",
        "| field | value |",
        "| --- | --- |",
        f"| phase | {phase} |",
        "| ownership | Memory `57433..57440`; v86 w1 only; CPU/external untouched |",
        "| batch | M01, M03, M04, M02; shared 32-seed exact-seven-cost grid |",
        "| fixed | work_mem 512MB; hash_mem_multiplier 2.0; enable_* fixed; capability 6.4 GiB |",
        f"| detail | {detail} |",
        f"| eligible_set_size | {eligible_count if eligible_count is not None else 'pending'} |",
        "| runtime | "
        + (
            "recommended but not started; must first report to main agent"
            if runtime_recommended
            else "not started"
        )
        + " |",
        "| fallback_db_qps | **114.12708344892893** |",
        "",
    ]
    STATUS_PATH.write_text("\n".join(rows), encoding="utf-8")


def write_markdown(decision: Dict[str, Any]) -> None:
    eligible = decision["eligible_set"]
    rows = [
        "# HyBenchXP Memory fair v86 Phase A eligibility",
        "",
        f"- Codex",
        f"- {decision['timestamp']}",
        f"-  seed{decision['map_summary']['seed_count']}{decision['map_summary']['signature_distinct_vector_count']}",
        f"- {decision['map_summary']['original_structure_pair_count']}",
        f"-  ANALYZE {len(decision['analyzed_pairs'])}",
        f"- eligible_set{len(eligible)}",
        f"- fallback  DB QPS`{decision['fallback_db_qps']}`",
        "",
        "## ",
        "",
    ]
    if eligible:
        rows.extend(
            [
                "Phase A  eligible  runtime path",
                " HyBench eligibility  runtime  agent",
            ]
        )
    else:
        rows.extend(
            [
                "`eligible_set`  controller  HyBenchbaseline",
                "candidate  full8 fallback `no_high_probability_operator_remaining`",
            ]
        )
    rows.extend(["", "## Family ", ""])
    for shape_id, item in decision["family_summary"].items():
        drop = item.get("best_peak_drop")
        speed = item.get("best_speed")
        drop_text = (
            "none"
            if drop is None
            else f"{drop['vector']} drop={drop['peak_drop_mib']:.1f}MiB ratio={drop['elapsed_ratio']:.3f}"
        )
        speed_text = (
            "none"
            if speed is None
            else f"{speed['vector']} speed={speed['elapsed_speedup_pct']:+.1f}% ratio={speed['elapsed_ratio']:.3f}"
        )
        rows.append(
            f"- `{shape_id}`best memory {drop_text}best speed {speed_text}"
            f"timeouts={len(item['timeouts'])}errors={len(item['errors'])}"
        )
    rows.extend(["", "## ANALYZE ", ""])
    for item in decision["analyzed_pairs"]:
        if item.get("error"):
            rows.append(
                f"- `{item['shape_id']}/{item['vector']}`censored={str(item.get('censored')).lower()}"
                f"error={item['error']}reject={','.join(item['reject'])}"
            )
        else:
            rows.append(
                "- `{shape}/{vector}`={structure}peak drop={drop:.1f} MiB"
                "elapsed={speed:+.1f}% (ratio={ratio:.3f})eligible={eligible}reject={reject}".format(
                    shape=item["shape_id"],
                    vector=item["vector"],
                    structure=item["intended_transition_reason"],
                    drop=item["peak_or_justified_concurrent_drop_mib"],
                    speed=item["elapsed_speedup_pct"],
                    ratio=item["elapsed_ratio"],
                    eligible=str(item["eligible"]).lower(),
                    reject=", ".join(item["reject"]) or "none",
                )
            )
    rows.extend(
        [
            "",
            "## ",
            "",
            "-  7  planner cost ",
            "- work_memhash_mem_multiplierenable_*datacapability  pair-fixed",
            "- CPU `56433..56440`  HyBench/OpenGauss/OpenCode/Phase2/VM ",
            "- `binary_plan_resource_tuner.py`  closeout ",
        ]
    )
    REPORT_PATH.write_text("\n".join(rows) + "\n", encoding="utf-8")


def stop_worker(container: str) -> None:
    result = subprocess.run(
        ["docker", "stop", "-t", "5", container],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    if result.returncode == 0:
        log(f"stopped v86 worker {container}; Memory port released")
    else:
        log(f"warning: failed to stop v86 worker: {result.stderr.strip()[:500]}")


def main() -> int:
    started = time.time()
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    container = str(config["container"])
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    vectors = validate_config(config)
    default = vectors["default"]
    update_status("Phase A map", "preflight and 128 EXPLAIN cells")
    try:
        ownership = preflight(config)
        log(f"preflight passed: {ownership['database_ready']}; cpuset={ownership['cpuset']}")
        sql_bodies: Dict[str, str] = {}
        provenance: List[Dict[str, Any]] = []
        for shape in config["shapes"]:
            path = REPO / shape["path"]
            sql_bodies[shape["id"]] = strip_sql(path.read_text(encoding="utf-8"))
            provenance.append(
                {
                    "shape_id": shape["id"],
                    "path": shape["path"],
                    "sha256": sha256(path),
                }
            )
        write_json(
            RUN_DIR / "sql_provenance_v86.json",
            {
                "timestamp": now_iso(),
                "config_sha256": sha256(CONFIG_PATH),
                "sql": provenance,
                "fixed_settings": config["fixed_settings"],
                "ownership": ownership,
            },
        )

        plan_grid: List[Dict[str, Any]] = []
        profile_by_seed: Dict[str, Tuple[str, ...]] = {}
        meta_by_seed_shape: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for seed_index, (seed, params) in enumerate(vectors.items(), start=1):
            log(f"map seed {seed_index:02d}/32 {seed}")
            profile: List[str] = []
            for shape in config["shapes"]:
                try:
                    meta = plan_meta(explain(config, sql_bodies[shape["id"]], params, analyze=False))
                    meta_by_seed_shape[(seed, shape["id"])] = meta
                    profile.append(meta["execution_fingerprint_hash"])
                    plan_grid.append(
                        {
                            "seed": seed,
                            "vector_params": {key: params[key] for key in SEVEN},
                            "shape_id": shape["id"],
                            "rank": shape["rank"],
                            "meta": meta,
                            "error": None,
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    error = str(exc)[:1000]
                    profile.append("ERROR:" + hashlib.sha1(error.encode()).hexdigest()[:8])
                    plan_grid.append(
                        {
                            "seed": seed,
                            "vector_params": {key: params[key] for key in SEVEN},
                            "shape_id": shape["id"],
                            "rank": shape["rank"],
                            "meta": None,
                            "error": error,
                        }
                    )
            profile_by_seed[seed] = tuple(profile)
        write_json(
            RUN_DIR / "plan_grid_v86.json",
            {"timestamp": now_iso(), "cells": plan_grid},
        )
        map_errors = [cell for cell in plan_grid if cell.get("error")]
        if map_errors:
            raise RuntimeError(f"EXPLAIN map incomplete: {len(map_errors)} cells failed")

        profile_groups: Dict[Tuple[str, ...], List[str]] = {}
        for seed, profile in profile_by_seed.items():
            profile_groups.setdefault(profile, []).append(seed)
        if len(profile_groups) > int(config["gates"]["max_signature_distinct_vectors"]):
            raise RuntimeError("global signature-distinct vector cap exceeded")
        default_profile = profile_by_seed["default"]
        representatives: List[str] = []
        collapsed: List[Dict[str, Any]] = []
        for profile, seeds in profile_groups.items():
            representative = seeds[0]
            collapsed.append(
                {
                    "representative": representative,
                    "seeds": seeds,
                    "profile": list(profile),
                    "same_as_default": profile == default_profile,
                }
            )
            if profile != default_profile and not any(item.startswith("ERROR:") for item in profile):
                representatives.append(representative)
        default_meta_by_shape = {
            shape["id"]: meta_by_seed_shape[("default", shape["id"])]
            for shape in config["shapes"]
        }
        structural_pairs: List[Tuple[str, Dict[str, Any]]] = []
        for seed in representatives:
            for shape in config["shapes"]:
                cand_meta = meta_by_seed_shape[(seed, shape["id"])]
                if cand_meta["execution_fingerprint_hash"] != default_meta_by_shape[shape["id"]][
                    "execution_fingerprint_hash"
                ]:
                    structural_pairs.append((seed, shape))

        candidate_groups: List[Dict[str, Any]] = []
        for shape in config["shapes"]:
            shape_id = shape["id"]
            default_fingerprint = default_meta_by_shape[shape_id][
                "execution_fingerprint_hash"
            ]
            by_fingerprint: Dict[str, List[str]] = {}
            for seed in vectors:
                fingerprint = meta_by_seed_shape[(seed, shape_id)][
                    "execution_fingerprint_hash"
                ]
                if fingerprint == default_fingerprint:
                    continue
                by_fingerprint.setdefault(fingerprint, []).append(seed)
            for fingerprint, seeds in by_fingerprint.items():
                candidate_groups.append(
                    {
                        "shape": shape,
                        "execution_fingerprint_hash": fingerprint,
                        "representative": seeds[0],
                        "aliases": [
                            {
                                "vector": seed,
                                "vector_params": {key: vectors[seed][key] for key in SEVEN},
                            }
                            for seed in seeds
                        ],
                    }
                )

        update_status(
            f"Phase A attempt{int(config.get('attempt', 1))} ANALYZE",
            f"attempt2_original_pairs=49; fresh_original_pairs={len(structural_pairs)}; "
            f"deduped_execution_fingerprints={len(candidate_groups)}; progress=defaults 0/4",
        )
        analyzed: List[Dict[str, Any]] = []
        default_analysis: Dict[str, Dict[str, Any]] = {}
        progress_path = RUN_DIR / "explain_analyze_pairs_v86.json"

        def write_progress() -> None:
            write_json(
                progress_path,
                {
                    "timestamp": now_iso(),
                    "attempt": int(config.get("attempt", 1)),
                    "complete": False,
                    "original_structure_pair_count": len(structural_pairs),
                    "attempt2_original_structure_pair_count": 49,
                    "deduped_candidate_fingerprint_count": len(candidate_groups),
                    "default_analysis": default_analysis,
                    "pairs": analyzed,
                },
            )

        candidate_index = 0
        for shape_index, shape in enumerate(config["shapes"], start=1):
            shape_id = shape["id"]
            shape_groups = [
                group for group in candidate_groups if group["shape"]["id"] == shape_id
            ]
            log(f"DEFAULT {shape_index}/4 {shape_id} (single cached ANALYZE)")
            try:
                default_meta = plan_meta(
                    explain(config, sql_bodies[shape_id], default, analyze=True)
                )
                default_analysis[shape_id] = {
                    "status": "complete",
                    "sample_count": 1,
                    "meta": default_meta,
                    "error": None,
                    "censored": False,
                }
            except Exception as exc:  # noqa: BLE001
                error = str(exc)[:1200]
                default_analysis[shape_id] = {
                    "status": "timeout" if is_timeout_error(error) else "error",
                    "sample_count": 0,
                    "meta": None,
                    "error": error,
                    "censored": is_timeout_error(error),
                }
                log(f"DEFAULT failed {shape_id}: {error}")
                for group in shape_groups:
                    analyzed.append(
                        analyze_error_record(
                            config,
                            shape,
                            group["representative"],
                            vectors,
                            group["execution_fingerprint_hash"],
                            group["aliases"],
                            error,
                            default_failed=True,
                        )
                    )
                write_progress()
                update_status(
                    f"Phase A attempt{int(config.get('attempt', 1))} ANALYZE",
                    f"attempt2_original_pairs=49; fresh_original_pairs={len(structural_pairs)}; "
                    f"deduped_execution_fingerprints={len(candidate_groups)}; "
                    f"{shape_id} default failed closed; defaults={shape_index}/4",
                )
                continue

            write_progress()
            for group in shape_groups:
                candidate_index += 1
                representative = group["representative"]
                log(
                    f"CANDIDATE {candidate_index}/{len(candidate_groups)} "
                    f"{shape_id}/{representative} aliases={len(group['aliases'])}"
                )
                try:
                    record = analyze_candidate(
                        config,
                        shape,
                        sql_bodies[shape_id],
                        representative,
                        default,
                        vectors[representative],
                        default_meta,
                        group["execution_fingerprint_hash"],
                        group["aliases"],
                    )
                except Exception as exc:  # noqa: BLE001
                    error = str(exc)[:1200]
                    record = analyze_error_record(
                        config,
                        shape,
                        representative,
                        vectors,
                        group["execution_fingerprint_hash"],
                        group["aliases"],
                        error,
                    )
                    log(f"CANDIDATE failed {shape_id}/{representative}: {error}")
                analyzed.append(record)
                write_progress()
                update_status(
                    f"Phase A attempt{int(config.get('attempt', 1))} ANALYZE",
                    f"attempt2_original_pairs=49; fresh_original_pairs={len(structural_pairs)}; "
                    f"deduped_execution_fingerprints={len(candidate_groups)}; "
                    f"progress={candidate_index}/{len(candidate_groups)} candidates; defaults={shape_index}/4",
                )
        write_json(
            progress_path,
            {
                "timestamp": now_iso(),
                "attempt": int(config.get("attempt", 1)),
                "complete": True,
                "original_structure_pair_count": len(structural_pairs),
                "attempt2_original_structure_pair_count": 49,
                "deduped_candidate_fingerprint_count": len(candidate_groups),
                "default_analysis": default_analysis,
                "pairs": analyzed,
            },
        )

        eligible = [item for item in analyzed if item.get("eligible")]
        summaries = family_summary(config["shapes"], default_analysis, analyzed)
        node_census: Counter[str] = Counter()
        strategy_census: Counter[str] = Counter()
        for meta in meta_by_seed_shape.values():
            node_census.update(meta.get("counts") or {})
            strategy_census.update(meta.get("strategies") or {})
        census = {
            "timestamp": now_iso(),
            "controller": config["controller"],
            "executor": "Codex",
            "attempt": int(config.get("attempt", 1)),
            "seed_count": len(vectors),
            "shape_count": len(config["shapes"]),
            "explain_cell_count": len(plan_grid),
            "signature_distinct_vector_count": len(profile_groups),
            "collapsed_profiles": collapsed,
            "node_occurrences": dict(sorted(node_census.items())),
            "strategy_occurrences": dict(sorted(strategy_census.items())),
            "original_structural_pairs": [
                {"vector": seed, "shape_id": shape["id"]} for seed, shape in structural_pairs
            ],
            "attempt2_original_structure_pair_count": 49,
            "deduped_candidate_fingerprint_count": len(candidate_groups),
            "deduped_candidate_groups": [
                {
                    "shape_id": group["shape"]["id"],
                    "execution_fingerprint_hash": group["execution_fingerprint_hash"],
                    "representative": group["representative"],
                    "aliases": group["aliases"],
                }
                for group in candidate_groups
            ],
        }
        write_json(CENSUS_PATH, census)
        classification = (
            "phase_a_pass / strict_same_vector_eligible"
            if eligible
            else "phase_a_empty / no_high_probability_operator_remaining"
        )
        decision = {
            "phase": "A",
            "controller": config["controller"],
            "executor": "Codex",
            "attempt": int(config.get("attempt", 1)),
            "attempt1_recovery": config.get("attempt1_recovery"),
            "attempt2_recovery": config.get("attempt2_recovery"),
            "attempt3_recovery": config.get("attempt3_recovery"),
            "timestamp": now_iso(),
            "classification": classification,
            "phase_a_eligible": bool(eligible),
            "eligible_set_size": len(eligible),
            "eligible_set": eligible,
            "runtime_recommended": bool(eligible),
            "runtime_started": False,
            "runtime_blocked_until_main_agent_report": bool(eligible),
            "stop_reason": None if eligible else "eligible_set_empty",
            "fallback_db_qps": float(config["fallback_db_qps"]),
            "map_summary": {
                "seed_count": len(vectors),
                "shape_count": len(config["shapes"]),
                "explain_cell_count": len(plan_grid),
                "signature_distinct_vector_count": len(profile_groups),
                "representative_nondefault_profiles": representatives,
                "attempt2_original_structure_pair_count": 49,
                "original_structure_pair_count": len(structural_pairs),
                "structure_changing_pair_count": len(structural_pairs),
                "deduped_candidate_fingerprint_count": len(candidate_groups),
                "numeric_duplicate_seeds": [
                    item for item in collapsed if len(item["seeds"]) > 1
                ],
            },
            "default_analysis": default_analysis,
            "analyzed_pairs": analyzed,
            "family_summary": summaries,
            "answer_gate_summary": {
                "checked_pairs": sum(
                    bool((item.get("answer_invariant") or {}).get("checked"))
                    for item in analyzed
                ),
                "eligible_without_answer": 0,
                "bypassed": False,
                "reason": (
                    "no candidate passed structure+peak+elapsed+IndexOnly pre-gates"
                    if not any(
                        bool((item.get("answer_invariant") or {}).get("checked"))
                        for item in analyzed
                    )
                    else "answer invariants executed for pre-gate survivors"
                ),
            },
            "rules": {
                "same_global_seven_vector": True,
                "no_cross_vector_splice": True,
                "exactly_seven_cost_keys": list(SEVEN),
                "fixed_settings": config["fixed_settings"],
                "min_peak_or_justified_concurrent_drop_mib": 328.0,
                "min_elapsed_speedup_pct": 15.0,
                "reject_new_indexonly_cliff": True,
                "answer_invariants": True,
            },
            "ownership": {
                **ownership,
                "memory_pool_ports": list(range(57433, 57441)),
                "cpu_pool_ports": list(range(56433, 56441)),
                "binary_plan_resource_tuner_modified": False,
                "hybench_started": False,
                "baseline_started": False,
                "candidate_started": False,
                "full8_started": False,
            },
            "artifacts": {
                "config": str(CONFIG_PATH),
                "run_dir": str(RUN_DIR),
                "plan_grid": str(RUN_DIR / "plan_grid_v86.json"),
                "analyze_pairs": str(RUN_DIR / "explain_analyze_pairs_v86.json"),
                "census": str(CENSUS_PATH),
                "markdown": str(REPORT_PATH),
            },
            "elapsed_wall_s": time.time() - started,
        }
        write_json(DECISION_PATH, decision)
        write_json(DATED_DECISION_PATH, decision)
        write_markdown(decision)
        timeout_count = sum(bool(item.get("censored")) for item in analyzed)
        error_count = sum(bool(item.get("error")) for item in analyzed)
        final_detail = (
            f"attempt{int(config.get('attempt', 1))} complete; "
            f"attempt2_original_pairs=49; fresh_original_pairs={len(structural_pairs)}; "
            f"deduped_execution_fingerprints={len(candidate_groups)}; "
            f"timeouts={timeout_count}; errors={error_count}"
        )
        if eligible:
            update_status(
                "Phase A complete; awaiting main-agent decision",
                final_detail + "; strict eligible set non-empty; runtime not started",
                eligible_count=len(eligible),
                runtime_recommended=True,
            )
        else:
            update_status(
                "stopped: phase_a_empty",
                final_detail + "; no_high_probability_operator_remaining; fallback preserved",
                eligible_count=0,
                runtime_recommended=False,
            )
        log(f"Phase A complete: {classification}; eligible={len(eligible)}")
        return 0
    except Exception as exc:  # noqa: BLE001
        failure = {
            "phase": "A",
            "controller": config.get("controller"),
            "executor": "Codex",
            "attempt": int(config.get("attempt", 1)),
            "timestamp": now_iso(),
            "classification": "phase_a_failed",
            "phase_a_eligible": False,
            "eligible_set_size": 0,
            "eligible_set": [],
            "runtime_recommended": False,
            "runtime_started": False,
            "stop_reason": "phase_a_error",
            "error": str(exc)[:2000],
            "fallback_db_qps": float(config.get("fallback_db_qps", 114.12708344892893)),
            "elapsed_wall_s": time.time() - started,
        }
        write_json(DECISION_PATH, failure)
        write_json(DATED_DECISION_PATH, failure)
        update_status(
            "stopped: phase_a_failed",
            str(exc)[:500].replace("|", "/"),
            eligible_count=0,
            runtime_recommended=False,
        )
        log(f"Phase A failed: {exc}")
        return 1
    finally:
        stop_worker(container)


if __name__ == "__main__":
    raise SystemExit(main())
