#!/usr/bin/env python3
"""EXPLAIN-only audit that verifies TPC-C candidate cost parameters take effect."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Dict, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tools.build_tpcc_batch06_prep_rangebound_discovery as discovery


common = discovery.common
FORMAL_STATE = discovery.FORMAL_STATE
FORMAL_STATE_SHA = discovery.EXPECTED_FORMAL_STATE_SHA256
OUTPUT = Path("/tmp/tpcc_estimator_extreme_param_audit_v1.json")
PARAM_NAMES = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)


def canonical_sha(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def value_at(
    name: str,
    fraction: float,
    ranges: Mapping[str, Any],
) -> float:
    return discovery.value_at_fraction(name, fraction, ranges[name])


def effective_settings(
    inner: Any,
    params: Mapping[str, float],
) -> Dict[str, float]:
    set_statements = inner._candidate_param_set_sql(dict(params))
    settings_sql = ", ".join(
        f"'{name}', current_setting('{name}')::double precision"
        for name in PARAM_NAMES
    )
    sql = ";\n".join(
        [
            "BEGIN",
            *set_statements,
            f"SELECT json_build_object({settings_sql})::text",
        ]
    ) + ";"
    raw = inner._psql_query(sql)
    records = []
    for line in raw.splitlines():
        text = line.strip()
        if text.startswith("{") and text.endswith("}"):
            try:
                records.append(json.loads(text))
            except json.JSONDecodeError:
                continue
    if len(records) != 1:
        raise RuntimeError(
            f"effective-setting audit expected one JSON object: {raw[:500]!r}"
        )
    return {name: float(records[0][name]) for name in PARAM_NAMES}


def query_feature_summary(metadata: Mapping[str, Any]) -> list[Dict[str, Any]]:
    result = []
    for item in metadata.get("query_features") or []:
        result.append(
            {
                "queryid": item.get("queryid"),
                "plan_fingerprint": item.get("plan_fingerprint"),
                "cpu_sha256": canonical_sha(item.get("cpu") or {}),
                "io_sha256": canonical_sha(item.get("io") or {}),
                "memory_sha256": canonical_sha(item.get("memory") or {}),
            }
        )
    return result


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"output already exists: {OUTPUT}")
    if common.sha256(FORMAL_STATE) != FORMAL_STATE_SHA:
        raise RuntimeError("formal state changed before estimator audit")
    state = load_json(FORMAL_STATE)
    config = load_json(common.CONFIG)
    ranges = config["param_ranges"]
    ranking = common.load_ranking()
    source_anchor = ranking["candidate_rankings"][0]
    queries = common.query_set(source_anchor)
    Estimator = common.load_estimator_class()
    estimator = Estimator(
        pg_host="127.0.0.1",
        pg_port="59640",
        pg_user="postgres",
        pg_dbname="fullgrid_tpcc",
        estimator_cfg=copy.deepcopy(config["metrics"]["plan_estimator"]),
        plan_capture_modes=["generic"],
        verbose=False,
    )
    estimator.calibrate(
        queries,
        source_anchor["initial_params"],
        source_anchor["initial_resource"],
    )

    params_a = {
        "seq_page_cost": value_at("seq_page_cost", 0.05, ranges),
        "random_page_cost": value_at("random_page_cost", 0.95, ranges),
        "cpu_tuple_cost": value_at("cpu_tuple_cost", 0.95, ranges),
        "cpu_index_tuple_cost": value_at(
            "cpu_index_tuple_cost", 0.95, ranges
        ),
        "cpu_operator_cost": value_at("cpu_operator_cost", 0.95, ranges),
        "parallel_setup_cost": value_at(
            "parallel_setup_cost", 0.95, ranges
        ),
        "parallel_tuple_cost": value_at(
            "parallel_tuple_cost", 0.95, ranges
        ),
    }
    params_b = {
        "seq_page_cost": value_at("seq_page_cost", 0.95, ranges),
        "random_page_cost": value_at("random_page_cost", 0.05, ranges),
        "cpu_tuple_cost": value_at("cpu_tuple_cost", 0.05, ranges),
        "cpu_index_tuple_cost": value_at(
            "cpu_index_tuple_cost", 0.05, ranges
        ),
        "cpu_operator_cost": value_at("cpu_operator_cost", 0.05, ranges),
        "parallel_setup_cost": value_at(
            "parallel_setup_cost", 0.05, ranges
        ),
        "parallel_tuple_cost": value_at(
            "parallel_tuple_cost", 0.05, ranges
        ),
    }

    effective_a = effective_settings(estimator._inner, params_a)
    effective_b = effective_settings(estimator._inner, params_b)
    resource_a, _stats_a, metadata_a = estimator.estimate_with_metadata(
        queries,
        params_a,
    )
    resource_b, _stats_b, metadata_b = estimator.estimate_with_metadata(
        queries,
        params_b,
    )
    summary_a = query_feature_summary(metadata_a)
    summary_b = query_feature_summary(metadata_b)
    by_query_a = {str(item["queryid"]): item for item in summary_a}
    by_query_b = {str(item["queryid"]): item for item in summary_b}
    common_queryids = sorted(set(by_query_a).intersection(by_query_b))
    plan_changed = [
        queryid
        for queryid in common_queryids
        if by_query_a[queryid]["plan_fingerprint"]
        != by_query_b[queryid]["plan_fingerprint"]
    ]
    feature_changed = [
        queryid
        for queryid in common_queryids
        if any(
            by_query_a[queryid][field] != by_query_b[queryid][field]
            for field in ("cpu_sha256", "io_sha256", "memory_sha256")
        )
    ]

    representative = queries[0]
    plan_a, mode_a = estimator._inner._explain_query_json(
        representative["query"],
        mode="generic",
        queryid=representative.get("queryid"),
        params=params_a,
    )
    plan_b, mode_b = estimator._inner._explain_query_json(
        representative["query"],
        mode="generic",
        queryid=representative.get("queryid"),
        params=params_b,
    )
    root_a = plan_a.get("Plan") if isinstance(plan_a, dict) else plan_a
    root_b = plan_b.get("Plan") if isinstance(plan_b, dict) else plan_b
    settings_effective = all(
        math.isclose(
            effective[name],
            requested[name],
            rel_tol=1e-5,
            abs_tol=1e-15,
        )
        for requested, effective in (
            (params_a, effective_a),
            (params_b, effective_b),
        )
        for name in PARAM_NAMES
    )
    output = {
        "schema_version": "tpcc_estimator_extreme_param_audit_v1",
        "explain_only": True,
        "workload_invocations": 0,
        "formal_state_path": str(FORMAL_STATE),
        "formal_state_sha256": FORMAL_STATE_SHA,
        "formal_state_or_range_mutated": False,
        "plan_resource_estimator_path": str(
            Path(sys.modules[estimator._inner.__class__.__module__].__file__)
        ),
        "params_a": params_a,
        "params_b": params_b,
        "metadata_current_params_a": metadata_a.get("current_params"),
        "metadata_current_params_b": metadata_b.get("current_params"),
        "effective_settings_a": effective_a,
        "effective_settings_b": effective_b,
        "set_local_effective": settings_effective,
        "workload_plan_fingerprint_a": metadata_a.get(
            "workload_plan_fingerprint"
        ),
        "workload_plan_fingerprint_b": metadata_b.get(
            "workload_plan_fingerprint"
        ),
        "feature_ratios_a": metadata_a.get("feature_ratios"),
        "feature_ratios_b": metadata_b.get("feature_ratios"),
        "predicted_resource_a": resource_a,
        "predicted_resource_b": resource_b,
        "query_feature_count_a": len(summary_a),
        "query_feature_count_b": len(summary_b),
        "query_plan_changed_count": len(plan_changed),
        "query_plan_changed_ids": plan_changed,
        "query_structural_feature_changed_count": len(feature_changed),
        "query_structural_feature_changed_ids": feature_changed,
        "representative_query": {
            "queryid": representative.get("queryid"),
            "mode_a": mode_a,
            "mode_b": mode_b,
            "raw_plan_sha256_a": canonical_sha(plan_a),
            "raw_plan_sha256_b": canonical_sha(plan_b),
            "structure_fingerprint_a": estimator._plan_fingerprint(root_a),
            "structure_fingerprint_b": estimator._plan_fingerprint(root_b),
            "total_cost_a": (
                root_a.get("Total Cost") if isinstance(root_a, dict) else None
            ),
            "total_cost_b": (
                root_b.get("Total Cost") if isinstance(root_b, dict) else None
            ),
        },
        "query_features_a": summary_a,
        "query_features_b": summary_b,
        "diagnosis": {
            "set_local_missing": not settings_effective,
            "planner_structure_insensitive_to_extremes": (
                settings_effective and not plan_changed
            ),
            "structural_features_insensitive_to_extremes": (
                settings_effective and not feature_changed
            ),
        },
    }
    OUTPUT.write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(OUTPUT),
                "sha256": common.sha256(OUTPUT),
                "set_local_effective": settings_effective,
                "query_plan_changed_count": len(plan_changed),
                "query_structural_feature_changed_count": len(feature_changed),
                "workload_plan_fingerprint_changed": (
                    metadata_a.get("workload_plan_fingerprint")
                    != metadata_b.get("workload_plan_fingerprint")
                ),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if settings_effective else 2


if __name__ == "__main__":
    raise SystemExit(main())
