"""Generic production sampling services for formal MySQL sessions.

This module contains no host, scenario, credential, container, or database
paths.  A deployment service factory must supply all of them explicitly.  The
sampler reads cgroup counters and one attributable MySQL-side completion
counter, emits one combined JSONL record per second, and exposes the two sample
views required by :mod:`formal_mysql_continuous_session_factory`.

Client progress is deliberately not accepted as DBQPS evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import threading
import time
from typing import Any, Callable, Mapping, Sequence

from formal_mysql_continuous_session_factory import FormalSessionRuntimeServices


class ProductionSamplingError(RuntimeError):
    """The production sampler is incomplete, unsafe, or lost attribution."""


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MYSQL_NAME = re.compile(r"[A-Za-z0-9_]+\Z")
_ALLOWED_SCOPES = {"registered_workload_digest_set"}
_FORBIDDEN_COUNTERS = {"questions", "queries", "client_qps", "client_completed"}
FORMAL_BENCHMARKS = ("hybench_ap", "job", "tpch", "hybench_xp", "tpcc")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProductionSamplingError(f"missing mapping: {label}")
    return value


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProductionSamplingError(f"{label} must be a positive integer")
    return value


def _absolute_file(value: Any, label: str, *, executable: bool = False) -> Path:
    if not isinstance(value, str) or not value:
        raise ProductionSamplingError(f"{label} is missing")
    path = Path(value)
    if not path.is_absolute() or path.is_symlink():
        raise ProductionSamplingError(f"{label} must be an absolute non-symlink file")
    try:
        mode = path.stat().st_mode
    except OSError as exc:
        raise ProductionSamplingError(f"{label} is unavailable") from exc
    if not stat.S_ISREG(mode):
        raise ProductionSamplingError(f"{label} is not a regular file")
    if executable and not os.access(path, os.X_OK):
        raise ProductionSamplingError(f"{label} is not executable")
    return path


def _absolute_directory(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ProductionSamplingError(f"{label} is missing")
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise ProductionSamplingError(f"{label} must be an absolute non-symlink directory")
    return path


def _read_int(path: Path) -> int:
    try:
        value = int(path.read_text(encoding="ascii").strip())
    except (OSError, UnicodeError, ValueError) as exc:
        raise ProductionSamplingError(f"invalid cgroup integer: {path.name}") from exc
    if value < 0:
        raise ProductionSamplingError(f"negative cgroup integer: {path.name}")
    return value


def _key_values(path: Path) -> dict[str, int]:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ProductionSamplingError(f"cannot read cgroup counters: {path.name}") from exc
    result: dict[str, int] = {}
    for line in lines:
        pieces = line.split()
        if len(pieces) != 2:
            continue
        try:
            result[pieces[0]] = int(pieces[1])
        except ValueError:
            continue
    return result


def _v1_io(path: Path, device: str) -> tuple[int, int]:
    read_bytes = write_bytes = 0
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ProductionSamplingError("cannot read cgroup-v1 IO counters") from exc
    for line in lines:
        pieces = line.split()
        if len(pieces) == 3 and pieces[0] == device:
            if pieces[1] == "Read":
                read_bytes += int(pieces[2])
            elif pieces[1] == "Write":
                write_bytes += int(pieces[2])
    return read_bytes, write_bytes


def _v2_io(path: Path, device: str) -> tuple[int, int]:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ProductionSamplingError("cannot read cgroup-v2 IO counters") from exc
    for line in lines:
        pieces = line.split()
        if pieces and pieces[0] == device:
            values = dict(piece.split("=", 1) for piece in pieces[1:] if "=" in piece)
            try:
                return int(values["rbytes"]), int(values["wbytes"])
            except (KeyError, ValueError) as exc:
                raise ProductionSamplingError("cgroup-v2 io.stat lacks rbytes/wbytes") from exc
    raise ProductionSamplingError(f"cgroup IO device is absent: {device}")


@dataclass(frozen=True)
class RawCgroupCounters:
    monotonic_ns: int
    epoch_ms: int
    cpu_usage_ns: int
    memory_current_bytes: int
    memory_inactive_file_bytes: int
    io_read_bytes: int
    io_write_bytes: int


class CgroupCounterReader:
    """Read explicit cgroup-v1 or cgroup-v2 files without shelling out."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = dict(config)
        version = config.get("version")
        if version not in {1, 2}:
            raise ProductionSamplingError("cgroup.version must be exactly 1 or 2")
        self.version = int(version)
        self.device = str(config.get("device") or "")
        if re.fullmatch(r"[0-9]+:[0-9]+", self.device) is None:
            raise ProductionSamplingError("cgroup.device must be a major:minor value")
        self.cpu_quota_cores = float(config.get("cpu_quota_cores", 0.0))
        if not math.isfinite(self.cpu_quota_cores) or self.cpu_quota_cores <= 0.0:
            raise ProductionSamplingError("cgroup.cpu_quota_cores must be positive")
        self.memory_limit_bytes = _positive_int(
            config.get("memory_limit_bytes"), "cgroup.memory_limit_bytes"
        )
        self.io_read_cap_bps = _positive_int(
            config.get("io_read_cap_bps"), "cgroup.io_read_cap_bps"
        )
        self.io_write_cap_bps = _positive_int(
            config.get("io_write_cap_bps"), "cgroup.io_write_cap_bps"
        )
        if self.version == 1:
            self.cpu_root = _absolute_directory(config.get("cpuacct_root"), "cpuacct_root")
            self.memory_root = _absolute_directory(config.get("memory_root"), "memory_root")
            self.io_root = _absolute_directory(config.get("blkio_root"), "blkio_root")
        else:
            root = _absolute_directory(config.get("root"), "cgroup.root")
            self.cpu_root = self.memory_root = self.io_root = root

    def snapshot(
        self, *, monotonic_ns: int | None = None, epoch_ms: int | None = None,
    ) -> RawCgroupCounters:
        if self.version == 1:
            cpu_usage_ns = _read_int(self.cpu_root / "cpuacct.usage")
            memory_current = _read_int(self.memory_root / "memory.usage_in_bytes")
            memory_stat = _key_values(self.memory_root / "memory.stat")
            inactive = memory_stat.get("total_inactive_file", memory_stat.get("inactive_file"))
            if inactive is None:
                raise ProductionSamplingError("cgroup-v1 memory.stat lacks inactive_file")
            read_bytes, write_bytes = _v1_io(
                self.io_root / "blkio.throttle.io_service_bytes", self.device
            )
        else:
            cpu_stat = _key_values(self.cpu_root / "cpu.stat")
            if "usage_usec" not in cpu_stat:
                raise ProductionSamplingError("cgroup-v2 cpu.stat lacks usage_usec")
            cpu_usage_ns = cpu_stat["usage_usec"] * 1000
            memory_current = _read_int(self.memory_root / "memory.current")
            memory_stat = _key_values(self.memory_root / "memory.stat")
            if "inactive_file" not in memory_stat:
                raise ProductionSamplingError("cgroup-v2 memory.stat lacks inactive_file")
            inactive = memory_stat["inactive_file"]
            read_bytes, write_bytes = _v2_io(self.io_root / "io.stat", self.device)
        return RawCgroupCounters(
            monotonic_ns=time.monotonic_ns() if monotonic_ns is None else monotonic_ns,
            epoch_ms=time.time_ns() // 1_000_000 if epoch_ms is None else epoch_ms,
            cpu_usage_ns=cpu_usage_ns,
            memory_current_bytes=memory_current,
            memory_inactive_file_bytes=inactive,
            io_read_bytes=read_bytes,
            io_write_bytes=write_bytes,
        )

    def derive(self, previous: RawCgroupCounters, current: RawCgroupCounters) -> dict[str, Any]:
        elapsed = (current.monotonic_ns - previous.monotonic_ns) / 1e9
        deltas = {
            "cpu": current.cpu_usage_ns - previous.cpu_usage_ns,
            "read": current.io_read_bytes - previous.io_read_bytes,
            "write": current.io_write_bytes - previous.io_write_bytes,
        }
        if elapsed <= 0.0 or any(value < 0 for value in deltas.values()):
            raise ProductionSamplingError("cgroup counters regressed or sample time is not positive")
        cpu_used_cores = deltas["cpu"] / 1e9 / elapsed
        read_bps = deltas["read"] / elapsed
        write_bps = deltas["write"] / elapsed
        working_set = max(
            0, current.memory_current_bytes - current.memory_inactive_file_bytes
        )
        return {
            "epoch_ms": current.epoch_ms,
            "sample_interval_seconds": elapsed,
            "cgroup_version": self.version,
            "cpu_usage_raw_ns": current.cpu_usage_ns,
            "cpu_delta_raw_ns": deltas["cpu"],
            "cpu_quota_cores": self.cpu_quota_cores,
            "cpu_used_cores": cpu_used_cores,
            "cpu_utilization_ratio": cpu_used_cores / self.cpu_quota_cores,
            "memory_current_raw_bytes": current.memory_current_bytes,
            "memory_inactive_file_raw_bytes": current.memory_inactive_file_bytes,
            "memory_working_set_bytes": working_set,
            "memory_limit_bytes": self.memory_limit_bytes,
            "memory_utilization_ratio": working_set / self.memory_limit_bytes,
            "io_read_raw_bytes": current.io_read_bytes,
            "io_write_raw_bytes": current.io_write_bytes,
            "io_read_delta_bytes": deltas["read"],
            "io_write_delta_bytes": deltas["write"],
            "io_read_bps": read_bps,
            "io_write_bps": write_bps,
            "io_read_cap_bps": self.io_read_cap_bps,
            "io_write_cap_bps": self.io_write_cap_bps,
            "io_utilization_ratio": (
                read_bps + write_bps
            ) / (self.io_read_cap_bps + self.io_write_cap_bps),
            "io_utilization_definition": "(read_bps+write_bps)/(read_cap_bps+write_cap_bps)",
            "device": self.device,
        }


