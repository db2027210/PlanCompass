"""Fail-closed POSIX lock shared by the JOB and TPC-H disk2 lanes."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
from typing import Any
import uuid


LOCK_PATH = Path(
    "/home/anonymous/program/resource_adaptive_mysql/history/mysql_gradient_debug/"
    "host_control/mysql_disk2_job_tpch.lock"
)
JOURNAL_PATH = Path(
    "/home/anonymous/program/resource_adaptive_mysql/history/mysql_gradient_debug/"
    "host_control/mysql_disk2_job_tpch_lease_journal.jsonl"
)
LANE_ID = "mysql_disk2_job_tpch_serial_v1"
SCENARIOS = frozenset({"job_memory", "tpch_memory"})


class Disk2LaneError(RuntimeError):
    pass


class Disk2SharedLane(AbstractContextManager["Disk2SharedLane"]):
    def __init__(
        self,
        scenario: str,
        *,
        lock_path: Path = LOCK_PATH,
        journal_path: Path = JOURNAL_PATH,
    ) -> None:
        if scenario not in SCENARIOS:
            raise Disk2LaneError(f"unsupported disk2 lane member: {scenario}")
        for label, path in (("lock", lock_path), ("journal", journal_path)):
            if not path.is_absolute() or path == Path("/tmp") or Path("/tmp") in path.parents:
                raise Disk2LaneError(f"{label} path must be absolute outside /tmp")
            if path.exists() and path.is_symlink():
                raise Disk2LaneError(f"{label} path must not be a symlink")
        self.scenario = scenario
        self.lock_path = lock_path
        self.journal_path = journal_path
        self.token = uuid.uuid4().hex
        self._descriptor: int | None = None

    def _append(self, event: str, **fields: Any) -> None:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "schema_version": "mysql_disk2_job_tpch_lease_event_v1",
            "timestamp": datetime.now().astimezone().isoformat(),
            "event": event,
            "lane_id": LANE_ID,
            "scenario": self.scenario,
            "pid": os.getpid(),
            "attempt_token": self.token,
            **fields,
        }
        descriptor = os.open(
            self.journal_path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_CLOEXEC", 0),
            0o640,
        )
        try:
            os.write(
                descriptor,
                (
                    json.dumps(row, sort_keys=True, separators=(",", ":"))
                    + "\n"
                ).encode("utf-8"),
            )
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def __enter__(self) -> "Disk2SharedLane":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._descriptor = os.open(
            self.lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0),
            0o640,
        )
        try:
            fcntl.flock(self._descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self._descriptor)
            self._descriptor = None
            raise Disk2LaneError(
                f"{LANE_ID} is already held; JOB and TPC-H must run serially"
            ) from exc
        self._append(
            "lease_acquired",
            acquire_before="fresh_work_copy_preparation",
            hold_through="runtime_cleanup_and_final_readback",
            device_major_minor="259:6",
        )
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if self._descriptor is None:
            return False
        try:
            self._append(
                "lease_released",
                completed=exc_type is None,
                exception_type=(
                    None if exc_type is None else getattr(exc_type, "__name__", str(exc_type))
                ),
            )
        finally:
            fcntl.flock(self._descriptor, fcntl.LOCK_UN)
            os.close(self._descriptor)
            self._descriptor = None
        return False


__all__ = ["Disk2LaneError", "Disk2SharedLane", "JOURNAL_PATH", "LANE_ID", "LOCK_PATH"]
