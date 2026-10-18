#!/usr/bin/env python3
"""Prepare and launch the four current PostgreSQL Algorithm C lanes."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

try:
    import tomllib
except ImportError:  # pragma: no cover
    tomllib = None  # type: ignore


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from binary_plan_resource_tuner import (  # noqa: E402
    PlanFeatureProbe,
    load_sql_statements,
    workload_queries_with_sampling,
)

PYTHON = Path("/home/anonymous/miniconda3/envs/resource_adaptive/bin/python")
if not PYTHON.is_file():
    PYTHON = Path(sys.executable)
RUN_BASE = Path(
    os.environ.get("PG_ALGORITHM_C_RUN_BASE")
    or "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809"
)
PHYSICAL_LAUNCHER = ROOT / "shell/launch_pg_algorithm_c_four_parallel2_20260809.py"
SCENE_CONTROLLER = ROOT / "shell/pg_algorithm_c_lane_entry.py"
JOB_FIXED_CAPTURE = ROOT / "shell/run_job_fixed_benchmark_capture.py"
JOB_FIXED_LAUNCHER = ROOT / "shell/run_pg_job_fixed_workload_isolated.sh"
SOURCE_PROVENANCE_PATHS = (
    ROOT / "algorithm_c_calibration_release.py",
    ROOT / "binary_plan_resource_tuner.py",
    ROOT / "bonect_interval_lineage.py",
    ROOT / "common_outer_loop.py",
    ROOT / "experience_table_subset_optimizer.py",
    ROOT / "extract_plan_trees.py",
    ROOT / "gradient_descent_optimizer.py",
    ROOT / "logger_utils.py",
    ROOT / "operator_resource_rules.py",
    ROOT / "plan_resource_estimator.py",
    ROOT / "resource_feature_predictor.py",
    ROOT / "resource_utility.py",
    ROOT / "same_ssd_concurrency.py",
    ROOT / "system_resource_monitor.py",
    ROOT / "shell/algorithm_c_parameter_support.py",
    ROOT / "shell/build_algorithm_c_experience_table_from_replays.py",
    ROOT / "shell/launch_pg_algorithm_c_four_experience_sgd_20260809.py",
    ROOT / "shell/launch_pg_algorithm_c_four_parallel2_20260809.py",
    ROOT / "shell/pg_algorithm_c_lane_entry.py",
    ROOT / "shell/run_benchmark_capture.py",
    ROOT / "shell/run_job_fixed_benchmark_capture.py",
    ROOT / "shell/run_hybench_native_xp_isolated.sh",
    ROOT / "shell/run_pg_algorithm_c_calibrate_then_scene.py",
    ROOT / "shell/run_pg_algorithm_c_scene.py",
    ROOT / "shell/run_pg_full_workload_isolated.sh",
    ROOT / "shell/run_pg_job_fixed_workload_isolated.sh",
    ROOT / "tools/bonect_fadvise_dontneed.c",
)
NATIVE_RELEASE = Path("/disk1/resource_adaptive/pg_algorithm_c_native_hybench_20260809_v2")
TPCH_QUERY_ROOT = Path("/home/anonymous/program/duckdb/extension/tpch/dbgen/queries")
ACTIVE_PARAMS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
INITIAL_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}
# Keep JOB on the same PostgreSQL default cost vector used by Algorithms A and
# B.  A non-default warm start changes the canonical baseline QPS and makes the
# cross-algorithm improvement ratios incomparable.  Historical accepted state
# remains audit-only and is never imported into a fresh campaign.
JOB_INITIAL_PARAMS = dict(INITIAL_PARAMS)
ALGORITHM_C_ACCEPTED_TARGET = 7
DB_CPUSETS = {
    # XP IO needs CPU headroom so CPU cannot co-saturate with the target SSD.
    # CPUs 88-103 and 228-255 were intentionally left free from the other
    # PostgreSQL lanes and the concurrently running MySQL experiment.
    # The clean 18/6 replay with a 16-CPU native-client pool reached only
    # 55.5--56.1% SSD while consuming about 26.5/32 database cores.  Give each
    # database eight additional CPUs from the oversized XP-CPU/TPC-H client
    # controller pools.  The revised 14/8 mix below shifts work from XTP to
    # plan-sensitive XAP IO without allowing CPU to become the new bottleneck.
    "xp_io": (
        "32-39,88-95,132-133,184-189,196-197,228-241",
        "40-47,96-103,134-135,190-195,198-199,242-255",
    ),
    # The clean 12-client/long-cap-7 baseline still timed out because each
    # PostgreSQL instance exposed only eight global parallel workers.  Give
    # each JOB database four additional CPUs from the stopped XP-CPU
    # controller reservation.  XP-CPU retains eight controller/client CPUs;
    # no database, controller, or external MySQL reservation overlaps.
    "job_memory": (
        "48-55,136-139,160-163,168-171",
        "56-63,140-143,164-167,172-175",
    ),
    "tpch_memory": ("64-71", "72-79"),
    "xp_cpu": ("80-83", "84-87"),
}
CONTROLLER_CPUSETS = {
    "xp_cpu": "128-131",
    "xp_io": "144-159",
    "job_memory": "176-179",
    "tpch_memory": "180-183",
}
# Read-only reservation for the concurrently running MySQL experiment on this
# host.  PostgreSQL never updates these containers or processes.  The first
# range is used by MySQL workload clients; the latter ranges are Docker DB
# cpusets.  Keeping the reservation in the launch manifest makes isolation
# independently auditable.
EXTERNAL_MYSQL_RESERVED_CPUSETS = ("0-31", "104-127", "200-227")
RESOURCE_CAPACITY = {
    "xp_cpu": {"cpu_cores": 4.0, "memory_gib": 12.0, "io_mib_s": 1000.0},
    # io_mib_s is only a bootstrap value.  FreshCalibration replaces it with
    # the same-run empirical saturation capacity before fitting T.
    "xp_io": {"cpu_cores": 40.0, "memory_gib": 24.0, "io_mib_s": 16.0},
    # The fixed two-lane canonical JOB baseline completed in 610--620 s, but
    # a 2 GiB cgroup with PostgreSQL's 128 MiB default shared_buffers reread
    # about 304 GiB per point and held the host SSD at 98.5--98.8%.  That is a
    # non-target IO bottleneck, not useful memory evidence.  Match the proven
    # TPC-H cache envelope so JOB remains a memory lane while avoiding cache
    # thrash; startup applies and reads this setting back fail-closed below.
    "job_memory": {"cpu_cores": 20.0, "memory_gib": 8.0, "io_mib_s": 1000.0},
    # SF5 with PostgreSQL's 128 MiB default shared_buffers reread about 200 GiB
    # per complete Q1--Q22 point and made the shared SSD a 92.5% non-target
    # bottleneck even with one terminal.  Use an enforced 8 GiB cgroup with a
    # 6 GiB shared-buffer cache: this preserves a high, measurable memory
    # target while preventing the memory lane from degenerating into IO.
    "tpch_memory": {"cpu_cores": 8.0, "memory_gib": 8.0, "io_mib_s": 1000.0},
}
EXPECTED_PLAN_QUERY_COUNT = {
    "xp_cpu": 50,
    "xp_io": 50,
    "job_memory": 113,
    "tpch_memory": 22,
}
# Two replay points run concurrently on the same physical SSD.  Ten clients
# per point completed quickly but the pair consumed about 96--108% of the
# calibrated device capacity.  Five clients still produced about 824 MiB/s
# aggregate container IO and 93.4% host busy.  Two clients preserve query-level
# parallelism and complete the canonical 113 queries in about 7.6 minutes, but
# allowing two historically long queries per candidate still drove host busy
# to 97.1%.  Keep two clients and serialize only the historical long-query
# class inside each candidate.  Both candidate containers and short queries
# remain parallel while aggregate concurrent IO-heavy queries fall from four
# to two.
JOB_REPLAY_CLIENTS = 2
JOB_MAX_CONCURRENT_LONG_QUERIES = 1
JOB_MAX_CONCURRENT_LONG_QUERIES_PER_FAMILY = 1
JOB_MAX_WORKER_PROCESSES = 20
JOB_MAX_PARALLEL_WORKERS = 12
JOB_MAX_PARALLEL_WORKERS_PER_GATHER = 2
JOB_SHARED_BUFFERS = "6GB"
JOB_TARGET_WALL_TIME_S = 15 * 60
JOB_OUTER_TECHNICAL_TIMEOUT_S = JOB_TARGET_WALL_TIME_S + 120
# Two LPT clients per replay completed Q1--Q22 in about 100 seconds, but two
# replay points together saturated the shared SSD (97--100% host busy and
# roughly 1474 MiB/s aggregate container IO).  One client per replay reduced
# aggregate IO to about 834 MiB/s but still produced 88--90% host busy and a
# 100% p99.  Preserve two-candidate replay concurrency and complete Q1--Q22
# exactly once, while serializing execution inside each PostgreSQL backend.
# This removes per-query parallel read amplification without changing the
# benchmark query set or running a second warm-up workload.
TPCH_REPLAY_CLIENTS = 1
TPCH_MAX_PARALLEL_WORKERS_PER_GATHER = 0
TPCH_SHARED_BUFFERS = "6GB"
TPCH_TARGET_WALL_TIME_S = 15 * 60
TPCH_OUTER_TECHNICAL_TIMEOUT_S = TPCH_TARGET_WALL_TIME_S + 120
# Four PostgreSQL lanes serialize physical disk1 measurements.  A healthy
# queue can therefore exceed the old 1200 s restore-lock timeout even though
# no workload is hung.  Keep queue waits long and signal-cancellable; this is
# deliberately separate from the 900 s analytical workload hard wall above.
SHARED_SSD_COORDINATION_TIMEOUT_S = 6 * 60 * 60
DOCKER_INSPECT_TIMEOUT_S = 60
DOCKER_UPDATE_TIMEOUT_S = 90
DOCKER_LIST_TIMEOUT_S = 60
DOCKER_EXEC_PSQL_TIMEOUT_S = 60
DOCKER_EXEC_PSQL_ATTEMPTS = 3
CALIBRATION_RELEASE_ROOT = (
    "/disk1/resource_adaptive/pg_algorithm_c_calibration_releases_v1"
)
XP_IO_XTP_CLIENTS = 18
# Keep enough analytical concurrency for PostgreSQL plan changes to affect the
# measured SSD signal.  The previous 18/4 mix produced a transaction-write-
# dominated baseline at only 75--79% of the empirically derived lane capacity;
# all 54 plan-changing calibration points then moved I/O upward.  Six XAP
# clients is the native large-resource reference in the verified SSD study.
# The current 14/8 (22 total clients) baseline reached only 53.9--55.0% SSD
# with 23.6--23.9 of 40 database cores.  The verified single-SSD study fixes
# 28 native clients as the high-concurrency engineering point.  Scale the
# same planner-sensitive mix to 18/10 (28 total).  The fresh r45 baseline on
# 2026-08-13 then measured 64.97%/64.19% host SSD busy.  Increasing to 18/11
# in the clean r46 baseline still measured only 63.98%/63.78%, below the
# fail-closed 65% admission floor, while database CPU remained only
# 27.56/28.05 of 40 cores and memory stayed below 3.5 of 24 GiB.  Apply the
# low-utilization rule with two additional planner-sensitive clients, 18/13.
# This preserves every IQ/AT family, percentage, statement, and SQL while
# retaining non-target CPU and memory headroom; the new setting must still
# pass a fresh two-point baseline.
XP_IO_XAP_CLIENTS = 13


def load_physical_module() -> Any:
    spec = importlib.util.spec_from_file_location("pg_physical_lane", PHYSICAL_LAUNCHER)
    if spec is None or spec.loader is None:
        raise ImportError(PHYSICAL_LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_job_fixed_schedule_contract() -> Dict[str, Any]:
    """Build the predeclared schedule consumed identically by both JOB DBs."""
    spec = importlib.util.spec_from_file_location(
        "pg_job_fixed_capture",
        JOB_FIXED_CAPTURE,
    )
    if spec is None or spec.loader is None:
        raise ImportError(JOB_FIXED_CAPTURE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_fixed_schedule(
        Path("/home/anonymous/dataset/pg_data/join-order-benchmark"),
        workers=JOB_REPLAY_CLIENTS,
        long_query_fraction=0.2,
        max_concurrent_long_queries=JOB_MAX_CONCURRENT_LONG_QUERIES,
    )


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expand_cpuset(spec: str) -> set[int]:
    result: set[int] = set()
    for part in spec.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            low_raw, high_raw = token.split("-", 1)
            result.update(range(int(low_raw), int(high_raw) + 1))
        else:
            result.add(int(token))
    if not result:
        raise ValueError(f"empty cpuset: {spec}")
    return result


def validate_cpu_contract() -> Dict[str, Any]:
    owners: Dict[str, set[int]] = {}
    for scene, specs in DB_CPUSETS.items():
        for index, spec in enumerate(specs, start=1):
            owners[f"{scene}.db_w{index}"] = expand_cpuset(spec)
    for scene, spec in CONTROLLER_CPUSETS.items():
        owners[f"{scene}.controller_clients"] = expand_cpuset(spec)
    for index, spec in enumerate(EXTERNAL_MYSQL_RESERVED_CPUSETS, start=1):
        owners[f"external_mysql_reserved_{index}"] = expand_cpuset(spec)
    names = sorted(owners)
    for index, left in enumerate(names):
        if max(owners[left]) > 255:
            raise ValueError(f"cpuset exceeds CPU255: {left}={sorted(owners[left])}")
        for right in names[index + 1 :]:
            overlap = owners[left].intersection(owners[right])
            if overlap:
                raise ValueError(f"cpuset overlap: {left} vs {right}: {sorted(overlap)}")
    return {
        "owners": {name: sorted(values) for name, values in owners.items()},
        "all_within_cpu_0_255": True,
        "pairwise_disjoint": True,
        "external_mysql_untouched": True,
    }


def replace_questions(sql: str, replacement: str) -> str:
    return re.sub(r"\?", replacement, sql)


def materialize_xp_plan_sql(output: Path) -> Dict[str, Any]:
    if tomllib is None:
        raise RuntimeError("Python tomllib is required")
    statement_path = NATIVE_RELEASE / "conf/stmt_postgres.toml"
    data = tomllib.loads(statement_path.read_text(encoding="utf-8"))
    rows: List[str] = []
    family_identities: List[str] = []
    family_to_plan_identity: Dict[str, str] = {}
    plan_identity_sources: Dict[str, List[str]] = {}
    normalized_to_plan_identity: Dict[str, str] = {}
    for section, payload in data.items():
        if not isinstance(payload, Mapping):
            continue
        raw_sql = payload.get("sql")
        statements = raw_sql if isinstance(raw_sql, list) else [raw_sql]
        for statement_index, raw_statement in enumerate(statements, start=1):
            if not isinstance(raw_statement, str):
                continue
            statement = raw_statement.strip().rstrip(";")
            if not statement:
                continue
            # For TP multi-statement transactions, EXPLAIN the read that chooses
            # or validates the row.  The native physical replay still executes
            # the complete unmodified transaction.
            if isinstance(raw_sql, list) and not statement.lower().startswith("select"):
                continue
            replacement = "'software_IT'" if section == "AP-12" else "1"
            statement = replace_questions(statement, replacement)
            if "?" in statement:
                raise RuntimeError(f"unbound XP plan placeholder: {section}")
            family_identity = f"{section}.{statement_index}"
            family_identities.append(family_identity)
            normalized_sql = re.sub(r"\s+", " ", statement.strip().rstrip(";"))
            plan_identity = normalized_to_plan_identity.get(normalized_sql)
            if plan_identity is None:
                plan_identity = f"plan_{len(rows) + 1}"
                normalized_to_plan_identity[normalized_sql] = plan_identity
                plan_identity_sources[plan_identity] = []
                rows.append(f"-- native {plan_identity}\n{statement};")
            family_to_plan_identity[family_identity] = plan_identity
            plan_identity_sources[plan_identity].append(family_identity)
    if len(family_identities) != 54:
        raise RuntimeError(
            f"expected 54 native XP family representatives, got {len(family_identities)}"
        )
    if len(rows) != EXPECTED_PLAN_QUERY_COUNT["xp_cpu"]:
        raise RuntimeError(
            f"expected {EXPECTED_PLAN_QUERY_COUNT['xp_cpu']} unique native XP plan SQLs, "
            f"got {len(rows)}"
        )
    output.write_text("\n\n".join(rows) + "\n", encoding="utf-8")
    return {
        "path": str(output),
        "sha256": sha256(output),
        "query_count": len(rows),
        "native_family_count": len(family_identities),
        "native_family_identities": family_identities,
        "family_to_plan_identity": family_to_plan_identity,
        "plan_identity_sources": plan_identity_sources,
        "duplicate_families_collapsed_only_for_plan_identity": (
            len(family_identities) - len(rows)
        ),
        "role": "offline_explain_all_unique_SQL_from_all_native_AP_and_TP_read_families",
        "physical_workload_sql_modified": False,
    }


def materialize_tpch_plan_sql(output: Path) -> Dict[str, Any]:
    chunks: List[str] = []
    source_hashes: Dict[str, str] = {}
    for query_number in range(1, 23):
        name = f"q{query_number:02d}.sql"
        source = TPCH_QUERY_ROOT / name
        if not source.is_file():
            raise FileNotFoundError(source)
        sql = source.read_text(encoding="utf-8").strip()
        if not sql:
            raise RuntimeError(f"empty TPC-H query source: {source}")
        source_hashes[f"Q{query_number}"] = sha256(source)
        chunks.append(f"-- Q{query_number} standard materialized bind\n{sql}")
    output.write_text("\n\n".join(chunks) + "\n", encoding="utf-8")
    if len(load_sql_statements(output)) != 22:
        raise RuntimeError("materialized TPC-H plan SQL is not exactly Q1-Q22")
    return {
        "path": str(output),
        "sha256": sha256(output),
        "query_count": 22,
        "query_sequence": [f"Q{index}" for index in range(1, 23)],
        "source_root": str(TPCH_QUERY_ROOT),
        "source_hashes": source_hashes,
        "role": "offline_explain_only_full_standard_Q1_Q22_materialized_binds",
        "physical_workload": "BenchBase_native_Q1_Q22_parallel_LPT_exactly_once",
    }


def workload_env(scene_name: str, run_root: Path) -> Dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "PYTHONPATH": str(ROOT),
            "BONECT_OPTIMIZER_LOCK_PATH": f"/tmp/pg_algorithm_c_{scene_name}.lock",
            "BONECT_SHARED_SSD_COORDINATION_ENABLED": "1",
            "BONECT_SHARED_SSD_COORDINATION_LOCK_PATH": (
                "/tmp/resource_adaptive_pg_ssd_measurement_v1.lock"
            ),
            # Every measured workload gets the same exclusive disk1 turn.
            # Allowing XP-CPU to reacquire a shared lease between waves could
            # starve already-waiting restore/JOB/XP-I/O writers.  Its two
            # within-scene workers still share this process-local lease, so
            # the required two-point replay parallelism is unchanged.
            "BONECT_SHARED_SSD_WORKLOAD_LOCK_MODE": "exclusive",
            "BONECT_SHARED_SSD_COORDINATION_TIMEOUT_S": str(
                SHARED_SSD_COORDINATION_TIMEOUT_S
            ),
            "PG_ALGORITHM_C_GLOBAL_RESTORE_SLOT_TIMEOUT_S": str(
                SHARED_SSD_COORDINATION_TIMEOUT_S
            ),
        }
    )
    if scene_name == "job_memory":
        workload_contract = read_json(
            run_root / scene_name / "workload_contract.json"
        )
        fixed_schedule = (
            workload_contract.get("benchmark_concurrency") or {}
        ).get("fixed_schedule") or {}
        fixed_schedule_sha256 = str(
            fixed_schedule.get("schedule_sha256") or ""
        ).strip()
        if not fixed_schedule_sha256:
            raise RuntimeError("JOB fixed schedule SHA is missing from contract")
        env.update(
            {
                "GENERIC_WORKLOAD": "job",
                "GENERIC_RUN_SECONDS": "0",
                "JOB_SQL_DIR": "/home/anonymous/dataset/pg_data/join-order-benchmark",
                "JOB_QUERY_FILES": "",
                "JOB_MAX_QUERIES": "0",
                "JOB_STATEMENT_TIMEOUT_MS": "3600000",
                "JOB_WORKERS": str(JOB_REPLAY_CLIENTS),
                # Both clients may execute a heavy query; each can consume a
                # leader plus two parallel workers without overcommitting the
                # 20-core database or recreating the shared-SSD overload.
                "JOB_MAX_CONCURRENT_LONG_QUERIES": str(
                    JOB_MAX_CONCURRENT_LONG_QUERIES
                ),
                "JOB_MAX_CONCURRENT_LONG_QUERIES_PER_FAMILY": str(
                    JOB_MAX_CONCURRENT_LONG_QUERIES_PER_FAMILY
                ),
                "JOB_LONG_QUERY_FRACTION": "0.2",
                "JOB_TARGET_WALL_TIME_S": str(JOB_TARGET_WALL_TIME_S),
                "JOB_FIXED_SCHEDULE_SHA256": fixed_schedule_sha256,
            }
        )
    elif scene_name == "tpch_memory":
        env.update(
            {
                "GENERIC_WORKLOAD": "tpch",
                "GENERIC_RUN_SECONDS": "0",
                "BENCHBASE_SOURCE_ROOT": "/home/anonymous/benchbase-postgres",
                "BENCHBASE_RUNTIME_ROOT": str(run_root / "benchbase_runtime"),
                "BENCHBASE_SCALEFACTOR": "5",
                "BENCHBASE_TPCH_WORKERS": str(TPCH_REPLAY_CLIENTS),
                "TPCH_TARGET_WALL_TIME_S": str(TPCH_TARGET_WALL_TIME_S),
                "BENCHBASE_JAVA_XMX": "-Xmx2g",
            }
        )
    return env


def prepare(tag: str, scene_names: Optional[Sequence[str]] = None) -> Path:
    cpu_contract = validate_cpu_contract()
    physical = load_physical_module()
    run_root = RUN_BASE / tag
    run_root.mkdir(parents=True, exist_ok=False)
    configs: Dict[str, str] = {}
    contracts: Dict[str, str] = {}
    selected = tuple(scene_names or physical.SCENES.keys())
    unknown = sorted(set(selected).difference(physical.SCENES))
    if unknown:
        raise ValueError(f"unknown scenes: {unknown}")
    for scene_name in selected:
        scene = physical.SCENES[scene_name]
        config_path = physical.configure_scene(scene_name, scene, run_root, {})
        cfg = read_json(config_path)
        scene_dir = config_path.parent
        workers = (((cfg.get("binary_plan_tuning") or {}).get("validation") or {}).get("docker_workers") or {}).get("workers") or []
        if len(workers) != 2:
            raise RuntimeError(f"{scene_name}: expected two workers")
        for index, worker in enumerate(workers):
            cpuset = DB_CPUSETS[scene_name][index]
            worker["cpuset_cpus"] = cpuset
            # Keep the persisted worker contract consistent with the Docker
            # CFS quota applied by ``apply_resource_contract``.  A stale
            # ``cpus: 8`` value made a 16-CPU JOB cpuset look authoritative
            # while the serialized assignment still described eight cores.
            worker["cpus"] = str(len(expand_cpuset(cpuset)))
            worker["memory"] = (
                f"{RESOURCE_CAPACITY[scene_name]['memory_gib']:g}g"
            )
        bpt = cfg["binary_plan_tuning"]
        plan_estimator_cfg = cfg.setdefault("metrics", {}).setdefault(
            "plan_estimator", {}
        )
        plan_estimator_cfg["physical_model"] = {
            "enabled": True,
            "contract": "algorithm_c_executor_physics_plan_ratio_v2",
            "require_complete_explain": True,
            "capture_first_point_as_baseline": False,
            "error_bounds_calibrated": False,
            "catalog_relation_stats_required_for_confidence": True,
            "nested_loop_execution_multiplier_enabled": True,
            "planned_parallel_work_enabled": True,
            "spill_bytes_and_batches_proxy_enabled": True,
            "parent_child_work_ratio_enabled": True,
            "qps_feature_forbidden": True,
        }
        bpt["validation"]["executor_telemetry"] = {
            "enabled": True,
            "required": True,
            "source": "pg_stat_statements_post_reset_aggregate_v1",
            "fields": [
                "rows",
                "shared_blks_hit",
                "shared_blks_read",
                "shared_blks_dirtied",
                "shared_blks_written",
                "temp_blks_read",
                "temp_blks_written",
                "blk_read_time",
                "blk_write_time",
                "wal_bytes",
            ],
            "role": "post_replay_calibration_label",
            "qps_used_for_resource_prediction": False,
        }
        bpt["validation"]["docker_workers"]["workers"] = workers
        bpt["validation"]["docker_workers"]["cpu_memory_parallelism"] = 2
        bpt["validation"]["docker_workers"]["io_parallelism"] = 2
        bpt["validation"]["docker_workers"]["same_ssd_adaptive_parallelism"]["effective_worker_cap"] = 2
        # Keep the legacy binary-search metadata consistent with the formal
        # Algorithm C convergence contract consumed by the lane controller.
        bpt["binary_search"]["accepted_shrink_target"] = (
            ALGORITHM_C_ACCEPTED_TARGET
        )
        cache_runtime = bpt["validation"]["docker_runtime"]
        # Worker pgdata directories are mode 0700 and owned by the container
        # account, so an unprivileged host walk cannot prepare cache state.
        # Use the same PostgreSQL-container fallback in every lane and fail
        # before physical replay if it cannot evict at least one data file.
        cache_runtime.update(
            {
                "evict_data_file_cache_before_validation": True,
                "evict_data_file_cache_host_sudo": False,
                "evict_data_file_cache_container_fallback": False,
                "evict_data_file_cache_postgres_program_fallback": True,
                "evict_data_file_cache_require_success": True,
                "evict_data_file_cache_max_files": 0,
                "evict_data_file_cache_max_bytes": 0,
            }
        )
        bpt["acceptance_policy"]["resource_capacity"] = copy.deepcopy(
            RESOURCE_CAPACITY[scene_name]
        )
        outer_acceptance = bpt["binary_search"]["nested_loop"]["outer_acceptance"]
        outer_acceptance.update(
            {
                # Round 1 compares with the canonical baseline.  Every later
                # formal round compares with the previous accepted incumbent;
                # rejected and technical rows never advance that reference.
                "qps_reference_mode": "last_accepted",
                "qps_threshold_mode": "static_floor",
            }
        )
        if scene_name == "xp_cpu":
            # A native-XP 32/4 workload drove both 4-core workers to
            # 3.900--3.915 cores (97.5--97.9%), above the 95% hard-overload
            # boundary.  At the cgroup ceiling, planner changes mostly alter
            # throughput while measured CPU stays flat, so a target-down
            # calibration cannot be identified.  The same native workload at
            # 17/3 consumes about 21 of 30 cores in the IO lane; scaling that
            # observed demand to the CPU lane showed that 3/1 still reached
            # the 4-core ceiling: one XAP leader can use two parallel workers,
            # and three simultaneous XTP clients consume the remaining CPU.
            # The completed 1/1 calibration on 2026-08-11 measured only about
            # 2.05 baseline cores and all 60 distinct plan-changing candidates
            # increased CPU by 4.1--77.8%; no target-down holdout existed.
            # That is an underloaded workload, not an identifiable CPU
            # bottleneck.  The earlier 3/1 pilot reached the four-core ceiling,
            # so use the intervening 2/1 setting.  Only the policy-approved
            # native-XP client count changes; every IQ/AT family, percentage,
            # statement and SQL remains unchanged.
            hybench = bpt["validation"]["hybench_xp"]
            hybench["xtpclient"] = 2
            hybench["xapclient"] = 1
        elif scene_name == "xp_io":
            # The old 32/4 setting saturated both 8 CPUs and the SSD.  Keep two
            # replay workers, but move the workload into the SSD's stable mean
            # utilization region while the database gets independent CPU
            # headroom.  A clean 17/3 baseline on 2026-08-10 measured
            # 64.306% SSD utilization, just below the fail-closed 65% IO-scene
            # admission floor, with only about 26.6% CPU and 51.8% memory in
            # the preceding qualified run.  The subsequent 18/4 run remained
            # below the target band and all 54 distinct plan-changing points
            # increased I/O.  Increase only the policy-approved XAP client
            # count to the study's native reference; the AP query universe,
            # percentages, statement file, and SQL remain unchanged.
            hybench = bpt["validation"]["hybench_xp"]
            hybench["xtpclient"] = XP_IO_XTP_CLIENTS
            hybench["xapclient"] = XP_IO_XAP_CLIENTS
        # Memory resource must not be cgroup usage at the hard limit; it is a
        # constant cache-fill signal and cannot identify planner effects.
        if scene_name == "job_memory":
            bpt["validation"]["docker_runtime"]["memory_mode"] = "working_set"
            job_validation = bpt["validation"]["hybench_xp"]
            job_validation["launcher"] = str(JOB_FIXED_LAUNCHER)
            # Keep the serialized config/audit aligned with the environment
            # consumed by the canonical JOB dispatcher.
            job_validation["tpclient"] = JOB_REPLAY_CLIENTS
            # The workload records the exact 900-second hard gate.  The outer
            # timeout prevents a point that has already missed that contract
            # from occupying the lane indefinitely, while leaving 120s for
            # setup, monitoring and orderly process teardown.
            job_validation["timeout_s"] = JOB_OUTER_TECHNICAL_TIMEOUT_S
            job_validation["timeout_grace_s"] = 120
        if scene_name == "tpch_memory":
            bpt["validation"]["docker_runtime"]["memory_mode"] = "working_set"
            # The physical database and BenchBase XML are SF5.  Keep the
            # generated outer props on the same value so structured replay
            # evidence cannot misleadingly report sf=1 while executing SF5.
            tpch_validation = bpt["validation"]["hybench_xp"]
            tpch_validation["tpclient"] = TPCH_REPLAY_CLIENTS
            tpch_validation["sf"] = "5"
            # A complete SF5 Q1--Q22 serial pass exceeded the legacy 660 s
            # wrapper timeout while remaining healthy.  Keep the recovery in
            # every future compatible config so a reusable T is not paired
            # with a configuration that kills valid large queries.
            tpch_validation["timeout_s"] = TPCH_OUTER_TECHNICAL_TIMEOUT_S
            tpch_validation["timeout_grace_s"] = 120
        if scene_name in {"xp_cpu", "xp_io"}:
            plan_meta = materialize_xp_plan_sql(scene_dir / "native_xp_full_plan_representatives.sql")
            bpt["workload"]["plan_sql_file"] = plan_meta["path"]
            bpt["endpoint_screening"]["max_queries"] = plan_meta["query_count"]
        elif scene_name == "tpch_memory":
            plan_meta = materialize_tpch_plan_sql(
                scene_dir / "tpch_q1_q22_full_plan.sql"
            )
            bpt["workload"]["plan_sql_file"] = plan_meta["path"]
            bpt["endpoint_screening"]["max_queries"] = 22
        else:
            plan_path = Path(str(bpt["workload"]["plan_sql_file"]))
            statement_count = len(load_sql_statements(plan_path))
            if statement_count != EXPECTED_PLAN_QUERY_COUNT[scene_name]:
                raise RuntimeError(
                    f"{scene_name}: plan query count {statement_count} != "
                    f"{EXPECTED_PLAN_QUERY_COUNT[scene_name]}"
                )
            plan_meta = {
                "path": str(plan_path),
                "sha256": sha256(plan_path),
                "query_count": statement_count,
                "role": "full_authoritative_workload_plan_set",
            }
        plan_query_count = int(plan_meta["query_count"])
        if plan_query_count != EXPECTED_PLAN_QUERY_COUNT[scene_name]:
            raise RuntimeError(
                f"{scene_name}: authoritative plan count {plan_query_count} != "
                f"{EXPECTED_PLAN_QUERY_COUNT[scene_name]}"
            )
        job_fixed_schedule = (
            build_job_fixed_schedule_contract()
            if scene_name == "job_memory"
            else None
        )
        bpt["workload"]["plan_coverage_gate"] = {
            "enabled": True,
            "catalog_complete": True,
            "coverage_scope": (
                "all_native_xp_families_unique_sql_plan_universe"
                if scene_name in {"xp_cpu", "xp_io"}
                else (
                    "canonical_job_113"
                    if scene_name == "job_memory"
                    else "tpch_sf5_q1_q22_parallel_lpt"
                )
            ),
            "expected_query_count": plan_query_count,
            "expected_query_ids": [
                f"file_{index}" for index in range(1, plan_query_count + 1)
            ],
            "plan_sql_sha256": plan_meta["sha256"],
            "block_physical_base_topk": True,
            "fail_closed_before_physical_workload": True,
        }
        contract = {
            "contract_version": 2,
            "scene": scene_name,
            "benchmark": scene["workload"],
            "target_resource": scene["target"],
            "policy": "20260806-pg-experiment-workload-policy",
            "physical_replay": (
                "native_HyBench_XP_default_full_workload"
                if scene_name in {"xp_cpu", "xp_io"}
                else (
                    f"canonical_JOB_113_exactly_once_{JOB_REPLAY_CLIENTS}_clients_shared_database"
                    if scene_name == "job_memory"
                    else "TPC-H_SF5_Q1_Q22_parallel_LPT_exactly_once"
                )
            ),
            "plan_evidence": plan_meta,
            "benchmark_concurrency": (
                {
                    "database_instances_per_replay": 1,
                    "client_count": JOB_REPLAY_CLIENTS,
                    "max_concurrent_long_queries": (
                        JOB_MAX_CONCURRENT_LONG_QUERIES
                    ),
                    "max_concurrent_long_queries_per_family": (
                        JOB_MAX_CONCURRENT_LONG_QUERIES_PER_FAMILY
                    ),
                    "task_dispatch": "fixed_worker_lanes",
                    "queue_order": "predeclared_historical_runtime_lpt_lanes",
                    "queue_order_changes_query_set": False,
                    "worker_assignment_fixed": True,
                    "docker_schedule_identical": True,
                    "fixed_schedule": job_fixed_schedule,
                    "query_execution": "canonical_113_exactly_once",
                    "target_wall_time_s": JOB_TARGET_WALL_TIME_S,
                    "target_wall_time_is_advisory": False,
                }
                if scene_name == "job_memory"
                else (
                    {
                        "database_instances_per_replay": 1,
                        "client_count": TPCH_REPLAY_CLIENTS,
                        "task_dispatch": "deterministic_historical_LPT_lanes",
                        "query_execution": "native_Q1_Q22_exactly_once",
                        "target_wall_time_s": TPCH_TARGET_WALL_TIME_S,
                        "target_wall_time_is_advisory": False,
                    }
                    if scene_name == "tpch_memory"
                    else None
                )
            ),
            "database": {
                "dbname": scene["dbname"],
                "template_db": scene["template_db"],
                "workers": [
                    {
                        "name": worker.get("name"),
                        "container": worker.get("container"),
                        "host": worker.get("host"),
                        "port": worker.get("port"),
                        "cpuset_cpus": worker.get("cpuset_cpus"),
                    }
                    for worker in workers
                ],
            },
            "resource_capacity": RESOURCE_CAPACITY[scene_name],
            "top5_replay_parallelism": 2,
            "wave_shape": [2, 2, 1],
            "shared_ssd_coordination": {
                "contract": "pg_algorithm_c_shared_ssd_measurement_rwlock_yield_v2",
                "device": "nvme1n1",
                "database_restore_mode": "exclusive",
                "physical_workload_mode": "exclusive",
                "parallel_workers_share_process_lease": True,
                "covers_cache_eviction_and_physical_workload": True,
                "technical_failure_or_acceptance_state_mutation": False,
            },
            "qps_inner_scoring": False,
            "outer_qps_acceptance": {
                "contract": (
                    "candidate_qps_strictly_above_previous_accepted_round_v1"
                ),
                "first_round_reference": "canonical_baseline",
                "subsequent_round_reference": "previous_accepted_round",
                "threshold_source": "outer_acceptance.min_db_qps_gain_pct",
                "resource_reference": "last_accepted_incumbent",
                "qps_noise_model_role": "required_audit_not_compounded_threshold",
                "rejected_round_updates_reference": False,
                "technical_failure_updates_reference": False,
            },
            "operator_resource_pruning": False,
            "outer_accept_reject_pruning": {
                "contract": "algorithm_c_outer_accept_reject_plan_context_v2",
                "label": "outer_semantic_acceptance.accepted",
                "hard_rule_key": [
                    "workload_contract",
                    "target",
                    "acceptance_policy",
                    "required_acceptance_tier",
                    "qps_noise_model",
                    "incumbent_params",
                    "full_plan_fingerprint",
                ],
                "operator_count_only_rules_forbidden": True,
                "technical_failures_excluded": True,
            },
            "inner_candidate_scoring": {
                "contract": "algorithm_c_directional_band_target_v1",
                "target_bottleneck_band_pct": [85.0, 90.0],
                "target_bottleneck_hard_pct": 95.0,
                "target_bottleneck_preferred_endpoint_pct": 85.0,
                "non_bottleneck_band_pct": [30.0, 50.0],
                "non_bottleneck_hard_pct": 90.0,
                "non_bottleneck_preferred_endpoint_pct": 50.0,
                "resource_weights": {"cpu": 1.0, "memory": 1.0, "io": 3.0},
                "qps_feature_forbidden": True,
            },
            "resource_estimation": {
                "contract": "algorithm_c_executor_physics_plan_ratio_v2",
                "pre_replay_features": [
                    "full_plan_tree",
                    "plan_rows_and_width",
                    "parent_child_work_ratio",
                    "nested_loop_execution_multiplier_proxy",
                    "planned_parallel_work",
                    "relation_pages_tuples_visibility",
                    "sort_hash_spill_bytes_and_batches_proxy",
                    "catalog_statistics_staleness",
                ],
                "post_replay_labels": "pg_stat_statements_post_reset_aggregate_v1",
                "actual_rows_loops_workers_not_fabricated_pre_replay": True,
                "qps_feature_forbidden": True,
            },
        }
        if scene_name in {"xp_cpu", "xp_io"}:
            hybench = bpt["validation"]["hybench_xp"]
            contract["native_xp"] = {
                "xtpclient": hybench["xtpclient"],
                "xapclient": hybench["xapclient"],
                "at_percentages": {
                    f"at{index}_percent": hybench[f"at{index}_percent"]
                    for index in range(1, 7)
                },
                "statement_file": hybench["statement_file"],
                "statement_file_sha256": hybench["statement_file_sha256"],
                "native_jar_file": hybench["native_jar_file"],
                "native_jar_sha256": hybench["native_jar_sha256"],
                "forbidden_query_selection_fields_absent": True,
            }
        elif scene_name == "job_memory":
            contract["postgres_parallel_worker_contract"] = {
                "contract": "job_postgresql_parallel_workers_v1",
                "max_worker_processes": JOB_MAX_WORKER_PROCESSES,
                "max_parallel_workers": JOB_MAX_PARALLEL_WORKERS,
                "max_parallel_workers_per_gather": (
                    JOB_MAX_PARALLEL_WORKERS_PER_GATHER
                ),
                "startup_apply_and_readback_required": True,
            }
            contract["postgres_memory_cache_contract"] = {
                "contract": "job_postgresql_memory_cache_v1",
                "docker_memory_gib": RESOURCE_CAPACITY[scene_name]["memory_gib"],
                "shared_buffers": JOB_SHARED_BUFFERS,
                "reason": "prevent_canonical_job_non_target_ssd_cache_thrash",
                "startup_apply_restart_and_readback_required": True,
            }
        elif scene_name == "tpch_memory":
            contract["scale_factor"] = 5
            contract["scale_factor_evidence"] = {
                "database_name": scene["dbname"],
                "template_database": scene["template_db"],
                "outer_props_key": "sf=5",
                "benchbase_xml_key": "scalefactor=5",
            }
            contract["postgres_parallel_worker_contract"] = {
                "contract": "tpch_postgresql_parallel_workers_v1",
                "max_parallel_workers_per_gather": (
                    TPCH_MAX_PARALLEL_WORKERS_PER_GATHER
                ),
                "reason": "prevent_non_target_shared_ssd_saturation",
                "startup_apply_and_readback_required": True,
            }
            contract["postgres_memory_cache_contract"] = {
                "contract": "tpch_postgresql_memory_cache_v1",
                "docker_memory_gib": RESOURCE_CAPACITY[scene_name]["memory_gib"],
                "shared_buffers": TPCH_SHARED_BUFFERS,
                "reason": "keep_sf5_memory_target_without_non_target_ssd_bottleneck",
                "startup_apply_restart_and_readback_required": True,
            }
        contract_path = scene_dir / "workload_contract.json"
        write_json(contract_path, contract)
        t_path = scene_dir / f"experience_table_T_{scene_name}_fresh.json"
        cfg["algorithm_c"] = {
            "scene": scene_name,
            "benchmark": scene["workload"],
            "method": "experience_table_all_subset_sgd_algorithm_c_v1",
            "algorithm_c_contract_compliant": True,
            "initial_params": copy.deepcopy(
                JOB_INITIAL_PARAMS
                if scene_name == "job_memory"
                else INITIAL_PARAMS
            ),
            "active_params": list(ACTIVE_PARAMS),
            "experience_table_path": str(t_path),
            "workload_contract_path": str(contract_path),
            "workload_contract": contract,
            "expected_plan_query_count": EXPECTED_PLAN_QUERY_COUNT[scene_name],
            "subset_max_workers": 4,
            "real_replay_top_k": 5,
            "real_replay_parallelism": 2,
            "accepted_start": 0,
            "accepted_target": ALGORITHM_C_ACCEPTED_TARGET,
            "normalized_width_threshold": 0.01,
            "stability_confirmation_rounds": 2,
            "fresh_t_calibration_required": True,
            "fresh_t_train_minimum": 20,
            "fresh_t_independent_holdout_minimum": 10,
            "calibration_release": {
                "enabled": True,
                "contract": "algorithm_c_verified_calibration_release_v1",
                "compatibility_contract": (
                    "algorithm_c_calibration_compatibility_v1"
                ),
                "root": CALIBRATION_RELEASE_ROOT,
                "reuse_requires_exact_baseline_plan_fingerprint": True,
                "incompatible_policy": "fresh_calibration",
                "formal_baseline_controls_still_required": True,
            },
            "technical_failure_state_mutation": False,
            "base_topk_dedupe_key": "validated_full_physical_plan_fingerprint",
            "same_plan_different_parameters_retained": False,
            "fallback_expansion_plan_tree_change_only": True,
            "target_support_abstention_enabled": scene_name in {
                "tpch_memory",
                "xp_io",
            },
            "controller_client_cpuset": CONTROLLER_CPUSETS[scene_name],
            "operator_resource_pruning_enabled": False,
            "outer_accept_reject_pruning": {
                "enabled": True,
                "contract": "algorithm_c_outer_accept_reject_plan_context_v2",
                "minimum_valid_rejects_per_plan": 2,
                "minimum_distinct_rounds": 2,
                "minimum_distinct_parameter_vectors": 2,
                "accepted_counterexample_disables_hard_rule": True,
                "technical_failures_excluded": True,
                "operator_count_only_rules_forbidden": True,
                "missing_context_policy": "fail_open_real_replay",
                "soft_evidence_policy": "deprioritize_without_modifying_inner_score",
                "qps_usage": "outer_label_only_not_resource_prediction_or_candidate_score",
                "evidence_log_event": "algorithm_c_outer_replay_admission",
            },
            "resource_estimation_contract_version": 2,
            "resource_estimation_contract": "algorithm_c_executor_physics_plan_ratio_v2",
            "inner_scoring_contract": "algorithm_c_directional_band_target_v1",
            "qps_noise_gate": {
                "enabled": True,
                "static_floor_pct": 0.1,
                "confidence_z": 1.645,
                "maximum_worker_repeat_ratio": 1.25,
                "startup_complete_workload_controls_per_worker": 2,
                "within_run_qps_dispersion_used": False,
                "intermediate_acceptance_threshold_mode": "static_floor",
                "intermediate_acceptance_reference": "canonical_baseline",
            },
            "ssd_replay_qualification": {
                "enabled": True,
                "expected_devices": ["nvme1n1"],
                "expected_source": "host_diskstats_io_busy_ms",
                "minimum_stable_samples": 45,
                "non_target_shared_ssd_policy": "container_io_capacity",
                "non_target_maximum_container_io_util_pct": 80.0,
                "non_target_maximum_ssd_avg_pct": 80.0,
                "io_target_baseline_minimum_ssd_avg_pct": 65.0,
                # XP IO deliberately starts from the overloaded side and must
                # converge back into the formal 85-90% target band.  The
                # admission ceiling only rejects an effectively pinned or
                # externally unstable device; it does not change scoring or
                # outer acceptance targets.
                "io_target_maximum_ssd_avg_pct": 98.0,
                # Analytical scans can legitimately produce brief 100% busy
                # samples even when IO is not the sustained bottleneck.  The
                # fail-closed average and aggregate-container ceilings remain
                # at 80%; do not turn a short scan burst into a technical
                # failure solely through p99.
                "non_target_maximum_ssd_p99_pct": 100.1,
                "io_target_maximum_ssd_p99_pct": 100.1,
                "maximum_parallel_avg_skew_pct_points": 5.0,
                "out_of_band_is_technical_failure": True,
            },
        }
        binary_search_cfg = bpt.setdefault("binary_search", {})
        binary_search_cfg.pop("operator_resource_reject_rules", None)
        binary_search_cfg["replay_candidate_pruning"] = {
            "enabled": False,
            "operator_resource_hard_pruning_forbidden": True,
            "reason": "operator changes are not sufficient evidence for direct reject",
        }
        if scene_name == "xp_io":
            cfg["algorithm_c"]["io_scene_qualification"] = {
                "enabled": True,
                "derive_io_capacity_from_ssd_util": True,
                "minimum_ssd_mean_util_pct": 65.0,
                "maximum_ssd_mean_util_pct": 98.0,
                "maximum_non_target_cpu_util_pct": 80.0,
                "maximum_non_target_memory_util_pct": 80.0,
                "required_parallel_workers": 2,
                "on_failure": "restore_clean_and_stop_before_candidate_batch",
                "overloaded_baseline_converges_to_formal_band": [85.0, 90.0],
                "admission_ceiling_is_not_acceptance_target": True,
            }
        machine_spec = dict(bpt["experiment_meta"].get("machine_spec") or {})
        machine_spec.update(
            {
                "cpu_cores": RESOURCE_CAPACITY[scene_name]["cpu_cores"],
                "mem_gb": RESOURCE_CAPACITY[scene_name]["memory_gib"],
                "disk_bw_mb": RESOURCE_CAPACITY[scene_name]["io_mib_s"],
            }
        )
        bpt["experiment_meta"].update(
            {
                "exp_id": f"PG-ALGORITHM-C-{scene_name}-20260809",
                "method": "experience_table_all_subset_sgd_algorithm_c_v1",
                "candidate_generator": "all_nonempty_experience_table_parameter_subsets",
                "algorithm_c_contract_compliant": True,
                "algorithm_c_contract_gap": None,
                "accepted_shrink_target": ALGORITHM_C_ACCEPTED_TARGET,
                "normalized_width_threshold": 0.01,
                "stability_confirmation_rounds": 2,
                "formal_accepted_start": 0,
                "controller_client_cpuset": CONTROLLER_CPUSETS[scene_name],
                "machine_spec": machine_spec,
            }
        )
        write_json(config_path, cfg)
        write_json(scene_dir / "worker_assignment.json", workers)
        configs[scene_name] = str(config_path)
        contracts[scene_name] = str(contract_path)
    write_json(
        run_root / "launch_manifest.json",
        {
            "run_root": str(run_root),
            "method": "experience_table_all_subset_sgd_algorithm_c_v1",
            "algorithm_c_contract_compliant": True,
            "scenes": configs,
            "workload_contracts": contracts,
            "fresh_t_calibration": {
                "required": True,
                "accepted_credit": 0,
                "per_physical_batch_limit": 5,
                "independent_holdout": True,
                "minimum_train_count": 20,
                "holdout_count": 10,
                "operator_resource_pruning": False,
                "outer_accept_reject_pruning": True,
            },
            "resource_estimation": {
                "contract": "algorithm_c_executor_physics_plan_ratio_v2",
                "executor_telemetry_required": True,
                "qps_feature_forbidden": True,
                "target_resource_holdout_required": True,
                "non_target_resource_holdout_diagnostic_only": False,
                "scored_resource_holdout_contract": (
                    "all_scored_resources_hard_independent_holdout_v3"
                ),
                "non_target_actual_utilization_safety_gate_required": True,
                "operator_resource_hard_pruning_forbidden": True,
                "outer_accept_reject_context_pruning_enabled": True,
                "outer_pruning_contract": "algorithm_c_outer_accept_reject_plan_context_v2",
            },
            "top_k": 5,
            "parallelism_per_scene": 2,
            "wave_shape": [2, 2, 1],
            "accepted_start": 0,
            "accepted_target": ALGORITHM_C_ACCEPTED_TARGET,
            "normalized_width_threshold": 0.01,
            "stability_confirmation_rounds": 2,
            "cpu_contract": cpu_contract,
            "source_provenance": {
                "contract": "algorithm_c_launch_source_sha256_v1",
                "files": {
                    str(path.relative_to(ROOT)): sha256(path)
                    for path in SOURCE_PROVENANCE_PATHS
                },
            },
            "mysql_containers_untouched": True,
        },
    )
    return run_root


def docker_readback(container: str) -> Dict[str, Any]:
    try:
        completed = subprocess.run(
            [
                "docker",
                "inspect",
                "-f",
                "{{json .HostConfig}}",
                container,
            ],
            text=True,
            capture_output=True,
            timeout=DOCKER_INSPECT_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"docker inspect timed out after {DOCKER_INSPECT_TIMEOUT_S}s "
            f"for {container}"
        ) from exc
    if completed.returncode != 0:
        raise RuntimeError(f"docker inspect failed for {container}: {completed.stderr}")
    value = json.loads(completed.stdout)
    return {
        "container": container,
        "cpuset_cpus": value.get("CpusetCpus"),
        "nano_cpus": value.get("NanoCpus"),
        "memory": value.get("Memory"),
    }


def _container_psql(
    worker: Mapping[str, Any],
    sql: str,
    *,
    timeout_s: float = DOCKER_EXEC_PSQL_TIMEOUT_S,
) -> subprocess.CompletedProcess[str]:
    """Run psql inside one PostgreSQL worker container.

    The worker postmasters listen on their externally assigned ports inside
    the containers as well.  Using the local Unix socket avoids depending on
    host libpq routing while keeping the mutation scoped to the explicitly
    selected PostgreSQL container.
    """

    command = [
        "docker",
        "exec",
        str(worker["container"]),
        "psql",
        "-p",
        str(worker["port"]),
        "-U",
        str(worker.get("user") or "postgres"),
        "-d",
        "postgres",
        "-v",
        "ON_ERROR_STOP=1",
        "-AtF",
        "|",
        "-c",
        sql,
    ]
    last_timeout: Optional[subprocess.TimeoutExpired] = None
    for attempt in range(1, DOCKER_EXEC_PSQL_ATTEMPTS + 1):
        try:
            return subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            last_timeout = exc
            if attempt < DOCKER_EXEC_PSQL_ATTEMPTS:
                time.sleep(2.0)
    raise RuntimeError(
        f"PostgreSQL docker exec timed out after "
        f"{DOCKER_EXEC_PSQL_ATTEMPTS} attempts x {timeout_s}s for "
        f"container={worker['container']}"
    ) from last_timeout


def _read_job_postgres_parallel_settings(
    worker: Mapping[str, Any],
) -> Dict[str, Dict[str, Any]]:
    names = (
        "max_worker_processes",
        "max_parallel_workers",
        "max_parallel_workers_per_gather",
    )
    quoted = ",".join(f"'{name}'" for name in names)
    completed = _container_psql(
        worker,
        (
            "select name,setting,context,pending_restart "
            f"from pg_settings where name in ({quoted}) order by name"
        ),
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "JOB PostgreSQL parallel setting readback failed for "
            f"{worker['container']}: {completed.stderr}"
        )
    result: Dict[str, Dict[str, Any]] = {}
    for line in completed.stdout.splitlines():
        fields = line.split("|")
        if len(fields) != 4:
            continue
        name, setting, context, pending_restart = fields
        result[name] = {
            "setting": int(setting),
            "context": context,
            "pending_restart": pending_restart == "t",
        }
    if set(result) != set(names):
        raise RuntimeError(
            "JOB PostgreSQL parallel setting readback incomplete for "
            f"{worker['container']}: {result}"
        )
    return result


def apply_job_postgres_parallel_contract(
    worker: Mapping[str, Any],
) -> Dict[str, Any]:
    """Apply and verify the fixed JOB PostgreSQL parallel-worker contract."""

    expected = {
        "max_worker_processes": JOB_MAX_WORKER_PROCESSES,
        "max_parallel_workers": JOB_MAX_PARALLEL_WORKERS,
        "max_parallel_workers_per_gather": JOB_MAX_PARALLEL_WORKERS_PER_GATHER,
    }
    before = _read_job_postgres_parallel_settings(worker)
    changed: List[str] = []
    for name, value in expected.items():
        if int(before[name]["setting"]) == value:
            continue
        completed = _container_psql(
            worker,
            f"ALTER SYSTEM SET {name} = '{value}'",
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"JOB PostgreSQL ALTER SYSTEM failed for {worker['container']} "
                f"setting={name}: {completed.stderr}"
            )
        changed.append(name)

    restarted = "max_worker_processes" in changed
    if restarted:
        completed = subprocess.run(
            ["docker", "restart", str(worker["container"])],
            text=True,
            capture_output=True,
            timeout=90,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"JOB PostgreSQL container restart failed for "
                f"{worker['container']}: {completed.stderr}"
            )
        deadline = time.monotonic() + 75.0
        last_error = ""
        while time.monotonic() < deadline:
            ready = _container_psql(worker, "select 1", timeout_s=8.0)
            if ready.returncode == 0 and ready.stdout.strip() == "1":
                break
            last_error = ready.stderr.strip()
            time.sleep(1.0)
        else:
            raise RuntimeError(
                f"JOB PostgreSQL did not become ready after restart for "
                f"{worker['container']}: {last_error}"
            )
    elif changed:
        completed = _container_psql(worker, "select pg_reload_conf()")
        if completed.returncode != 0 or completed.stdout.strip() != "t":
            raise RuntimeError(
                f"JOB PostgreSQL reload failed for {worker['container']}: "
                f"stdout={completed.stdout!r} stderr={completed.stderr!r}"
            )

    after = _read_job_postgres_parallel_settings(worker)
    mismatches = {
        name: {"expected": expected_value, "observed": after[name]}
        for name, expected_value in expected.items()
        if int(after[name]["setting"]) != expected_value
        or bool(after[name]["pending_restart"])
    }
    if mismatches:
        raise RuntimeError(
            f"JOB PostgreSQL parallel contract readback mismatch for "
            f"{worker['container']}: {mismatches}"
        )
    return {
        "container": str(worker["container"]),
        "contract": "job_postgresql_parallel_workers_v1",
        "before": before,
        "expected": expected,
        "after": after,
        "changed": changed,
        "container_restarted": restarted,
        "passed": True,
    }


def _read_job_shared_buffers_setting(
    worker: Mapping[str, Any],
) -> Dict[str, Any]:
    """Read JOB's postmaster cache setting for exact startup verification."""
    completed = _container_psql(
        worker,
        (
            "select current_setting('shared_buffers'),context,pending_restart "
            "from pg_settings where name = 'shared_buffers'"
        ),
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "JOB shared_buffers readback failed for "
            f"{worker['container']}: {completed.stderr}"
        )
    fields = completed.stdout.strip().split("|")
    if len(fields) != 3:
        raise RuntimeError(
            "JOB shared_buffers readback incomplete for "
            f"{worker['container']}: {completed.stdout!r}"
        )
    setting, context, pending_restart = fields
    return {
        "setting": setting,
        "context": context,
        "pending_restart": pending_restart == "t",
    }