class MySQLAttributableCounterReader:
    """Read a fixed database-side counter through a protected defaults file."""

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        runner: Callable[[Sequence[str]], str] | None = None,
    ) -> None:
        self.config = dict(config)
        self.scope = str(config.get("attribution_scope") or "")
        if self.scope not in _ALLOWED_SCOPES:
            raise ProductionSamplingError(
                "production DBQPS only permits registered_workload_digest_set; "
                "dedicated global counters include the monitoring query itself"
            )
        self.counter_name = str(config.get("counter_name") or "")
        if not self.counter_name or self.counter_name.lower() in _FORBIDDEN_COUNTERS:
            raise ProductionSamplingError("database counter is missing or forbidden")
        expected_counter = "performance_schema_digest_sum_count_star"
        if self.counter_name != expected_counter:
            raise ProductionSamplingError(
                f"database counter_name must be {expected_counter} for selected scope"
            )
        raw_digests = config.get("digests")
        if not isinstance(raw_digests, list) or not raw_digests:
            raise ProductionSamplingError("registered digest attribution requires digests")
        digests = sorted(set(str(value).lower() for value in raw_digests))
        if any(_SHA256.fullmatch(value) is None for value in digests):
            raise ProductionSamplingError("registered MySQL digest is invalid")
        canonical = json.dumps(digests, separators=(",", ":"), ensure_ascii=True)
        observed = hashlib.sha256(canonical.encode("ascii")).hexdigest()
        expected = config.get("digest_set_sha256")
        if observed != expected:
            raise ProductionSamplingError("digest_set_sha256 does not bind configured digests")
        schema = str(config.get("schema") or "")
        if _MYSQL_NAME.fullmatch(schema) is None:
            raise ProductionSamplingError("registered digest attribution requires safe schema")
        digest_sql = ",".join(f"'{value.upper()}'" for value in digests)
        self.query = (
            "SELECT COALESCE(SUM(COUNT_STAR),0) FROM "
            "performance_schema.events_statements_summary_by_digest "
            f"WHERE SCHEMA_NAME='{schema}' AND DIGEST IN ({digest_sql})"
        )
        self.digest_set_sha256 = observed
        if "Questions" in self.query or "Queries" in self.query:
            raise ProductionSamplingError("Questions/Queries cannot be used as DBQPS")
        self._runner = runner
        if runner is None:
            self.mysql_client = _absolute_file(
                config.get("mysql_client"), "database.mysql_client", executable=True
            )
            self.defaults_file = _absolute_file(
                config.get("defaults_extra_file"), "database.defaults_extra_file"
            )
            if self.defaults_file.stat().st_mode & 0o077:
                raise ProductionSamplingError("database defaults file must have mode 0600 or stricter")

    def _run(self) -> str:
        if self._runner is not None:
            return self._runner((self.query,))
        command = (
            str(self.mysql_client), f"--defaults-extra-file={self.defaults_file}",
            "--batch", "--skip-column-names", "--raw", "--execute", self.query,
        )
        completed = subprocess.run(
            command, text=True, capture_output=True, check=False, timeout=5.0,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        )
        if completed.returncode != 0:
            # stderr is intentionally not propagated; it may contain endpoint details.
            raise ProductionSamplingError(
                f"database counter command failed with rc={completed.returncode}"
            )
        return completed.stdout

    def sample(self, epoch_ms: int) -> dict[str, Any]:
        output = self._run().strip()
        if not re.fullmatch(r"[0-9]+", output):
            raise ProductionSamplingError("database counter output is not one non-negative integer")
        return {
            "epoch_ms": epoch_ms,
            "completed_total": int(output),
            "source": "database_status_counter",
            "attribution_scope": self.scope,
            "counter_name": self.counter_name,
            "digest_set_sha256": self.digest_set_sha256,
        }


