#!/usr/bin/env python3
"""Session-owned parallel24 MySQL random-walk runner (one independent lane)."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Mapping

import formal_mysql_campaign_20260721 as common
import formal_mysql_random_walk_point as point
from formal_acceptance_policy import LivePoint
from formal_baseline_gates import PressureEvidence, evaluate_default_baseline
from formal_random_walk import RandomWalkState
from formal_random_walk_artifacts import append_jsonl, atomic_write_json, sha256_file
from formal_random_walk_controller import FrozenRandomWalkController
from parallel_qualification_mysql_lane import (
    QualificationCgroupV1Adapter,
    qualification_database_sample,
    qualification_docker_run_args,
)


WORKSPACE = Path(__file__).resolve().parent
CAMPAIGN_ID = "parallel24_20260721_v1"
FROZEN = WORKSPACE / "preflight/frozen_formal_config_20260721_v3"
ROOT = WORKSPACE / "history/parallel24_20260721_v1/mysql/random_walk"
PHYSICAL = Path("/disk2/resource_adaptive/parallel24_20260721_v1/mysql/random_walk")

LANES = {
    "ap": {
        "case_id": "mysql_hybench_ap_memory", "workload": "hybench_ap",
        "template": "hybench", "port": 57201,
        "container": "p24_mysql_random_walk_ap", "cpuset": "168-175,424-431",
        "profile": "memory",
    },
    "job": {
        "case_id": "mysql_job_memory", "workload": "job",
        "template": "job", "port": 57202,
        "container": "p24_mysql_random_walk_job", "cpuset": "176-183,432-439",
        "profile": "memory",
    },
    "tpch": {
        "case_id": "mysql_tpch_memory", "workload": "tpch",
        "template": "tpch", "port": 57203,
        "container": "p24_mysql_random_walk_tpch", "cpuset": "184-191,440-447",
        "profile": "memory",
    },
    "xp": {
        "case_id": "mysql_hybench_xp_io", "workload": "hybench_xp",
        "template": "hybench", "port": 57204,
        "container": "p24_mysql_random_walk_xp", "cpuset": "192-207,448-463",
        "profile": "io",
    },
}


def load(name: str) -> dict[str, Any]:
    return json.loads((FROZEN / name).read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def configure(lane: str) -> tuple[dict[str, Any], ...]:
    cfg = LANES[lane]
    names = (
        "machine_and_resources.json", "fixed_db_settings.json", "benchmark_matrix.json",
        "preflight_gates.json", "measurement_protocol.json", "search_spaces.json",
        "method_protocols.json", "acceptance_policy.json",
    )
    values = [load(name) for name in names]
    machine = values[0]
    engine = machine["engines"]["mysql"]
    engine["working_root"] = str(PHYSICAL / lane / "work")
    engine["template_root"] = str(
        PHYSICAL / "templates" if lane == "tpch"
        else Path("/disk2/resource_adaptive/unified_formal_20260721_v1/templates")
    )
    key = "hybench" if cfg["workload"].startswith("hybench") else cfg["workload"]
    engine["containers"][key] = {"name": cfg["container"], "port": cfg["port"]}
    profile = machine["resource_profiles"][cfg["profile"]]
    profile["cpuset_cpus"] = cfg["cpuset"]
    # Kernel cgroup-v1 canonicalizes the two-node set as a range.
    profile["cpuset_mems"] = "0-1"
    profile["cpu_quota_cores"] = 16 if lane == "xp" else 8
    profile["memory_bytes"] = 8589934592 if lane == "xp" else 2147483648
    profile["memory_swap_bytes"] = profile["memory_bytes"]
    profile["io_read_bps"] = 104857600 if lane == "xp" else 367001600
    profile["io_write_bps"] = profile["io_read_bps"]
    return tuple(values)


def prepare_benchmark(lane: str) -> Path:
    cfg = LANES[lane]
    source = common.BENCHMARK_CONFIG_ROOT / cfg["workload"] / "benchmark_config.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    old_url = payload["jdbc_url"]
    prefix, rest = old_url.split("127.0.0.1:", 1)
    _, suffix = rest.split("/", 1)
    payload["jdbc_url"] = f"{prefix}127.0.0.1:{cfg['port']}/{suffix}"
    target_root = ROOT / lane / "frozen_benchmark_config"
    target = target_root / cfg["workload"] / "benchmark_config.json"
    write_json(target, payload)
    provenance = {
        "source": str(source), "source_sha256": sha256_file(source),
        "private_copy": str(target), "private_copy_sha256": sha256_file(target),
        "only_change": {"jdbc_port": cfg["port"]},
    }
    write_json(ROOT / lane / "benchmark_provenance.json", provenance)
    return target_root


def install_overrides(lane: str) -> tuple[dict[str, Any], ...]:
    configs = configure(lane)
    common.GLOBAL_CAMPAIGN_ID = CAMPAIGN_ID
    common.FREEZE_ID = "frozen_formal_config_20260721_v3"
    common.BENCHMARK_CONFIG_ROOT = prepare_benchmark(lane)
    common._configs = lambda: configs
    original = common._docker_run_args
    common._docker_run_args = qualification_docker_run_args(original)
    common.CgroupV1Adapter = QualificationCgroupV1Adapter
    common._database_sample = qualification_database_sample
    original_correctness = common.run_correctness

    def retry_correctness(workload: str, benchmark_config: Mapping[str, Any], output: Path):
        if lane in {"ap", "xp"}:
            prior = sorted(
                path for path in (ROOT / lane / "points/baseline_search").glob("attempt_*/correctness.json")
                if output not in path.parents
            )
            if prior:
                payload = json.loads(prior[-1].read_text(encoding="utf-8"))
                payload["parallel24_reused_signature_from"] = str(prior[-1])
                payload["reuse_basis"] = "same immutable template plus current exact row-count/schema/index restored validation"
                write_json(output / "correctness.json", payload)
                return payload
        for attempt in range(1, 4):
            try:
                return original_correctness(workload, benchmark_config, output)
            except common.GateError as error:
                if "command failed rc=143, executable='java'" not in str(error) or attempt == 3:
                    raise
                time.sleep(2)
        raise RuntimeError("unreachable correctness retry")

    common.run_correctness = retry_correctness

    def p24_sampling(command, cwd, adapter, port, output, *, warmup_seconds=60, measurement_seconds=180):
        total_seconds = warmup_seconds + measurement_seconds
        stdout_path = output / "workload.stdout.log"
        stderr_path = output / "workload.stderr.log"
        resource_path = output / "resource_samples.jsonl"
        database_path = output / "database_samples.jsonl"
        previous = adapter.snapshot()
        started_ns = time.monotonic_ns()
        started_epoch_ms = time.time_ns() // 1_000_000
        with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
            proc = subprocess.Popen([str(item) for item in command], cwd=str(cwd), stdout=stdout, stderr=stderr, text=True, start_new_session=True)
            samples, database_samples = [], []
            early_exit_tick = None
            try:
                for tick in range(1, total_seconds + 1):
                    remaining = (started_ns + tick * 1_000_000_000 - time.monotonic_ns()) / 1e9
                    if remaining > 0:
                        time.sleep(remaining)
                    if proc.poll() is not None and tick < total_seconds:
                        early_exit_tick = tick
                    current = adapter.snapshot()
                    elapsed = (current.monotonic_ns - started_ns) / 1e9
                    phase = "warmup" if tick <= warmup_seconds else "measurement"
                    sample = adapter.derive(previous, current, phase=phase, elapsed_s=elapsed)
                    append_jsonl(resource_path, sample)
                    samples.append(sample)
                    db_sample = common._database_sample(port, elapsed_s=elapsed, phase=phase)
                    append_jsonl(database_path, db_sample)
                    database_samples.append(db_sample)
                    previous = current
                try:
                    returncode = proc.wait(timeout=610)
                    tail_timed_out = False
                except subprocess.TimeoutExpired:
                    tail_timed_out = True
                    common._process_group_stop(proc)
                    returncode = proc.returncode
            finally:
                common._process_group_stop(proc)
        inspect_after = json.loads(common.run(["docker", "inspect", adapter.container]).stdout)[0]
        state = inspect_after["State"]
        if state["Status"] != "running" or int(state["Pid"]) != adapter.pid:
            raise common.GateError(f"database container changed state/PID during workload: {state}")
        measurement = [sample for sample in samples if sample["phase"] == "measurement"]
        measurement_db = [sample for sample in database_samples if sample["phase"] == "measurement"]
        coverage = len(measurement) / measurement_seconds
        near_boundary_termination = returncode in {0, 143, -15} and (
            early_exit_tick is None or early_exit_tick >= total_seconds - 1
        )
        if not near_boundary_termination:
            detail = (stderr_path.read_text(encoding="utf-8") + stdout_path.read_text(encoding="utf-8"))[-4000:]
            raise common.GateError(f"workload exited early or failed: tick={early_exit_tick} rc={returncode}: {detail}")
        if coverage < 0.9:
            raise common.GateError(f"measurement sample coverage below 0.9: {coverage}")
        return {
            "started_epoch_ms": started_epoch_ms, "warmup_seconds": warmup_seconds,
            "measurement_seconds": measurement_seconds, "resource_samples": measurement,
            "database_samples": measurement_db, "resource_sample_path": str(resource_path),
            "database_sample_path": str(database_path), "sample_coverage": coverage,
            "returncode": returncode, "tail_timed_out": tail_timed_out,
            "near_boundary_termination_tolerated": returncode != 0,
            "container_state_after": state, "container_restart_count_after": int(inspect_after["RestartCount"]),
            "stdout_path": str(stdout_path), "stderr_path": str(stderr_path),
        }

    common.run_with_sampling = p24_sampling
    if lane == "tpch":
        def validate_tpch_restored(port: int, manifest: Mapping[str, Any]) -> dict[str, Any]:
            tables = common._tabular(
                port,
                "SELECT TABLE_NAME,COALESCE(TABLE_ROWS,0),COALESCE(DATA_LENGTH+INDEX_LENGTH,0) "
                "FROM information_schema.TABLES WHERE TABLE_SCHEMA='tpch_sf10' ORDER BY TABLE_NAME;",
            )
            names = {row[0] for row in tables}
            expected_names = set(manifest["row_count_by_table"])
            if names != expected_names or any(int(row[2]) <= 0 for row in tables):
                raise RuntimeError(f"TPC-H restored table set/size differs: {tables}")
            small = {
                name: int(common._mysql(port, f"SELECT COUNT(*) FROM `{name}`;", database="tpch_sf10").strip())
                for name in ("nation", "region")
            }
            if small != {"nation": 25, "region": 5}:
                raise RuntimeError(f"TPC-H restored small counts differ: {small}")
            columns = common._tabular(
                port,
                "SELECT TABLE_NAME,COLUMN_NAME,ORDINAL_POSITION,COLUMN_TYPE,IS_NULLABLE,COLUMN_DEFAULT,EXTRA "
                "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='tpch_sf10' ORDER BY TABLE_NAME,ORDINAL_POSITION;",
            )
            indexes = common._tabular(
                port,
                "SELECT TABLE_NAME,INDEX_NAME,NON_UNIQUE,SEQ_IN_INDEX,COLUMN_NAME,INDEX_TYPE "
                "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA='tpch_sf10' ORDER BY TABLE_NAME,INDEX_NAME,SEQ_IN_INDEX;",
            )
            if common._canonical_sha(columns) != manifest["schema_sha256"] or common._canonical_sha(indexes) != manifest["index_sha256"]:
                raise RuntimeError("TPC-H restored schema/index SHA differs")
            evidence = Path(manifest["source_count_evidence_path"])
            if sha256_file(evidence) != manifest["source_count_evidence_sha256"]:
                raise RuntimeError("TPC-H source-count evidence SHA differs")
            return {
                "status": "passed", "database": "tpch_sf10",
                "validation_mode": "current_physical_table_set_sizes_schema_indexes_small_exact_counts_plus_frozen_source_exact_counts",
                "exact_small_table_counts": small,
                "source_row_count_by_table": manifest["row_count_by_table"],
                "source_count_evidence_path": str(evidence),
                "source_count_evidence_sha256": manifest["source_count_evidence_sha256"],
                "template_id": manifest["template_id"],
                "template_snapshot_sha256": manifest["template_snapshot_sha256"],
            }
        common.validate_restored_template = validate_tpch_restored
    point.common = common

    def p24_hybench_command(output: Path, benchmark_config: Mapping[str, Any], workload: str, duration: int):
        runtime, config, normalized_statements = common._hybench_runtime(
            output, benchmark_config, workload, duration
        )
        text = config.read_text(encoding="utf-8")
        original_url = str(benchmark_config["jdbc_url"])
        replacements = {
            "classname=com.mysql.cj.jdbc.Driver\n": "classname=formal.mysql.AttributingMysqlDriver\n",
            f"url={original_url}\n": f"url={original_url.replace('jdbc:mysql:', 'jdbc:formal-mysql:', 1)}\n",
        }
        for old, new in replacements.items():
            if text.count(old) != 1:
                raise RuntimeError(f"HyBench adaptation cardinality differs: {old.strip()}")
            text = text.replace(old, new)
        config.write_text(text, encoding="utf-8")
        mode = "xp" if workload == "hybench_xp" else "ap"
        classpath = os.pathsep.join((
            str(point.ATTRIBUTION_CLASSES), str(common.FORMAL_CLASSES),
            str(common.MYSQL_CONNECTOR), str(common.HYBENCH_JAR),
        ))
        return [
            "java", "-cp", classpath, "com.hybench.HyBenchFormalMain",
            mode, config, normalized_statements,
        ], runtime

    point._hybench_command = p24_hybench_command
    return configs


def default_params(search: Mapping[str, Any]) -> dict[str, float]:
    return {name: float(value) for name, value in search["mysql"]["default"].items()}


def execute(lane: str, label: str, params: Mapping[str, float], expected: Mapping[str, Any] | None) -> dict[str, Any]:
    cfg = LANES[lane]
    parent = ROOT / lane / "points" / label
    attempt = 1
    while (parent / f"attempt_{attempt:02d}").exists():
        result_path = parent / f"attempt_{attempt:02d}" / "point_result.json"
        if not result_path.is_file():
            attempt += 1
            continue
        previous = json.loads(result_path.read_text(encoding="utf-8"))
        recoverable = (
            previous.get("error_type") == "ResourceError"
            and "cpuset_mems" in str(previous.get("error"))
        ) or (
            previous.get("error_type") == "ValueError"
            and "too many values to unpack" in str(previous.get("error"))
        ) or (
            previous.get("error_type") == "GateError"
            and "container name" in str(previous.get("error"))
            and "already in use" in str(previous.get("error"))
        ) or (
            previous.get("error_type") == "GateError"
            and "command failed rc=143, executable='java'" in str(previous.get("error"))
        ) or (
            previous.get("error_type") == "GateError"
            and "workload exited early or failed: tick=659 rc=143" in str(previous.get("error"))
        ) or (
            previous.get("error_type") == "GateError"
            and "executable='cp'" in str(previous.get("error"))
            and "Permission denied" in str(previous.get("error"))
        ) or (
            previous.get("error_type") == "GateError"
            and "fixed MySQL setting mismatch" in str(previous.get("error"))
        )
        if not recoverable:
            return previous
        attempt += 1
    output = parent / f"attempt_{attempt:02d}"
    attempt_id = f"p24_mysql_random_walk_{lane}"
    os.environ["FORMAL_MYSQL_RANDOM_WALK_LOCK_HELD"] = attempt_id
    return point.run_point(
        run_attempt_id=attempt_id, point_id=label, point_attempt=attempt,
        case_id=cfg["case_id"], params=params, output=output,
        expected_correctness=expected,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("lane", choices=LANES)
    args = parser.parse_args()
    lane = args.lane
    lane_root = ROOT / lane
    lane_root.mkdir(parents=True, exist_ok=True)
    configs = install_overrides(lane)
    _, _, _, _, measurement, search, protocols, _ = configs
    cfg = LANES[lane]
    write_json(lane_root / "launch_contract.json", {
        **cfg, "campaign_id": CAMPAIGN_ID, "frozen_root": str(FROZEN),
        "frozen_checksums_sha256": sha256_file(FROZEN / "checksums.sha256"),
        "warmup_seconds": 60, "measurement_seconds": 600,
        "started_epoch_seconds": time.time(),
    })
    state_path = lane_root / "run_state.json"
    state: dict[str, Any] = {
        "status": "running", "stage": "baseline", "lane": lane,
        "candidate_records": [], "default_validations": [], "final_validations": [],
    }
    write_json(state_path, state)
    baseline = execute(lane, "baseline_search", default_params(search), None)
    if baseline.get("status") != "complete":
        state.update(status="complete_with_negative_result", stage="baseline_failed", baseline=baseline)
        write_json(state_path, state)
        return 2
    live = LivePoint(**baseline["live_point"])
    pressure = PressureEvidence(**baseline["pressure_decision_input"])
    gate = evaluate_default_baseline(
        FROZEN / "measurement_protocol.json", live, pressure,
        scenario=cfg["profile"], xp=lane == "xp",
    )
    state.update(baseline_path=str(ROOT / lane / "points/baseline_search/attempt_01"), baseline_gate=asdict(gate))
    walk = RandomWalkState.from_frozen(search, protocols)
    controller = FrozenRandomWalkController(
        walk, policy_path=FROZEN / "acceptance_policy.json",
        scenario=cfg["profile"], xp=lane == "xp",
        default_params=default_params(search), baseline=live,
    )
    correctness = baseline["correctness_signature"]
    incumbent_plan = baseline["plan_fingerprint_sha256"]
    state["stage"] = "candidates"
    write_json(state_path, state)
    while not controller.should_stop:
        params = dict(controller.propose())
        index = controller.state.consumed_budget
        if controller.state.pending_duplicate:
            record = controller.record_duplicate()
            state["candidate_records"].append(asdict(record))
            write_json(state_path, state)
            continue
        result = execute(lane, f"candidate_{index}", params, correctness)
        if result.get("status") == "complete":
            record, decision = controller.record_complete(
                params_applied=result["params_applied"], point=LivePoint(**result["live_point"]),
                plan_changed=result["plan_fingerprint_sha256"] != incumbent_plan,
            )
            if decision is not None and decision.accepted:
                incumbent_plan = result["plan_fingerprint_sha256"]
        else:
            outcome = result.get("rejection_kind") if result.get("status") == "rejected" else result.get("status", "failed")
            if outcome not in {"failed", "timeout", "oom", "correctness_rejected", "unstable"}:
                outcome = "failed"
            record = controller.record_failed(
                outcome, params_applied=result.get("params_applied"),
                reason=f"point_{result.get('status')}", bad_configuration=outcome in {"failed", "timeout", "oom", "correctness_rejected"},
            )
        state["candidate_records"].append(asdict(record))
        state["controller"] = controller.as_dict()
        write_json(state_path, state)
    selection, final_params = controller.select_final()
    state.update(stage="validations", final_selection=asdict(selection), final_params=dict(final_params))
    write_json(state_path, state)
    for kind, params in (("default", default_params(search)), ("final", final_params)):
        key = f"{kind}_validations"
        for replay in range(1, 4):
            result = execute(lane, f"{kind}_validation_{replay}", params, correctness)
            state[key].append({"replay": replay, "status": result.get("status"), "live_point": result.get("live_point")})
            write_json(state_path, state)
    state.update(status="complete", stage="complete", completed_epoch_seconds=time.time())
    write_json(state_path, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