def apply_job_postgres_memory_contract(
    worker: Mapping[str, Any],
) -> Dict[str, Any]:
    """Apply the fixed JOB cache envelope and verify it after restart."""
    before = _read_job_shared_buffers_setting(worker)
    changed = str(before["setting"]).upper() != JOB_SHARED_BUFFERS.upper()
    if changed:
        completed = _container_psql(
            worker,
            f"ALTER SYSTEM SET shared_buffers = '{JOB_SHARED_BUFFERS}'",
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"JOB shared_buffers ALTER SYSTEM failed for "
                f"{worker['container']}: {completed.stderr}"
            )
        completed = subprocess.run(
            ["docker", "restart", str(worker["container"])],
            text=True,
            capture_output=True,
            timeout=90,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"JOB PostgreSQL restart failed for {worker['container']}: "
                f"{completed.stderr}"
            )
        deadline = time.monotonic() + 75.0
        last_error = ""
        while time.monotonic() < deadline:
            ready = _container_psql(worker, "select 1", timeout_s=8.0)
            if ready.returncode == 0 and ready.stdout.strip() == "1":
                break
            last_error = ready.stderr.strip()
            time.sleep(1.0)
        else:
            raise RuntimeError(
                f"JOB PostgreSQL did not become ready after cache restart "
                f"for {worker['container']}: {last_error}"
            )
    after = _read_job_shared_buffers_setting(worker)
    if (
        str(after["setting"]).upper() != JOB_SHARED_BUFFERS.upper()
        or bool(after["pending_restart"])
    ):
        raise RuntimeError(
            "JOB shared_buffers contract readback mismatch for "
            f"{worker['container']}: expected={JOB_SHARED_BUFFERS} "
            f"observed={after}"
        )
    return {
        "container": str(worker["container"]),
        "contract": "job_postgresql_memory_cache_v1",
        "before": before,
        "expected": {"shared_buffers": JOB_SHARED_BUFFERS},
        "after": after,
        "changed": ["shared_buffers"] if changed else [],
        "container_restarted": changed,
        "passed": True,
    }


