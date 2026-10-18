#!/usr/bin/env python3
"""Run four-scene Algorithm C with audited 1%-interval convergence."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common_outer_loop import (
    advance_acceptance_tier,
    advance_outer_shrink_progress,
    decide_validation_acceptance,
)
from gradient_descent_optimizer import ParameterRange
from mysql_algorithm_c_resource_model import (
    confidence_adjusted_score,
    enhance_resource_prediction,
)
from mysql_benchmark_command import (
    HEAVY_QUERY_THRESHOLD_S,
    _HYBENCH_AP_EXPLAIN,
    _TPCH_EXPLAINS,
    _job_estimates_from_manifest,
    discover_job_query_files,
    run_job_tpch_parallel,
)
from mysql_workload_adapter import (
    MySQLEndpoint,
    _mysqlize_job_sql,
    _validate_job_sql,
    build_job_manifest,
    run_benchbase,
    run_hybench,
    run_job,
)
from plan_resource_estimator import CacheProfile, MySQLRelationStatsCollector
from scripts import provision_mysql_four_scene_benchmark_users_v5 as users
from scripts import reset_mysql_xp_data_planes_v5 as reset
from scripts import run_mysql_four_scene_r0_v5 as r0mod
from scripts import mysql_four_scene_adaptive_measurement_v1 as adaptive_measurement


FREEZE = r0mod.FREEZE
R0 = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_native_iq1_iq7_r0_v26_20260811T144000p0800/r0_binding.json"
)
POST_R0_BENCHMARK_ACCOUNTS = (
    ROOT / "history/mysql_four_scene_20260809"
    / "benchmark_accounts_v28_20260811T152200p0800"
    / "benchmark_accounts_binding.json"
)
SCENES = ("job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io")
TARGET = {
    "job_memory": "memory", "tpch_sf4_memory": "memory",
    "hybench_xp_cpu": "cpu", "hybench_xp_io": "io",
}
TABLES = {
    "job_memory": "T.job_memory.operator_physics_v1.json",
    "tpch_sf4_memory": "T.tpch_memory.operator_physics_v1.json",
    "hybench_xp_cpu": "T.hybench_xp_io.operator_physics_v1.json",
    "hybench_xp_io": "T.hybench_xp_io.operator_physics_v1.json",
}
TABLE_ROOT = ROOT / "config/formal/frozen_mysql_operator_physics_experience_tables_20260724_v1"
RESOURCE_MODEL_SOURCE = ROOT / "mysql_algorithm_c_resource_model.py"
RESOURCE_MODEL_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_contextual_plan_physics_20260809_v2"
    / "model_contract.json"
)
DEFAULTS = {
    "io_block_read_cost": 1.0,
    "memory_block_read_cost": 0.25,
    "row_evaluate_cost": 0.1,
    "key_compare_cost": 0.05,
    "disk_temptable_create_cost": 20.0,
    "disk_temptable_row_cost": 0.5,
    "memory_temptable_create_cost": 1.0,
    "memory_temptable_row_cost": 0.1,
}
# mysql.server_cost.cost_value and mysql.engine_cost.cost_value are FLOAT
# (binary32), and the mysql client renders these columns with only about six
# significant decimal digits.  A value written from Python therefore cannot
# be required to round-trip as the identical binary64 value.  Six significant
# digits can introduce up to 5e-6 relative error at a decimal rounding
# boundary; leave a small margin for the preceding binary32 conversion while
# remaining far below a material optimizer-cost change.
MYSQL_FLOAT_READBACK_REL_TOL = 6e-6
MYSQL_FLOAT_READBACK_ABS_TOL = 1e-8
XP_IDS = ("xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2")
XP_RESET_MAX_WORKERS = 4
WORKER_IDS = (
    "job_w1", "job_w2", "tpch_w1", "tpch_w2",
    "xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2",
)
JOB_QUERY_ROOT = Path("/home/anonymous/dataset/pg_data/join-order-benchmark")
SCENE_CONCURRENCY = {
    "job_memory": 16,
    "tpch_sf4_memory": 5,
    "hybench_xp_cpu": 8,
    "hybench_xp_io": 8,
}
PLAN_CACHE_PROFILE = CacheProfile(0.85, 0.30)
HYBENCH_ADAPTIVE_RUNNER_JAR = (
    ROOT / "config/formal/frozen_mysql_pg_outer_20260819_v35/runtime"
    / "hybench-adaptive-runner-v3.jar"
)
CONVERGENCE_MAX_INTERVAL_RATIO = 1.01
CONVERGENCE_REQUIRED_STABLE_ROUNDS = 2
# Candidate vectors are persisted with about twelve significant decimal
# digits.  A value generated exactly at a saved interval boundary can therefore
# differ from that boundary by roughly 1e-12 after JSON round-tripping.  This
# tolerance is only for the boundary-membership comparison; it does not relax
# the <=1% interval-width rule.
CONVERGENCE_BOUNDARY_REL_TOL = 2e-12
CONVERGENCE_BOUNDARY_ABS_TOL = 2e-12
MAXIMUM_OUTER_ATTEMPTS = 10
FORMAL_CANDIDATE_BATCHES = ((1, 2), (3, 4), (5,))
_RELATION_STATS_CACHE: dict[str, dict[str, dict[str, float]]] = {}
_EXECUTOR_PARAMETERS_CACHE: dict[str, dict[str, float]] = {}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(f"{sha(path)}  {path.name}\n", encoding="ascii")


def private_credential(worker: Mapping[str, Any]) -> tuple[str, str]:
    return r0mod._private_credential(worker)


def mysql_root(
    worker: Mapping[str, Any], sql: str, *, timeout_seconds: int = 60,
) -> str:
    defaults = Path(str(worker["credential_defaults"]))
    completed = subprocess.run(
        ["mysql", f"--defaults-file={defaults}", "--host=127.0.0.1",
         f"--port={worker['host_port']}", "--connect-timeout=10", "-NBe", sql],
        text=True, capture_output=True, check=True, timeout=timeout_seconds,
    )
    return completed.stdout.strip()


def optimizer_cost_readback_matches(requested: float, observed: float) -> bool:
    expected = float(requested)
    actual = float(observed)
    return (
        math.isfinite(expected)
        and math.isfinite(actual)
        and math.isclose(
            actual,
            expected,
            rel_tol=MYSQL_FLOAT_READBACK_REL_TOL,
            abs_tol=MYSQL_FLOAT_READBACK_ABS_TOL,
        )
    )


def apply_costs(worker: Mapping[str, Any], params: Mapping[str, float]) -> dict[str, float]:
    complete = {**DEFAULTS, **{name: float(value) for name, value in params.items()}}
    server = [name for name in complete if name not in {"io_block_read_cost", "memory_block_read_cost"}]
    server_case = " ".join(f"WHEN '{name}' THEN {complete[name]:.17g}" for name in server)
    engine_case = " ".join(
        f"WHEN '{name}' THEN {complete[name]:.17g}"
        for name in ("io_block_read_cost", "memory_block_read_cost")
    )
    mysql_root(worker, (
        "START TRANSACTION; "
        f"UPDATE mysql.server_cost SET cost_value=CASE cost_name {server_case} ELSE cost_value END "
        f"WHERE cost_name IN ({','.join(repr(name) for name in server)}); "
        f"UPDATE mysql.engine_cost SET cost_value=CASE cost_name {engine_case} ELSE cost_value END "
        "WHERE engine_name='default' AND device_type=0 AND cost_name IN "
        "('io_block_read_cost','memory_block_read_cost'); COMMIT; FLUSH OPTIMIZER_COSTS;"
    ))
    rows = mysql_root(worker, (
        "SELECT cost_name,cost_value FROM mysql.server_cost WHERE cost_name IN "
        f"({','.join(repr(name) for name in server)}) UNION ALL "
        "SELECT cost_name,cost_value FROM mysql.engine_cost WHERE engine_name='default' "
        "AND device_type=0 AND cost_name IN ('io_block_read_cost','memory_block_read_cost')"
    )).splitlines()
    readback = {name: float(value) for name, value in (row.split("\t") for row in rows)}
    mismatches = []
    if set(readback) == set(DEFAULTS):
        for name in DEFAULTS:
            requested = float(complete[name])
            observed = float(readback[name])
            if not optimizer_cost_readback_matches(requested, observed):
                mismatches.append({
                    "parameter": name,
                    "requested": requested,
                    "observed": observed,
                    "absolute_difference": abs(observed - requested),
                    "relative_difference": (
                        abs(observed - requested) / abs(requested)
                        if requested != 0.0 else None
                    ),
                })
    else:
        mismatches.append({
            "missing": sorted(set(DEFAULTS) - set(readback)),
            "unexpected": sorted(set(readback) - set(DEFAULTS)),
        })
    if mismatches:
        raise RuntimeError(
            f"{worker['worker_id']} optimizer cost readback differs: "
            f"{json.dumps(mismatches, sort_keys=True)}"
        )
    return readback


def db_counter(worker: Mapping[str, Any]) -> int:
    value = mysql_root(worker, (
        "SELECT COALESCE(SUM(COUNT_STAR),0) FROM "
        "performance_schema.events_statements_summary_by_account_by_event_name "
        "WHERE USER='ra_benchmark' AND EVENT_NAME LIKE 'statement/sql/%' "
        "AND EVENT_NAME NOT IN ('statement/sql/set_option','statement/sql/show_status',"
        "'statement/sql/show_variables','statement/sql/show_processlist')"
    ))
    return int(value)


def explain(worker: Mapping[str, Any], sqls: list[str]) -> list[str]:
    private_credential(worker)
    defaults = Path(str(worker["benchmark_defaults"]))
    runner = r0mod.PinnedRunner(worker, Path(worker["formal_client_log"]))
    # --defaults-extra-file is followed by the caller's ~/.my.cnf and can be
    # silently overridden back to root.  --defaults-file makes the sealed
    # benchmark account the only option-file authority for plan capture.
    base = ["mysql", f"--defaults-file={defaults}",
            "--batch", "--raw", "--skip-column-names", "--get-server-public-key",
            "--ssl-mode=DISABLED", "-h", "127.0.0.1", "-P", str(worker["host_port"]),
            str(worker["database"])]
    plans: list[str] = []
    for index, sql in enumerate(sqls, 1):
        completed = runner(
            base, input=sql, env=dict(os.environ), text=True,
            capture_output=True, timeout=180, check=False,
        )
        if completed.returncode or not completed.stdout.strip():
            error = (completed.stderr or "empty EXPLAIN output").strip()[-1000:]
            raise RuntimeError(
                f"{worker['worker_id']} EXPLAIN JSON #{index} failed: "
                f"rc={completed.returncode} stderr={error!r}"
            )
        plans.append(completed.stdout.strip())
    return plans


def job_representative_sqls(freeze: Mapping[str, Any]) -> list[str]:
    contract = freeze.get("plan_capture_contract") or {}
    names = list(contract.get("job_representative_queries") or [])
    if (
        contract.get("outside_formal_measurement_wall_time") is not True
        or contract.get("job_full_workload_query_count") != 113
        or len(names) != 8
        or len(set(names)) != len(names)
    ):
        raise RuntimeError("frozen JOB plan-capture contract differs")
    sqls: list[str] = []
    for name in names:
        path = JOB_QUERY_ROOT / str(name)
        if not path.is_file():
            raise RuntimeError(f"frozen JOB plan representative is absent: {path}")
        sql = _mysqlize_job_sql(_validate_job_sql(path.read_text(encoding="utf-8"), path))
        sqls.append("EXPLAIN FORMAT=JSON " + sql)
    return sqls


def explain_job_representatives(
    freeze: Mapping[str, Any], worker: Mapping[str, Any]
) -> list[str]:
    return explain(worker, job_representative_sqls(freeze))


def capture_scene_plans(
    freeze: Mapping[str, Any], scene: str, worker: Mapping[str, Any]
) -> dict[str, str]:
    if scene == "job_memory":
        sqls = job_representative_sqls(freeze)
        prefix = "job"
    elif scene == "tpch_sf4_memory":
        sqls = [_TPCH_EXPLAINS[6], _TPCH_EXPLAINS[11]]
        prefix = "tpch"
    elif scene in {"hybench_xp_cpu", "hybench_xp_io"}:
        sqls = [_HYBENCH_AP_EXPLAIN]
        prefix = "hybench_xp"
    else:
        raise RuntimeError(f"unsupported plan-physics scene: {scene}")
    plans = explain(worker, sqls)
    return {f"{prefix}_{index:03d}": plan for index, plan in enumerate(plans, 1)}


class _WorkerRootSQLClient:
    def __init__(self, worker: Mapping[str, Any]) -> None:
        self.worker = worker

    def execute(self, sql: str) -> str:
        return mysql_root(self.worker, sql)


def relation_stats(worker: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    worker_id = str(worker["worker_id"])
    if worker_id not in _RELATION_STATS_CACHE:
        _RELATION_STATS_CACHE[worker_id] = MySQLRelationStatsCollector(
            _WorkerRootSQLClient(worker)
        ).collect()
    return copy.deepcopy(_RELATION_STATS_CACHE[worker_id])


def executor_parameters(worker: Mapping[str, Any]) -> dict[str, float]:
    worker_id = str(worker["worker_id"])
    if worker_id not in _EXECUTOR_PARAMETERS_CACHE:
        fields = mysql_root(worker, (
            "SELECT @@GLOBAL.join_buffer_size,@@GLOBAL.sort_buffer_size,"
            "@@GLOBAL.tmp_table_size,@@GLOBAL.max_heap_table_size"
        )).split("\t")
        if len(fields) != 4:
            raise RuntimeError(f"{worker_id} executor-parameter readback differs")
        _EXECUTOR_PARAMETERS_CACHE[worker_id] = {
            name: float(value)
            for name, value in zip(
                ("join_buffer_size", "sort_buffer_size", "tmp_table_size",
                 "max_heap_table_size"),
                fields, strict=True,
            )
        }
    return dict(_EXECUTOR_PARAMETERS_CACHE[worker_id])


def candidate_rankings(
    scene: str, incumbent: Mapping[str, float], ranges: Mapping[str, Any],
    resources: Mapping[str, float], *, freeze: Mapping[str, Any],
    worker: Mapping[str, Any], step_fraction: float = 0.08,
    direction_mode: str = "gradient",
) -> list[dict[str, Any]]:
    if not math.isfinite(step_fraction) or not 0.0 < step_fraction <= 1.0:
        raise ValueError("step_fraction must be finite and in (0, 1]")
    if direction_mode not in {"gradient", "inward_if_boundary_collapsed"}:
        raise ValueError("unsupported candidate direction_mode")
    document = load(TABLE_ROOT / TABLES[scene])
    active = [str(name) for name in document["parameter_space"]["active_params"]]
    table = document["table"]
    target = TARGET[scene]
    rankings: list[dict[str, Any]] = []
    for size in range(1, len(active) + 1):
        for subset in itertools.combinations(active, size):
            params = dict(incumbent)
            predicted = {name: float(resources[name]) for name in ("cpu", "memory", "io")}
            events = []
            for name in subset:
                low = float(ranges[name]["lower"])
                high = float(ranges[name]["upper"])
                before = float(params[name])
                effect = float(table[name][target])
                direction = -1.0 if effect > 0 else 1.0
                after = min(high, max(low, math.exp(
                    math.log(before)
                    + direction * step_fraction * (math.log(high) - math.log(low))
                )))
                boundary_fallback_applied = False
                if (
                    direction_mode == "inward_if_boundary_collapsed"
                    and float(f"{after:.12g}") == before
                ):
                    direction *= -1.0
                    after = min(high, max(low, math.exp(
                        math.log(before)
                        + direction * step_fraction
                        * (math.log(high) - math.log(low))
                    )))
                    boundary_fallback_applied = True
                params[name] = float(f"{after:.12g}")
                log_delta = math.log(params[name] / before)
                for resource in predicted:
                    predicted[resource] = max(0.000001, predicted[resource] + float(table[name][resource]) * log_delta)
                events.append({
                    "parameter": name, "before": before, "after": params[name],
                    "gradient_effect": effect,
                    "direction_mode": direction_mode,
                    "boundary_fallback_applied": boundary_fallback_applied,
                })
            rankings.append({
                "subset_id": "+".join(subset), "active_subset": list(subset),
                "candidate_step_fraction": step_fraction,
                "candidate_direction_mode": direction_mode,
                "optimized_params": params,
                "experience_prediction": predicted,
                "gradient_events": events,
            })
    raw_nonempty_subset_count = len(rankings)
    rows_by_parameter_point: dict[str, list[dict[str, Any]]] = {}
    for row in rankings:
        key = json.dumps(
            {name: float(value) for name, value in sorted(
                row["optimized_params"].items()
            )},
            sort_keys=True, separators=(",", ":"),
        )
        rows_by_parameter_point.setdefault(key, []).append(row)
    apply_costs(worker, incumbent)
    baseline_plans = capture_scene_plans(freeze, scene, worker)
    stats = relation_stats(worker)
    executor = executor_parameters(worker)
    try:
        for point_rows in rows_by_parameter_point.values():
            effective_costs = apply_costs(
                worker, point_rows[0]["optimized_params"],
            )
            candidate_plans = capture_scene_plans(freeze, scene, worker)
            for row in point_rows:
                row["effective_optimizer_cost_readback"] = dict(effective_costs)
                row["optimizer_cost_readback_tolerance"] = {
                    "mysql_column_type": "FLOAT",
                    "relative": MYSQL_FLOAT_READBACK_REL_TOL,
                    "absolute": MYSQL_FLOAT_READBACK_ABS_TOL,
                }
                enhanced = enhance_resource_prediction(
                    incumbent_resources=resources,
                    experience_prediction=row["experience_prediction"],
                    baseline_plans=baseline_plans,
                    candidate_plans=candidate_plans,
                    relation_stats=stats,
                    executor_parameters=executor,
                    concurrency=SCENE_CONCURRENCY[scene],
                    cache_profile=PLAN_CACHE_PROFILE,
                )
                predicted = enhanced["predicted_resources"]
                non_targets = [
                    name for name in ("cpu", "memory", "io") if name != target
                ]
                raw_score = (
                    resources[target] - predicted[target]
                    + 0.25 * sum(
                        predicted[name] - resources[name] for name in non_targets
                    )
                )
                row["predicted_resources"] = predicted
                row["candidate_score_before_confidence"] = raw_score
                row["candidate_score"] = confidence_adjusted_score(
                    raw_score, float(enhanced["confidence"])
                )
                row["resource_model"] = enhanced
    finally:
        apply_costs(worker, incumbent)
    rankings.sort(key=lambda row: (
        -float(row["candidate_score"]),
        float(row["candidate_step_fraction"]),
        row["candidate_direction_mode"],
        row["subset_id"],
    ))
    unique_rankings: list[dict[str, Any]] = []
    selected_parameter_points: set[str] = set()
    for row in rankings:
        key = json.dumps(
            {name: float(value) for name, value in sorted(
                row["optimized_params"].items()
            )},
            sort_keys=True, separators=(",", ":"),
        )
        if key in selected_parameter_points:
            continue
        selected_parameter_points.add(key)
        equivalents = rows_by_parameter_point[key]
        row["raw_nonempty_subset_count"] = raw_nonempty_subset_count
        row["unique_parameter_point_count"] = len(rows_by_parameter_point)
        row["equivalent_subset_ids"] = [
            item["subset_id"] for item in equivalents
        ]
        row["equivalent_active_subsets"] = [
            item["active_subset"] for item in equivalents
        ]
        row["equivalent_subset_candidate_scores"] = [
            {
                "subset_id": item["subset_id"],
                "candidate_score": item["candidate_score"],
            }
            for item in equivalents
        ]
        row["equivalent_subset_count"] = len(equivalents)
        row["duplicate_parameter_points_explained_once"] = True
        row["equivalent_subset_representative_policy"] = (
            "highest_original_candidate_score_then_subset_id"
        )
        unique_rankings.append(row)
    rankings = unique_rankings
    for rank, row in enumerate(rankings, 1):
        row["rank"] = rank
    return rankings


def merge_candidate_rankings(
    pools: Sequence[Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    """Merge deterministic step-fraction pools without changing their scores."""
    merged = [copy.deepcopy(dict(row)) for pool in pools for row in pool]
    merged.sort(key=lambda row: (
        -float(row["candidate_score"]),
        float(row.get("candidate_step_fraction", 0.08)),
        str(row.get("candidate_direction_mode", "gradient")),
        str(row["subset_id"]),
    ))
    for rank, row in enumerate(merged, 1):
        row["rank"] = rank
    return merged


def optimizer_params_key(params: Mapping[str, Any]) -> str:
    """Return a stable identity for one fully specified optimizer point."""
    return json.dumps(
        {name: float(params[name]) for name in sorted(params)},
        sort_keys=True,
        separators=(",", ":"),
    )


def select_unreplayed_candidates(
    rankings: list[dict[str, Any]], replayed_param_keys: set[str], *,
    top_k: int = 5, permanently_invalid_param_keys: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select the best points not replayed for the current incumbent epoch.

    A rejected outer round leaves the incumbent and its ranges unchanged, so a
    deterministic ranking otherwise returns the same top-k forever.  Exhaust
    the ranked candidate pool before allowing a new replay cycle.  This changes
    candidate exploration only: every selected point still passes the same
    hard/latency/acceptance gates and requires a real workload replay.
    """
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    permanently_invalid = set(permanently_invalid_param_keys or set())
    unique_rankings: list[dict[str, Any]] = []
    ranked_param_keys: set[str] = set()
    for row in rankings:
        key = optimizer_params_key(row["optimized_params"])
        if key in ranked_param_keys or key in permanently_invalid:
            continue
        ranked_param_keys.add(key)
        unique_rankings.append(row)
    unseen = [
        row for row in unique_rankings
        if optimizer_params_key(row["optimized_params"]) not in replayed_param_keys
    ]
    cycle_reset = False
    pool = unseen
    selected = pool[:top_k]
    return selected, {
        "policy": (
            "exclude_soft_replayed_until_allowed_pool_exhausted_and_never_"
            "select_permanently_invalid"
        ),
        "replayed_unique_points": len(replayed_param_keys),
        "permanently_invalid_unique_points": len(permanently_invalid),
        "permanent_invalid_selected": False,
        "unseen_unique_ranked_rows": len(unseen),
        "candidate_pool_rows": len(rankings),
        "candidate_pool_unique_points": len(unique_rankings),
        "cycle_reset": cycle_reset,
        "top_k_is_upper_bound": True,
        "partial_top_k": len(selected) < top_k,
        "duplicate_plan_padding_allowed": False,
        "baseline_padding_allowed": False,
        "selected_original_ranks": [int(row["rank"]) for row in selected],
        "selected_param_keys": [
            optimizer_params_key(row["optimized_params"]) for row in selected
        ],
    }


