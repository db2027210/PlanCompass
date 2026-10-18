#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bonect_interval_lineage import (  # noqa: E402
    COST_PARAMS,
    IntervalInvariantError,
    candidate_gate,
    canonical_sha256,
    dedupe_plan_resource_candidates,
    exact_cost_params,
    freeze_round_manifest,
    io_resource_score,
    make_root_manifest,
    manifest_sha256,
    normalize_bounds,
)
from extract_plan_trees import normalize_plan_structure  # noqa: E402
from plan_resource_estimator import PlanResourceEstimator  # noqa: E402
from shell.bonect_full_grid_estimator_scan import (  # noqa: E402
    build_explained_queries,
    estimate_from_explained,
    make_connection,
    normalize_workload_rows,
    set_local_sql,
)


RUN_ROOT = ROOT / "history/bonect_parallel_vm_replay/pg_bonect_p2_12h_four_scenario_20260714_0218"
PACK_DIR = RUN_ROOT / "io_stage_a/io_answer_blind_recall_pack_20260714_1825"
SCAN_DIR = RUN_ROOT / "io_scan_prep/full_grid_5e7"
DEFAULT_OUTPUT = (
    RUN_ROOT
    / "io_stage_a/io_historical_bonect_memory_reuse_audit_20260714/nested_interval_run_resource_only_v4"
)
CONFIG_PATH = ROOT / "config/generated/bonect_phase2_12h_freeze_20260714/io/io_provisional_vm.json"
FROZEN_CANDIDATES = PACK_DIR / "io_answer_blind_log_boundary_candidates_FROZEN.json"
GENERATION_SPEC = PACK_DIR / "IO_ANSWER_BLIND_GENERATION_SPEC.json"
GENERATION_TRACE = PACK_DIR / "IO_ANSWER_BLIND_GENERATION_TRACE.json"

DEFAULT_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}

# Frozen far-default resource baseline used to normalize estimator units.
DEFAULT_REAL_RESOURCE = {
    "cpu": 12.870759947853866,
    "memory": 26.330939361027312,
    "io": 83.79153289453993,
}
DEFAULT_REAL_FEEDBACK_SOURCE = (
    RUN_ROOT
    / "io_stage_a/screen_2m_mild_diagonal_batch1_20260714_1629/workers/bonect-replay-v87-3/"
    "waves/wave_000/validation_results.jsonl"
)

def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_hash(value: Any) -> str:
    return canonical_sha256(value)


def walk_plan(root: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    stack: List[Mapping[str, Any]] = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed([child for child in (node.get("Plans") or []) if isinstance(child, Mapping)]))


def effective_params(conn: Any, params: Mapping[str, Any]) -> Dict[str, float]:
    cleaned = exact_cost_params(params)
    conn.execute(set_local_sql(cleaned), timeout_s=60.0)
    try:
        select_list = ", ".join(f"current_setting('{name}')" for name in COST_PARAMS)
        raw = conn.execute(f"SELECT {select_list};", timeout_s=60.0)
        row = next((line for line in raw.splitlines() if line.strip()), "")
        values = row.split("\t")
        if len(values) != len(COST_PARAMS):
            raise RuntimeError(f"cannot parse effective seven-cost settings: {raw!r}")
        return {name: float(values[index]) for index, name in enumerate(COST_PARAMS)}
    finally:
        conn.execute("ROLLBACK;", timeout_s=30.0)


def effective_interval_bounds(conn: Any, bounds: Mapping[str, Any]) -> Dict[str, List[float]]:
    normalized = normalize_bounds(bounds)
    result: Dict[str, List[float]] = {}
    for name in COST_PARAMS:
        low_params = dict(DEFAULT_PARAMS)
        high_params = dict(DEFAULT_PARAMS)
        low_params[name] = normalized[name][0]
        high_params[name] = normalized[name][1]
        low = effective_params(conn, low_params)[name]
        high = effective_params(conn, high_params)[name]
        result[name] = [min(low, high), max(low, high)]
    return result