def _read_tpch_postgres_parallel_setting(
    worker: Mapping[str, Any],
) -> Dict[str, Any]:
    """Read the fixed per-query TPC-H parallel-worker setting."""

    name = "max_parallel_workers_per_gather"
    completed = _container_psql(
        worker,
        (
            "select setting,context,pending_restart from pg_settings "
            f"where name = '{name}'"
        ),
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "TPC-H PostgreSQL parallel setting readback failed for "
            f"{worker['container']}: {completed.stderr}"
        )
    fields = completed.stdout.strip().split("|")
    if len(fields) != 3:
        raise RuntimeError(
            "TPC-H PostgreSQL parallel setting readback incomplete for "
            f"{worker['container']}: {completed.stdout!r}"
        )
    setting, context, pending_restart = fields
    return {
        "setting": int(setting),
        "context": context,
        "pending_restart": pending_restart == "t",
    }


def apply_tpch_postgres_parallel_contract(
    worker: Mapping[str, Any],
) -> Dict[str, Any]:
    """Apply and verify serial execution inside each TPC-H backend."""

    name = "max_parallel_workers_per_gather"
    expected = TPCH_MAX_PARALLEL_WORKERS_PER_GATHER
    before = _read_tpch_postgres_parallel_setting(worker)
    changed = int(before["setting"]) != expected
    if changed:
        completed = _container_psql(
            worker,
            f"ALTER SYSTEM SET {name} = '{expected}'",
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"TPC-H PostgreSQL ALTER SYSTEM failed for "
                f"{worker['container']} setting={name}: {completed.stderr}"
            )
        completed = _container_psql(worker, "select pg_reload_conf()")
        if completed.returncode != 0 or completed.stdout.strip() != "t":
            raise RuntimeError(
                f"TPC-H PostgreSQL reload failed for {worker['container']}: "
                f"stdout={completed.stdout!r} stderr={completed.stderr!r}"
            )

    after = _read_tpch_postgres_parallel_setting(worker)
    if int(after["setting"]) != expected or bool(after["pending_restart"]):
        raise RuntimeError(
            "TPC-H PostgreSQL parallel contract readback mismatch for "
            f"{worker['container']}: expected={expected} observed={after}"
        )
    return {
        "container": str(worker["container"]),
        "contract": "tpch_postgresql_parallel_workers_v1",
        "before": before,
        "expected": {name: expected},
        "after": after,
        "changed": [name] if changed else [],
        "container_restarted": False,
        "passed": True,
    }