def append_atomic_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    """Append one durable JSON line with O_APPEND and one write syscall."""
    if not path.is_absolute() or path.is_symlink():
        raise ProductionSamplingError("sampling output must be an absolute non-symlink path")
    if (
        not path.parent.is_dir()
        or path.parent.is_symlink()
        or path.parent.resolve() != path.parent
    ):
        raise ProductionSamplingError("sampling output parent must already exist")
    parent_stat = path.parent.stat()
    if parent_stat.st_uid != os.geteuid() or stat.S_IMODE(parent_stat.st_mode) & 0o022:
        raise ProductionSamplingError(
            "sampling output parent must be owned by the sampler and not group/world writable"
        )
    if path.exists():
        output_stat = path.lstat()
        if (
            not stat.S_ISREG(output_stat.st_mode)
            or output_stat.st_uid != os.geteuid()
            or stat.S_IMODE(output_stat.st_mode) != 0o600
        ):
            raise ProductionSamplingError(
                "existing sampling output must be sampler-owned mode 0600 regular file"
            )
    data = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")
    if len(data) > 65_536:
        raise ProductionSamplingError("sampling JSONL record exceeds 64 KiB")
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != 0o600
        ):
            raise ProductionSamplingError(
                "opened sampling output must be sampler-owned mode 0600 regular file"
            )
        written = os.write(descriptor, data)
        if written != len(data):
            raise ProductionSamplingError("short atomic JSONL write")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class ProductionSamplingStream:
    """Lazy one-second sampler backing both formal runtime service providers."""

    def __init__(
        self,
        resource_reader: CgroupCounterReader,
        database_reader: MySQLAttributableCounterReader,
        output_jsonl: Path,
        *,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        epoch_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
    ) -> None:
        self.resource_reader = resource_reader
        self.database_reader = database_reader
        self.output_jsonl = output_jsonl
        self.monotonic_ns = monotonic_ns
        self.epoch_ms = epoch_ms
        self._lock = threading.Lock()
        self._resources: list[Mapping[str, Any]] = []
        self._database: list[Mapping[str, Any]] = []
        self._previous: RawCgroupCounters | None = None
        self._last_database_total: int | None = None
        self._sequence = 0
        self._failure: BaseException | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def sample_once(self) -> Mapping[str, Any] | None:
        now_mono = self.monotonic_ns()
        now_epoch = self.epoch_ms()
        raw = self.resource_reader.snapshot(monotonic_ns=now_mono, epoch_ms=now_epoch)
        database = self.database_reader.sample(now_epoch)
        if (
            self._last_database_total is not None
            and int(database["completed_total"]) < self._last_database_total
        ):
            raise ProductionSamplingError("database-side counter regressed")
        self._last_database_total = int(database["completed_total"])
        if self._previous is None:
            self._previous = raw
            return None
        resource = self.resource_reader.derive(self._previous, raw)
        self._previous = raw
        self._sequence += 1
        record = {
            "schema_version": "formal_mysql_one_second_sample_v1",
            "sample_sequence": self._sequence,
            "epoch_ms": now_epoch,
            "resource": resource,
            "database": database,
        }
        append_atomic_jsonl(self.output_jsonl, record)
        with self._lock:
            self._resources.append(resource)
            self._database.append(database)
        return record

    def _run(self) -> None:
        deadline = time.monotonic()
        try:
            while not self._stop.is_set():
                self.sample_once()
                deadline += 1.0
                delay = deadline - time.monotonic()
                if delay < -0.25:
                    raise ProductionSamplingError("one-second sampler missed its cadence")
                self._stop.wait(max(0.0, delay))
        except BaseException as exc:
            self._failure = exc
            self._stop.set()

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run, name="formal-mysql-production-sampler", daemon=True
            )
            self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _raise_failure(self) -> None:
        if self._failure is not None:
            raise ProductionSamplingError("production sampling stream failed") from self._failure

    def resource_samples(self) -> Sequence[Mapping[str, Any]]:
        self.start()
        self._raise_failure()
        with self._lock:
            return tuple(dict(row) for row in self._resources)

    def database_qps_samples(self) -> Sequence[Mapping[str, Any]]:
        self.start()
        self._raise_failure()
        with self._lock:
            return tuple(dict(row) for row in self._database)