def summarize_capture(
    explained: Sequence[Mapping[str, Any]],
    estimated_resource: Mapping[str, Any],
) -> Dict[str, Any]:
    node_types: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    query_plan_hashes: Dict[str, str] = {}
    query_costs: Dict[str, float] = {}
    root_cost_weighted = 0.0
    root_rows_weighted = 0.0
    parallel_aware = 0
    gather_workers = 0.0
    spill_risk_nodes = 0
    errors: List[str] = []
    raw_plans: List[Dict[str, Any]] = []
    for item in explained:
        queryid = str(item.get("queryid"))
        if item.get("error") or not isinstance(item.get("plan_root"), Mapping):
            errors.append(f"{queryid}: {item.get('error')}")
            continue
        root = item["plan_root"]
        replay_count = float(item.get("replay_count") or 1.0)
        normalized_plan = normalize_plan_structure(root)
        plan_hash = stable_hash(normalized_plan)
        query_plan_hashes[queryid] = plan_hash
        query_costs[queryid] = float(root.get("Total Cost") or 0.0)
        root_cost_weighted += query_costs[queryid] * replay_count
        root_rows_weighted += float(root.get("Plan Rows") or 0.0) * replay_count
        for node in walk_plan(root):
            node_type = str(node.get("Node Type") or "Unknown")
            node_types[node_type] += 1
            if "Scan" in node_type:
                category_counts["Scan"] += 1
            elif "Join" in node_type or node_type == "Nested Loop":
                category_counts["Join"] += 1
            elif "Sort" in node_type:
                category_counts["Sort"] += 1
            elif node_type == "Hash" or node_type.startswith("Hash "):
                category_counts["Hash"] += 1
            elif node_type in {"Materialize", "Memoize"}:
                category_counts["Materialize"] += 1
            elif node_type.startswith("Gather"):
                category_counts["Gather"] += 1
            if bool(node.get("Parallel Aware")):
                parallel_aware += 1
            if node_type.startswith("Gather"):
                gather_workers += float(node.get("Workers Planned") or 0.0)
            if any(token in node_type for token in ("Sort", "Hash", "Aggregate", "Materialize")):
                estimated_bytes = float(node.get("Plan Rows") or 0.0) * max(
                    1.0, float(node.get("Plan Width") or 0.0)
                )
                if estimated_bytes > 16.0 * 1024.0 * 1024.0:
                    spill_risk_nodes += 1
        raw_plans.append(
            {
                "queryid": queryid,
                "replay_count": replay_count,
                "plan_mode": item.get("plan_mode"),
                "plan_doc": item.get("plan_doc"),
                "structural_plan_hash": plan_hash,
            }
        )
    combined_plan_hash = stable_hash(query_plan_hashes)
    return {
        "estimated_resource_raw": {name: float(estimated_resource.get(name, 0.0)) for name in ("cpu", "memory", "io")},
        "plan_hashes": query_plan_hashes,
        "combined_plan_hash": combined_plan_hash,
        "query_root_costs": query_costs,
        "root_total_cost_weighted_sum": root_cost_weighted,
        "root_plan_rows_weighted_sum": root_rows_weighted,
        "node_type_counts": dict(sorted(node_types.items())),
        "category_counts": dict(sorted(category_counts.items())),
        "parallel_aware_node_count": parallel_aware,
        "gather_workers_planned_sum": gather_workers,
        "spill_risk_node_count_16mb_proxy": spill_risk_nodes,
        "error_count": len(errors),
        "errors": errors,
        "raw_plans": raw_plans,
    }