def _read_tpch_shared_buffers_setting(
    worker: Mapping[str, Any],
) -> Dict[str, Any]:
    completed = _container_psql(
        worker,
        (
            "select current_setting('shared_buffers'),context,pending_restart "
            "from pg_settings where name = 'shared_buffers'"
        ),
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "TPC-H shared_buffers readback failed for "
            f"{worker['container']}: {completed.stderr}"
        )
    fields = completed.stdout.strip().split("|")
    if len(fields) != 3:
        raise RuntimeError(
            "TPC-H shared_buffers readback incomplete for "
            f"{worker['container']}: {completed.stdout!r}"
        )
    setting, context, pending_restart = fields
    return {
        "setting": setting,
        "context": context,
        "pending_restart": pending_restart == "t",
    }


def apply_tpch_postgres_memory_contract(
    worker: Mapping[str, Any],
) -> Dict[str, Any]:
    """Apply the fixed cache that makes SF5 a memory rather than IO lane."""
    before = _read_tpch_shared_buffers_setting(worker)
    changed = str(before["setting"]).upper() != TPCH_SHARED_BUFFERS.upper()
    if changed:
        completed = _container_psql(
            worker,
            f"ALTER SYSTEM SET shared_buffers = '{TPCH_SHARED_BUFFERS}'",
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"TPC-H shared_buffers ALTER SYSTEM failed for "
                f"{worker['container']}: {completed.stderr}"
            )
        completed = subprocess.run(
            ["docker", "restart", str(worker["container"])],
            text=True,
            capture_output=True,
            timeout=90,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"TPC-H PostgreSQL restart failed for {worker['container']}: "
                f"{completed.stderr}"
            )
        deadline = time.monotonic() + 75.0
        last_error = ""
        while time.monotonic() < deadline:
            ready = _container_psql(worker, "select 1", timeout_s=8.0)
            if ready.returncode == 0 and ready.stdout.strip() == "1":
                break
            last_error = ready.stderr.strip()
            time.sleep(1.0)
        else:
            raise RuntimeError(
                f"TPC-H PostgreSQL did not become ready after cache restart "
                f"for {worker['container']}: {last_error}"
            )
    after = _read_tpch_shared_buffers_setting(worker)
    if (
        str(after["setting"]).upper() != TPCH_SHARED_BUFFERS.upper()
        or bool(after["pending_restart"])
    ):
        raise RuntimeError(
            "TPC-H shared_buffers contract readback mismatch for "
            f"{worker['container']}: expected={TPCH_SHARED_BUFFERS} "
            f"observed={after}"
        )
    return {
        "container": str(worker["container"]),
        "contract": "tpch_postgresql_memory_cache_v1",
        "before": before,
        "expected": {"shared_buffers": TPCH_SHARED_BUFFERS},
        "after": after,
        "changed": ["shared_buffers"] if changed else [],
        "container_restarted": changed,
        "passed": True,
    }