def build_production_sampling_services(
    config: Mapping[str, Any],
) -> tuple[FormalSessionRuntimeServices, ProductionSamplingStream]:
    """Build services for one scenario; all deployment values are mandatory."""
    sampling = _mapping(config.get("production_sampling"), "production_sampling")
    if sampling.get("interval_seconds") != 1:
        raise ProductionSamplingError("production_sampling.interval_seconds must equal 1")
    raw_output = sampling.get("output_jsonl")
    if not isinstance(raw_output, str) or not raw_output:
        raise ProductionSamplingError("production_sampling.output_jsonl is missing")
    output = Path(raw_output)
    if not output.is_absolute():
        raise ProductionSamplingError("production_sampling.output_jsonl must be absolute")
    resource = CgroupCounterReader(_mapping(sampling.get("cgroup"), "production_sampling.cgroup"))
    database = MySQLAttributableCounterReader(
        _mapping(sampling.get("database"), "production_sampling.database")
    )
    stream = ProductionSamplingStream(resource, database, output)
    services = FormalSessionRuntimeServices(
        resource_samples=stream.resource_samples,
        database_qps_samples=stream.database_qps_samples,
    )
    return services, stream


def build_five_scenario_sampling_services(
    config: Mapping[str, Any],
) -> tuple[
    Mapping[str, FormalSessionRuntimeServices],
    Mapping[str, ProductionSamplingStream],
]:
    """Build exactly the five formal sampling services from explicit config."""
    scenarios = _mapping(
        config.get("production_sampling_by_benchmark"),
        "production_sampling_by_benchmark",
    )
    missing = [name for name in FORMAL_BENCHMARKS if name not in scenarios]
    extras = sorted(set(scenarios) - set(FORMAL_BENCHMARKS))
    if missing or extras:
        raise ProductionSamplingError(
            f"five-scenario sampling config mismatch: missing={missing} extras={extras}"
        )
    services: dict[str, FormalSessionRuntimeServices] = {}
    streams: dict[str, ProductionSamplingStream] = {}
    for benchmark in FORMAL_BENCHMARKS:
        scenario = _mapping(
            scenarios[benchmark], f"production_sampling_by_benchmark.{benchmark}"
        )
        service, stream = build_production_sampling_services(scenario)
        services[benchmark] = service
        streams[benchmark] = stream
    return services, streams
