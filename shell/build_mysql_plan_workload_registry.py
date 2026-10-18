#!/usr/bin/env python3
"""Build frozen MySQL plan-workload registries without contacting a database.

Only workloads whose complete ordered SQL set is static, read-only, and backed by
the frozen workload artifacts are publishable.  Dynamic SQL is deliberately
fail-closed unless an authoritative representative-binding artifact is supplied;
MySQL generic/custom plan semantics are never invented.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
FREEZE_DIR = ROOT / "config/formal/frozen_mysql_workloads_and_parameters_20260723_v3"
BASE_WORKLOAD = Path(
    "/home/anonymous/program/resource_adaptive_double_loop_sgd_isolated/"
    "config/formal/frozen_mysql_workloads_and_parameters_20260722_v2/mysql_workloads.json"
)
JOB_DIR = Path("/home/anonymous/dataset/pg_data/join-order-benchmark")
JOB_SENSITIVE = Path(
    "/home/anonymous/program/resource_adaptive_double_loop_sgd_isolated/"
    "config/workloads/mysql_job_sensitive_cast_role5.sql"
)
HYBENCH_STMTS = Path("/home/anonymous/HyBench-2024-master/conf/stmt_mysql.toml")
TPCH_PROCEDURES = Path(
    "/home/anonymous/benchbase/src/main/java/com/oltpbenchmark/benchmarks/tpch/procedures"
)
HYBENCH_AP_CLIENT = Path(
    "/home/anonymous/HyBench-2024-master/src/main/java/com/hybench/workload/APClient.java"
)
HYBENCH_CLIENT = Path(
    "/home/anonymous/HyBench-2024-master/src/main/java/com/hybench/workload/Client.java"
)
HYBENCH_RANDOM = Path(
    "/home/anonymous/HyBench-2024-master/src/main/java/com/hybench/util/RandomGenerator.java"
)
TPCH_WORKER = Path(
    "/home/anonymous/benchbase/src/main/java/com/oltpbenchmark/benchmarks/tpch/TPCHWorker.java"
)
TPCH_SEED_AGENT = ROOT / "formal_clients_20260721/com/formal/TpchSeedAgent.java"
TPCH_SEED_AGENT_JAR = ROOT / "formal_clients_20260721/tpch-seed-agent.jar"
TPCH_FORMAL_RUN_ROOT = (
    ROOT / "history/brute_formal_mixed_20260722_v6_remaining_v1/mysql/brute_force/A7_r1"
)
DEFAULT_OUTPUT = FREEZE_DIR / "plan_workload_registries"
FREEZE_ID = "frozen_mysql_workloads_and_parameters_20260723_v3"


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def normalized_sql(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip().rstrip(";")


def checked_file(path: Path, expected_sha: str, label: str) -> dict[str, str]:
    if not path.is_file():
        raise ValueError(f"missing frozen source {label}: {path}")
    actual = sha256_file(path)
    if actual != expected_sha:
        raise ValueError(f"frozen source SHA mismatch for {label}: expected={expected_sha} actual={actual}")
    return {"path": str(path), "sha256": actual}


def source_ref(path: Path, start: int, end: int) -> dict[str, Any]:
    """Return an immutable source citation and fail on stale line coordinates."""
    if not path.is_file():
        raise ValueError(f"missing binding-authority source: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    if start < 1 or end < start or end > len(lines):
        raise ValueError(f"invalid source line span {start}-{end}: {path}")
    return {
        "line_end": end,
        "line_sha256": sha256_bytes(("\n".join(lines[start - 1:end]) + "\n").encode()),
        "line_start": start,
        "path": str(path),
        "sha256": sha256_file(path),
    }


def java_method_ref(path: Path, method_marker: str) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8").splitlines()
    matches = [index for index, line in enumerate(lines) if method_marker in line]
    if len(matches) != 1:
        raise ValueError(f"expected one Java method marker {method_marker!r} in {path}, found {len(matches)}")
    start_index = matches[0]
    depth = 0
    opened = False
    for end_index in range(start_index, len(lines)):
        # These methods contain no braces in string literals before the closing method brace.
        depth += lines[end_index].count("{") - lines[end_index].count("}")
        opened = opened or "{" in lines[end_index]
        if opened and depth == 0:
            return source_ref(path, start_index + 1, end_index + 1)
    raise ValueError(f"unterminated Java method {method_marker!r} in {path}")


def toml_section_ref(path: Path, section: str) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8").splitlines()
    header = f"[{section}]"
    matches = [index for index, line in enumerate(lines) if line.strip() == header]
    if len(matches) != 1:
        raise ValueError(f"expected one TOML section {section!r} in {path}, found {len(matches)}")
    start = matches[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].strip().startswith("[") and lines[index].strip().endswith("]"):
            end = index
            break
    return source_ref(path, start + 1, end)


def write_artifact(path: Path, value: Any, *, check: bool) -> str:
    payload = canonical_bytes(value)
    digest = sha256_bytes(payload)
    sidecar = path.with_name(path.name + ".sha256")
    sidecar_payload = f"{digest}  {path.name}\n".encode()
    if check:
        if not path.is_file() or path.read_bytes() != payload:
            raise ValueError(f"canonical artifact differs or is absent: {path}")
        if not sidecar.is_file() or sidecar.read_bytes() != sidecar_payload:
            raise ValueError(f"SHA sidecar differs or is absent: {sidecar}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        sidecar.write_bytes(sidecar_payload)
    return digest


def build_job(workload: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    spec = workload["workloads"]["job"]
    ids = ["1a", "5a", "9a", "12a", "16a", "20a", "26a", "33a"]
    expected_sequence = [f"{query_id}.sql" for query_id in ids] + [
        "sensitive/title-in-cast-role-5-movie100k"
    ]
    if spec.get("query_sequence") != expected_sequence:
        raise ValueError("JOB frozen query sequence is inconsistent")
    paths = [JOB_DIR / f"{query_id}.sql" for query_id in ids] + [JOB_SENSITIVE]
    query_ids = [f"job_{query_id}" for query_id in ids] + ["job_sensitive_title_cast_role5_movie100k"]
    weights = [float(value) for value in spec["query_weights"]]
    if len(paths) != len(weights) or not math.isclose(sum(weights), float(spec["weight_sum"]), abs_tol=0.0):
        raise ValueError("JOB frozen order/weight total is inconsistent")
    expected = [spec["base_query_sha256"][f"{query_id}.sql"] for query_id in ids]
    expected.append(spec["sensitive_query"]["sha256"])
    sources = [checked_file(path, digest, query_id) for path, digest, query_id in zip(paths, expected, query_ids)]
    sql_rows = [normalized_sql(path) for path in paths]
    for query_id, sql in zip(query_ids, sql_rows):
        if not sql.upper().startswith(("SELECT", "WITH")) or "?" in sql:
            raise ValueError(f"JOB query is not a frozen static read-only statement: {query_id}")
    queries = [
        {
            "dynamic": False,
            "order": order,
            "query_id": query_id,
            "representative_bindings": [],
            "required_plan_modes": [],
            "sql_sha256": sha256_bytes(sql.encode()),
            "weight": weight,
        }
        for order, (query_id, sql, weight) in enumerate(zip(query_ids, sql_rows, weights), 1)
    ]
    registry = {
        "benchmark": "job",
        "freeze_id": FREEZE_ID,
        "immutable": True,
        "queries": queries,
        "schema_version": "mysql_plan_workload_registry_v1",
    }
    feature_source = {
        "active_sql_count": float(sum(weights)),
        "benchmark_id": "job",
        "queries": [
            {
                "dynamic": False,
                "query_id": query_id,
                "representative_bindings": [],
                "sql": sql,
                "weight": weight,
            }
            for query_id, sql, weight in zip(query_ids, sql_rows, weights)
        ],
        "registry_query_ids": query_ids,
        "require_strict_plan_registry": True,
        "workload_freeze_id": FREEZE_ID,
        "workload_registry_path": str((DEFAULT_OUTPUT / "job.mysql_plan_workload_registry_v1.json").resolve()),
        "workload_registry_sha256": "__POPULATED_AFTER_CANONICAL_REGISTRY_BUILD__",
    }
    audit = {
        "benchmark": "job",
        "complete_workload_coverage": True,
        "decision": "PUBLISHED",
        "dynamic_sql": False,
        "mysql_generic_custom_semantics_claimed": False,
        "ordered_query_ids": query_ids,
        "source_files": sources,
        "weight_sum": float(sum(weights)),
    }
    return registry, feature_source, audit


def gap_audits(workload: Mapping[str, Any]) -> list[dict[str, Any]]:
    ap = workload["workloads"]["hybench_ap"]
    tpch = workload["workloads"]["tpch"]
    return [
        {
            "benchmark": "hybench_ap",
            "decision": "FAIL_CLOSED_NOT_PUBLISHED",
            "missing": "authoritative representative bindings for AP-1,2,3,4,5,7,12,13",
            "reason": "the full AP13 workload contains positional parameters; seed/config are not binding evidence",
            "source": checked_file(HYBENCH_STMTS, ap["statement_artifact"]["sha256"], "HyBench stmt_mysql.toml"),
            "static_subset_not_published_as_full_workload": ["AP-6", "AP-8", "AP-9", "AP-10", "AP-11"],
        },
        {
            "benchmark": "tpch",
            "decision": "FAIL_CLOSED_NOT_PUBLISHED",
            "missing": "authoritative representative bindings for the complete Q1-Q22 prepared-statement workload",
            "reason": "Java random domains and workload seed do not by themselves freeze representative binding instances",
            "source": {
                "logical_source": tpch["artifact"]["logical_source"],
                "procedure_directory": str(TPCH_PROCEDURES),
                "procedure_files_present": all((TPCH_PROCEDURES / f"Q{i}.java").is_file() for i in range(1, 23)),
                "frozen_mysql_jar_sha256": tpch["artifact"]["mysql_jar_sha256"],
            },
        },
        {
            "benchmark": "tpcc",
            "decision": "FAIL_CLOSED_NOT_PUBLISHED",
            "missing": "complete ordered SQL and authoritative bindings for all five transactional procedures",
            "reason": "the write workload cannot be represented by its sensitive SELECT or another read-only subset",
            "transaction_types": ["NewOrder", "Payment", "OrderStatus", "Delivery", "StockLevel"],
        },
        {
            "benchmark": "hybench_xp",
            "decision": "FAIL_CLOSED_NOT_PUBLISHED",
            "missing": "complete AP13 bindings plus complete AT1-AT6 transaction statement/binding traces",
            "reason": "the AP SELECT subset cannot stand in for the mixed read/write XP workload",
            "transaction_types": ["AT1", "AT2", "AT3", "AT4", "AT5", "AT6"],
        },
    ]


def tpch_completed_trace_audit() -> dict[str, Any]:
    """Audit old full-mix traces without treating query IDs as binding values."""
    traces: list[dict[str, Any]] = []
    completed: set[str] = set()
    if TPCH_FORMAL_RUN_ROOT.is_dir():
        for path in sorted(TPCH_FORMAL_RUN_ROOT.glob("**/tpch*.raw.csv")):
            names: set[str] = set()
            with path.open(encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    name = row.get("Transaction Name", "")
                    if name.startswith("Q") and name[1:].isdigit():
                        names.add(name)
            completed.update(names)
            traces.append({
                "completed_query_ids": sorted(names, key=lambda value: int(value[1:])),
                "path": str(path),
                "sha256": sha256_file(path),
            })
    all_queries = {f"Q{query_id}" for query_id in range(1, 23)}
    return {
        "binding_columns_present": False,
        "completed_query_ids": sorted(completed, key=lambda value: int(value[1:])),
        "missing_completed_query_ids": sorted(all_queries - completed, key=lambda value: int(value[1:])),
        "raw_schema": [
            "Transaction Type Index", "Transaction Name", "Start Time (microseconds)",
            "Latency (microseconds)", "Worker Id (start number)", "Phase Id (index in config file)",
        ],
        "traces": traces,
    }


def build_binding_gap_audit(workload: Mapping[str, Any]) -> dict[str, Any]:
    """Explain, query by query, why AP/TPC-H bindings cannot be published."""
    ap = workload["workloads"]["hybench_ap"]
    tpch = workload["workloads"]["tpch"]
    ap_generators = {
        1: ("execQ1(Connection conn)", "custid := Java-Random.nextInt(customer_number + 1); repeated in slots 1-4"),
        2: ("execQ2(Connection conn)", "custid := result of AP-2.1, SELECT ... ORDER BY RAND() LIMIT 1; database RNG is not seeded by random_seed"),
        3: ("execQ3(Connection conn)", "custid := Java-Random.nextInt(customer_number + 1); repeated in slots 1-2"),
        4: ("execQ4(Connection conn)", "custid := Java-Random.nextInt(customer_number + 1); repeated in slots 1-2"),
        5: ("execQ5(Connection conn)", "companyid := Java-Random.nextInt(customer_number + 1, customer_number + 1 + company_number)"),
        7: ("execQ7(Connection conn)", "companyid := Java-Random.nextInt(customer_number + 1, customer_number + 1 + company_number); repeated in slots 1-4"),
        12: ("execQ12(Connection conn)", "category := CompanyCategories[Java-Random.nextInt(CompanyCategories.length)]"),
        13: ("execQ13(Connection conn)", "custid := Java-Random.nextInt(customer_number + 1)"),
    }
    ap_rows: list[dict[str, Any]] = []
    for query_id, weight in zip(ap["ap_query_ids"], ap["ap_query_weights"]):
        dynamic = query_id in ap_generators
        row: dict[str, Any] = {
            "dynamic": dynamic,
            "query_id": f"AP-{query_id}",
            "representative_bindings": [],
            "sql_source": toml_section_ref(HYBENCH_STMTS, f"AP-{query_id}"),
            "weight": float(weight),
        }
        if dynamic:
            marker, formula = ap_generators[query_id]
            row.update({
                "binding_formula": formula,
                "binding_generator_source": java_method_ref(HYBENCH_AP_CLIENT, marker),
                "closure": "MISSING_AUTHORITATIVE_CONCRETE_BINDING",
            })
            if query_id == 2:
                row["auxiliary_sql_source"] = toml_section_ref(HYBENCH_STMTS, "AP-2.1")
                row["non_seeded_dependency"] = "MySQL RAND() plus current table contents and execution timing"
        else:
            row["closure"] = "STATIC_NO_BINDING_REQUIRED"
        ap_rows.append(row)

    tpch_formulas = {
        1: "delta integer [60,120]", 2: "size [1,50], type suffix, region",
        3: "segment, March-1995 day [1,31]", 4: "year [1993,1997], month [1,10]",
        5: "region, year [1993,1997]", 6: "year [1993,1997], discount [0.02,0.09], quantity [24,25]",
        7: "two distinct nations", 8: "nation and its region, three-syllable type",
        9: "part-name color", 10: "month from 1993-02 through 1995-01",
        11: "nation; fraction := 0.0001 / scaleFactor", 12: "two distinct ship modes, year [1993,1997]",
        13: "word1 and word2", 14: "year [1993,1997], month [1,12]",
        15: "year [1993,1997], month [1,12] except 1997 month <= 10; substituted into CREATE VIEW SQL",
        16: "brand, two-syllable type prefix, eight distinct sizes [1,50]",
        17: "brand, two-syllable container", 18: "quantity [312,315]",
        19: "three quantities and three independently generated brands",
        20: "color prefix, year [1993,1997], nation", 21: "nation",
        22: "seven distinct country codes [10,34]",
    }
    tpch_rows = []
    for query_id, weight in zip(tpch["query_ids"], tpch["query_weights"]):
        path = TPCH_PROCEDURES / f"Q{query_id}.java"
        marker = "public void run(" if query_id == 15 else "getStatement("
        tpch_rows.append({
            "binding_formula_domain": tpch_formulas[query_id],
            "binding_generator_source": java_method_ref(path, marker),
            "closure": "MISSING_AUTHORITATIVE_CONCRETE_BINDING",
            "dynamic": True,
            "query_id": f"Q{query_id}",
            "representative_bindings": [],
            "weight": float(weight),
        })

    return {
        "benchmarks": [
            {
                "benchmark": "hybench_ap",
                "decision": "FAIL_CLOSED_NOT_PUBLISHED",
                "frozen_config": {
                    "clients": ap["clients"], "scale_factor": ap["scale_factor"],
                    "seed": 4508333, "weight_sum": float(sum(ap["ap_query_weights"])),
                },
                "global_seed_sources": [
                    java_method_ref(HYBENCH_CLIENT, "protected long configuredRandomSeed()"),
                    java_method_ref(HYBENCH_CLIENT, "protected long deriveSeed(String phase, int ordinal)"),
                    java_method_ref(HYBENCH_RANDOM, "public static void setThreadSeed(long seed)"),
                ],
                "queries": ap_rows,
                "unresolved_authority": [
                    "the frozen workload binds stmt_mysql.toml but does not bind a HyBench executable/source SHA for the AP parameter generator",
                    "four concurrent workers consume independent derived Java-Random streams; a seed does not identify a representative occurrence without the per-worker executed query trace",
                    "AP-2 obtains its value from unseeded MySQL ORDER BY RAND(), so it cannot be reconstructed from workload seed 4508333",
                    "existing workload logs do not contain a SHA-closed complete concrete binding trace for all eight dynamic AP statements",
                ],
            },
            {
                "benchmark": "tpch",
                "decision": "FAIL_CLOSED_NOT_PUBLISHED",
                "frozen_config": {
                    "scale_factor": float(tpch["scale_factor"]), "seed": 4508333,
                    "terminals": int(tpch["terminals"]), "weight_sum": float(sum(tpch["query_weights"])),
                },
                "queries": tpch_rows,
                "runtime_seed_chain": {
                    "benchbase_jar": {"path": "/home/anonymous/benchbase-mysql/benchbase.jar", "sha256": tpch["artifact"]["mysql_jar_sha256"]},
                    "seed_agent_jar": {"path": str(TPCH_SEED_AGENT_JAR), "sha256": sha256_file(TPCH_SEED_AGENT_JAR)},
                    "seed_agent_source": source_ref(TPCH_SEED_AGENT, 8, 18),
                    "worker_source": java_method_ref(TPCH_WORKER, "TPCHWorker(TPCHBenchmark benchmarkModule, int id)"),
                },
                "trace_audit": tpch_completed_trace_audit(),
                "unresolved_authority": [
                    "the seed agent fixes the parameter RNG seed but does not log parameter setter values",
                    "parameter RNG state advances according to each terminal's actual query sequence, including attempts and retries",
                    "raw CSV records query ID, start time, latency, worker and phase, but no bound parameters",
                    "the audited full-mix formal traces have no completed Q9 or Q15 instance",
                    "BenchBase has no MySQL generic/custom plan mode contract; no such mode is claimed",
                ],
            },
        ],
        "freeze_id": FREEZE_ID,
        "generated_without_database_access": True,
        "mysql_dynamic_plan_cluster_deletion_authorized": False,
        "mysql_generic_custom_semantics_claimed": False,
        "schema_version": "mysql_binding_authority_gap_audit_v1",
    }


def build(output_dir: Path, *, check: bool) -> dict[str, Any]:
    workload = json.loads(BASE_WORKLOAD.read_text(encoding="utf-8"))
    if workload.get("freeze_id") != "frozen_mysql_workloads_and_parameters_20260722_v2":
        raise ValueError("unexpected base workload freeze")
    effective = json.loads((FREEZE_DIR / "effective_workload_bundle.json").read_text(encoding="utf-8"))
    if effective.get("freeze_id") != FREEZE_ID:
        raise ValueError("unexpected effective workload freeze")
    base_expected_sha = effective["base"]["workloads"]["sha256"]
    if sha256_file(BASE_WORKLOAD) != base_expected_sha:
        raise ValueError("effective bundle base-workload SHA mismatch")
    parameter_path = FREEZE_DIR / effective["effective_parameter_spaces"]["path"]
    parameter_sha = sha256_file(parameter_path)
    if parameter_sha != effective["effective_parameter_spaces"]["sha256"]:
        raise ValueError("effective parameter-space SHA mismatch")
    job_mix_path = FREEZE_DIR / effective["delta"]["artifact"]["path"]
    job_mix_sha = sha256_file(job_mix_path)
    if job_mix_sha != effective["delta"]["artifact"]["sha256"]:
        raise ValueError("effective JOB-mix SHA mismatch")
    job_mix = json.loads(job_mix_path.read_text(encoding="utf-8"))
    if float(job_mix["weight_total"]) != 107.5 or float(job_mix["sensitive_fraction"]) != 7.5 / 107.5:
        raise ValueError("effective JOB v3 mix is inconsistent")
    expected_mix = {
        "1a": 10, "5a": 10, "9a": 10, "12a": 10, "16a": 10,
        "20a": 10, "26a": 30, "33a": 10,
        "sensitive/title-in-cast-role-5-movie100k": 7.5,
    }
    if job_mix.get("weights") != expected_mix:
        raise ValueError("effective JOB v3 ordered weights are inconsistent")

    registry, feature_source, job_audit = build_job(workload)
    registry_path = output_dir / "job.mysql_plan_workload_registry_v1.json"
    registry_digest = write_artifact(registry_path, registry, check=check)
    feature_source["workload_registry_path"] = str(registry_path.resolve())
    feature_source["workload_registry_sha256"] = registry_digest
    config_digest = write_artifact(output_dir / "job.trained_estimator_feature_source.json", feature_source, check=check)
    audit = {
        "authority": {
            "base_workload": {"path": str(BASE_WORKLOAD), "sha256": base_expected_sha},
            "effective_parameter_spaces": {"path": str(parameter_path), "sha256": parameter_sha},
            "effective_workload_bundle": {
                "path": str(FREEZE_DIR / "effective_workload_bundle.json"),
                "sha256": sha256_file(FREEZE_DIR / "effective_workload_bundle.json"),
            },
            "job_mix": {"path": str(job_mix_path), "sha256": job_mix_sha},
        },
        "freeze_id": FREEZE_ID,
        "generated_without_database_access": True,
        "mysql_generic_custom_semantics_claimed": False,
        "published": [job_audit],
        "schema_version": "mysql_plan_workload_registry_build_audit_v1",
        "unpublished": gap_audits(workload),
    }
    audit_digest = write_artifact(output_dir / "registry_build_audit.json", audit, check=check)
    binding_gap_digest = write_artifact(
        output_dir / "binding_authority_gap_audit.json",
        build_binding_gap_audit(workload),
        check=check,
    )
    result = {
        "audit_sha256": audit_digest,
        "binding_authority_gap_audit_sha256": binding_gap_digest,
        "feature_source_sha256": config_digest,
        "registry_sha256": registry_digest,
        "status": "PASS",
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        result = build(args.output_dir.resolve(), check=args.check)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