def audit_running_mysql_isolation() -> Dict[str, Any]:
    """Read MySQL container cpusets and fail before any PostgreSQL mutation."""
    try:
        completed = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Image}}"],
            text=True,
            capture_output=True,
            timeout=DOCKER_LIST_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"docker ps timed out after {DOCKER_LIST_TIMEOUT_S}s during "
            "MySQL isolation audit"
        ) from exc
    if completed.returncode != 0:
        raise RuntimeError(f"cannot audit running MySQL containers: {completed.stderr}")
    planned: Set[int] = set()
    for specs in DB_CPUSETS.values():
        for spec in specs:
            planned.update(expand_cpuset(spec))
    for spec in CONTROLLER_CPUSETS.values():
        planned.update(expand_cpuset(spec))
    readbacks: Dict[str, Any] = {}
    for line in completed.stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        container, image = parts
        if "mysql" not in f"{container} {image}".lower():
            continue
        readback = docker_readback(container)
        cpuset = str(readback.get("cpuset_cpus") or "").strip()
        if not cpuset:
            raise RuntimeError(
                f"running MySQL container has no explicit cpuset; "
                f"cannot prove isolation: {container}"
            )
        overlap = planned.intersection(expand_cpuset(cpuset))
        if overlap:
            raise RuntimeError(
                f"PostgreSQL/MySQL CPU overlap before launch: "
                f"container={container} cpuset={cpuset} overlap={sorted(overlap)}"
            )
        readbacks[container] = readback
    return {
        "planned_postgresql_cpus": sorted(planned),
        "reserved_mysql_client_and_server_cpusets": list(
            EXTERNAL_MYSQL_RESERVED_CPUSETS
        ),
        "running_mysql_containers": readbacks,
        "overlap": [],
        "mysql_untouched": True,
    }


