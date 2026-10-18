#!/usr/bin/env python3
"""Execute one sealed XP Top-5 replay through the v26 raw executor.

Cost values are applied/read back through the adopted client container using
its node02-local private defaults file.  The host mysql client is never used
and no secret enters argv or output.  Baseline costs are restored in finally.
This executor always emits zero accepted-round credit; QPS acceptance is a
later controller-only decision.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace


TARGET_ID = "b8e574ea5d2a47faae7cbe1776c7407c6e4b6122b4567c8a9b7dde5513382dcc"
JAVA_SHA256 = "c8f339ba5d433ef5ecacef4de0640b0a35c99381804413bfbc24375a0410e51c"
PARAMETERS = (
    "io_block_read_cost", "memory_block_read_cost", "row_evaluate_cost",
    "key_compare_cost", "disk_temptable_create_cost",
    "disk_temptable_row_cost", "memory_temptable_row_cost",
)
ENGINE = {"io_block_read_cost", "memory_block_read_cost"}
DOCKER = ["docker", "--host", "unix:///run/ra-docker-mysql-xp-v2.sock"]
DEFAULTS = "/run/mysql-xp-private/mysql-client.cnf"


class ReplayError(RuntimeError):
    pass


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ReplayError("bundle module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    old = list(sys.path); sys.path.insert(0, str(path.parent))
    try: spec.loader.exec_module(module)
    finally: sys.path[:] = old
    return module


def number(value):
    if isinstance(value, bool): raise ReplayError("cost value differs")
    result = float(value)
    if not math.isfinite(result) or result <= 0: raise ReplayError("cost value differs")
    return format(result, ".17g")


def sql(values):
    if not isinstance(values, dict) or set(values) != set(PARAMETERS):
        raise ReplayError("exact seven cost values are required")
    statements = []
    for name in PARAMETERS:
        table = "engine_cost" if name in ENGINE else "server_cost"
        where = "cost_name='%s'" % name
        if name in ENGINE: where += " AND engine_name='default' AND device_type=0"
        statements.append("UPDATE mysql.%s SET cost_value=%s WHERE %s" %
                          (table, number(values[name]), where))
    statements.append("FLUSH OPTIMIZER_COSTS")
    statements.append(
        "SELECT cost_name,COALESCE(cost_value,default_value) FROM mysql.server_cost "
        "WHERE cost_name IN ('row_evaluate_cost','key_compare_cost','disk_temptable_create_cost',"
        "'disk_temptable_row_cost','memory_temptable_row_cost') UNION ALL SELECT cost_name,"
        "COALESCE(cost_value,default_value) FROM mysql.engine_cost WHERE engine_name='default' "
        "AND device_type=0 AND cost_name IN ('io_block_read_cost','memory_block_read_cost') "
        "ORDER BY cost_name")
    return ";".join(statements) + ";"


def apply(client, values):
    argv = DOCKER + ["exec", client, "mysql", "--defaults-extra-file=" + DEFAULTS,
                     "--batch", "--raw", "--skip-column-names", "-e", sql(values)]
    completed = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, check=False)
    if completed.returncode:
        raise ReplayError("client-container cost apply/readback failed")
    rows = {}
    for line in completed.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) == 2: rows[fields[0]] = float(fields[1])
    expected = {name: float(values[name]) for name in PARAMETERS}
    if set(rows) != set(PARAMETERS) or any(not math.isclose(rows[k], expected[k], rel_tol=1e-12)
                                           for k in PARAMETERS):
        raise ReplayError("cost readback differs")
    return rows


def execute(bundle, adoption_path, request_path, candidate_plan_path, java, formal_readback,
            counter_command, fingerprint_observation, stop_file, output):
    bundle = Path(bundle).resolve(strict=True)
    target = load_module(bundle / "launch/mysql_xp_node02_formal10_materializer_v1.py",
                         "xp_replay_target")
    raw = load_module(bundle / "launch/mysql_xp_raw_evidence_executor_v1.py",
                      "xp_replay_raw")
    manifest = target.verify_bundle(bundle)
    adoption, identity = target.verify_adoption(adoption_path, manifest["bundle_id"])
    request = target.load_sealed(request_path, "Top-5 replay request")
    candidate_plan = target.load_sealed(candidate_plan_path, "candidate plan")
    rank = request.get("rank")
    ranked = candidate_plan.get("global_candidate_ranking") or []
    runtime_root = Path(adoption["runtime_publication"]["live_tree_readback"]["root"])
    java = Path(java).resolve(strict=True)
    if (manifest["bundle_id"] != TARGET_ID
            or request.get("schema_version") != "mysql_xp_algorithm_c_real_replay_request_v1"
            or request.get("status") != "SEALED_FIXED_TOP5_POINT"
            or request.get("bundle_id") != TARGET_ID
            or request.get("same_live_identity") != identity
            or rank not in range(1, 6)
            or not request.get("subset_id") or not request.get("attempt_token")
            or request.get("qps_resource_input_present") is not False
            or candidate_plan.get("schema_version") != "mysql_xp_algorithm_c_candidate_plan_v1"
            or candidate_plan.get("status") != "SEALED_127_GLOBAL_RANKING_FIXED_TOP5_NOT_REPLAYED"
            or candidate_plan.get("same_live_identity") != identity or len(ranked) != 127
            or request.get("candidate_plan_sha256") != target.sha256(candidate_plan_path)
            or ranked[rank-1].get("subset_id") != request.get("subset_id")
            or ranked[rank-1].get("candidate_parameters") != request.get("candidate_parameters")
            or request.get("java_sha256") != JAVA_SHA256
            or java != (runtime_root/"bin/java").resolve()
            or target.sha256(java) != JAVA_SHA256):
        raise ReplayError("fixed Top-5 request differs")
    client = identity["client_container_id"]
    candidate = request.get("candidate_parameters")
    baseline = request.get("baseline_parameters")
    candidate_readback = None
    try:
        candidate_readback = apply(client, candidate)
        args = SimpleNamespace(
            mode="fresh-r0", bundle=str(bundle), java=str(java), java_sha256=JAVA_SHA256,
            properties=str(bundle / "qualification/config/hybench_xp_qualification.properties"),
            formal_readback=str(formal_readback), capture_readback=None,
            counter_command=str(counter_command), stop_file=str(stop_file),
            fingerprint_observation=str(fingerprint_observation), trace=None,
            trace_run_id=None, client_container=client,
            runtime_authority_sha256="", capture_authorization_sha256="",
            output=str(output),
        )
        run = raw._run(args)
    finally:
        apply(client, baseline)
    result = {
        "schema_version": "mysql_xp_algorithm_c_real_replay_result_v1",
        "status": "PASS_REAL_REPLAY_MEASUREMENT_COMPLETE",
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True, "bundle_id": TARGET_ID,
        "same_live_identity": identity, "rank": request["rank"],
        "subset_id": request["subset_id"], "attempt_token": request["attempt_token"],
        "candidate_parameters": candidate, "candidate_cost_readback": candidate_readback,
        "baseline_restored": True, "real_replay": True, "failed_replay": False,
        "measurement_seconds": 180, "database_counter_timeout_count": 0,
        "fingerprint": run["fingerprint_evidence"]["fingerprint"],
        "fingerprint_observation_sha256":
            run["fingerprint_evidence"]["observation_sha256"],
        "raw_executor_result": run, "qps_used_for_gradient": False,
        "qps_resource_input_present": False, "accepted_rounds_credit": 0,
    }
    target.atomic_json(output, result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bundle", type=Path, required=True); p.add_argument("--adopted-service", type=Path, required=True)
    p.add_argument("--request", type=Path, required=True); p.add_argument("--candidate-plan", type=Path, required=True); p.add_argument("--java", type=Path, required=True)
    p.add_argument("--formal-readback", type=Path, required=True); p.add_argument("--counter-command", type=Path, required=True)
    p.add_argument("--fingerprint-observation", type=Path, required=True); p.add_argument("--stop-file", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True); a = p.parse_args()
    result = execute(a.bundle, a.adopted_service, a.request, a.candidate_plan, a.java, a.formal_readback,
                     a.counter_command, a.fingerprint_observation, a.stop_file, a.output)
    print(json.dumps({"status": result["status"], "rank": result["rank"],
                      "accepted_rounds_credit": 0}, sort_keys=True))


if __name__ == "__main__":
    try: main()
    except (ReplayError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise SystemExit("NOT_READY: " + str(exc))
