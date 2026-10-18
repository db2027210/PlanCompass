from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile

from mysql_plan_clustering import (
    build_canonical_plan_bundle,
    cluster_ranked_candidates,
    formal_replay_cap,
    replay_stage_boundaries,
    validate_canonical_plan_bundle,
)
from trained_model_estimator import MySQLExplainFeatureProvider

REGISTRY_SHA = "f" * 64


def plan(*, cost: float = 1.0, rows: int = 10, key: str = "idx_a", reverse: bool = False):
    children = [
        {"table": {"table_name": "a", "access_type": "ref", "key": key,
                   "rows_examined_per_scan": rows, "cost_info": {"read_cost": cost}}},
        {"table": {"table_name": "b", "access_type": "ALL", "key": None,
                   "rows_produced_per_join": rows * 2, "cost_info": {"prefix_cost": cost * 2}}},
    ]
    if reverse:
        children.reverse()
    return {"query_block": {"select_id": 1, "nested_loop": children,
                            "cost_info": {"query_cost": cost}}}


def static_capture(query_id: str, raw, *, query_order: int = 1, query_weight: float = 1.0):
    return {
        "query_id": query_id,
        "query_sql_sha256": "a" * 64,
        "query_order": query_order,
        "query_weight": query_weight,
        "dynamic": False,
        "binding_id": "static",
        "binding_values": None,
        "plan_mode": "custom",
        "raw_explain": raw,
    }


def test_cost_and_rows_do_not_change_cluster_but_full_bundle_sha_changes() -> None:
    first = build_canonical_plan_bundle([static_capture("q1", plan())], registry_query_ids=["q1"], registry_sha256=REGISTRY_SHA, registry_authority_verified=True)
    second = build_canonical_plan_bundle(
        [static_capture("q1", plan(cost=999.0, rows=99999))], registry_query_ids=["q1"], registry_sha256=REGISTRY_SHA, registry_authority_verified=True
    )
    assert first["strict_cluster_usable"] is True
    assert first["strict_cluster_id_sha256"] == second["strict_cluster_id_sha256"]
    assert first["plan_similarity_cluster_id"] == first["strict_canonical_physical_bundle_sha256"]
    assert first["plan_similarity_cluster_id"] == second["plan_similarity_cluster_id"]
    assert first["raw_explain_bundle_sha256"] != second["raw_explain_bundle_sha256"]
    assert first["canonical_plan_bundle_sha256"] != second["canonical_plan_bundle_sha256"]
    assert first["query_captures"][0]["raw_explain"]["query_block"]["cost_info"]
    assert validate_canonical_plan_bundle(first)["valid"] is True
    tampered = copy.deepcopy(first)
    tampered["query_captures"][0]["raw_explain"]["query_block"]["nested_loop"].reverse()
    assert validate_canonical_plan_bundle(tampered)["valid"] is False


def test_join_order_index_parallel_and_ordered_subtrees_change_cluster() -> None:
    base = build_canonical_plan_bundle([static_capture("q1", plan())], registry_query_ids=["q1"], registry_sha256=REGISTRY_SHA, registry_authority_verified=True)
    variants = [
        plan(key="idx_other"),
        plan(reverse=True),
        {"query_block": {"select_id": 1, "parallel": True, "workers": 2,
                         "nested_loop": plan()["query_block"]["nested_loop"]}},
    ]
    for raw in variants:
        current = build_canonical_plan_bundle([static_capture("q1", raw)], registry_query_ids=["q1"], registry_sha256=REGISTRY_SHA, registry_authority_verified=True)
        assert current["strict_cluster_id_sha256"] != base["strict_cluster_id_sha256"]


def test_all_registry_queries_and_dynamic_generic_custom_coverage_are_required() -> None:
    missing = build_canonical_plan_bundle([static_capture("q1", plan())], registry_query_ids=["q1", "q2"], registry_sha256=REGISTRY_SHA, registry_authority_verified=True)
    assert missing["strict_cluster_usable"] is False
    assert missing["strict_cluster_id_sha256"] is None

    dynamic = []
    for binding in ("small", "large"):
        for mode in ("generic", "custom"):
            dynamic.append({
                **static_capture("q1", plan(key=f"idx_{binding}")),
                "dynamic": True,
                "binding_id": binding,
                "binding_values": [1 if binding == "small" else 1_000_000],
                "plan_mode": mode,
                "required_binding_ids": ["small", "large"],
                "required_plan_modes": ["generic", "custom"],
                "generic_plan_semantics_available": True,
            })
    complete = build_canonical_plan_bundle(dynamic, registry_query_ids=["q1"], registry_sha256=REGISTRY_SHA, registry_authority_verified=True)
    assert complete["strict_cluster_usable"] is True
    incomplete = build_canonical_plan_bundle(dynamic[:-1], registry_query_ids=["q1"], registry_sha256=REGISTRY_SHA, registry_authority_verified=True)
    assert incomplete["strict_cluster_usable"] is False