class PlanProbe:
    def __init__(self) -> None:
        config = read_json(CONFIG_PATH)
        scan_config = read_json(SCAN_DIR / "scan_config.json")
        workload_doc = read_json(SCAN_DIR / "workload_sql.json")
        self.workload_context = read_json(SCAN_DIR / "workload_context.json")
        self.queries = normalize_workload_rows(workload_doc.get("queries") or [])
        self.pg = {**scan_config["pg"], "password": "CHANGE_ME"}
        plan_capture_cfg = config.get("plan_capture") or {}
        modes = [
            str(mode).strip().lower()
            for mode in (plan_capture_cfg.get("modes") or ["generic"])
            if str(mode).strip()
        ] or ["generic"]
        custom = {
            str(key): list(value) if isinstance(value, list) else [value]
            for key, value in (plan_capture_cfg.get("custom_plan_params") or {}).items()
        }
        estimator_cfg = copy.deepcopy(((config.get("metrics") or {}).get("plan_estimator")) or {})
        estimator_cfg.setdefault("shared", {})["explain_workers"] = 1
        estimator_cfg.setdefault("logging", {})["log_topn"] = 100
        self.estimator = PlanResourceEstimator(
            pg_host=str(self.pg["host"]),
            pg_port=str(self.pg["port"]),
            pg_user=str(self.pg["user"]),
            pg_dbname=str(self.pg["dbname"]),
            plan_capture_modes=modes,
            custom_plan_params=custom,
            estimator_cfg=estimator_cfg,
        )
        self.conn = make_connection(self.pg)
        identity = self.conn.execute(
            "SELECT current_database(), inet_server_addr()::text, inet_server_port();",
            timeout_s=30.0,
        ).split("\t")
        if not identity or identity[0] != "hybench_sf10x":
            self.close()
            raise RuntimeError(f"wrong planning database identity: {identity}")

    def capture(self, candidate_id: str, params: Mapping[str, Any]) -> Dict[str, Any]:
        requested = exact_cost_params(params)
        effective = effective_params(self.conn, requested)
        explained = build_explained_queries(self.estimator, self.conn, self.queries, requested)
        estimated, _metadata = estimate_from_explained(
            self.estimator,
            explained,
            self.workload_context,
        )
        summary = summarize_capture(explained, estimated)
        if summary["error_count"]:
            raise RuntimeError(f"plan capture errors for {candidate_id}: {summary['errors'][:3]}")
        return {
            "candidate_id": candidate_id,
            "params": requested,
            "effective_params": effective,
            **summary,
        }

    def close(self) -> None:
        if getattr(self, "conn", None) is not None:
            self.conn.close()
            self.conn = None


def map_log_vector(bounds: Mapping[str, Any], vector: Sequence[Any]) -> Dict[str, float]:
    normalized = normalize_bounds(bounds)
    if len(vector) != len(COST_PARAMS):
        raise ValueError("normalized vector must contain seven values")
    result: Dict[str, float] = {}
    for index, name in enumerate(COST_PARAMS):
        low, high = normalized[name]
        fraction = min(1.0, max(0.0, float(vector[index])))
        result[name] = math.exp(math.log(low) + fraction * (math.log(high) - math.log(low)))
    return result


def derive_child_bounds(
    parent_bounds: Mapping[str, Any],
    ranked_predictions: Sequence[Mapping[str, Any]],
    topk_candidate_ids: Sequence[str],
    *,
    trim_fraction: float = 0.05,
) -> Tuple[Dict[str, List[float]], Dict[str, Any]]:
    """Drop a low log tail supported only by the frozen resource top-k."""
    if not 0.0 < trim_fraction < 0.25:
        raise ValueError("trim_fraction must be in (0, 0.25)")
    normalized = normalize_bounds(parent_bounds)
    selected_ids = [str(candidate_id) for candidate_id in topk_candidate_ids]
    if not 1 <= len(selected_ids) <= 4:
        raise IntervalInvariantError("cannot contract without one to four resource top-k candidates")
    by_id = {str(row["candidate_id"]): row for row in ranked_predictions}
    if any(candidate_id not in by_id for candidate_id in selected_ids):
        raise IntervalInvariantError("resource top-k candidate is missing from frozen predictions")
    if select_topk(ranked_predictions) != selected_ids:
        raise IntervalInvariantError("resource top-k changed before interval contraction")
    topk = [by_id[candidate_id] for candidate_id in selected_ids]
    child: Dict[str, List[float]] = {}
    support: Dict[str, Any] = {}
    for name in COST_PARAMS:
        low, high = normalized[name]
        cutoff = math.exp(math.log(low) + trim_fraction * (math.log(high) - math.log(low)))
        selected_values = [float((row.get("effective_params") or row["params"])[name]) for row in topk]
        if min(selected_values) <= cutoff:
            raise IntervalInvariantError(
                f"top-k covers proposed low-tail contraction for {name}; model/search adjustment required"
            )
        child[name] = [cutoff, high]
        support[name] = {
            "parent_low": low,
            "parent_high": high,
            "trim_fraction_log_width": trim_fraction,
            "proposed_low": cutoff,
            "topk_min": min(selected_values),
            "topk_max": max(selected_values),
            "all_topk_above_removed_tail": True,
            "topk_candidate_ids": selected_ids,
            "boundary_fill_prohibited": True,
        }
    return child, support


