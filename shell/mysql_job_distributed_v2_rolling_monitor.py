#!/usr/bin/env python3
"""Continuous fail-closed JOB v2 host/client/DB isolation monitor.

Run this on node02 while a fresh R0 or real replay is active.  It writes only
newline-delimited JSON to stdout so the observer does not write the target
MySQL SSD.  Any incomplete inventory, PID/container change, affinity drift, or
foreign target-device writer terminates the monitor non-zero.
"""

from __future__ import print_function

import argparse
import json
import os
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mysql_distributed_live_readback_qualification_v1 as collector


class MonitorError(RuntimeError):
    pass


CLIENT_DEFAULTS_RE = re.compile(r"^/measurement/[a-z0-9_.-]{8,96}/mysql\.cnf$")


def canonical(value):
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )


def config_identity(row):
    return collector.sha_bytes(
        collector.canonical(
            {
                "Config": row.get("Config"),
                "HostConfig": row.get("HostConfig"),
                "Mounts": row.get("Mounts"),
                "Image": row.get("Image"),
                "Created": row.get("Created"),
            }
        )
    )


def _binding(row):
    return {
        "name": str(row.get("Name") or "").lstrip("/"),
        "container_id": row.get("Id"),
        "image_id": row.get("Image"),
        "created": row.get("Created"),
        "inspect_config_sha256": config_identity(row),
        "required_read_only_mounts": [
            {
                "source": item.get("Source"),
                "destination": item.get("Destination"),
            }
            for item in row.get("Mounts") or []
            if item.get("Type") in ("bind", "volume")
        ],
    }


def _validate_inventory(stream, major_minor):
    inventory_stream = stream[
        "process_cpu_affinity_and_device_inventory"
    ]
    rolling = inventory_stream["rolling_full_host_inventory"]
    if (
        inventory_stream.get("affinity_exact") is not True
        or rolling.get("inventory_complete") is not True
        or rolling.get("conflicts") != []
        or rolling.get("permission_failure_count") != 0
        or rolling.get("race_count") != 0
        or rolling.get("foreign_writer_count") != 0
    ):
        raise MonitorError("rolling full-host inventory failed closed")
    client_device = inventory_stream["client_cgroup_devices"].get(
        major_minor, {}
    )
    return {
        "read_bytes": int(client_device.get("read_bytes", 0)),
        "write_bytes": int(client_device.get("write_bytes", 0)),
    }


def _database_counters(client_container, defaults_file, port):
    """Sample DB counters through the exact client container only.

    The defaults file lives on the client's private tmpfs, so this deliberately
    does not call the host mysql CLI nor use the legacy local SSH tunnel.
    """
    if CLIENT_DEFAULTS_RE.fullmatch(defaults_file) is None:
        raise MonitorError("client defaults path differs")
    names = ",".join("'%s'" % name for name in collector.DB_COUNTERS)
    sql = (
        "SELECT VARIABLE_NAME,VARIABLE_VALUE FROM performance_schema."
        "global_status WHERE VARIABLE_NAME IN (%s)" % names
    )
    output = collector.run(
        [
            "docker",
            "exec",
            client_container,
            "/usr/bin/mysql",
            "--defaults-file=%s" % defaults_file,
            "--protocol=TCP",
            "--host=127.0.0.1",
            "--port=%d" % int(port),
            "--batch",
            "--skip-column-names",
            "--execute",
            sql,
        ],
        timeout=1,
        label="JOB exact-client database counter sample",
    )
    observed = {}
    for line in output.splitlines():
        pieces = line.split("\t")
        if len(pieces) == 2:
            try:
                observed[pieces[0].lower()] = int(pieces[1])
            except ValueError as exc:
                raise MonitorError("database counter is not integral") from exc
    result = {
        name: observed[name.lower()]
        for name in collector.DB_COUNTERS
        if name.lower() in observed
    }
    if set(result) != set(collector.DB_COUNTERS):
        raise MonitorError("database counter coverage is incomplete")
    return result


