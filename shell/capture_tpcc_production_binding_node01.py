#!/usr/bin/env python3
"""Run one exact production BenchBase TPC-C capture on node01."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from mysql_cost_adapter import MySQLCLIClient
from shell.manual_mysql_sgd_r0_campaign import Campaign, LANES, MYSQL, ROOT, BENCHBASE_JAR


RUN_ID = "bindcap_tpcc_node01_20260726T000000Z"
OUTPUT = ROOT / "history/mysql_sgd_algorithm_c/tpcc_cpu/production_binding_capture"
REMOTE_LOG = "/var/lib/mysql/tpcc_production_binding_capture_general.log"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=False)
    campaign = Campaign(
        LANES["tpcc"], OUTPUT / "campaign", RUN_ID,
        measurement_seconds=600, minimum_measurement_seconds=180,
    )
    client = None
    session = None
    log_copied = False
    try:
        campaign.output.mkdir(parents=True, exist_ok=False)
        campaign.prepare_remote()
        campaign.start_tunnel()
        client = MySQLCLIClient({
            "mysql_binary": str(MYSQL),
            "defaults_extra_file": str(campaign.output / "private/mysql.cnf"),
            "defaults_file_only": True,
            "host": "127.0.0.1",
            "port": campaign.lane.remote_port,
            "user": "root",
            "database": campaign.lane.database,
            "query_timeout_sec": 60,
        })
        client.execute("SET GLOBAL log_output='FILE'")
        client.execute(f"SET GLOBAL general_log_file='{REMOTE_LOG}'")
        client.execute("SET GLOBAL general_log=ON")
        atomic_json(OUTPUT / "capture_gate.json", {
            "schema_version": "tpcc_production_binding_capture_gate_v1",
            "status": "GENERAL_LOG_ENABLED",
            "host": campaign.lane.host,
            "hostname": campaign.lane.hostname,
            "benchmark": "tpcc",
            "jar": str(BENCHBASE_JAR),
            "jar_sha256": sha256(BENCHBASE_JAR),
            "remote_log": REMOTE_LOG,
            "warmup_seconds": 60,
            "measurement_seconds": 180,
            "formal_algorithm_c_accepted_round": False,
        })
        campaign.start_sampler()
        session = campaign.make_session()
        campaign.session = session
        session.start(warmup_seconds=60, maximum_measurement_seconds=600)
        campaign.wait_initial_marker()
        session.wait_until_measurement(180)
        final = session.stop(180)
        client.execute("SET GLOBAL general_log=OFF")
        remote_copy = f"{campaign.remote_root}/production_general.log"
        campaign.remote(f"docker cp {campaign.container}:{REMOTE_LOG} {remote_copy}", timeout=120)
        local_copy = OUTPUT / "production_general.log"
        result = subprocess.run(
            ["scp", "-o", "BatchMode=yes", f"{campaign.lane.host}:{remote_copy}", str(local_copy)],
            text=True, capture_output=True, timeout=180, check=True,
        )
        del result
        log_copied = True
        atomic_json(OUTPUT / "capture_result.json", {
            "schema_version": "tpcc_production_binding_capture_result_v1",
            "status": "RAW_GENERAL_LOG_CAPTURED",
            "run_id": RUN_ID,
            "host": campaign.lane.host,
            "hostname": campaign.lane.hostname,
            "benchmark": "tpcc",
            "production_jar": str(BENCHBASE_JAR),
            "production_jar_sha256": sha256(BENCHBASE_JAR),
            "warmup_seconds": 60,
            "measurement_seconds": 180,
            "session_final": final,
            "raw_general_log": str(local_copy),
            "raw_general_log_sha256": sha256(local_copy),
            "actual_bindings_status": "PENDING_RAW_LOG_PARSE_AND_EXPLAIN",
            "formal_algorithm_c_accepted_round": False,
        })
        return 0
    finally:
        if client is not None:
            try:
                client.execute("SET GLOBAL general_log=OFF")
            except BaseException:
                pass
        if session is not None and getattr(session, "process", None) is not None:
            try:
                session.stop(180)
            except BaseException:
                pass
        campaign.cleanup()
        if not log_copied:
            atomic_json(OUTPUT / "capture_failure.json", {
                "schema_version": "tpcc_production_binding_capture_failure_v1",
                "status": "CAPTURE_FAILED_OR_NOT_SEALED",
                "run_id": RUN_ID,
                "formal_algorithm_c_accepted_round": False,
            })


if __name__ == "__main__":
    raise SystemExit(main())