def test_missing_fingerprints_are_independent_and_best_estimate_represents_cluster() -> None:
    bundle = build_canonical_plan_bundle([static_capture("q1", plan())], registry_query_ids=["q1"], registry_sha256=REGISTRY_SHA, registry_authority_verified=True)
    result = cluster_ranked_candidates([
        {"subset_id": "low", "final_score": 1.0, "canonical_plan_bundle": copy.deepcopy(bundle)},
        {"subset_id": "high", "final_score": 3.0, "canonical_plan_bundle": copy.deepcopy(bundle)},
        {"subset_id": "missing-a", "final_score": 2.0},
        {"subset_id": "missing-b", "final_score": 4.0},
    ])
    assert result["strict_plan_cluster_count"] == 3
    assert [row["subset_id"] for row in result["representatives"]] == ["missing-b", "high", "missing-a"]
    strict = next(row for row in result["representatives"] if row["subset_id"] == "high")
    assert strict["strict_plan_cluster_member_subset_ids"] == ["low", "high"]


def test_formal_caps() -> None:
    assert formal_replay_cap("hybench_ap") == 20
    assert formal_replay_cap("tpcc") == 20
    assert formal_replay_cap("JOB") == 16
    assert formal_replay_cap("TPC-H") == 16
    assert formal_replay_cap("hybench_xp") == 10
    assert replay_stage_boundaries(4, "job") == [4]
    assert replay_stage_boundaries(8, "job") == [5, 8]
    assert replay_stage_boundaries(12, "job") == [5, 10, 12]
    assert replay_stage_boundaries(99, "job") == [5, 10, 16]
    assert replay_stage_boundaries(99, "tpcc") == [5, 10, 20]
    assert replay_stage_boundaries(99, "hybench_xp") == [5, 10]


def test_feature_provider_saves_every_full_explain_in_canonical_bundle() -> None:
    class Client:
        def __init__(self):
            self.calls = []

        def execute(self, sql):
            self.calls.append(sql)
            table = "a" if "FROM a" in sql else "b"
            return __import__("json").dumps({
                "query_block": {"select_id": 1, "table": {
                    "table_name": table, "access_type": "ALL",
                    "rows_examined_per_scan": 123,
                    "cost_info": {"read_cost": 4.5},
                }}
            })

    client = Client()
    queries = [
        {"query_id": "q1", "sql": "SELECT * FROM a", "weight": 1.0},
        {"query_id": "q2", "sql": "SELECT * FROM b", "weight": 1.0},
    ]
    registry = {
        "schema_version": "mysql_plan_workload_registry_v1",
        "immutable": True,
        "freeze_id": "freeze-test",
        "benchmark": "job",
        "queries": [
            {
                "order": index,
                "query_id": row["query_id"],
                "sql_sha256": hashlib.sha256(row["sql"].encode()).hexdigest(),
                "weight": row["weight"],
                "dynamic": False,
                "representative_bindings": [],
                "required_plan_modes": ["custom"],
            }
            for index, row in enumerate(queries, 1)
        ],
    }
    temporary = tempfile.TemporaryDirectory()
    registry_path = Path(temporary.name) / "registry.json"
    registry_path.write_text(json.dumps(registry, sort_keys=True), encoding="utf-8")
    actual_registry_sha = hashlib.sha256(registry_path.read_bytes()).hexdigest()
    provider = MySQLExplainFeatureProvider(client, {
        "workload_registry_path": str(registry_path.resolve()),
        "workload_registry_sha256": actual_registry_sha,
        "workload_freeze_id": "freeze-test",
        "benchmark_id": "job",
        "registry_query_ids": ["q1", "q2"],
        "queries": queries,
    })
    collected = provider.collect()
    bundle = collected["canonical_plan_bundle"]
    assert bundle["strict_cluster_usable"] is True
    assert bundle["registry_query_ids"] == ["q1", "q2"]
    assert len(bundle["query_captures"]) == 2
    assert all("cost_info" in row["raw_explain"]["query_block"]["table"] for row in bundle["query_captures"])
    assert client.calls == [
        "EXPLAIN FORMAT=JSON SELECT * FROM a",
        "EXPLAIN FORMAT=JSON SELECT * FROM b",
    ]
    assert bundle["registry_authority_verified"] is True
    assert bundle["plan_similarity_cluster_id"] == bundle["strict_canonical_physical_bundle_sha256"]
    temporary.cleanup()

    without_registry_hash = MySQLExplainFeatureProvider(Client(), {
        "queries": [{"query_id": "q1", "sql": "SELECT * FROM a"}],
    }).collect()["canonical_plan_bundle"]
    assert without_registry_hash["strict_cluster_usable"] is False
    assert "missing_or_invalid_registry_sha256" in without_registry_hash["strict_cluster_ineligible_reasons"]
    assert "registry_authority_not_verified" in without_registry_hash["strict_cluster_ineligible_reasons"]

    try:
        MySQLExplainFeatureProvider(Client(), {
            "workload_registry_sha256": REGISTRY_SHA,
            "registry_query_ids": ["q1", "q2"],
            "queries": [{"query_id": "q1", "sql": "SELECT * FROM a"}],
        })
        raise AssertionError("incomplete frozen registry unexpectedly accepted")
    except ValueError as exc:
        assert "exactly cover registry_query_ids" in str(exc)


