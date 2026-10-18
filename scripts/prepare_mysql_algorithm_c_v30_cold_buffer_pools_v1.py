#!/usr/bin/env python3
"""Evict memory-scene InnoDB pages online and restore frozen pool sizes."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import apply_mysql_algorithm_c_paper_resource_v22 as base
from scripts import apply_mysql_algorithm_c_paper_resource_v30_inplace_v1 as hot


OUTPUT = hot.OUTPUT / "buffer_pool_cold_preparation.json"
WORKERS = ("job_w1", "job_w2", "tpch_w1", "tpch_w2")
EVICTOR_SOURCE = ROOT / "scripts/drop_mysql_datadir_page_cache_v1.c"
EVICTOR_BINARY = Path(f"/tmp/mysql_algorithm_c_page_cache_evictor_{os.getpid()}")
CONTAINER_EVICTOR = "/tmp/mysql_algorithm_c_page_cache_evictor_v1"


def sql(container: str, statement: str, *, timeout: int = 90) -> str:
    return base.run([
        "docker", "exec", container, "mysql",
        "--defaults-file=/run/ra-credentials/selected.cnf", "-Nse", statement,
    ], timeout=timeout).stdout.strip()


def state(container: str) -> dict[str, int]:
    parts = sql(container, (
        "SELECT @@global.innodb_buffer_pool_size, "
        "@@global.innodb_buffer_pool_chunk_size, "
        "@@global.innodb_buffer_pool_instances; "
        "SHOW GLOBAL STATUS WHERE Variable_name IN "
        "('Innodb_buffer_pool_resize_status_code',"
        "'Innodb_buffer_pool_pages_total','Innodb_buffer_pool_pages_data')"
    )).splitlines()
    if len(parts) != 4:
        raise RuntimeError(f"{container} buffer pool readback cardinality differs")
    size, chunk, instances = map(int, parts[0].split("\t"))
    status = {name.lower(): int(value) for name, value in (part.split("\t") for part in parts[1:])}
    return {
        "size": size, "chunk_size": chunk, "instances": instances,
        "resize_status_code": status["innodb_buffer_pool_resize_status_code"],
        "pages_total": status["innodb_buffer_pool_pages_total"],
        "pages_data": status["innodb_buffer_pool_pages_data"],
    }


def idle(container: str) -> None:
    count = int(sql(container, (
        "SELECT COUNT(*) FROM information_schema.PROCESSLIST "
        "WHERE ID<>CONNECTION_ID() "
        "AND USER NOT IN ('event_scheduler','system user')"
    )))
    if count != 0:
        raise RuntimeError(f"{container} has {count} external SQL sessions")


def wait_size(container: str, target: int, *, maximum_seconds: float = 600.0) -> dict[str, int]:
    deadline = time.monotonic() + maximum_seconds
    while time.monotonic() < deadline:
        observed = state(container)
        if (
            observed["size"] == target
            and observed["resize_status_code"] == 0
            and abs(observed["pages_total"] * 16_384 - target) <= 134_217_728
        ):
            return observed
        time.sleep(5)
    raise TimeoutError(f"{container} buffer pool resize did not finish")


def build_evictor() -> None:
    base.run([
        "gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-std=c11",
        "-o", str(EVICTOR_BINARY), str(EVICTOR_SOURCE),
    ], timeout=120)


def evict_datadir_page_cache(container: str) -> dict[str, int]:
    base.run(["docker", "cp", str(EVICTOR_BINARY), f"{container}:{CONTAINER_EVICTOR}"],
             timeout=120)
    try:
        raw = base.run([
            "docker", "exec", container, CONTAINER_EVICTOR, "/var/lib/mysql",
        ], timeout=600).stdout.strip()
        value = json.loads(raw)
        if (
            int(value.get("regular_files") or 0) <= 0
            or int(value.get("advised_bytes") or 0) <= 0
            or int(value.get("failed_files") or 0) != 0
        ):
            raise RuntimeError(f"{container} datadir page-cache eviction differs: {value}")
        return {key: int(value[key]) for key in (
            "regular_files", "advised_bytes", "skipped_files", "failed_files",
        )}
    finally:
        base.run(["docker", "exec", container, "rm", "-f", CONTAINER_EVICTOR],
                 timeout=90)


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError("fresh v30 buffer pool preparation required")
    build_evictor()
    application_path = hot.OUTPUT / "application_manifest.json"
    application = json.loads(application_path.read_text())
    freeze = json.loads(hot.NEW.read_text())
    frozen = {str(row["worker_id"]): dict(row) for row in freeze["workers"]}
    if (
        application.get("status") != "PASS"
        or application.get("new_freeze", {}).get("sha256") != base.sha(hot.NEW)
        or set(WORKERS) - set(frozen)
    ):
        raise RuntimeError("v30 effective resource application differs")
    record: dict[str, Any] = {
        "schema_version": "mysql_algorithm_c_v30_cold_buffer_pool_preparation_v1",
        "status": "RUNNING", "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "resource_freeze_path": str(hot.NEW), "resource_freeze_sha256": base.sha(hot.NEW),
        "application_path": str(application_path),
        "application_sha256": base.sha(application_path),
        "page_cache_evictor_source_path": str(EVICTOR_SOURCE),
        "page_cache_evictor_source_sha256": base.sha(EVICTOR_SOURCE),
        "page_cache_evictor_binary_sha256": base.sha(EVICTOR_BINARY),
        "page_cache_scope": "only_each_idle_mysql_worker_/var/lib/mysql_regular_files",
        "host_global_page_cache_drop_performed": False,
        "container_stop_restart_or_replacement_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "workers": {},
    }
    base.atomic(OUTPUT, record)
    try:
        for worker_id in WORKERS:
            row = frozen[worker_id]
            container = str(row["container"])
            idle(container)
            before = state(container)
            full = int(row["innodb_buffer_pool_bytes"])
            minimum = before["chunk_size"] * before["instances"]
            if before["size"] != full or before["resize_status_code"] != 0 or not 0 < minimum < full:
                raise RuntimeError(f"{worker_id} frozen buffer pool or resize state differs")
            worker_record = {
                "status": "STARTED", "before": before,
                "temporary_minimum_bytes": minimum,
                "frozen_size_bytes": full,
                "external_mysql_sessions_before": 0,
            }
            record["workers"][worker_id] = worker_record
            base.atomic(OUTPUT, record)
            try:
                sql(container, f"SET GLOBAL innodb_buffer_pool_size={minimum}", timeout=120)
                worker_record["status"] = "SHRINKING"
                base.atomic(OUTPUT, record)
                after_shrink = wait_size(container, minimum)
                worker_record["after_shrink"] = after_shrink
                worker_record["status"] = "EVICTING_DATADIR_PAGE_CACHE"
                base.atomic(OUTPUT, record)
                worker_record["datadir_page_cache_eviction"] = (
                    evict_datadir_page_cache(container)
                )
                worker_record["status"] = "RESTORING"
                base.atomic(OUTPUT, record)
                sql(container, f"SET GLOBAL innodb_buffer_pool_size={full}", timeout=120)
                after_restore = wait_size(container, full)
                idle(container)
                worker_record.update({
                    "status": "PASS", "after_restore": after_restore,
                    "engine_internal_fixed_setting_restored": True,
                    "external_mysql_sessions_after": 0,
                })
                base.atomic(OUTPUT, record)
            except BaseException:
                try:
                    if state(container)["size"] != full:
                        sql(container, f"SET GLOBAL innodb_buffer_pool_size={full}", timeout=120)
                    worker_record["best_effort_restore"] = wait_size(container, full)
                except BaseException as restore_error:
                    worker_record["restore_error"] = f"{type(restore_error).__name__}: {restore_error}"
                base.atomic(OUTPUT, record)
                raise
        record["status"] = "PASS"
        record["completed_at"] = datetime.now().astimezone().isoformat()
        base.atomic(OUTPUT, record)
        print(OUTPUT)
        return 0
    except BaseException as error:
        record["status"] = "FAILED_CLOSED"
        record["error"] = f"{type(error).__name__}: {error}"
        base.atomic(OUTPUT, record)
        raise
    finally:
        EVICTOR_BINARY.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