def xp_active_client_session_count(
    worker: Mapping[str, Any], *, timeout_seconds: float = 15.0,
) -> int:
    value = mysql_root(
        worker,
        "SELECT COUNT(*) FROM information_schema.processlist "
        "WHERE ID<>CONNECTION_ID() AND COMMAND<>'Daemon' "
        "AND USER<>'event_scheduler'",
        timeout_seconds=timeout_seconds,
    )
    return int(value)


def classify_xp_mysql_liveness(worker: Mapping[str, Any]) -> dict[str, Any]:
    """Prove that an exact XP container has no live mysqld process.

    Docker can keep an init wrapper running after mysqld aborts.  A SQL timeout
    alone is never authority to discard a data plane: require the frozen
    container name/id/labels and two consistent inspect reads around an exact
    process-table read.  Only an absent mysqld or mysqld rows whose process
    state begins with ``Z`` is safe to treat as incapable of retaining client
    sessions.
    """
    worker_id = str(worker.get("worker_id") or "")
    expected_container = reset.XP_CONTAINER_ALLOWLIST.get(worker_id)
    container = str(worker.get("container") or "")
    expected_id = str(worker.get("container_id") or "")
    base = {
        "worker_id": worker_id,
        "expected_container": expected_container,
        "observed_container": container,
        "expected_container_id": expected_id,
        "safe_to_discard_unresponsive_predecessor": False,
        "session_count_semantics": "explicit_sql_probe_required",
    }
    if (
        not expected_container
        or container != expected_container
        or "0708" in container
        or len(expected_id) != 64
    ):
        return {**base, "status": "IDENTITY_MISMATCH"}
    try:
        first = reset._docker_json("inspect", container)
        first_state = first.get("State") or {}
        labels = (first.get("Config") or {}).get("Labels") or {}
        stopped_initial_slot_recovery = bool(
            worker.get("stopped_initial_slot_recovery_authorized") is True
            and first.get("Id") == expected_id
            and first_state.get("Running") is False
            and int(first_state.get("Pid") or 0) == 0
            and first_state.get("Restarting") is False
            and first_state.get("Dead") is False
            and labels.get("ra.owner") == "mysql_four_scene_v5"
            and labels.get("ra.worker_id") == worker_id
            and "--init-file=/run/ra-init/slot.sql"
            in ((first.get("Config") or {}).get("Cmd") or [])
            and not (
                Path("/sys/fs/cgroup/blkio/docker-limited") / expected_id
            ).exists()
        )
        if stopped_initial_slot_recovery:
            return {
                **base,
                "status": "PROVEN_NO_LIVE_MYSQLD",
                "first_container_id": first.get("Id"),
                "docker_running": False,
                "container_identity_stable": True,
                "mysqld_rows": [], "live_mysqld_rows": [],
                "safe_to_discard_unresponsive_predecessor": True,
                "session_count_semantics": (
                    "zero_by_exact_stopped_initial_slot_identity_and_absent_cgroup"
                ),
                "stopped_initial_slot_recovery": True,
            }
        identity_matches = (
            first.get("Id") == expected_id
            and first_state.get("Running") is True
            and labels.get("ra.owner") == "mysql_four_scene_v5"
            and labels.get("ra.worker_id") == worker_id
        )
        if not identity_matches:
            return {
                **base,
                "status": "IDENTITY_MISMATCH",
                "first_container_id": first.get("Id"),
                "docker_running": first_state.get("Running"),
                "owner_label": labels.get("ra.owner"),
                "worker_label": labels.get("ra.worker_id"),
            }
        process_table = reset._docker_run(
            ["docker", "top", container, "-eo", "pid,stat,comm,args"],
            timeout=30,
        ).stdout
        rows: list[dict[str, Any]] = []
        for line in process_table.splitlines()[1:]:
            fields = line.strip().split(maxsplit=3)
            if len(fields) < 3:
                continue
            pid, state, command = fields[:3]
            arguments = fields[3] if len(fields) == 4 else ""
            if command == "mysqld" or re.search(r"(^|/)mysqld(?:\s|$)", arguments):
                rows.append({
                    "pid": int(pid), "state": state,
                    "command": command, "arguments_sha256": hashlib.sha256(
                        arguments.encode("utf-8", errors="replace")
                    ).hexdigest(),
                })
        second = reset._docker_json("inspect", container)
        second_state = second.get("State") or {}
        stable_identity = (
            second.get("Id") == expected_id
            and second_state.get("Running") is True
            and (second.get("Config") or {}).get("Labels", {}).get("ra.owner")
            == "mysql_four_scene_v5"
            and (second.get("Config") or {}).get("Labels", {}).get("ra.worker_id")
            == worker_id
        )
        live_rows = [row for row in rows if not str(row["state"]).startswith("Z")]
        safe = stable_identity and not live_rows
        return {
            **base,
            "status": "PROVEN_NO_LIVE_MYSQLD" if safe else "LIVE_OR_UNPROVEN",
            "first_container_id": first.get("Id"),
            "second_container_id": second.get("Id"),
            "docker_running": second_state.get("Running"),
            "container_identity_stable": stable_identity,
            "mysqld_rows": rows,
            "live_mysqld_rows": live_rows,
            "safe_to_discard_unresponsive_predecessor": safe,
            "session_count_semantics": (
                "zero_by_proven_absence_of_live_mysqld"
                if safe else "explicit_sql_probe_required"
            ),
        }
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError) as error:
        return {
            **base,
            "status": "LIVENESS_READ_FAILED",
            "error_type": type(error).__name__,
        }