def configured_scenes(run_root: Path) -> Tuple[str, ...]:
    manifest = read_json(run_root / "launch_manifest.json")
    scenes = manifest.get("scenes") or {}
    if not isinstance(scenes, Mapping) or not scenes:
        raise RuntimeError("launch manifest contains no scenes")
    return tuple(str(name) for name in scenes)


def verify_source_provenance(run_root: Path) -> Dict[str, Any]:
    """Fail before any DB/container action if prepared source bytes changed."""

    manifest = read_json(run_root / "launch_manifest.json")
    provenance = manifest.get("source_provenance") or {}
    declared = provenance.get("files") if isinstance(provenance, Mapping) else {}
    if not isinstance(declared, Mapping):
        declared = {}
    expected_names = {str(path.relative_to(ROOT)) for path in SOURCE_PROVENANCE_PATHS}
    declared_names = {str(name) for name in declared}
    missing_declarations = sorted(expected_names - declared_names)
    extra_declarations = sorted(declared_names - expected_names)
    observed: Dict[str, str] = {}
    missing_files: List[str] = []
    mismatches: Dict[str, Dict[str, str]] = {}
    for relative_name in sorted(expected_names):
        path = ROOT / relative_name
        if not path.is_file():
            missing_files.append(relative_name)
            continue
        observed_sha = sha256(path)
        observed[relative_name] = observed_sha
        expected_sha = str(declared.get(relative_name) or "")
        if observed_sha != expected_sha:
            mismatches[relative_name] = {
                "expected": expected_sha,
                "observed": observed_sha,
            }
    passed = not (
        missing_declarations or extra_declarations or missing_files or mismatches
    )
    readback = {
        "contract": "algorithm_c_launch_source_sha256_readback_v2",
        "checked_at_epoch_s": time.time(),
        "passed": passed,
        "expected_file_count": len(expected_names),
        "missing_declarations": missing_declarations,
        "extra_declarations": extra_declarations,
        "missing_files": missing_files,
        "mismatches": mismatches,
        "observed": observed,
        "physical_workload_invocations": 0,
        "database_mutation": False,
    }
    write_json(run_root / "source_provenance_readback.json", readback)
    if not passed:
        raise RuntimeError(f"Algorithm C source provenance mismatch: {readback}")
    return readback