def test_authoritative_registry_mismatch_and_mysql_dynamic_semantics_fail_open_to_replay() -> None:
    class Client:
        def __init__(self):
            self.calls = []

        def execute(self, sql):
            self.calls.append(sql)
            return json.dumps({"query_block": {"table": {
                "table_name": "t", "access_type": "ALL",
            }}})

    dynamic_query = {
        "query_id": "qd",
        "sql": "SELECT * FROM t WHERE id = ?",
        "weight": 2.0,
        "dynamic": True,
        "representative_bindings": [
            {"binding_id": "small", "values": [1]},
            {"binding_id": "large", "values": [1_000_000]},
        ],
    }
    registry = {
        "schema_version": "mysql_plan_workload_registry_v1",
        "immutable": True,
        "freeze_id": "freeze-test",
        "benchmark": "job",
        "queries": [{
            "order": 1,
            "query_id": "qd",
            "sql_sha256": hashlib.sha256(dynamic_query["sql"].encode()).hexdigest(),
            "weight": 2.0,
            "dynamic": True,
            "representative_bindings": dynamic_query["representative_bindings"],
            "required_plan_modes": ["generic", "custom"],
        }],
    }
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "registry.json"
        path.write_text(json.dumps(registry, sort_keys=True), encoding="utf-8")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        client = Client()
        provider = MySQLExplainFeatureProvider(client, {
            "workload_registry_path": str(path.resolve()),
            "workload_registry_sha256": digest,
            "workload_freeze_id": "freeze-test",
            "benchmark_id": "job",
            "registry_query_ids": ["qd"],
            "queries": [dynamic_query],
        })
        collected = provider.collect()
        bundle = collected["canonical_plan_bundle"]
        assert client.calls == [
            "EXPLAIN FORMAT=JSON SELECT * FROM t WHERE id = 1",
            "EXPLAIN FORMAT=JSON SELECT * FROM t WHERE id = 1000000",
        ]
        assert validate_canonical_plan_bundle(bundle)["valid"] is True
        assert bundle["strict_cluster_usable"] is False
        assert bundle["strict_cluster_ineligible_reasons"] == [
            "generic_plan_semantics_unavailable:qd"
        ]
        assert bundle["plan_similarity_cluster_id"] is None
        assert bundle["strict_canonical_physical_bundle_sha256"] is None
        assert len(collected["plan_rows"]) == 2
        clustered = cluster_ranked_candidates([
            {"subset_id": "a", "final_score": 2.0, "canonical_plan_bundle": bundle},
            {"subset_id": "b", "final_score": 1.0, "canonical_plan_bundle": copy.deepcopy(bundle)},
        ])
        assert clustered["strict_plan_cluster_count"] == 2
        assert all(len(row["strict_plan_cluster_member_subset_ids"]) == 1
                   for row in clustered["representatives"])

        try:
            MySQLExplainFeatureProvider(Client(), {
                "workload_registry_path": str(path.resolve()),
                "workload_registry_sha256": "0" * 64,
                "workload_freeze_id": "freeze-test",
                "benchmark_id": "job",
                "registry_query_ids": ["qd"],
                "queries": [dynamic_query],
            })
            raise AssertionError("tampered authority SHA unexpectedly accepted")
        except ValueError as exc:
            assert "registry SHA mismatch" in str(exc)