def wait_for_xp_session_drain(
    workers: Mapping[str, Mapping[str, Any]], *, timeout_seconds: float = 60.0,
    poll_seconds: float = 1.0,
    liveness: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Wait for timed-out workload connections to close before data reset.

    The workload runner terminates at the formal 900-second boundary.  MySQL
    may retain those TCP sessions briefly while the JVM and JDBC sockets are
    unwound.  The reset remains fail-closed, but gets a bounded drain window
    instead of racing the client teardown.
    """
    if timeout_seconds <= 0.0 or poll_seconds <= 0.0:
        raise ValueError("XP session-drain bounds must be positive")
    started = time.monotonic()
    deadline = started + timeout_seconds
    polls: list[dict[str, Any]] = []
    available_ids = tuple(
        worker_id for worker_id in XP_IDS if worker_id in workers
    )
    if not available_ids:
        raise RuntimeError("XP session drain received no frozen XP workers")
    liveness_rows = {
        worker_id: dict((liveness or {}).get(worker_id) or {})
        for worker_id in available_ids
    }
    provably_dead = {
        worker_id for worker_id, row in liveness_rows.items()
        if row.get("safe_to_discard_unresponsive_predecessor") is True
        and row.get("status") == "PROVEN_NO_LIVE_MYSQLD"
    }
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            last_poll = polls[-1] if polls else {}
            raise RuntimeError(
                "XP active client sessions were not proven drained before "
                f"the {timeout_seconds:g}s bound: {last_poll}"
            )
        probe_timeout = min(15.0, remaining)
        counts: dict[str, int | None] = {}
        probe_errors: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(
            max_workers=len(available_ids), thread_name_prefix="xp-session-drain",
        ) as pool:
            futures = {
                pool.submit(
                    xp_active_client_session_count,
                    workers[worker_id],
                    timeout_seconds=probe_timeout,
                ): worker_id
                for worker_id in available_ids
                if worker_id not in provably_dead
            }
            for future in as_completed(futures):
                worker_id = futures[future]
                try:
                    counts[worker_id] = future.result()
                except subprocess.TimeoutExpired as error:
                    counts[worker_id] = None
                    probe_errors[worker_id] = {
                        "type": type(error).__name__,
                        "timeout_seconds": error.timeout,
                    }
                except subprocess.CalledProcessError as error:
                    counts[worker_id] = None
                    probe_errors[worker_id] = {
                        "type": type(error).__name__,
                        "returncode": error.returncode,
                    }
        counts = {
            worker_id: 0 if worker_id in provably_dead else counts.get(worker_id)
            for worker_id in available_ids
        }
        poll = {"observed_at": now(), "active_sessions": counts}
        if probe_errors:
            poll["probe_errors"] = probe_errors
        polls.append(poll)
        if all(counts[worker_id] == 0 for worker_id in available_ids):
            return {
                "status": "PASS",
                "timeout_seconds": timeout_seconds,
                "poll_seconds": poll_seconds,
                "probe_concurrency": len(available_ids),
                "per_probe_timeout_cap_seconds": 15.0,
                "all_workers_explicit_zero_required": not bool(provably_dead),
                "all_live_workers_explicit_zero_required": True,
                "provably_no_live_mysqld_workers": sorted(provably_dead),
                "liveness": liveness_rows,
                "elapsed_seconds": time.monotonic() - started,
                "polls": polls,
            }
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "XP active client sessions were not proven drained before "
                f"the {timeout_seconds:g}s bound: {poll}"
            )
        time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))


def measurement_campaign_root(output: Path) -> Path:
    """Resolve reset evidence beneath the current run, never global history."""
    if re.fullmatch(r"outer_\d+", output.parent.name):
        return output.parent.parent
    return output.parent


def reset_xp(
    freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
    output: Path, token: str, *, worker_ids: set[str] | None = None,
) -> None:
    available_ids = tuple(
        worker_id for worker_id in XP_IDS if worker_id in workers
    )
    requested = set(available_ids if worker_ids is None else worker_ids)
    if not requested or not requested.issubset(available_ids):
        raise RuntimeError(f"XP reset worker subset differs: {sorted(requested)}")
    reset_started = time.monotonic()
    stage = output / "xp_resets" / token
    stage.mkdir(parents=True, mode=0o700)
    liveness = {
        worker_id: classify_xp_mysql_liveness(workers[worker_id])
        for worker_id in available_ids
    }
    drain = wait_for_xp_session_drain(workers, liveness=liveness)
    atomic(stage / "pre_reset_session_drain.json", {
        "schema_version": "mysql_four_scene_xp_pre_reset_session_drain_v1",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        **drain,
    })
    # Include the formal campaign/run id as well as the reset-stage token.
    # Stage tokens repeat across fresh launches (for example the incumbent
    # baseline token), so token-only quarantine paths make a safe restart
    # collide with an interrupted predecessor.  The mixed-SSD reset authority
    # preserves both trailing components on each worker's own filesystem and
    # its aggregate cleanup groups workers by the resulting exact parent.
    quarantine = (
        Path(str(freeze["storage"]["runtime_root"]))
        / "quarantine" / output.name / token
    )
    frozen = {row["worker_id"]: row for row in freeze["workers"]}
    reset_rows: dict[str, dict[str, Any]] = {}
    worker_finished_seconds: dict[str, float] = {}
    with ThreadPoolExecutor(
        max_workers=XP_RESET_MAX_WORKERS,
        thread_name_prefix="formal-xp-reset",
    ) as pool:
        futures = {
            pool.submit(
                reset._prepare_fresh_xp,
                freeze, frozen[worker_id], workers[worker_id], quarantine,
                stage, False, "formal_point",
                allow_unresponsive_old_container=(
                    liveness[worker_id].get(
                        "safe_to_discard_unresponsive_predecessor"
                    ) is True
                ),
            ): worker_id
            for worker_id in requested
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            reset_rows[worker_id] = future.result()
            worker_finished_seconds[worker_id] = time.monotonic() - reset_started
    account_root = stage / "accounts"
    account_root.mkdir()
    with ThreadPoolExecutor(
        max_workers=XP_RESET_MAX_WORKERS,
        thread_name_prefix="formal-xp-users",
    ) as pool:
        futures = {
            pool.submit(users._run_worker, reset_rows[worker_id], account_root, True, False): worker_id
            for worker_id in requested
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            account = future.result()
            workers[worker_id] = {**frozen[worker_id], **reset_rows[worker_id], **account}
    reset_binding = stage / "reset_binding.json"
    atomic(reset_binding, {
        "schema_version": "mysql_four_scene_algorithm_c_xp_point_reset_v1", "status": "PASS",
        "created_at": now(), "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "freeze_sha256": sha(FREEZE),
        "workers": [workers[worker_id] for worker_id in available_ids],
        "reset_worker_ids": sorted(requested),
        "reset_concurrency": min(XP_RESET_MAX_WORKERS, len(requested)),
        "worker_reset_finished_seconds": worker_finished_seconds,
        "elapsed_seconds": time.monotonic() - reset_started,
    })
    reset.purge_completed_discard_quarantine(
        quarantine_root=quarantine,
        reset_workers={worker_id: workers[worker_id] for worker_id in requested},
        binding_path=reset_binding,
        cleanup_path=stage / "quarantine_cleanup.json",
    )


def pair_measure(pair: int, workers: Mapping[str, dict[str, Any]], output: Path, runners: Mapping[str, r0mod.PinnedRunner]) -> dict[str, Any]:
    job = workers[f"job_w{pair}"]
    tpch = workers[f"tpch_w{pair}"]
    query_files = discover_job_query_files(JOB_QUERY_ROOT)
    manifest = build_job_manifest(query_files, output / f"pair_{pair}" / "manifest")
    job_user, job_password = private_credential(job)
    tpch_user, tpch_password = private_credential(tpch)
    measurement_started_monotonic = time.monotonic()
    result = run_job_tpch_parallel(
        job_manifest=manifest,
        job_endpoint=MySQLEndpoint("127.0.0.1", int(job["host_port"]), "imdb_job", job_user, job_password),
        tpch_endpoint=MySQLEndpoint("127.0.0.1", int(tpch["host_port"]), "tpch", tpch_user, tpch_password),
        output_dir=output / f"pair_{pair}", benchbase_jar=r0mod.BENCHBASE_JAR,
        clients=16, tpch_clients=5, scale_factor=4, timeout_s=900,
        target_wall_time_s=900.0, job_heavy_clients=4, formal_scope="mysql_only",
        workload_freeze=r0mod.WORKLOAD_FREEZE,
        capture_job_explain=False,
        job_runner=lambda *args, **kwargs: run_job(*args, **kwargs, runner=runners[job["worker_id"]]),
        tpch_runner=lambda *args, **kwargs: run_benchbase(*args, **kwargs, runner=runners[tpch["worker_id"]]),
    )
    result["measurement_started_monotonic"] = measurement_started_monotonic
    return result


def xp_measure(
    worker: Mapping[str, Any], output: Path, runner: r0mod.PinnedRunner,
    sampler: r0mod.CgroupSampler,
) -> dict[str, Any]:
    user, password = private_credential(worker)
    worker_output = output / str(worker["worker_id"])
    stop_file = worker_output / "adaptive_stop_request.txt"
    evidence_file = worker_output / "resource_stability.json"
    workload_done = threading.Event()
    monitor_result: dict[str, Any] = {}
    monitor_error: list[BaseException] = []
    started_monotonic = time.monotonic()

    def monitor() -> None:
        try:
            monitor_result.update(adaptive_measurement.monitor_xp_stop_request(
                sampler=sampler, worker=worker,
                started_monotonic=started_monotonic, stop_file=stop_file,
                evidence_file=evidence_file, workload_done=workload_done,
            ))
        except BaseException as error:
            monitor_error.append(error)

    monitor_thread = threading.Thread(
        target=monitor, name=f"adaptive-stop-{worker['worker_id']}", daemon=True,
    )
    monitor_thread.start()
    try:
        result = run_hybench(
            "xp", MySQLEndpoint(
                "127.0.0.1", int(worker["host_port"]),
                "hybench_sf10x", user, password,
            ),
            worker_output, hybench_home=r0mod.HYBENCH_HOME,
            duration_s=adaptive_measurement.XP_MAXIMUM_MEASUREMENT_SECONDS,
            clients=8, extra_properties={"sf": "10x"},
            runtime_jar=r0mod.NATIVE_XP_JAR,
            formal_control={
                "runner_jar": HYBENCH_ADAPTIVE_RUNNER_JAR,
                "stop_file": stop_file,
                "minimum_measurement_seconds": adaptive_measurement.MINIMUM_MEASUREMENT_SECONDS,
                "maximum_measurement_seconds": adaptive_measurement.XP_MAXIMUM_MEASUREMENT_SECONDS,
                "graceful_quiescence_seconds": 300,
            },
            dry_run=False, timeout_s=1210, runner=runner,
        )
    finally:
        workload_done.set()
        monitor_thread.join(timeout=10)
    if monitor_thread.is_alive():
        raise RuntimeError(f"{worker['worker_id']} adaptive stability monitor did not stop")
    if monitor_error:
        raise RuntimeError(
            f"{worker['worker_id']} adaptive stability monitor failed: {monitor_error[0]}"
        ) from monitor_error[0]
    result["resource_stability"] = monitor_result
    return result


def set_runtime_stage(state_path: Path | None, stage: str) -> None:
    if state_path is None or not state_path.is_file():
        return
    state = load(state_path)
    state["stage"] = stage
    state["stage_updated_at"] = now()
    atomic(state_path, state)


def capture_plans_fail_closed(
    worker_id: str, callback: Any,
) -> tuple[dict[str, Any], dict[str, str] | None]:
    """Turn a post-replay plan-capture error into a candidate-level failure.

    Plan capture is part of the three-level hard gate.  A timeout therefore
    invalidates this candidate, but must not terminate the entire formal
    controller or discard valid measurements from the other workers.
    """
    try:
        plans = callback()
    except Exception as error:
        return {}, {
            "task": f"{worker_id}_plan_capture",
            "error": f"{type(error).__name__}: {error}",
        }
    return plans, None


def mysql_server_liveness(worker: Mapping[str, Any]) -> bool:
    """Probe mysqld itself; a live Docker entrypoint can contain a zombie server."""
    try:
        return mysql_root(worker, "SELECT 1", timeout_seconds=15) == "1"
    except Exception:
        return False


def measure_batch(
    freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
    assignments: Mapping[str, Mapping[str, Any]], output: Path, reset_token: str,
    *, state_path: Path | None = None, stage_prefix: str = "FORMAL_POINT",
) -> dict[str, dict[str, Any]]:
    if any(worker_id in assignments for worker_id in XP_IDS):
        set_runtime_stage(state_path, f"{stage_prefix}_RESETTING_XP")
        campaign_root = measurement_campaign_root(output)
        reset_xp(
            freeze, workers, campaign_root, reset_token,
            worker_ids=set(assignments).intersection(XP_IDS),
        )
    output.mkdir(parents=True, mode=0o700)
    for worker_id, assignment in assignments.items():
        apply_costs(workers[worker_id], assignment["params"])
    before_counter = {worker_id: db_counter(workers[worker_id]) for worker_id in assignments}
    before_inspect = {worker_id: reset.prep._docker_json("inspect", workers[worker_id]["container"]) for worker_id in assignments}
    for worker_id in assignments:
        workers[worker_id]["formal_client_log"] = str(output / worker_id / "client_processes.jsonl")
    runners = {worker_id: r0mod.PinnedRunner(workers[worker_id], Path(workers[worker_id]["formal_client_log"])) for worker_id in assignments}
    sampler = r0mod.CgroupSampler([workers[worker_id] for worker_id in assignments], output / "resource_samples.jsonl")
    set_runtime_stage(state_path, f"{stage_prefix}_MEASURING")
    sampler.start()
    started = time.monotonic()
    raw: dict[str, Any] = {}
    failures: list[dict[str, str]] = []
    task_windows: dict[str, tuple[float, float]] = {}

    def timed(task: str, callback: Any, *callback_args: Any) -> Any:
        task_started = time.monotonic()
        try:
            return callback(*callback_args)
        finally:
            task_windows[task] = (task_started, time.monotonic())

    try:
        tasks: dict[Any, str] = {}
        # The original four-scene layout has at most six independent tasks,
        # while the mixed-SSD XP successor can assign eight XP workers in one
        # physical wave.  Size the pool to the actual assignment count so an
        # eight-worker wave does not silently execute as 6+2.
        with ThreadPoolExecutor(
            max_workers=max(6, len(assignments)),
            thread_name_prefix="formal-four-scene",
        ) as pool:
            for pair in (1, 2):
                if f"job_w{pair}" in assignments and f"tpch_w{pair}" in assignments:
                    tasks[pool.submit(
                        timed, f"pair_{pair}", pair_measure,
                        pair, workers, output, runners,
                    )] = f"pair_{pair}"
            for worker_id in XP_IDS:
                if worker_id in assignments:
                    tasks[pool.submit(
                        timed, worker_id, xp_measure,
                        workers[worker_id], output, runners[worker_id], sampler,
                    )] = worker_id
            for future in as_completed(tasks):
                task = tasks[future]
                try:
                    raw[task] = future.result()
                except BaseException as error:
                    failures.append({"task": task, "error": f"{type(error).__name__}: {error}"})
    finally:
        worker_windows = {
            worker_id: task_windows[
                f"pair_{worker_id[-1]}"
                if worker_id.startswith(("job_", "tpch_")) else worker_id
            ]
            for worker_id in assignments
            if (
                f"pair_{worker_id[-1]}"
                if worker_id.startswith(("job_", "tpch_")) else worker_id
            ) in task_windows
        }
        resources = sampler.finish(worker_windows)
    elapsed = time.monotonic() - started
    after_counter = {worker_id: db_counter(workers[worker_id]) for worker_id in assignments}
    stability_by_worker: dict[str, dict[str, Any]] = {}
    for worker_id in assignments:
        scene = str(workers[worker_id]["scene"])
        if scene in {"job_memory", "tpch_sf4_memory"}:
            pair = int(worker_id[-1])
            pair_result = raw.get(f"pair_{pair}") or {}
            workload_key = "job" if scene == "job_memory" else "tpch"
            workload = pair_result.get("workloads", {}).get(workload_key, {})
            pair_window = task_windows.get(f"pair_{pair}")
            if pair_window is None:
                continue
            measurement_started = float(
                pair_result.get("measurement_started_monotonic", pair_window[0])
            )
            workload_elapsed = float(
                workload.get("independent_wall_time_s", workload.get("wall_time_s", 0.0))
            )
            stability_by_worker[worker_id] = adaptive_measurement.completed_suite_stability(
                list(sampler.rows.get(worker_id, ())), workers[worker_id],
                measurement_started, measurement_started + workload_elapsed,
            )
        else:
            stability_by_worker[worker_id] = dict(
                (raw.get(worker_id) or {}).get("resource_stability") or {}
            )
    set_runtime_stage(state_path, f"{stage_prefix}_PLAN_CAPTURE")
    measurements: dict[str, dict[str, Any]] = {}
    for worker_id, assignment in assignments.items():
        scene = str(workers[worker_id]["scene"])
        pair = int(worker_id[-1]) if scene in {"job_memory", "tpch_sf4_memory"} else None
        if scene == "job_memory":
            workload = (raw.get(f"pair_{pair}") or {}).get("workloads", {}).get("job", {})
            coverage = workload.get("query_coverage", {}).get("coverage_complete") is True
            formal_valid = (raw.get(f"pair_{pair}") or {}).get("formal_measurement_valid") is True
            plan_callback = lambda: explain_job_representatives(
                freeze, workers[worker_id]
            )
        elif scene == "tpch_sf4_memory":
            workload = (raw.get(f"pair_{pair}") or {}).get("workloads", {}).get("tpch", {})
            coverage = workload.get("query_coverage", {}).get("coverage_complete") is True
            formal_valid = (raw.get(f"pair_{pair}") or {}).get("formal_measurement_valid") is True
            plan_callback = lambda: explain(
                workers[worker_id], [_TPCH_EXPLAINS[6], _TPCH_EXPLAINS[11]]
            )
        else:
            workload = raw.get(worker_id) or {}
            coverage = workload.get("workload_coverage", {}).get("coverage_complete") is True
            formal_valid = True
            plan_callback = lambda: explain(
                workers[worker_id], [_HYBENCH_AP_EXPLAIN]
            )
        metrics = workload.get("metrics") or {}
        if (
            formal_valid
            and coverage
            and float(metrics.get("qps", 0)) > 0
        ):
            plans, plan_failure = capture_plans_fail_closed(
                worker_id, plan_callback
            )
        else:
            plans, plan_failure = {}, {
                "task": f"{worker_id}_plan_capture",
                "error": "skipped_after_invalid_workload",
            }
        if plan_failure is not None:
            failures.append(plan_failure)
        resource = resources.get(worker_id) or {}
        utilization = {
            "cpu": float(resource.get("cpu_fraction_of_quota", math.nan)),
            "memory": float(resource.get("memory_peak_fraction", math.nan)),
            "io": max(float(resource.get("read_fraction_of_cap", math.nan)), float(resource.get("write_fraction_of_cap", math.nan))),
        }
        after = reset.prep._docker_json("inspect", workers[worker_id]["container"])
        server_live = mysql_server_liveness(workers[worker_id])
        restart_ok = (
            after.get("RestartCount") == before_inspect[worker_id].get("RestartCount")
            and (after.get("State") or {}).get("OOMKilled") is not True
            and server_live
        )
        active_elapsed = float(resource.get("elapsed_seconds", 0.0))
        db_qps = (
            (after_counter[worker_id] - before_counter[worker_id]) / active_elapsed
            if active_elapsed > 0 else 0.0
        )
        plan_sha = hashlib.sha256(json.dumps(plans, sort_keys=True).encode()).hexdigest() if plans else None
        fingerprint = {
            "l1_plan_tree_sha256": plan_sha,
            "l2_parameter_sha256": hashlib.sha256(json.dumps(assignment["params"], sort_keys=True).encode()).hexdigest(),
            "l3_resource_workload_sha256": hashlib.sha256(json.dumps({"worker": worker_id, "freeze": sha(FREEZE), "coverage": coverage}, sort_keys=True).encode()).hexdigest(),
            "complete": bool(plan_sha),
        }
        hard_gate_checks = {
            "formal_measurement_valid": formal_valid,
            "coverage_complete": coverage,
            "restart_and_oom_gate_passed": restart_ok,
            "mysql_server_liveness": server_live,
            "three_level_fingerprint_complete": fingerprint["complete"],
            "resource_utilization_finite": all(
                math.isfinite(value) for value in utilization.values()
            ),
            "resource_sample_coverage_sufficient": (
                active_elapsed > 0
                and float(resource.get("sample_count", 0)) >= active_elapsed * 0.85
            ),
            "database_qps_positive": db_qps > 0,
            "client_qps_audit_positive": float(metrics.get("qps", 0)) > 0,
            "target_resource_stability_passed": (
                stability_by_worker.get(worker_id, {}).get("stable") is True
            ),
        }
        hard_gate = all(hard_gate_checks.values())
        measurements[worker_id] = {
            "scene": scene, "worker_id": worker_id, "rank": assignment["rank"],
            "params": assignment["params"], "db_qps": db_qps,
            "client_qps_audit": float(metrics.get("qps", 0)),
            "p95_ms": float(metrics.get("latency_p95_ms", 0)),
            "p99_ms": float(metrics.get("latency_p99_ms", 0)),
            "resource_means": utilization, "hard_gate_passed": hard_gate,
            "hard_gate_checks": hard_gate_checks,
            "coverage_complete": coverage, "restart_gate_passed": restart_ok,
            "three_level_fingerprint": fingerprint, "resource_summary": resource,
            "resource_stability": stability_by_worker.get(worker_id, {}),
            "candidate_physical_plan_sha256": assignment.get(
                "candidate_physical_plan_sha256"
            ),
            "workload_result": workload,
        }
    atomic(output / "batch_result.json", {
        "schema_version": "mysql_four_scene_algorithm_c_batch_v1",
        "status": "PASS" if not failures and all(row["hard_gate_passed"] for row in measurements.values()) else "FAILED_CLOSED",
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "elapsed_seconds": elapsed, "measurements": measurements, "failures": failures,
    })
    return measurements


def retryable_xp_timeout_batch(document: Mapping[str, Any]) -> bool:
    failures = document.get("failures")
    measurements = document.get("measurements")
    if not isinstance(failures, list) or not failures or not isinstance(measurements, Mapping):
        return False
    failed_workers: set[str] = set()
    for failure in failures:
        if not isinstance(failure, Mapping):
            return False
        worker_id = str(failure.get("task", ""))
        if worker_id not in XP_IDS or not str(failure.get("error", "")).startswith("TimeoutExpired:"):
            return False
        failed_workers.add(worker_id)
    if set(measurements) != set(WORKER_IDS):
        return False
    for worker_id, row in measurements.items():
        if not isinstance(row, Mapping):
            return False
        if worker_id in failed_workers:
            checks = row.get("hard_gate_checks") or {}
            if (
                row.get("hard_gate_passed") is not False
                or checks.get("coverage_complete") is not False
                or checks.get("client_qps_audit_positive") is not False
            ):
                return False
        elif row.get("hard_gate_passed") is not True:
            return False
    return True


def shrink_ranges(ranges: Mapping[str, Any], params: Mapping[str, float], active: list[str]) -> dict[str, Any]:
    typed_ranges: dict[str, ParameterRange] = {}
    for name, specification in ranges.items():
        if not isinstance(specification, Mapping):
            raise TypeError(f"{name} optimizer range must be a mapping")
        if "lower" not in specification or "upper" not in specification:
            raise ValueError(f"{name} optimizer range requires lower/upper")
        lower = float(specification["lower"])
        upper = float(specification["upper"])
        if not math.isfinite(lower) or not math.isfinite(upper) or lower > upper:
            raise ValueError(f"{name} optimizer range bounds differ")
        typed_ranges[name] = ParameterRange(lower, upper)
    decision = decide_validation_acceptance(
        {"db_qps": 1.0, "resource_means": {"cpu": 1.0, "memory": 1.0, "io": 1.0}},
        {"db_qps": 2.0, "resource_means": {"cpu": 0.5, "memory": 1.1, "io": 1.1}},
        target_resource="cpu", hard_gate_passed=True, utilization_key="resource_means",
    )
    progress = advance_outer_shrink_progress(
        ranges=typed_ranges, accepted_shrink_count=0, rejected_count=0,
        target_accepted_shrinks=10, decision=decision,
        candidate_params=params, active_params=active,
    )
    result = copy.deepcopy(dict(ranges))
    for name, updated in progress.ranges.items():
        if name not in result:
            continue
        result[name]["lower"] = float(updated.min_value)
        result[name]["upper"] = float(updated.max_value)
    return result


def interval_convergence_audit(
    ranges: Mapping[str, Any], active: list[str], *,
    max_interval_ratio: float = CONVERGENCE_MAX_INTERVAL_RATIO,
) -> dict[str, Any]:
    """Audit positive cost-parameter intervals using ``upper / lower``.

    All eight optimizer costs use a ratio/log scale.  A scene is interval-ready
    only when every active parameter has ``upper / lower <= 1.01``.
    """
    if not math.isfinite(max_interval_ratio) or max_interval_ratio <= 1.0:
        raise ValueError("max_interval_ratio must be finite and greater than one")
    per_parameter: dict[str, dict[str, Any]] = {}
    for name in active:
        specification = ranges.get(name)
        if not isinstance(specification, Mapping):
            raise TypeError(f"{name} convergence range must be a mapping")
        lower = float(specification["lower"])
        upper = float(specification["upper"])
        if (
            not math.isfinite(lower)
            or not math.isfinite(upper)
            or lower <= 0.0
            or upper < lower
        ):
            raise ValueError(f"{name} convergence range bounds differ")
        ratio = upper / lower
        per_parameter[name] = {
            "lower": lower,
            "upper": upper,
            "upper_lower_ratio": ratio,
            "relative_width": ratio - 1.0,
            "within_threshold": ratio <= max_interval_ratio,
        }
    return {
        "metric": "upper_divided_by_lower",
        "max_interval_ratio": max_interval_ratio,
        "max_relative_width": max_interval_ratio - 1.0,
        "all_active_parameters_within_threshold": bool(per_parameter) and all(
            row["within_threshold"] for row in per_parameter.values()
        ),
        "per_parameter": per_parameter,
    }


def advance_interval_convergence(
    *, ranges: Mapping[str, Any], active: list[str],
    candidate_params: Mapping[str, Any] | None, accepted: bool,
    previous_stable_rounds: int,
    previous_interval: Mapping[str, Any] | None,
    max_interval_ratio: float = CONVERGENCE_MAX_INTERVAL_RATIO,
    required_stable_rounds: int = CONVERGENCE_REQUIRED_STABLE_ROUNDS,
) -> dict[str, Any]:
    """Require two consecutive accepted optima inside a <=1% interval.

    A rejected outer round breaks the consecutive-stability streak.  The first
    accepted round whose post-shrink interval is within 1% establishes the
    stable interval; the next accepted optimum must remain inside that saved
    interval before the scene is declared converged.
    """
    if previous_stable_rounds < 0:
        raise ValueError("previous_stable_rounds must be non-negative")
    if required_stable_rounds <= 0:
        raise ValueError("required_stable_rounds must be positive")
    audit = interval_convergence_audit(
        ranges, active, max_interval_ratio=max_interval_ratio,
    )
    if not accepted:
        return {
            **audit,
            "accepted": False,
            "candidate_inside_previous_interval": False,
            "stable_rounds": 0,
            "required_stable_rounds": required_stable_rounds,
            "stable_interval": None,
            "converged": False,
            "streak_reset_reason": "outer_round_not_accepted",
        }
    if candidate_params is None:
        raise ValueError("accepted convergence update requires candidate_params")
    if not audit["all_active_parameters_within_threshold"]:
        return {
            **audit,
            "accepted": True,
            "candidate_inside_previous_interval": False,
            "stable_rounds": 0,
            "required_stable_rounds": required_stable_rounds,
            "stable_interval": None,
            "converged": False,
            "streak_reset_reason": "interval_wider_than_threshold",
        }
    previous_available = isinstance(previous_interval, Mapping)
    inside_previous = previous_available and all(
        name in candidate_params
        and name in previous_interval
        and (
            float(previous_interval[name]["lower"])
            <= float(candidate_params[name])
            or math.isclose(
                float(candidate_params[name]),
                float(previous_interval[name]["lower"]),
                rel_tol=CONVERGENCE_BOUNDARY_REL_TOL,
                abs_tol=CONVERGENCE_BOUNDARY_ABS_TOL,
            )
        )
        and (
            float(candidate_params[name])
            <= float(previous_interval[name]["upper"])
            or math.isclose(
                float(candidate_params[name]),
                float(previous_interval[name]["upper"]),
                rel_tol=CONVERGENCE_BOUNDARY_REL_TOL,
                abs_tol=CONVERGENCE_BOUNDARY_ABS_TOL,
            )
        )
        for name in active
    )
    stable_rounds = previous_stable_rounds + 1 if inside_previous else 1
    stable_interval = {
        name: {
            "lower": audit["per_parameter"][name]["lower"],
            "upper": audit["per_parameter"][name]["upper"],
        }
        for name in active
    }
    return {
        **audit,
        "accepted": True,
        "candidate_inside_previous_interval": inside_previous,
        "boundary_membership_rel_tolerance": CONVERGENCE_BOUNDARY_REL_TOL,
        "boundary_membership_abs_tolerance": CONVERGENCE_BOUNDARY_ABS_TOL,
        "stable_rounds": stable_rounds,
        "required_stable_rounds": required_stable_rounds,
        "stable_interval": stable_interval,
        "converged": stable_rounds >= required_stable_rounds,
        "streak_reset_reason": None if inside_previous else "stable_interval_established",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", args.run_id) is None:
        parser.error("unsafe run-id")
    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh formal output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    lock = Path("/tmp/mysql_four_scene_algorithm_c_v1.lock").open("w")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise RuntimeError("another four-scene Algorithm C controller owns the lock") from error
    state_path = output / "run_state.json"
    state: dict[str, Any] = {
        "schema_version": "mysql_four_scene_algorithm_c_formal_state_v2",
        "status": "RUNNING", "stage": "VALIDATING_AUTHORITY", "started_at": now(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "active_scenes": list(SCENES), "accepted_rounds": {scene: 0 for scene in SCENES},
        "acceptance_tier": {scene: "strict_three_level" for scene in SCENES},
        "consecutive_failed_rounds": {scene: 0 for scene in SCENES},
        "acceptance_tier_downgrade_after_rounds": 2,
        "accepted_rounds_role": "audit_only_not_a_termination_target",
        "converged": {scene: False for scene in SCENES},
        "interval_stable_rounds": {scene: 0 for scene in SCENES},
        "stable_intervals": {scene: None for scene in SCENES},
        "convergence_rule": {
            "metric": "upper_divided_by_lower",
            "maximum_interval_ratio": CONVERGENCE_MAX_INTERVAL_RATIO,
            "maximum_relative_width": CONVERGENCE_MAX_INTERVAL_RATIO - 1.0,
            "required_consecutive_stable_accepted_rounds":
                CONVERGENCE_REQUIRED_STABLE_ROUNDS,
            "rejected_outer_round_resets_stability_streak": True,
            "all_active_parameters_must_satisfy_interval": True,
        },
        "maximum_outer_attempts": MAXIMUM_OUTER_ATTEMPTS,
        "fixed_top_k": 5, "pid": os.getpid(),
        "r0_path": str(R0), "r0_sha256": sha(R0),
        "post_r0_benchmark_accounts_path": str(POST_R0_BENCHMARK_ACCOUNTS),
        "post_r0_benchmark_accounts_sha256": sha(POST_R0_BENCHMARK_ACCOUNTS),
        "freeze_path": str(FREEZE), "freeze_sha256": sha(FREEZE),
        "resource_model_contract_path": str(RESOURCE_MODEL_CONTRACT),
        "resource_model_contract_sha256": sha(RESOURCE_MODEL_CONTRACT),
        "resource_model_source_sha256": sha(RESOURCE_MODEL_SOURCE),
        "controller_source_path": str(Path(__file__).resolve()),
        "controller_source_sha256": sha(Path(__file__).resolve()),
        "formal_outer_started": True,
    }
    atomic(state_path, state)
    try:
        freeze, workers, point_gate = r0mod._validate_authority()
        r0 = load(R0)
        post_r0_accounts = load(POST_R0_BENCHMARK_ACCOUNTS)
        resource_model_contract = load(RESOURCE_MODEL_CONTRACT)
        if (
            r0.get("status") != "PASS" or r0.get("fresh_r0_complete") is not True
            or r0.get("formal_outer_launch_authorized") is not True
            or r0.get("freeze_sha256") != sha(FREEZE)
            or freeze.get("algorithm_c_acceptance", {}).get("downgrade_after_consecutive_failed_outer_rounds") != 2
        ):
            raise RuntimeError("formal R0/Algorithm C authority differs")
        post_r0_workers = {
            row.get("worker_id"): row
            for row in (post_r0_accounts.get("workers") or [])
            if isinstance(row, dict)
        }
        if (
            post_r0_accounts.get("status") != "PASS"
            or post_r0_accounts.get("prepared_binding_sha256")
            != r0.get("prepared_binding_sha256")
            or post_r0_accounts.get("freeze_sha256") != sha(FREEZE)
            or post_r0_accounts.get("passwords_emitted") is not False
            or set(post_r0_workers) != set(workers)
            or any(
                post_r0_workers[worker_id].get("ready") is not True
                or post_r0_workers[worker_id].get(
                    "account_mutation_performed_in_this_run"
                ) is not True
                or post_r0_workers[worker_id].get("container_id")
                != workers[worker_id].get("container_id")
                or post_r0_workers[worker_id].get("benchmark_defaults")
                != workers[worker_id].get("benchmark_defaults")
                for worker_id in workers
            )
        ):
            raise RuntimeError("post-R0 benchmark credential authority differs")
        if (
            resource_model_contract.get("schema_version")
            != "mysql_algorithm_c_contextual_plan_physics_contract_v2"
            or resource_model_contract.get("status") != "FROZEN"
            or resource_model_contract.get("model", {}).get("sha256")
            != sha(RESOURCE_MODEL_SOURCE)
            or resource_model_contract.get("empirical_replay_basis", {}).get(
                "effective_replays"
            ) != 151
            or resource_model_contract.get("empirical_replay_basis", {}).get(
                "trusted_hard_reject_rules"
            ) != 0
            or resource_model_contract.get("ranking", {}).get(
                "operator_count_hard_pruning"
            ) is not False
            or resource_model_contract.get("ranking", {}).get(
                "real_replay_required"
            ) is not True
        ):
            raise RuntimeError("contextual resource-model authority differs")
        state["stage"] = "FORMAL_BASELINE_PREPARE"
        state["prestart_point_gate"] = point_gate
        atomic(state_path, state)
        baseline_assignments = {worker_id: {"rank": 0, "params": dict(DEFAULTS)} for worker_id in workers}
        baseline_rows: dict[str, dict[str, Any]] = {}
        state["formal_baseline_attempts"] = []
        for baseline_attempt in range(1, 3):
            baseline_output = (
                output / "formal_baseline"
                if baseline_attempt == 1
                else output / "formal_baseline" / f"transient_retry_{baseline_attempt - 1}"
            )
            reset_token = (
                "formal_baseline"
                if baseline_attempt == 1
                else f"formal_baseline_transient_retry_{baseline_attempt - 1}"
            )
            stage_prefix = (
                "FORMAL_BASELINE"
                if baseline_attempt == 1
                else f"FORMAL_BASELINE_TRANSIENT_RETRY_{baseline_attempt - 1}"
            )
            baseline_rows = measure_batch(
                freeze, workers, baseline_assignments,
                baseline_output, reset_token,
                state_path=state_path, stage_prefix=stage_prefix,
            )
            baseline_document = load(baseline_output / "batch_result.json")
            retryable = retryable_xp_timeout_batch(baseline_document)
            state["formal_baseline_attempts"].append({
                "attempt": baseline_attempt,
                "batch_result_path": str(baseline_output / "batch_result.json"),
                "batch_result_sha256": sha(baseline_output / "batch_result.json"),
                "status": baseline_document.get("status"),
                "retryable_xp_timeout": retryable,
            })
            atomic(state_path, state)
            if baseline_document.get("status") == "PASS" or not retryable:
                break
        incumbents: dict[str, dict[str, Any]] = {}
        params_by_scene = {scene: dict(DEFAULTS) for scene in SCENES}
        ranges_by_scene: dict[str, dict[str, Any]] = {}
        active_by_scene: dict[str, list[str]] = {}
        replayed_param_keys_by_scene: dict[str, set[str]] = {
            scene: set() for scene in SCENES
        }
        for scene in SCENES:
            rows = [row for row in baseline_rows.values() if row["scene"] == scene]
            if len(rows) != 2 or not all(row["hard_gate_passed"] for row in rows):
                raise RuntimeError(f"{scene} formal baseline hard gate failed")
            incumbents[scene] = {
                "db_qps": sum(row["db_qps"] for row in rows) / 2,
                "p95_ms": sum(row["p95_ms"] for row in rows) / 2,
                "p99_ms": sum(row["p99_ms"] for row in rows) / 2,
                "resource_means": {name: sum(row["resource_means"][name] for row in rows) / 2 for name in ("cpu", "memory", "io")},
            }
            table = load(TABLE_ROOT / TABLES[scene])
            active_by_scene[scene] = list(table["parameter_space"]["active_params"])
            ranges_by_scene[scene] = copy.deepcopy(table["parameter_space"]["ranges"])
        outer_attempt = 0
        while (
            outer_attempt < MAXIMUM_OUTER_ATTEMPTS
            and not all(state["converged"].values())
        ):
            outer_attempt += 1
            state["stage"] = f"OUTER_{outer_attempt:03d}_PLAN_PHYSICS_RANKING"
            atomic(state_path, state)
            rankings: dict[str, list[dict[str, Any]]] = {}
            all_rankings: dict[str, list[dict[str, Any]]] = {}
            replay_dedup: dict[str, dict[str, Any]] = {}
            for scene in SCENES:
                if state["converged"][scene]:
                    continue
                representative_id = sorted(
                    worker_id for worker_id, worker in workers.items()
                    if worker["scene"] == scene
                )[0]
                all_rankings[scene] = candidate_rankings(
                    scene, params_by_scene[scene], ranges_by_scene[scene],
                    incumbents[scene]["resource_means"], freeze=freeze,
                    worker=workers[representative_id],
                )
                rankings[scene], replay_dedup[scene] = select_unreplayed_candidates(
                    all_rankings[scene], replayed_param_keys_by_scene[scene],
                    top_k=5,
                )
                if replay_dedup[scene]["cycle_reset"]:
                    replayed_param_keys_by_scene[scene].clear()
            atomic(output / f"outer_{outer_attempt:03d}" / "predicted_rankings.json", {
                "schema_version": "mysql_four_scene_algorithm_c_rankings_v3",
                "outer_attempt": outer_attempt,
                "all_nonempty_subsets": all_rankings,
                "selected_top_k": rankings,
                "replay_dedup": replay_dedup,
                "fixed_top_k": 5,
                "qps_used_for_generation_or_ranking": False,
                "resource_model": "mysql_algorithm_c_contextual_plan_physics_soft_v2",
                "operator_count_hard_pruning": False,
                "real_replay_required": True,
            })
            evaluations: dict[str, list[dict[str, Any]]] = {scene: [] for scene in rankings}
            for batch_index, ranks in enumerate(FORMAL_CANDIDATE_BATCHES, 1):
                assignments: dict[str, dict[str, Any]] = {}
                for scene, rows in rankings.items():
                    scene_workers = sorted(worker_id for worker_id, worker in workers.items() if worker["scene"] == scene)
                    for slot, rank in enumerate(ranks):
                        if slot >= len(scene_workers):
                            break
                        candidate = rows[rank - 1]
                        assignments[scene_workers[slot]] = {"rank": rank, "params": candidate["optimized_params"]}
                measured = measure_batch(
                    freeze, workers, assignments,
                    output / f"outer_{outer_attempt:03d}" / f"top5_batch_{batch_index}",
                    f"outer_{outer_attempt:03d}_batch_{batch_index}",
                    state_path=state_path,
                    stage_prefix=f"OUTER_{outer_attempt:03d}_BATCH_{batch_index}",
                )
                for row in measured.values():
                    evaluations[row["scene"]].append(row)
            for scene, rows in evaluations.items():
                eligible: list[tuple[dict[str, Any], Any]] = []
                for row in sorted(rows, key=lambda item: item["rank"]):
                    latency_gate = row["p95_ms"] <= incumbents[scene]["p95_ms"] * 1.10 and row["p99_ms"] <= incumbents[scene]["p99_ms"] * 1.10
                    decision = decide_validation_acceptance(
                        incumbents[scene], row, target_resource=TARGET[scene],
                        hard_gate_passed=row["hard_gate_passed"] and latency_gate,
                        utilization_key="resource_means",
                        acceptance_tier=state["acceptance_tier"][scene],
                    )
                    row["latency_gate_passed"] = latency_gate
                    row["outer_decision"] = decision.to_dict()
                    if decision.accepted:
                        eligible.append((row, decision))
                accepted = bool(eligible)
                if accepted:
                    chosen, decision = eligible[0]
                    params_by_scene[scene] = dict(chosen["params"])
                    incumbents[scene] = {
                        key: copy.deepcopy(chosen[key])
                        for key in ("db_qps", "p95_ms", "p99_ms", "resource_means")
                    }
                    ranges_by_scene[scene] = shrink_ranges(ranges_by_scene[scene], params_by_scene[scene], active_by_scene[scene])
                    state["accepted_rounds"][scene] += 1
                    replayed_param_keys_by_scene[scene].clear()
                else:
                    replayed_param_keys_by_scene[scene].update(
                        optimizer_params_key(row["optimized_params"])
                        for row in rankings[scene]
                    )
                convergence = advance_interval_convergence(
                    ranges=ranges_by_scene[scene],
                    active=active_by_scene[scene],
                    candidate_params=(chosen["params"] if accepted else None),
                    accepted=accepted,
                    previous_stable_rounds=state["interval_stable_rounds"][scene],
                    previous_interval=state["stable_intervals"][scene],
                )
                state["interval_stable_rounds"][scene] = convergence["stable_rounds"]
                state["stable_intervals"][scene] = convergence["stable_interval"]
                state["converged"][scene] = convergence["converged"]
                tier = advance_acceptance_tier(
                    acceptance_tier=state["acceptance_tier"][scene],
                    consecutive_failed_rounds=state["consecutive_failed_rounds"][scene],
                    accepted=accepted, downgrade_after_rounds=2,
                )
                state["acceptance_tier"][scene] = tier.acceptance_tier
                state["consecutive_failed_rounds"][scene] = tier.consecutive_failed_rounds
                state.setdefault("candidate_replay_dedup", {})[scene] = {
                    **replay_dedup[scene],
                    "retained_for_next_outer": len(
                        replayed_param_keys_by_scene[scene]
                    ),
                }
                atomic(output / f"outer_{outer_attempt:03d}" / f"{scene}.decision.json", {
                    "schema_version": "mysql_four_scene_algorithm_c_outer_decision_v1",
                    "scene": scene, "outer_attempt": outer_attempt, "accepted": accepted,
                    "evaluations": rows, "tier_progress": tier.to_dict(),
                    "accepted_rounds": state["accepted_rounds"][scene],
                    "interval_convergence": convergence,
                })
            state["outer_attempt"] = outer_attempt
            state["stage"] = "FORMAL_OUTER_RUNNING"
            atomic(state_path, state)
        state["status"] = "COMPLETE" if all(state["converged"].values()) else "NON_CONVERGED"
        state["stage"] = "FINISHED"
        state["completed_at"] = now()
        atomic(state_path, state)
        return 0 if state["status"] == "COMPLETE" else 2
    except BaseException as error:
        state["status"] = "FAILED_CLOSED"
        state["stage"] = "FAILED"
        state["failed_at"] = now()
        state["error"] = f"{type(error).__name__}: {error}"
        atomic(state_path, state)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
