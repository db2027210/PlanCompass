#!/usr/bin/env python3
"""Independent verifier for the host01 AP production binding/plan capture."""

from __future__ import annotations

from collections import Counter, defaultdict
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mysql_plan_clustering import validate_canonical_plan_bundle  # noqa: E402

EXPECTED_JAR_SHA = "6e615d1b704da0b095266fd5592fc72245a9c5034707a37ef91abfa65a7fc7f4"
EXPECTED_QUERIES = {f"AP-{i}" for i in range(1, 14)} | {"AP-2-RAND"}
DYNAMIC = {"AP-1", "AP-2-RAND", "AP-2", "AP-3", "AP-4", "AP-5", "AP-7", "AP-12", "AP-13"}


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def verify_canonical(path: Path) -> tuple[dict[str, Any], str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if path.read_bytes() != canonical_bytes(value):
        raise ValueError(f"noncanonical JSON: {path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if path.with_name(path.name + ".sha256").read_text(encoding="ascii") != f"{digest}  {path.name}\n":
        raise ValueError(f"invalid sidecar: {path}")
    return value, digest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    args = parser.parse_args()
    run_root = args.run_root.resolve()
    props = dict(line.split("=", 1) for line in (run_root / "production.props").read_text().splitlines() if "=" in line)
    if props.get("random_seed") != "4508333" or props.get("apclient") != "4" \
            or props.get("ap_query_sequence_limit") != "13":
        raise ValueError("seed/client/sequence-limit mismatch")
    local_jar = Path("/disk2/resource_adaptive_mysql/gd_20260718/hybench_client/target/HyBench-1.0-SNAPSHOT-jar-with-dependencies.jar")
    if hashlib.sha256(local_jar.read_bytes()).hexdigest() != EXPECTED_JAR_SHA:
        raise ValueError("frozen production JAR mismatch")
    client_log = (run_root / "artifacts/client.log").read_text(encoding="utf-8")
    if 'HYBENCH_METRICS_JSON={"qps":0.25,"query_count":52}' not in client_log:
        raise ValueError("production client did not report 52 AP queries")
    if (run_root / "readback/client_exit_code.txt").read_text().strip() != "0":
        raise ValueError("production client exit was nonzero")
    if (run_root / "readback/mysql_gate.tsv").read_text().strip().split("\t")[:3] != ["1", "1", "1"]:
        raise ValueError("production DB read-only/general-log gate mismatch")
    if (run_root / "readback/explain_mysql_gate.tsv").read_text().strip() != "1\t1":
        raise ValueError("EXPLAIN DB read-only gate mismatch")

    trace_path = run_root / "artifacts/production_server_binding_trace.jsonl"
    trace_sha = hashlib.sha256(trace_path.read_bytes()).hexdigest()
    sidecar = trace_path.with_name(trace_path.name + ".sha256").read_text(encoding="ascii")
    if sidecar != f"{trace_sha}  {trace_path.name}\n":
        raise ValueError("binding trace sidecar mismatch")
    rows = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    counts = Counter(row["query_id"] for row in rows)
    if len(rows) != 56 or set(counts) != EXPECTED_QUERIES or set(counts.values()) != {4}:
        raise ValueError("binding trace event coverage mismatch")
    by_connection: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["global_seed_from_runtime_props"] != 4508333 \
                or row["server_observation_authority"] != "MySQL_8.0_general_log_Query_argument":
            raise ValueError("binding event authority mismatch")
        by_connection[row["connection_id"]].append(row)
    if len(by_connection) != 4:
        raise ValueError("binding trace does not contain four DB connections")
    for connection, connection_rows in by_connection.items():
        ordered = sorted(connection_rows, key=lambda row: row["connection_sequence"])
        if [row["connection_sequence"] for row in ordered] != list(range(1, 15)):
            raise ValueError(f"connection {connection} sequence mismatch")
        main = {row["query_id"] for row in ordered if row["query_id"] != "AP-2-RAND"}
        if main != {f"AP-{i}" for i in range(1, 14)}:
            raise ValueError(f"connection {connection} main coverage mismatch")
        selector = next(i for i, row in enumerate(ordered) if row["query_id"] == "AP-2-RAND")
        if ordered[selector + 1]["query_id"] != "AP-2" \
                or ordered[selector]["database_rand_result_observed_in_adjacent_ap2_binding"] \
                != ordered[selector + 1]["actual_bindings"][0]["value"]:
            raise ValueError(f"connection {connection} AP-2 equality chain mismatch")

    raw_files = sorted((run_root / "artifacts/raw_explain").glob("*.json"))
    if len(raw_files) != 56 or any(not isinstance(json.loads(path.read_text()), dict) for path in raw_files):
        raise ValueError("raw EXPLAIN coverage/JSON mismatch")
    inventory, _ = verify_canonical(run_root / "artifacts/raw_explain_inventory.json")
    if inventory["raw_explain_file_count"] != 56 or inventory["all_56_raw_explain_files_retained"] is not True:
        raise ValueError("raw EXPLAIN inventory mismatch")
    for ref in inventory["inventory"]:
        path = Path(ref["path"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != ref["raw_file_sha256"]:
            raise ValueError(f"raw EXPLAIN SHA mismatch: {path}")

    registry_path = ROOT / "config/formal/frozen_mysql_workloads_and_parameters_20260723_v3/plan_workload_registries/hybench_ap.mysql_plan_workload_registry_v1.json"
    registry, registry_sha = verify_canonical(registry_path)
    if registry["workload_seed"] != 4508333 or registry["exact_frozen_production_jar_sha256"] != EXPECTED_JAR_SHA \
            or len(registry["queries"]) != 14 or registry["mysql_generic_custom_semantics_available"] is not False:
        raise ValueError("AP registry authority mismatch")
    bundle, bundle_sha = verify_canonical(run_root / "artifacts/canonical_plan_bundle.json")
    if validate_canonical_plan_bundle(bundle)["valid"] is not True:
        raise ValueError("canonical plan bundle validation failed")
    expected_reasons = sorted(f"generic_plan_semantics_unavailable:{query_id}" for query_id in DYNAMIC)
    if bundle["registry_sha256"] != registry_sha \
            or bundle["strict_cluster_usable"] is not False \
            or bundle["strict_cluster_ineligible_reasons"] != expected_reasons \
            or bundle["plan_similarity_cluster_id"] is not None \
            or bundle["strict_canonical_physical_bundle_sha256"] is not None:
        raise ValueError("strict bundle did not fail open")
    evidence, evidence_sha = verify_canonical(run_root / "production_binding_and_plan_evidence.json")
    if evidence["mysql_dynamic_plan_cluster_deletion_authorized"] is not False \
            or evidence["generic_custom_plan_semantics_claimed"] is not False \
            or evidence["canonical_plan_bundle"]["sha256"] != bundle_sha:
        raise ValueError("final evidence incorrectly authorizes plan deletion")
    print(json.dumps({
        "binding_events": 56, "bundle_sha256": bundle_sha, "evidence_sha256": evidence_sha,
        "raw_explains": 56, "registry_sha256": registry_sha, "status": "PASS_FAIL_OPEN",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, OSError, ValueError, json.JSONDecodeError, IndexError) as error:
        print(json.dumps({"error": str(error), "status": "FAIL"}, sort_keys=True))
        raise SystemExit(1)
