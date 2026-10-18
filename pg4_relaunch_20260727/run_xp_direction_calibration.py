#!/usr/bin/env python3
"""Add independent XP/IO direction-calibration scopes.

This is a PostgreSQL pilot calibration.  It restores the frozen XP snapshot
before every point, applies all seven planner GUCs with readback, runs the
frozen warmup and stable-measurement wrapper, and emits the fresh-calibration
schema consumed by build_replay_direction_dataset.py.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
from typing import Any, Mapping


ATTEMPT = Path(
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
    "hybench_xp_compute00_59630/experience_sgd/"
    "attempt39_fresh_from_accepted3_20260726"
)
CONFIG = ATTEMPT / "config/live.json"
RESTORE = Path(
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
    "hybench_xp_compute00_59630/experience_sgd/"
    "restore_with_physical_io_reapply_attempt25.sh"
)
CONTAINER = "pg5v1_hyxp_compute00_59630_w1"
DB_LEASE_LOCK = Path("/tmp/resource_adaptive_hyxp_pg59630_db_lease.lock")
PARAMETERS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
DEFAULTS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}
PROBE_BOUNDS = {
    "seq_page_cost": (0.1, 100.0),
    "random_page_cost": (0.1, 100.0),
    "cpu_tuple_cost": (0.001, 0.1),
    "cpu_index_tuple_cost": (0.0005, 0.05),
    "cpu_operator_cost": (0.00025, 0.025),
    "parallel_setup_cost": (10.0, 10000.0),
    "parallel_tuple_cost": (0.001, 1.0),
}
PROBES = tuple(
    (
        f"{parameter}_{direction}",
        {**DEFAULTS, parameter: value},
    )
    for parameter in PARAMETERS
    for direction, value in zip(("down", "up"), PROBE_BOUNDS[parameter])
)


def run(
    command: list[str] | str,
    *,
    shell: bool = False,
    env: Mapping[str, str] | None = None,
) -> None:
    process = subprocess.Popen(
        command,
        shell=shell,
        text=True,
        env=dict(env) if env is not None else None,
        start_new_session=True,
    )
    try:
        returncode = process.wait()
    except BaseException:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        raise
    if returncode != 0:
        raise RuntimeError(f"command failed ({returncode}): {command}")


def readback_params() -> dict[str, dict[str, Any]]:
    connection = [
        "psql", "-h", "127.0.0.1", "-p", "59630",
        "-U", "postgres", "-d", "hybench_sf10x",
    ]
    environment = os.environ.copy()
    environment["PGPASSWORD"] = "postgres"
    readback = subprocess.run(
        connection
        + [
            "-AtF", "|",
            "-c",
            "SELECT name,setting,source,coalesce(sourcefile,''),pending_restart "
            "FROM pg_settings "
            "WHERE name IN ('seq_page_cost','random_page_cost',"
            "'cpu_tuple_cost','cpu_index_tuple_cost','cpu_operator_cost',"
            "'parallel_setup_cost','parallel_tuple_cost') ORDER BY name;",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    actual: dict[str, dict[str, Any]] = {}
    for line in readback.stdout.splitlines():
        fields = line.split("|")
        if len(fields) != 5:
            raise RuntimeError(f"unexpected planner GUC readback row: {line!r}")
        name, value, source, sourcefile, pending_restart = fields
        actual[name] = {
            "setting": float(value),
            "source": source,
            "sourcefile": sourcefile or None,
            "pending_restart": pending_restart == "t",
        }
    if set(actual) != set(PARAMETERS):
        raise RuntimeError(f"incomplete planner GUC readback: {sorted(actual)}")
    return actual


def assert_params(
    actual: Mapping[str, Mapping[str, Any]],
    expected: Mapping[str, float],
) -> None:
    for name in PARAMETERS:
        expected_value = float(expected[name])
        actual_value = float(actual[name]["setting"])
        if abs(actual_value - expected_value) > max(
            1e-12, abs(expected_value) * 1e-9
        ):
            raise RuntimeError(
                f"planner GUC readback mismatch: {name}={actual_value} "
                f"expected={expected_value}"
            )
        if bool(actual[name].get("pending_restart")):
            raise RuntimeError(f"planner GUC unexpectedly pending restart: {name}")


def apply_params(params: Mapping[str, float]) -> dict[str, dict[str, Any]]:
    connection = [
        "psql", "-h", "127.0.0.1", "-p", "59630",
        "-U", "postgres", "-d", "hybench_sf10x",
    ]
    environment = os.environ.copy()
    environment["PGPASSWORD"] = "postgres"
    for name in PARAMETERS:
        completed = subprocess.run(
            connection
            + [
                "-v", "ON_ERROR_STOP=1", "-Atc",
                f"ALTER SYSTEM SET {name} = '{float(params[name]):.17g}';",
            ],
            check=False,
            text=True,
            env=environment,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"failed to apply planner GUC: {name}")
    completed = subprocess.run(
        connection
        + ["-v", "ON_ERROR_STOP=1", "-Atc", "SELECT pg_reload_conf();"],
        check=False,
        text=True,
        env=environment,
    )
    if completed.returncode != 0:
        raise RuntimeError("failed to reload planner GUCs")
    actual = readback_params()
    assert_params(actual, params)
    return actual


def physical_io_readback() -> dict[str, Any]:
    container_id = subprocess.run(
        ["docker", "inspect", "-f", "{{.Id}}", CONTAINER],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    root = Path("/sys/fs/cgroup/blkio/docker-limited") / container_id
    device = "259:5"

    def read_limit(filename: str) -> int:
        rows = (root / filename).read_text(encoding="utf-8").splitlines()
        values = [
            int(line.split()[1])
            for line in rows
            if len(line.split()) == 2 and line.split()[0] == device
        ]
        if len(values) != 1:
            raise RuntimeError(
                f"missing/duplicate XP I/O cap for {device} in {filename}: {values}"
            )
        return values[0]

    result = {
        "container_id": container_id,
        "cgroup_root": str(root),
        "device": device,
        "read_bps": read_limit("blkio.throttle.read_bps_device"),
        "write_bps": read_limit("blkio.throttle.write_bps_device"),
    }
    if result["read_bps"] != 100000 or result["write_bps"] != 837500:
        raise RuntimeError(f"XP physical I/O cap readback mismatch: {result}")
    return result


def measurement(summary_path: Path, params: Mapping[str, float]) -> dict[str, Any]:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    windows = []
    with (summary_path.parent / "measurement_windows.jsonl").open(
        encoding="utf-8"
    ) as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("event") == "measurement_window":
                windows.append(row)
    recent = windows[-6:]
    if (
        summary.get("status") != "ok"
        or summary.get("stable") is not True
        or summary.get("correctness_passed") is not True
        or len(recent) != 6
    ):
        raise RuntimeError(f"unusable XP measurement: {summary_path}")
    effective = summary.get("effective_parameters") or {}
    effective_readback = {
        name: {
            "setting": float(effective[name]),
            "source": "measurement_summary",
            "sourcefile": None,
            "pending_restart": False,
        }
        for name in PARAMETERS
        if name in effective
    }
    if set(effective_readback) != set(PARAMETERS):
        raise RuntimeError(f"incomplete effective_parameters in {summary_path}")
    assert_params(effective_readback, params)

    def mean(field: str) -> float:
        return sum(float(row[field]) for row in recent) / len(recent)

    return {
        "status": "ok",
        "double_loop_measurement_usable": True,
        "workload": "hybench_xp",
        "target_resource": "io",
        "scenario": "io",
        "params": dict(params),
        "db_qps": mean("db_qps"),
        "resource_utilization": {
            "cpu": mean("cpu_utilization"),
            "memory": mean("memory_utilization"),
            "io": mean("io_utilization"),
        },
        "runtime_capability": {
            "cpu": 10.5,
            "memory": 30404000000,
            "io": 937500,
        },
        "candidate_measurement_protocol": {
            "stable": True,
            "effective_measurement_seconds": summary.get(
                "effective_measurement_seconds"
            ),
            "stop_reason": summary.get("stop_reason"),
        },
        "summary_path": str(summary_path.resolve()),
    }


def run_point(
    *,
    params: Mapping[str, float],
    points_dir: Path,
    warmup_command: str,
    workload_command: str,
) -> dict[str, Any]:
    DB_LEASE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    lease_handle = DB_LEASE_LOCK.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lease_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lease_handle.close()
        raise RuntimeError(f"XP database lane is already leased: {DB_LEASE_LOCK}") from exc
    child_env = os.environ.copy()
    child_env["HYXP_DB_LEASE_HELD"] = "1"
    try:
        run([str(RESTORE)], env=child_env)
        applied = apply_params(params)
        run(warmup_command, shell=True, env=child_env)
        pre_measurement = readback_params()
        assert_params(pre_measurement, params)
        io_readback = physical_io_readback()
        before = {
            item.resolve()
            for item in points_dir.iterdir()
            if item.is_dir()
        } if points_dir.exists() else set()
        command = shlex.split(workload_command)
        output_index = command.index("--output-root") + 1
        command[output_index] = str(points_dir)
        run(command, env=child_env)
        created = [
            item
            for item in points_dir.iterdir()
            if item.is_dir() and item.resolve() not in before
        ]
        if not created:
            raise RuntimeError("XP wrapper did not create a measurement directory")
        point_dir = max(created, key=lambda item: item.stat().st_mtime_ns)
        raw = measurement(point_dir / "summary.json", params)
        final_readback = readback_params()
        assert_params(final_readback, params)
        seal = {
            "schema_version": 1,
            "requested_parameters": dict(params),
            "applied_readback": applied,
            "pre_measurement_readback": pre_measurement,
            "post_measurement_readback": final_readback,
            "physical_io_readback": io_readback,
            "db_lease_lock": str(DB_LEASE_LOCK),
            "db_lease_held_across_restore_to_summary": True,
        }
        (point_dir / "direction_calibration_seal.json").write_text(
            json.dumps(seal, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        raw["parameter_seal"] = seal
        return raw
    finally:
        fcntl.flock(lease_handle.fileno(), fcntl.LOCK_UN)
        lease_handle.close()


def reusable_result(raw: Mapping[str, Any], params: Mapping[str, float]) -> bool:
    try:
        summary_path = Path(str(raw["summary_path"]))
        validated = measurement(summary_path, params)
        return (
            summary_path.is_file()
            and validated["double_loop_measurement_usable"] is True
            and dict(raw.get("params") or {}) == dict(params)
        )
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        return False


def append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--blocks", type=int, default=3)
    args = parser.parse_args()
    if args.blocks < 1:
        parser.error("--blocks must be at least 1")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=args.resume)
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    warmup_command = str(config["benchmark"]["warmup_cmd"])
    workload_command = str(config["benchmark"]["workload_cmd"])
    initial_params = {
        name: float(details["setting"])
        for name, details in readback_params().items()
    }

    try:
        for scope in range(1, args.blocks + 1):
            scope_dir = output / f"scope_{scope:02d}" / "hybench_xp" / "io"
            points_dir = scope_dir / "points"
            points_dir.mkdir(parents=True, exist_ok=True)
            baseline_path = scope_dir / "fresh_baseline.json"
            if baseline_path.exists() and args.resume:
                candidate = json.loads(baseline_path.read_text(encoding="utf-8"))
                if reusable_result(candidate.get("raw") or {}, DEFAULTS):
                    baseline = candidate
                else:
                    raise RuntimeError(
                        f"resume baseline failed revalidation: {baseline_path}"
                    )
            else:
                raw = run_point(
                    params=DEFAULTS,
                    points_dir=points_dir,
                    warmup_command=warmup_command,
                    workload_command=workload_command,
                )
                utilization = raw["resource_utilization"]
                if not (
                    0.85 <= utilization["io"] <= 0.90
                    and 0.30 <= utilization["cpu"] <= 0.50
                    and 0.30 <= utilization["memory"] <= 0.50
                ):
                    raise RuntimeError(
                        f"fresh XP R0 is outside the strict profile: {utilization}"
                    )
                baseline = {
                    "event": "fresh_baseline",
                    "scenario": "io",
                    "fresh_prefix_source": f"xp_direction_scope_{scope:02d}",
                    "params": dict(DEFAULTS),
                    "measurement": {
                        "db_qps": raw["db_qps"],
                        **raw["resource_utilization"],
                    },
                    "raw": raw,
                }
                baseline_path.write_text(
                    json.dumps(baseline, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

            calibration_path = scope_dir / "fresh_model_calibration.jsonl"
            completed = set()
            if calibration_path.exists():
                for line in calibration_path.read_text(encoding="utf-8").splitlines():
                    row = json.loads(line)
                    calibration_id = row.get("calibration_id")
                    expected_probe = next(
                        (
                            (label, params)
                            for label, params in PROBES
                            if calibration_id == f"scope{scope:02d}_{label}"
                        ),
                        None,
                    )
                    if expected_probe is not None and reusable_result(
                        row.get("raw_result") or {},
                        expected_probe[1],
                    ):
                        completed.add(calibration_id)
            for label, params in PROBES:
                calibration_id = f"scope{scope:02d}_{label}"
                if args.resume and calibration_id in completed:
                    continue
                raw = run_point(
                    params=params,
                    points_dir=points_dir,
                    warmup_command=warmup_command,
                    workload_command=workload_command,
                )
                append_jsonl(
                    calibration_path,
                    {
                        "event": "fresh_model_calibration_replay",
                        "calibration_id": calibration_id,
                        "phase": "coordinate_axis_direction_calibration",
                        "design": "single_coordinate_full_endpoint_batch",
                        "parameter": next(
                            name for name in PARAMETERS if label.startswith(name + "_")
                        ),
                        "direction": "decrease" if label.endswith("down") else "increase",
                        "changed_parameters": [
                            name for name in PARAMETERS if label.startswith(name + "_")
                        ],
                        "params": dict(params),
                        "status": "usable",
                        "current_run_only": True,
                        "used_for_optimizer_candidate_selection": False,
                        "raw_result": raw,
                    },
                )
    finally:
        apply_params(initial_params)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
