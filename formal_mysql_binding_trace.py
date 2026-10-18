"""Durable, fail-closed runtime binding trace protocol for formal MySQL runs.

This module deliberately does not infer bindings from seeds or benchmark output.
The code at the SQL execution site must call :meth:`BindingTraceWriter.record`
with the values it actually supplied to the database driver.  The Python
session wrapper may then seal and verify the append-only JSONL trace.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
import uuid


EVENT_SCHEMA_VERSION = "formal_mysql_binding_trace_event_v1"
MANIFEST_SCHEMA_VERSION = "formal_mysql_binding_trace_manifest_v1"
TRACE_ENV_PATH = "FORMAL_MYSQL_BINDING_TRACE_PATH"
TRACE_ENV_RUN_ID = "FORMAL_MYSQL_BINDING_TRACE_RUN_ID"
TRACE_ENV_SCHEMA = "FORMAL_MYSQL_BINDING_TRACE_SCHEMA"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_RFC3339_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z")
_FORBIDDEN_KEY = re.compile(r"(?:password|passwd|credential|secret|token|result|row_data)", re.I)
_OUTCOMES = {"success", "sql_error", "aborted"}


class BindingTraceError(RuntimeError):
    """The trace is absent, malformed, contradictory, or no longer appendable."""


def canonical_sql_sha256(sql: str) -> str:
    """Hash SQL after only whitespace canonicalisation; literals stay intact."""
    if not isinstance(sql, str) or not sql.strip():
        raise BindingTraceError("SQL must be a non-empty string")
    canonical = " ".join(sql.split())
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _safe_json(value: Any, path: str = "value") -> None:
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise BindingTraceError(f"{path} is non-finite")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _safe_json(item, f"{path}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise BindingTraceError(f"{path} has a non-string/empty key")
            if _FORBIDDEN_KEY.search(key):
                raise BindingTraceError(f"{path} contains forbidden key {key!r}")
            _safe_json(item, f"{path}.{key}")
        return
    raise BindingTraceError(f"{path} has unsupported type {type(value).__name__}")


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _validate_binding(binding: Any, index: int) -> None:
    if not isinstance(binding, Mapping):
        raise BindingTraceError(f"actual_bindings[{index}] is not an object")
    if set(binding) != {"position", "jdbc_type", "value"}:
        raise BindingTraceError(f"actual_bindings[{index}] fields differ from schema")
    if binding["position"] != index + 1:
        raise BindingTraceError("binding positions must be contiguous and one-based")
    if not isinstance(binding["jdbc_type"], str) or not binding["jdbc_type"]:
        raise BindingTraceError("binding jdbc_type must be non-empty")
    _safe_json(binding["value"], f"actual_bindings[{index}].value")


def validate_event(event: Mapping[str, Any], *, expected_run_id: str | None = None) -> None:
    required = {
        "schema_version", "event_id", "run_id", "benchmark", "query_id",
        "worker_or_terminal", "sequence", "attempt", "retry", "seed_state",
        "seed_derivation", "actual_bindings", "canonical_executable_sql_sha256",
        "template_sql_sha256", "outcome", "timestamp_utc",
    }
    if set(event) != required:
        raise BindingTraceError(f"event fields differ from schema: {sorted(set(event) ^ required)}")
    if event["schema_version"] != EVENT_SCHEMA_VERSION:
        raise BindingTraceError("event schema version differs")
    for field in ("event_id", "run_id", "benchmark", "query_id", "worker_or_terminal"):
        if not isinstance(event[field], str) or not event[field]:
            raise BindingTraceError(f"{field} must be non-empty")
    if _SHA256.fullmatch(event["event_id"]) is None:
        raise BindingTraceError("event_id is not SHA-256")
    if event["benchmark"] not in {"hybench_ap", "tpch"}:
        raise BindingTraceError("benchmark is not trace-authorized")
    if expected_run_id is not None and event["run_id"] != expected_run_id:
        raise BindingTraceError("event run_id differs")
    for field in ("canonical_executable_sql_sha256", "template_sql_sha256"):
        if not isinstance(event[field], str) or _SHA256.fullmatch(event[field]) is None:
            raise BindingTraceError(f"{field} is not SHA-256")
    if not isinstance(event["sequence"], int) or event["sequence"] < 1:
        raise BindingTraceError("sequence must be positive")
    if not isinstance(event["attempt"], int) or event["attempt"] < 1:
        raise BindingTraceError("attempt must be positive")
    if event["retry"] != event["attempt"] - 1:
        raise BindingTraceError("retry must equal attempt-1")
    if event["outcome"] not in _OUTCOMES:
        raise BindingTraceError("outcome is invalid")
    if not isinstance(event["timestamp_utc"], str) or _RFC3339_UTC.fullmatch(event["timestamp_utc"]) is None:
        raise BindingTraceError("timestamp_utc must be microsecond RFC3339 UTC")
    for field in ("seed_state", "seed_derivation"):
        if not isinstance(event[field], Mapping) or not event[field]:
            raise BindingTraceError(f"{field} must be a non-empty object")
        _safe_json(event[field], field)
    bindings = event["actual_bindings"]
    if not isinstance(bindings, list):
        raise BindingTraceError("actual_bindings must be a list")
    for index, binding in enumerate(bindings):
        _validate_binding(binding, index)


@dataclass(frozen=True)
class BindingTracePaths:
    events: Path

    @property
    def lock(self) -> Path:
        return self.events.with_suffix(self.events.suffix + ".lock")

    @property
    def manifest(self) -> Path:
        return self.events.with_suffix(self.events.suffix + ".manifest.json")

    @property
    def manifest_sha256(self) -> Path:
        return self.events.with_suffix(self.events.suffix + ".manifest.json.sha256")


class BindingTraceWriter:
    """Multi-process-safe append writer used at the actual SQL execution site."""

    def __init__(self, events: Path, run_id: str) -> None:
        if not run_id:
            raise BindingTraceError("run_id is required")
        self.paths = BindingTracePaths(Path(events))
        self.run_id = run_id
        self.paths.events.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> BindingTraceWriter | None:
        environment = os.environ if environment is None else environment
        raw_path = environment.get(TRACE_ENV_PATH, "")
        if not raw_path:
            return None
        if environment.get(TRACE_ENV_SCHEMA) != EVENT_SCHEMA_VERSION:
            raise BindingTraceError("binding trace environment schema differs")
        return cls(Path(raw_path), environment.get(TRACE_ENV_RUN_ID, ""))

    def record(
        self, *, benchmark: str, query_id: str, worker_or_terminal: str,
        sequence: int, attempt: int, seed_state: Mapping[str, Any],
        seed_derivation: Mapping[str, Any], actual_bindings: Sequence[Mapping[str, Any]],
        executable_sql: str, template_sql: str, outcome: str,
        timestamp_utc: str | None = None,
    ) -> Mapping[str, Any]:
        identity = "\0".join((self.run_id, worker_or_terminal, str(sequence), str(attempt), query_id))
        event = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "event_id": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
            "run_id": self.run_id,
            "benchmark": benchmark,
            "query_id": query_id,
            "worker_or_terminal": worker_or_terminal,
            "sequence": sequence,
            "attempt": attempt,
            "retry": attempt - 1,
            "seed_state": dict(seed_state),
            "seed_derivation": dict(seed_derivation),
            "actual_bindings": [dict(value) for value in actual_bindings],
            "canonical_executable_sql_sha256": canonical_sql_sha256(executable_sql),
            "template_sql_sha256": canonical_sql_sha256(template_sql),
            "outcome": outcome,
            "timestamp_utc": timestamp_utc or _timestamp(),
        }
        validate_event(event, expected_run_id=self.run_id)
        payload = _canonical_json(event)
        lock_descriptor = os.open(self.paths.lock, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
            if self.paths.manifest.exists() or self.paths.manifest_sha256.exists():
                raise BindingTraceError("binding trace is already sealed")
            seen: set[str] = set()
            if self.paths.events.exists():
                if self.paths.events.is_symlink() or not self.paths.events.is_file():
                    raise BindingTraceError("binding trace path is not a regular file")
                for line in self.paths.events.read_text(encoding="utf-8").splitlines():
                    previous = json.loads(line)
                    validate_event(previous, expected_run_id=self.run_id)
                    seen.add(previous["event_id"])
            if event["event_id"] in seen:
                raise BindingTraceError("duplicate execution event; recovery must not recount it")
            descriptor = os.open(self.paths.events, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                written = os.write(descriptor, payload)
                if written != len(payload):
                    raise BindingTraceError("short append to binding trace")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            _fsync_directory(self.paths.events.parent)
        finally:
            os.close(lock_descriptor)
        return event


def _read_events(paths: BindingTracePaths, expected_run_id: str | None) -> tuple[list[Mapping[str, Any]], bytes]:
    if not paths.events.is_file() or paths.events.is_symlink():
        raise BindingTraceError("binding trace JSONL is absent or non-regular")
    raw = paths.events.read_bytes()
    if not raw or not raw.endswith(b"\n"):
        raise BindingTraceError("binding trace is empty or has a torn final line")
    rows: list[Mapping[str, Any]] = []
    event_ids: set[str] = set()
    sequence_attempts: set[tuple[str, int, int]] = set()
    for line_number, line in enumerate(raw.splitlines(), 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise BindingTraceError(f"invalid JSONL event at line {line_number}") from error
        if not isinstance(event, Mapping):
            raise BindingTraceError(f"event {line_number} is not an object")
        validate_event(event, expected_run_id=expected_run_id)
        if event["event_id"] in event_ids:
            raise BindingTraceError("duplicate event_id in trace")
        key = (event["worker_or_terminal"], event["sequence"], event["attempt"])
        if key in sequence_attempts:
            raise BindingTraceError("duplicate worker/sequence/attempt in trace")
        event_ids.add(event["event_id"])
        sequence_attempts.add(key)
        rows.append(event)
    return rows, raw


def finalize_binding_trace(events: Path, *, expected_run_id: str | None = None) -> Mapping[str, Any]:
    """Seal a successful trace. Missing/malformed traces produce no manifest."""
    paths = BindingTracePaths(Path(events))
    lock_descriptor = os.open(paths.lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        rows, raw = _read_events(paths, expected_run_id)
        run_ids = {str(row["run_id"]) for row in rows}
        if len(run_ids) != 1:
            raise BindingTraceError("trace contains multiple run IDs")
        run_id = next(iter(run_ids))
        if expected_run_id is not None and run_id != expected_run_id:
            raise BindingTraceError("trace run ID differs")
        per_worker: dict[str, int] = {}
        for row in rows:
            worker = str(row["worker_or_terminal"])
            per_worker[worker] = max(per_worker.get(worker, 0), int(row["sequence"]))
        manifest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "event_schema_version": EVENT_SCHEMA_VERSION,
            "run_id": run_id,
            "event_count": len(rows),
            "failed_attempt_count": sum(row["outcome"] != "success" for row in rows),
            "maximum_sequence_by_worker": dict(sorted(per_worker.items())),
            "events_sha256": hashlib.sha256(raw).hexdigest(),
            "sealed_timestamp_utc": _timestamp(),
        }
        payload = _canonical_json(manifest)
        digest_payload = (hashlib.sha256(payload).hexdigest() + "  " + paths.manifest.name + "\n").encode("ascii")
        if paths.manifest_sha256.exists() and not paths.manifest.exists():
            raise BindingTraceError("manifest SHA sidecar exists without manifest")
        if paths.manifest.exists():
            existing = json.loads(paths.manifest.read_text(encoding="utf-8"))
            for field in ("schema_version", "event_schema_version", "run_id", "event_count",
                          "failed_attempt_count", "maximum_sequence_by_worker", "events_sha256"):
                if existing.get(field) != manifest[field]:
                    raise BindingTraceError("sealed manifest does not match recovered trace")
            expected_digest = hashlib.sha256(paths.manifest.read_bytes()).hexdigest()
            existing_digest_payload = (expected_digest + "  " + paths.manifest.name + "\n").encode("ascii")
            if paths.manifest_sha256.exists():
                if paths.manifest_sha256.read_bytes() != existing_digest_payload:
                    raise BindingTraceError("manifest SHA sidecar differs")
            else:
                # Recovery after a crash between the two atomic publications.
                # The manifest is accepted only after all content fields were
                # checked against the current append-only JSONL above.
                _atomic_bytes(paths.manifest_sha256, existing_digest_payload)
            return existing
        _atomic_bytes(paths.manifest, payload)
        _atomic_bytes(paths.manifest_sha256, digest_payload)
        return manifest
    finally:
        os.close(lock_descriptor)


def binding_trace_environment(
    base: Mapping[str, str], *, enabled: bool, events: Path, run_id: str,
) -> dict[str, str]:
    """Return an unchanged environment when disabled, or explicit trace wiring."""
    environment = dict(base)
    if not enabled:
        return environment
    if not run_id:
        raise BindingTraceError("run_id is required when binding trace is enabled")
    environment.update({
        TRACE_ENV_PATH: str(Path(events).resolve()),
        TRACE_ENV_RUN_ID: run_id,
        TRACE_ENV_SCHEMA: EVENT_SCHEMA_VERSION,
    })
    return environment