def run_monitor(args):
    bundle = args.bundle.resolve(strict=True)
    _, runtime = collector.verify_bundle(bundle)
    if (
        runtime.get("scenario") != "job_memory"
        or runtime.get("formal_scope") != "mysql_only"
        or runtime.get("cross_engine_comparison_prohibited") is not True
        or runtime.get("postgresql_mutation_prohibited") is not True
        or runtime["host"]["hostname"] != "node02"
        or runtime["storage"]["expected_live_major_minor"] != "259:3"
    ):
        raise MonitorError("JOB v2 runtime contract differs")
    database = collector.inspect_container(args.database_container)
    client = collector.inspect_container(args.client_container)
    database_id = database.get("Id")
    client_id = client.get("Id")
    database_pid = int((database.get("State") or {}).get("Pid") or 0)
    client_pid = int((client.get("State") or {}).get("Pid") or 0)
    if (
        database_id != args.database_container_id
        or client_id != args.client_container_id
        or database_pid != args.database_pid
        or client_pid != args.client_pid
        or config_identity(database) != args.database_inspect_sha256
        or config_identity(client) != args.client_inspect_sha256
    ):
        raise MonitorError("initial live DB/client identity differs")
    collector.validate_container_readback(database, runtime)
    collector.validate_client_container_readback(
        client,
        runtime["client_resource_envelope"],
        _binding(client),
        "/measurement",
    )
    duration = int(args.duration_seconds)
    if duration < 180 or duration > 660:
        raise MonitorError("monitor duration must be within 180..660 seconds")
    started = time.monotonic()
    prior_sampled = None
    initial_client_device = None
    for index in range(duration + 1):
        target = started + float(index)
        delay = target - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        sampled = time.monotonic()
        if prior_sampled is not None and sampled - prior_sampled > 1.5:
            raise MonitorError("rolling monitor sample gap exceeded")
        prior_sampled = sampled
        current_database = collector.inspect_container(
            args.database_container
        )
        current_client = collector.inspect_container(args.client_container)
        if (
            current_database.get("Id") != database_id
            or int((current_database.get("State") or {}).get("Pid") or 0)
            != database_pid
            or config_identity(current_database)
            != args.database_inspect_sha256
            or current_client.get("Id") != client_id
            or int((current_client.get("State") or {}).get("Pid") or 0)
            != client_pid
            or config_identity(current_client) != args.client_inspect_sha256
        ):
            raise MonitorError("live DB/client identity changed")
        stream = collector.resource_stream_sample(
            database_pid,
            client_pid,
            os.getpid(),
            runtime["storage"]["expected_live_major_minor"],
            runtime["resource_envelope"],
            {
                "cpuset_cpus":
                    runtime["client_resource_envelope"]["cpuset_cpus"],
                "cpuset_mems":
                    runtime["client_resource_envelope"]["cpuset_mems"],
            },
        )
        counters = _database_counters(
            args.client_container,
            args.client_defaults_file,
            runtime["resource_envelope"]["mysql_port"],
        )
        client_device = _validate_inventory(
            stream, runtime["storage"]["expected_live_major_minor"]
        )
        if initial_client_device is None:
            initial_client_device = client_device
        elif (
            client_device["read_bytes"]
            != initial_client_device["read_bytes"]
            or client_device["write_bytes"]
            != initial_client_device["write_bytes"]
        ):
            raise MonitorError("client cgroup accessed target MySQL device")
        value = {
            "schema_version":
                "mysql_job_distributed_v2_rolling_gate_sample_v1",
            "attempt_token": args.attempt_token,
            "point_token": args.point_token,
            "index": index,
            "epoch_ms": int(time.time() * 1000),
            "sampled_monotonic": sampled,
            "database_container_id": database_id,
            "database_pid": database_pid,
            "client_container_id": client_id,
            "client_pid": client_pid,
            "client_target_device": client_device,
            "resource_stream": stream,
            "database_counters": counters,
            "status": "PASS_SAMPLE",
            "accepted_round_authorized": False,
        }
        sys.stdout.write(
            "MYSQL_JOB_V2_ROLLING_GATE_SAMPLE_JSON=" + canonical(value)
        )
        sys.stdout.flush()
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--database-container", required=True)
    parser.add_argument("--database-container-id", required=True)
    parser.add_argument("--database-pid", type=int, required=True)
    parser.add_argument("--database-inspect-sha256", required=True)
    parser.add_argument("--client-container", required=True)
    parser.add_argument("--client-container-id", required=True)
    parser.add_argument("--client-pid", type=int, required=True)
    parser.add_argument("--client-inspect-sha256", required=True)
    parser.add_argument("--attempt-token", required=True)
    parser.add_argument("--point-token", required=True)
    parser.add_argument("--client-defaults-file", required=True)
    parser.add_argument("--duration-seconds", type=int, default=660)
    args = parser.parse_args()
    try:
        return run_monitor(args)
    except (
        MonitorError,
        collector.QualificationError,
        OSError,
        ValueError,
    ) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