def apply_resource_contract(
    run_root: Path,
    scene_names: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    readbacks: Dict[str, Any] = {}
    for scene_name in tuple(scene_names or configured_scenes(run_root)):
        cfg = read_json(run_root / scene_name / "optimizer_config.json")
        workers = (((cfg["binary_plan_tuning"]["validation"])["docker_workers"])["workers"])
        for index, worker in enumerate(workers):
            cpuset = DB_CPUSETS[scene_name][index]
            cpu_count = len(expand_cpuset(cpuset))
            # The Algorithm C capacity and non-target bottleneck gate use the
            # full isolated cpuset.  Keeping a stale worker quota (8 cores for
            # JOB/XP-IO) while declaring 12/30 cores makes CPU saturate before
            # the gate can observe it.  Make the CFS quota match the audited
            # cpuset for every lane; CPU-target XP remains four cores because
            # its cpuset is exactly four cores.
            cpu_quota = float(cpu_count)
            command = [
                "docker",
                "update",
                "--cpuset-cpus",
                cpuset,
                "--cpus",
                str(cpu_quota),
            ]
            command.extend(
                [
                    "--memory",
                    str(
                        worker.get("memory")
                        or f"{RESOURCE_CAPACITY[scene_name]['memory_gib']:g}g"
                    ),
                ]
            )
            command.append(str(worker["container"]))
            expected_nano_cpus = int(round(cpu_quota * 1_000_000_000))
            expected_memory = int(
                RESOURCE_CAPACITY[scene_name]["memory_gib"] * (1024 ** 3)
            )
            readback = docker_readback(str(worker["container"]))
            update_required = (
                readback["cpuset_cpus"] != cpuset
                or int(readback["nano_cpus"] or 0) != expected_nano_cpus
                or int(readback["memory"] or 0) != expected_memory
            )
            if update_required:
                try:
                    completed = subprocess.run(
                        command,
                        text=True,
                        capture_output=True,
                        timeout=DOCKER_UPDATE_TIMEOUT_S,
                        check=False,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError(
                        f"docker resource update timed out after "
                        f"{DOCKER_UPDATE_TIMEOUT_S}s: {command}"
                    ) from exc
                if completed.returncode != 0:
                    raise RuntimeError(
                        f"docker resource update failed: {command}: "
                        f"{completed.stderr}"
                    )
                readback = docker_readback(str(worker["container"]))
            readback["resource_update_skipped_already_compliant"] = (
                not update_required
            )
            if readback["cpuset_cpus"] != cpuset:
                raise RuntimeError(f"Docker cpuset readback mismatch: {readback}")
            if int(readback["nano_cpus"] or 0) != expected_nano_cpus:
                raise RuntimeError(f"Docker CPU quota readback mismatch: {readback}")
            if int(readback["memory"] or 0) != expected_memory:
                raise RuntimeError(f"Docker memory readback mismatch: {readback}")
            readbacks[f"{scene_name}.w{index + 1}"] = readback
            if scene_name == "job_memory":
                readbacks[f"{scene_name}.w{index + 1}.postgres_parallel"] = (
                    apply_job_postgres_parallel_contract(worker)
                )
                readbacks[f"{scene_name}.w{index + 1}.postgres_memory"] = (
                    apply_job_postgres_memory_contract(worker)
                )
            elif scene_name == "tpch_memory":
                readbacks[f"{scene_name}.w{index + 1}.postgres_parallel"] = (
                    apply_tpch_postgres_parallel_contract(worker)
                )
                readbacks[f"{scene_name}.w{index + 1}.postgres_memory"] = (
                    apply_tpch_postgres_memory_contract(worker)
                )
    readback_path = run_root / "docker_resource_readback.json"
    merged_readbacks = read_json(readback_path) if readback_path.is_file() else {}
    merged_readbacks.update(readbacks)
    write_json(readback_path, merged_readbacks)
    return readbacks


def preflight(
    run_root: Path,
    scene_names: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    source_readback = verify_source_provenance(run_root)
    if not SCENE_CONTROLLER.is_file():
        raise FileNotFoundError(SCENE_CONTROLLER)
    active = subprocess.run(
        ["ps", "-eo", "pid=,cmd="],
        text=True,
        capture_output=True,
        check=False,
    )
    selected = tuple(scene_names or configured_scenes(run_root))
    controller_entry_markers = (
        "run_pg_algorithm_c_calibrate_then_scene.py",
        "pg_algorithm_c_lane_entry.py",
    )
    conflicting = [
        line.strip()
        for line in active.stdout.splitlines()
        if any(marker in line for marker in controller_entry_markers)
        and str(run_root) not in line
        and "ps -eo" not in line
        and any(f"/{scene_name}/" in line for scene_name in selected)
    ]
    if conflicting:
        raise RuntimeError(f"another PostgreSQL Algorithm C controller is active: {conflicting}")
    checks: Dict[str, Any] = {
        "process_conflicts": [],
        "ports": {},
        "full_plan_coverage": {},
        "source_provenance": source_readback,
        "external_mysql_isolation": audit_running_mysql_isolation(),
    }
    for scene_name in selected:
        cfg = read_json(run_root / scene_name / "optimizer_config.json")
        workers = cfg["binary_plan_tuning"]["validation"]["docker_workers"]["workers"]
        for worker in workers:
            env = os.environ.copy()
            env["PGPASSWORD"] = str(worker.get("password") or "postgres")
            target_db = str(worker["dbname"])
            validation_cfg = ((cfg.get("binary_plan_tuning") or {}).get("validation") or {})
            hybench_cfg = validation_cfg.get("hybench_xp") or {}
            template_db = str(hybench_cfg.get("template_db") or f"{target_db}_base")
            command = [
                "psql",
                "-h",
                str(worker["host"]),
                "-p",
                str(worker["port"]),
                "-U",
                str(worker.get("user") or "postgres"),
                "-d",
                "postgres",
                "-Atc",
                (
                    "select "
                    f"(select count(*) from pg_database where datname='{template_db}'),"
                    f"(select count(*) from pg_database where datname='{target_db}')"
                ),
            ]
            completed = subprocess.run(
                command,
                env=env,
                text=True,
                capture_output=True,
                timeout=12,
                check=False,
            )
            database_counts = completed.stdout.strip().split("|")
            if (
                completed.returncode != 0
                or len(database_counts) != 2
                or database_counts[0] != "1"
            ):
                raise RuntimeError(
                    f"PostgreSQL preflight failed {scene_name}/{worker_key(worker)}: "
                    f"stdout={completed.stdout!r} stderr={completed.stderr!r}"
                )
            checks["ports"][f"{scene_name}.{worker_key(worker)}"] = {
                "host": worker["host"],
                "port": worker["port"],
                "dbname": target_db,
                "template_db": template_db,
                "template_exists": database_counts[:1] == ["1"],
                "target_exists_before_clean_restore": (
                    len(database_counts) == 2 and database_counts[1] == "1"
                ),
                "reachable": True,
            }

        # This is a read-only EXPLAIN preflight.  It verifies that the plan
        # evidence used by Algorithm C is the complete authoritative workload,
        # not a sampled or frozen substitute, before any physical replay starts.
        targets_exist = all(
            checks["ports"][f"{scene_name}.{worker_key(worker)}"].get(
                "target_exists_before_clean_restore"
            )
            is True
            for worker in workers
        )
        if not targets_exist:
            checks["full_plan_coverage"][scene_name] = {
                "pending_after_startup_clean_restore": True,
                "reason": "target_database_missing_before_clean_restore",
                "expected_query_count": EXPECTED_PLAN_QUERY_COUNT[scene_name],
            }
            continue
        probe = PlanFeatureProbe(cfg)
        queries, sampling = workload_queries_with_sampling(cfg, probe)
        expected_count = EXPECTED_PLAN_QUERY_COUNT[scene_name]
        if sampling.get("sampling_applied") or len(queries) != expected_count:
            raise RuntimeError(
                f"{scene_name}: incomplete plan workload before launch: "
                f"count={len(queries)} expected={expected_count} sampling={sampling}"
            )
        initial_params = (
            (cfg.get("algorithm_c") or {}).get("initial_params")
            or cfg.get("initial_params")
            or INITIAL_PARAMS
        )
        result = probe.probe(queries, copy.deepcopy(initial_params))
        coverage = (
            (result.get("exact_plan_metadata") or {}).get(
                "plan_fingerprint_coverage"
            )
            or {}
        )
        if not coverage.get("complete"):
            raise RuntimeError(
                f"{scene_name}: exact full-plan coverage failed before launch: "
                f"coverage={coverage} errors={result.get('errors')}"
            )
        checks["full_plan_coverage"][scene_name] = {
            "expected_query_count": expected_count,
            "query_count": len(queries),
            "explained_query_count": result.get("explained_query_count"),
            "sampling_applied": bool(sampling.get("sampling_applied")),
            "coverage": coverage,
        }
    preflight_path = run_root / "preflight.json"
    if preflight_path.is_file():
        previous = read_json(preflight_path)
        previous_ports = previous.get("ports") or {}
        previous_ports.update(checks["ports"])
        checks["ports"] = previous_ports
        previous_coverage = previous.get("full_plan_coverage") or {}
        previous_coverage.update(checks["full_plan_coverage"])
        checks["full_plan_coverage"] = previous_coverage
    write_json(preflight_path, checks)
    return checks


def worker_key(worker: Mapping[str, Any]) -> str:
    return str(worker.get("name") or worker.get("container") or "")


def launch(
    run_root: Path,
    scene_names: Optional[Sequence[str]] = None,
) -> Dict[str, int]:
    selected = tuple(scene_names or configured_scenes(run_root))
    preflight(run_root, selected)
    apply_resource_contract(run_root, selected)
    taskset = shutil.which("taskset")
    if not taskset:
        raise FileNotFoundError("taskset")
    pids: Dict[str, int] = {}
    for scene_name in selected:
        cfg = read_json(run_root / scene_name / "optimizer_config.json")
        target = str(cfg["algorithm_c"]["workload_contract"]["target_resource"])
        command = [
            taskset,
            "-c",
            CONTROLLER_CPUSETS[scene_name],
            str(PYTHON),
            "-B",
            "-u",
            str(SCENE_CONTROLLER),
            "--config",
            str(run_root / scene_name / "optimizer_config.json"),
            "--output-dir",
            str(run_root / scene_name),
            "--target",
            target,
        ]
        log_path = run_root / scene_name / "controller.log"
        handle = log_path.open("ab", buffering=0)
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=workload_env(scene_name, run_root),
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        (run_root / scene_name / "controller.pid").write_text(
            f"{process.pid}\n", encoding="utf-8"
        )
        pids[scene_name] = process.pid
    time.sleep(3.0)
    startup: Dict[str, Any] = {}
    for scene_name, pid in pids.items():
        try:
            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        expected_affinity = expand_cpuset(CONTROLLER_CPUSETS[scene_name])
        observed_affinity = set(os.sched_getaffinity(pid)) if alive else set()
        affinity_matches = observed_affinity == expected_affinity
        startup[scene_name] = {
            "pid": pid,
            "alive_after_3s": alive,
            "controller_cpuset": CONTROLLER_CPUSETS[scene_name],
            "controller_cpuset_readback": sorted(observed_affinity),
            "controller_cpuset_readback_matches": affinity_matches,
            "log": str(run_root / scene_name / "controller.log"),
        }
        if not alive:
            tail = (run_root / scene_name / "controller.log").read_text(
                encoding="utf-8", errors="replace"
            )[-8000:]
            raise RuntimeError(f"{scene_name} controller exited at startup:\n{tail}")
        if not affinity_matches:
            os.kill(pid, signal.SIGTERM)
            raise RuntimeError(
                f"{scene_name} controller affinity mismatch: "
                f"expected={sorted(expected_affinity)} "
                f"observed={sorted(observed_affinity)}"
            )
    controllers_path = run_root / "controllers.json"
    merged_pids = read_json(controllers_path) if controllers_path.is_file() else {}
    merged_pids.update(pids)
    startup_path = run_root / "startup_readback.json"
    merged_startup = read_json(startup_path) if startup_path.is_file() else {}
    merged_startup.update(startup)
    write_json(controllers_path, merged_pids)
    write_json(startup_path, merged_startup)
    manifest_path = run_root / "launch_manifest.json"
    manifest = read_json(manifest_path)
    manifest["controllers"] = merged_pids
    manifest["startup_readback"] = merged_startup
    manifest["launched_at_epoch_s"] = time.time()
    write_json(manifest_path, manifest)
    return pids


def run_foreground(run_root: Path, scene_name: str) -> None:
    """Run one lane as the foreground process owned by tmux/systemd.

    The regular ``launch`` command intentionally returns after spawning a
    controller.  Some execution environments reap all descendants when that
    short-lived launcher exits, even when ``start_new_session`` is true.  A
    supervisor must therefore own the controller itself, not only the
    launcher.  This entry point performs the same preflight/resource setup and
    then replaces the launcher process with the lane controller.
    """
    selected = (scene_name,)
    preflight(run_root, selected)
    apply_resource_contract(run_root, selected)
    taskset = shutil.which("taskset")
    if not taskset:
        raise FileNotFoundError("taskset")
    cfg = read_json(run_root / scene_name / "optimizer_config.json")
    target = str(cfg["algorithm_c"]["workload_contract"]["target_resource"])
    command = [
        taskset,
        "-c",
        CONTROLLER_CPUSETS[scene_name],
        str(PYTHON),
        "-B",
        "-u",
        str(SCENE_CONTROLLER),
        "--config",
        str(run_root / scene_name / "optimizer_config.json"),
        "--output-dir",
        str(run_root / scene_name),
        "--target",
        target,
    ]
    pid = os.getpid()
    (run_root / scene_name / "controller.pid").write_text(
        f"{pid}\n", encoding="utf-8"
    )
    controllers_path = run_root / "controllers.json"
    controllers = read_json(controllers_path) if controllers_path.is_file() else {}
    controllers[scene_name] = pid
    write_json(controllers_path, controllers)
    log_path = run_root / scene_name / "controller.log"
    with log_path.open("ab", buffering=0) as handle:
        os.dup2(handle.fileno(), 1)
        os.dup2(handle.fileno(), 2)
        os.execvpe(command[0], command, workload_env(scene_name, run_root))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "launch", "run"))
    parser.add_argument("--tag", default=time.strftime("%Y%m%d_%H%M%S"))
    parser.add_argument(
        "--scene",
        choices=("xp_cpu", "xp_io", "job_memory", "tpch_memory"),
        help="Prepare or launch only one isolated scene.",
    )
    args = parser.parse_args()
    run_root = RUN_BASE / args.tag
    selected = (args.scene,) if args.scene else None
    if args.command == "prepare":
        run_root = prepare(args.tag, selected)
        print(run_root, flush=True)
        return 0
    if not run_root.is_dir():
        raise FileNotFoundError(f"prepare first: {run_root}")
    if args.command == "run":
        if not args.scene:
            parser.error("run requires --scene")
        run_foreground(run_root, args.scene)
        raise AssertionError("foreground controller unexpectedly returned")
    try:
        pids = launch(run_root, selected)
    except Exception as exc:
        write_json(
            run_root / "launch_failure.json",
            {
                "event": "launch_preflight_or_startup_failure",
                "technical_failure": True,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "physical_workload_invocations": 0,
                "accepted_state_mutated": False,
                "container_resource_mutation_may_have_occurred": (
                    (run_root / "docker_resource_readback.json").is_file()
                ),
                "recorded_at_epoch_s": time.time(),
            },
        )
        raise
    print(json.dumps({"run_root": str(run_root), "pids": pids}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