def predict_and_rank(
    captures: Sequence[Mapping[str, Any]],
    default_capture: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    default_est = default_capture["estimated_resource_raw"]
    rows: List[Dict[str, Any]] = []
    for capture in captures:
        estimated = capture["estimated_resource_raw"]
        predicted_resources = {
            name: DEFAULT_REAL_RESOURCE[name] * float(estimated[name]) / max(float(default_est[name]), 1e-15)
            for name in ("cpu", "memory", "io")
        }
        gate = candidate_gate(DEFAULT_REAL_RESOURCE, predicted_resources)
        score_evidence = io_resource_score(DEFAULT_REAL_RESOURCE, predicted_resources)
        rows.append(
            {
                "candidate_id": capture["candidate_id"],
                "params": capture["params"],
                "effective_params": capture["effective_params"],
                "plan_hashes": capture["plan_hashes"],
                "combined_plan_hash": capture["combined_plan_hash"],
                "predicted_resources": predicted_resources,
                "candidate_gate": gate,
                "resource_score": score_evidence["resource_score"],
                "resource_score_evidence": score_evidence,
                "plan_features": {
                    name: capture[name]
                    for name in (
                        "node_type_counts",
                        "category_counts",
                        "parallel_aware_node_count",
                        "gather_workers_planned_sum",
                        "spill_risk_node_count_16mb_proxy",
                        "root_total_cost_weighted_sum",
                        "root_plan_rows_weighted_sum",
                    )
                },
            }
        )

    kept, dropped = dedupe_plan_resource_candidates(rows)
    kept.sort(
        key=lambda row: (
            -float(row["resource_score"]),
            str(row.get("candidate_id")),
        )
    )
    return kept, dropped, rows


def select_topk(ranked: Sequence[Mapping[str, Any]]) -> List[str]:
    gated = [row for row in ranked if bool((row.get("candidate_gate") or {}).get("passed"))]
    return [str(row["candidate_id"]) for row in gated[:4]]


def initial_probe_specs() -> Tuple[List[Dict[str, Any]], List[List[float]]]:
    candidates = read_json(FROZEN_CANDIDATES)
    trace_rows = read_json(GENERATION_TRACE)["rows"]
    vectors = {str(row["candidate_id"]): list(row["normalized_log_vector"]) for row in trace_rows}
    specs = [
        {
            "candidate_id": str(row["candidate_id"]),
            "params": exact_cost_params(row["params"]),
        }
        for row in candidates
    ]
    return specs, [vectors[row["candidate_id"]] for row in specs]


def capture_specs(probe: PlanProbe, specs: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for index, spec in enumerate(specs, start=1):
        print(f"plan-only capture {index}/{len(specs)} {spec['candidate_id']}", flush=True)
        rows.append(probe.capture(str(spec["candidate_id"]), spec["params"]))
    return rows


def build_non_teacher_qps_calibration(
    probe: PlanProbe,
    default_capture: Mapping[str, Any],
) -> Dict[str, Any]:
    default_cost = float(default_capture["root_total_cost_weighted_sum"])
    rows: List[Dict[str, Any]] = []
    for run_dir, candidate_wave, baseline_wave in NON_TEACHER_CALIBRATION_WAVES:
        candidate_doc = read_json(run_dir / "waves" / candidate_wave / "candidate.json")
        candidate_row = candidate_doc[0] if isinstance(candidate_doc, list) else candidate_doc
        result = read_jsonl_first(run_dir / "waves" / candidate_wave / "validation_results.jsonl")
        baseline = read_jsonl_first(run_dir / "waves" / baseline_wave / "validation_results.jsonl")
        candidate_id = str(result.get("candidate_id") or candidate_row.get("candidate_id"))
        capture = probe.capture(f"calibration_{candidate_id}", candidate_row["params"])
        candidate_qps = float((result.get("db_delta") or {}).get("db_qps"))
        baseline_qps = float((baseline.get("db_delta") or {}).get("db_qps"))
        actual_gain = (candidate_qps / baseline_qps - 1.0) * 100.0
        proxy_gain = (
            default_cost / max(float(capture["root_total_cost_weighted_sum"]), 1e-15) - 1.0
        ) * 100.0
        rows.append(
            {
                "candidate_id": candidate_id,
                "params_sha256": canonical_sha256(exact_cost_params(candidate_row["params"])),
                "plan_capture_sha256": canonical_sha256(capture),
                "combined_plan_hash": capture["combined_plan_hash"],
                "baseline_qps": baseline_qps,
                "candidate_qps": candidate_qps,
                "actual_db_qps_gain_pct": actual_gain,
                "inverse_plan_cost_proxy_gain_pct": proxy_gain,
                "absolute_proxy_error_pct": abs(actual_gain - proxy_gain),
                "candidate_result_source": str(
                    run_dir / "waves" / candidate_wave / "validation_results.jsonl"
                ),
                "candidate_result_source_sha256": file_sha256(
                    run_dir / "waves" / candidate_wave / "validation_results.jsonl"
                ),
                "baseline_result_source": str(
                    run_dir / "waves" / baseline_wave / "validation_results.jsonl"
                ),
                "baseline_result_source_sha256": file_sha256(
                    run_dir / "waves" / baseline_wave / "validation_results.jsonl"
                ),
                "teacher_or_holdout_label": False,
            }
        )
    max_error = max(float(row["absolute_proxy_error_pct"]) for row in rows)
    result = {
        "event": "io_nested_interval_non_teacher_qps_proxy_calibration",
        "calibration_row_count": len(rows),
        "rows": rows,
        "max_absolute_proxy_error_pct": max_error,
        "qps_uncertainty_pct": max(35.0, max_error * 1.10),
        "uncertainty_rule": "110_percent_of_max_absolute_error_on_current_v87_3_non_teacher_labels",
        "teacher_or_holdout_loaded": False,
    }
    result["calibration_sha256"] = canonical_sha256(result)
    return result


def validate_previous_receipt(output_dir: Path, round_index: int, previous_manifest: Mapping[str, Any]) -> None:
    receipt_path = output_dir / f"ROUND{round_index - 1}_HOLDOUT_AUDIT.json"
    if not receipt_path.exists():
        raise IntervalInvariantError(f"previous post-freeze holdout receipt missing: {receipt_path}")
    receipt = read_json(receipt_path)
    if str(receipt.get("interval_manifest_sha256")) != str(previous_manifest.get("interval_manifest_sha256")):
        raise IntervalInvariantError("wrong_parent_feedback: previous receipt manifest SHA mismatch")
    reasons = set(str(reason) for reason in (receipt.get("fail_closed_reasons") or []))
    if reasons - {"candidate_gate_failed"}:
        raise IntervalInvariantError("previous interval is fail-closed: " + ",".join(sorted(reasons)))
    if not bool(receipt.get("interval_invariants_passed")):
        raise IntervalInvariantError("previous interval invariants did not pass")
    if not bool(receipt.get("no_teacher_leakage")):
        raise IntervalInvariantError("teacher_leakage: previous receipt failed")


def prepare_root(
    probe: PlanProbe,
    output_dir: Path,
) -> Tuple[Dict[str, Any], Dict[str, Any], List[List[float]], Dict[str, Any]]:
    root_manifest_path = output_dir / "ROUND0_ROOT_INTERVAL.json"
    root_captures_path = output_dir / "ROUND0_PLAN_CAPTURES.json"
    root_predictions_path = output_dir / "ROUND0_PREDICTIONS.json"
    default_path = output_dir / "DEFAULT_PLAN_CAPTURE.json"
    calibration_path = output_dir / "NON_TEACHER_QPS_CALIBRATION.json"
    specs, vectors = initial_probe_specs()
    generation = read_json(GENERATION_SPEC)
    bounds = {
        name: [generation["positive_log_bounds"][name]["low"], generation["positive_log_bounds"][name]["high"]]
        for name in COST_PARAMS
    }
    if all(
        path.exists()
        for path in (root_manifest_path, root_captures_path, root_predictions_path, default_path, calibration_path)
    ):
        return (
            read_json(root_manifest_path),
            read_json(default_path),
            vectors,
            read_json(calibration_path),
        )

    effective_bounds = effective_interval_bounds(probe.conn, bounds)
    root_manifest = make_root_manifest(
        bounds,
        effective_bounds=effective_bounds,
        source="frozen_28_log_latin_plan_estimator_probe_pool; no holdout input",
    )
    default_capture = probe.capture("current_default", DEFAULT_PARAMS)
    calibration = build_non_teacher_qps_calibration(probe, default_capture)
    captures = capture_specs(probe, specs)
    ranked, dropped, raw_predictions = predict_and_rank(
        captures,
        default_capture,
        qps_uncertainty_pct=float(calibration["qps_uncertainty_pct"]),
    )
    topk = select_topk(ranked)
    ranked = reordered_for_topk(ranked, topk)
    root_evidence = {
        "event": "io_nested_interval_round0_inner_probe_evidence",
        "probe_count_before_dedupe": len(captures),
        "probe_count_after_dedupe": len(ranked),
        "frozen_pack_sha256": file_sha256(FROZEN_CANDIDATES),
        "generation_trace_sha256": file_sha256(GENERATION_TRACE),
        "default_real_resource": DEFAULT_REAL_RESOURCE,
        "default_real_feedback_source": str(DEFAULT_REAL_FEEDBACK_SOURCE),
        "default_real_feedback_source_sha256": (
            file_sha256(DEFAULT_REAL_FEEDBACK_SOURCE) if DEFAULT_REAL_FEEDBACK_SOURCE.exists() else None
        ),
        "non_teacher_qps_calibration_sha256": calibration["calibration_sha256"],
        "qps_uncertainty_pct": calibration["qps_uncertainty_pct"],
        "ranked_predictions": ranked,
        "topk_candidate_ids": topk,
        "diagnostic_topk_candidate_ids": [row["candidate_id"] for row in ranked[:4]],
        "dedupe_dropped": dropped,
        "raw_prediction_sha256": canonical_sha256(raw_predictions),
        "holdout_loaded": False,
    }
    root_evidence["evidence_sha256"] = canonical_sha256(root_evidence)
    write_json(root_manifest_path, root_manifest)
    write_json(default_path, default_capture)
    write_json(calibration_path, calibration)
    write_json(root_captures_path, captures)
    write_json(root_predictions_path, root_evidence)
    write_json(output_dir / "ROUND0_CANDIDATES.json", specs)
    write_json(output_dir / "ROUND0_ORDER.json", [row["candidate_id"] for row in ranked])
    return root_manifest, default_capture, vectors, calibration


def run_target_round(probe: PlanProbe, output_dir: Path, target_round: int) -> Path:
    root_manifest, default_capture, vectors, calibration = prepare_root(probe, output_dir)
    if target_round == 1:
        parent_manifest = root_manifest
        parent_predictions = read_json(output_dir / "ROUND0_PREDICTIONS.json")
    else:
        parent_manifest_path = output_dir / f"ROUND{target_round - 1}_FROZEN_INTERVAL.json"
        parent_predictions_path = output_dir / f"ROUND{target_round - 1}_PREDICTIONS.json"
        if not parent_manifest_path.exists() or not parent_predictions_path.exists():
            raise IntervalInvariantError("wrong_parent_feedback: previous frozen round is missing")
        parent_manifest = read_json(parent_manifest_path)
        parent_predictions = read_json(parent_predictions_path)
        validate_previous_receipt(output_dir, target_round, parent_manifest)

    parent_bounds = parent_manifest["requested_bounds"]
    child_bounds, contraction_support = derive_child_bounds(
        parent_bounds,
        parent_predictions["ranked_predictions"],
    )
    specs = [
        {
            "candidate_id": f"io_nested_r{target_round}_{index:03d}",
            "params": map_log_vector(child_bounds, vector),
        }
        for index, vector in enumerate(vectors, start=1)
    ]
    effective_bounds = effective_interval_bounds(probe.conn, child_bounds)
    captures = capture_specs(probe, specs)
    ranked, dropped, raw_predictions = predict_and_rank(
        captures,
        default_capture,
        qps_uncertainty_pct=float(calibration["qps_uncertainty_pct"]),
    )
    topk = select_topk(ranked)
    ranked = reordered_for_topk(ranked, topk)
    kept_ids = {str(row["candidate_id"]) for row in ranked}
    kept_captures = [row for row in captures if str(row["candidate_id"]) in kept_ids]
    capture_by_id = {str(row["candidate_id"]): row for row in kept_captures}
    frozen_candidates = [
        {
            "candidate_id": str(row["candidate_id"]),
            "params": row["params"],
            "effective_params": row["effective_params"],
            "plan_capture_sha256": canonical_sha256(capture_by_id[str(row["candidate_id"])]),
        }
        for row in ranked
    ]
    parent_feedback = {
        "parent_round": int(parent_manifest["round"]),
        "parent_interval_sha": str(parent_manifest["interval_manifest_sha256"]),
        "feedback_class": "plan_capture_resource_estimator_only_non_teacher",
        "feedback_sha256": str(parent_predictions["evidence_sha256"]),
    }
    manifest = freeze_round_manifest(
        round_index=target_round,
        requested_bounds=child_bounds,
        effective_bounds=effective_bounds,
        parent_manifest=parent_manifest,
        parent_feedback=parent_feedback,
        candidates=frozen_candidates,
        ranked_predictions=ranked,
        topk_candidate_ids=topk,
        generation_provenance={
            "probe_source": "frozen_28_normalized_log_vectors_mapped_into_parent_child_interval",
            "frozen_pack_sha256": file_sha256(FROZEN_CANDIDATES),
            "generation_trace_sha256": file_sha256(GENERATION_TRACE),
            "non_teacher_qps_calibration_sha256": calibration["calibration_sha256"],
            "holdout_loaded_before_freeze": False,
            "teacher_coordinate_or_label_available_to_controller": False,
            "contraction_rule": "remove lowest five percent of log width only when all prior top-k probes are above tail",
            "contraction_support": contraction_support,
        },
    )
    predictions = {
        "event": "io_nested_interval_inner_prediction_frozen",
        "round": target_round,
        "interval_manifest_sha256": manifest["interval_manifest_sha256"],
        "ranked_predictions": ranked,
        "topk_candidate_ids": topk,
        "diagnostic_topk_candidate_ids": [row["candidate_id"] for row in ranked[:4]],
        "dedupe_dropped": dropped,
        "raw_prediction_sha256": canonical_sha256(raw_predictions),
        "holdout_loaded": False,
    }
    predictions["evidence_sha256"] = canonical_sha256(predictions)
    write_json(output_dir / f"ROUND{target_round}_PLAN_CAPTURES.json", captures)
    write_json(output_dir / f"ROUND{target_round}_CANDIDATES.json", manifest["candidates"])
    write_json(output_dir / f"ROUND{target_round}_ORDER.json", [row["candidate_id"] for row in ranked])
    write_json(output_dir / f"ROUND{target_round}_PREDICTIONS.json", predictions)
    manifest["inner_prediction_evidence_sha256"] = predictions["evidence_sha256"]
    manifest["interval_manifest_sha256"] = manifest_sha256(manifest)
    write_json(output_dir / f"ROUND{target_round}_FROZEN_INTERVAL.json", manifest)
    print(
        json.dumps(
            {
                "event": "frozen_before_holdout",
                "round": target_round,
                "manifest": str(output_dir / f"ROUND{target_round}_FROZEN_INTERVAL.json"),
                "manifest_sha256": manifest["interval_manifest_sha256"],
                "topk": topk,
                "diagnostic_topk": [row["candidate_id"] for row in ranked[:4]],
                "holdout_loaded": False,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return output_dir / f"ROUND{target_round}_FROZEN_INTERVAL.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one IO nested interval plan-only/estimator round.")
    parser.add_argument("--target-round", type=int, choices=(1, 2, 3), required=True)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    probe = PlanProbe()
    try:
        run_target_round(probe, output_dir, args.target_round)
    finally:
        probe.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
