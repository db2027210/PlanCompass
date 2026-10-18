#!/usr/bin/env python3
"""Fresh frozen-template container lifecycle for JOB/Memory candidates."""

from __future__ import annotations

from contextlib import contextmanager
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, Iterator, Tuple


WORKSPACE = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02/workspace_attempt7"
)
LANE_ROOT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02"
)
LOCK_PATH = Path("/tmp/resource_adaptive_job_pg64311_algorithm_c.lock")
MEMORY_BYTES = 3_125_000_000


def _atomic_json(path: Path, value: Dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _runtime() -> Tuple[Any, Any]:
    if str(WORKSPACE) not in sys.path:
        sys.path.insert(0, str(WORKSPACE))
    os.environ["RA_PG_JDBC_JAR"] = str(
        LANE_ROOT / "assets/postgresql-42.7.4.jar"
    )
    os.environ["RA_PG_FROZEN_ROOT"] = str(
        LANE_ROOT / "bundle/attempt8_frozen"
    )
    os.environ["RA_PG_UNPRIVILEGED_RUNTIME"] = "1"
    os.environ["RA_PG_SKIP_DIRECT_CGROUP_WRITE"] = "1"

    import formal_pg_engine as engine
    import formal_pg_point_runner as runner

    os.environ["PGPASSWORD"] = engine.PG_PASSWORD
    original_machine_config = engine.machine_config

    def corrected_machine_config() -> Dict[str, Any]:
        value = copy.deepcopy(original_machine_config())
        for profiles in (
            value["resource_profiles"],
            value["engine_resource_profiles"]["postgresql"],
        ):
            profile = profiles["memory"]
            profile["memory_bytes"] = MEMORY_BYTES
            profile["memory_swap_bytes"] = MEMORY_BYTES
            profile["cpuset_cpus"] = "18-23,54-59"
        postgres = value["engines"]["postgresql"]
        postgres["mount_root"] = "/mnt/nvme2n1"
        postgres["io_device_source"] = "/dev/nvme2n1"
        postgres["observed_device"] = "259:0"
        postgres["credential_file"] = str(
            LANE_ROOT
            / "history/job/outer/attempt_8e_codex_sealed_r0_20260727/runtime.pgpass"
        )
        postgres["template_root"] = str(LANE_ROOT / "template")
        postgres["working_root"] = str(LANE_ROOT / "runtime")
        postgres["containers"]["job"] = {
            "name": "ra_pg5_job_node02_v1_codex_r0",
            "port": 64311,
        }
        return value

    def lane_template(kind: str) -> Dict[str, Any]:
        if kind != "job":
            raise RuntimeError(f"unexpected template kind: {kind}")
        template = LANE_ROOT / "template/job"
        manifest_path = template / "template_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["resolved_pgdata"] = str(template / "pgdata")
        manifest["resolved_template_manifest_sha256"] = hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest()
        return manifest

    engine.machine_config = corrected_machine_config
    runner.machine_config = corrected_machine_config
    engine.runtime_pg_password = lambda kind: engine.PG_PASSWORD
    runner.validate_template = lane_template
    return engine, runner


@contextmanager
def fresh_container(point_dir: Path) -> Iterator[Dict[str, Any]]:
    """Hold the dedicated lane through restore, measurement, and cleanup."""
    lock_handle = LOCK_PATH.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock_handle.close()
        raise RuntimeError(f"JOB lane is already leased: {LOCK_PATH}") from exc

    token = f"algorithm_c_{point_dir.name}_{os.getpid()}"
    engine: Any = None
    runner: Any = None
    restore: Dict[str, Any] | None = None
    try:
        engine, runner = _runtime()
        restore = runner.restore_overlay("job", token)
        container = engine.start_container(
            "job",
            Path(restore["merged"]),
            "memory",
            cost_parameters=None,
        )
        engine.prepare_copied_database("job", container["name"])
        evidence = {"restore": restore, "container": container}
        _atomic_json(point_dir / "fresh_restore.json", evidence)
        yield evidence
    finally:
        cleanup: Dict[str, Any] = {"cleanup_complete": False}
        try:
            if engine is not None:
                engine.remove_stopped_container("ra_pg5_job_node02_v1_codex_r0")
            if runner is not None and restore is not None:
                cleanup = runner.cleanup_overlay("job", restore)
        finally:
            try:
                _atomic_json(point_dir / "fresh_cleanup.json", cleanup)
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
                lock_handle.close()
