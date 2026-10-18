#!/usr/bin/env python3
"""Fail-closed host02 JOB/XP admission guards with append-only journals."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Mapping
import uuid


HOST02_SCENARIOS = frozenset({"job_memory", "hybench_xp_io"})
HOST02_BENCHMARK_SCENARIO = {
    "job": "job_memory",
    "hybench_xp": "hybench_xp_io",
}
DEFAULT_HOST02_MUTEX_ROOT = Path(
    "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/host02_control"
)
DEFAULT_HOST02_LOCK_PATH = DEFAULT_HOST02_MUTEX_ROOT / "job_xp_pressure.lock"
DEFAULT_HOST02_JOURNAL_PATH = DEFAULT_HOST02_MUTEX_ROOT / "job_xp_lease_journal.jsonl"


class Host02MutexError(RuntimeError):
    """The shared JOB/XP pressure lane cannot be entered safely."""


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _is_under_tmp(path: Path) -> bool:
    resolved = path.resolve(strict=False)
    return resolved == Path("/tmp") or Path("/tmp") in resolved.parents


def _validate_control_path(path: Path, label: str) -> Path:
    if not path.is_absolute():
        raise Host02MutexError(f"{label} must be absolute: {path}")
    if _is_under_tmp(path):
        raise Host02MutexError(f"{label} must not be under /tmp: {path}")
    if path.exists() and path.is_symlink():
        raise Host02MutexError(f"{label} must not be a symlink: {path}")
    return path


def _validate_probe_payload(payload: Any, phase: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise Host02MutexError(f"{phase} pressure probe did not return an object")
    result = dict(payload)
    required = ("observed_at", "probe_id", "job_pressure_count", "xp_pressure_count")
    missing = [field for field in required if field not in result]
    if missing:
        raise Host02MutexError(f"{phase} pressure probe missing fields: {missing}")
    for field in ("job_pressure_count", "xp_pressure_count"):
        value = result[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise Host02MutexError(f"{phase} pressure probe {field} is invalid: {value!r}")
    if result["job_pressure_count"] != 0 or result["xp_pressure_count"] != 0:
        raise Host02MutexError(
            f"{phase} zero-pressure gate failed: "
            f"job={result['job_pressure_count']} xp={result['xp_pressure_count']}"
        )
    if not isinstance(result["observed_at"], str) or not result["observed_at"].strip():
        raise Host02MutexError(f"{phase} pressure probe observed_at is missing")
    if not isinstance(result["probe_id"], str) or not result["probe_id"].strip():
        raise Host02MutexError(f"{phase} pressure probe probe_id is missing")
    return result


def _validate_parallel_probe_payload(
    payload: Any, phase: str, scenario: str,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise Host02MutexError(f"{phase} parallel pressure probe did not return an object")
    result = dict(payload)
    required = (
        "observed_at", "probe_id", "job_pressure_count", "xp_pressure_count",
        "isolation_verified", "cross_engine_comparison_prohibited",
    )
    missing = [field for field in required if field not in result]
    if missing:
        raise Host02MutexError(f"{phase} parallel pressure probe missing fields: {missing}")
    for field in ("job_pressure_count", "xp_pressure_count"):
        value = result[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 1:
            raise Host02MutexError(f"{phase} parallel pressure probe {field} is invalid: {value!r}")
    own_field = "job_pressure_count" if scenario == "job_memory" else "xp_pressure_count"
    expected_own = 0
    if result[own_field] != expected_own:
        raise Host02MutexError(
            f"{phase} own-lane zero-pressure gate failed: {own_field}={result[own_field]}"
        )
    if result["isolation_verified"] is not True:
        raise Host02MutexError(f"{phase} JOB/XP isolation verification failed")
    if result["cross_engine_comparison_prohibited"] is not True:
        raise Host02MutexError(f"{phase} mysql_only scope declaration is absent")
    return result


class Host02IsolatedParallelGuard(AbstractContextManager["Host02IsolatedParallelGuard"]):
    """Allow JOB and XP together only after frozen isolation is read back.

    The guard intentionally does not hold the shared pressure mutex.  Each
    scenario has an independent journal, so concurrent evidence writes cannot
    corrupt a shared checksum chain. Runtime stability remains fail-closed in
    the campaign's rolling-window gate.
    """

    def __init__(
        self,
        *,
        scenario: str,
        pressure_probe: Callable[[str, str], Mapping[str, Any]],
        journal_path: Path,
        owner_id: str | None = None,
    ) -> None:
        if scenario not in HOST02_SCENARIOS:
            raise Host02MutexError(f"unsupported host02 parallel scenario: {scenario!r}")
        if not callable(pressure_probe):
            raise Host02MutexError("host02 pressure_probe must be callable")
        self.scenario = scenario
        self.pressure_probe = pressure_probe
        base = _validate_control_path(Path(journal_path), "host02 parallel journal_path")
        self.journal_path = base.with_name(f"{base.stem}.{scenario}.parallel.jsonl")
        self.owner_id = owner_id or uuid.uuid4().hex

    def _append(self, event: str, **payload: Any) -> None:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "schema_version": 1,
            "event": event,
            "timestamp": datetime.now().astimezone().isoformat(),
            "owner_id": self.owner_id,
            "pid": os.getpid(),
            "scenario": self.scenario,
            "mode": "isolated_parallel_dynamic_gate",
            "payload": payload,
        }
        descriptor = os.open(
            self.journal_path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_CLOEXEC", 0),
            0o640,
        )
        try:
            os.write(descriptor, (_canonical_json(row) + "\n").encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def __enter__(self) -> "Host02IsolatedParallelGuard":
        try:
            probe = _validate_parallel_probe_payload(
                self.pressure_probe(self.scenario, "before_pressure"),
                "before_pressure",
                self.scenario,
            )
        except Exception as exc:
            self._append("parallel_admission_rejected", reason=str(exc))
            raise
        self._append(
            "parallel_isolation_admitted",
            probe=probe,
            peer_pressure_allowed=True,
            runtime_rolling_window_gate="PENDING_FAIL_CLOSED",
        )
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if exc_type is not None:
            self._append(
                "parallel_campaign_aborted",
                exception_type=getattr(exc_type, "__name__", str(exc_type)),
                reason=str(exc),
            )
        try:
            probe = _validate_parallel_probe_payload(
                self.pressure_probe(self.scenario, "after_recovery_checksum"),
                "after_recovery_checksum",
                self.scenario,
            )
        except Exception as probe_error:
            self._append(
                "parallel_recovery_checksum_failed",
                reason=str(probe_error),
                primary_exception_preserved=exc_type is not None,
            )
            if exc_type is not None:
                return False
            raise
        self._append(
            "parallel_recovery_checksum_complete",
            probe=probe,
            runtime_rolling_window_gate="ENFORCED_BY_CAMPAIGN_RESULT",
        )
        return False


class Host02JobXpMutex(AbstractContextManager["Host02JobXpMutex"]):
    """Hold one POSIX flock across an entire JOB or XP pressure lifecycle.

    Acquisition happens before R0 or any other pressure. Release happens only
    after the wrapped campaign returns and a post-run zero-pressure probe proves
    that workload pressure has stopped after recovery/checksum finalization.
    """

    def __init__(
        self,
        *,
        scenario: str,
        pressure_probe: Callable[[str, str], Mapping[str, Any]],
        lock_path: Path = DEFAULT_HOST02_LOCK_PATH,
        journal_path: Path = DEFAULT_HOST02_JOURNAL_PATH,
        owner_id: str | None = None,
    ) -> None:
        if scenario not in HOST02_SCENARIOS:
            raise Host02MutexError(f"unsupported host02 mutex scenario: {scenario!r}")
        if not callable(pressure_probe):
            raise Host02MutexError("host02 pressure_probe must be callable")
        self.scenario = scenario
        self.pressure_probe = pressure_probe
        self.lock_path = _validate_control_path(Path(lock_path), "host02 mutex lock_path")
        self.journal_path = _validate_control_path(
            Path(journal_path), "host02 mutex journal_path"
        )
        if self.lock_path == self.journal_path:
            raise Host02MutexError("lock_path and journal_path must differ")
        self.state_path = self.journal_path.with_suffix(".state.json")
        _validate_control_path(self.state_path, "host02 mutex state_path")
        self.owner_id = owner_id or uuid.uuid4().hex
        self._handle: Any = None
        self._last_event: dict[str, Any] | None = None

    def _read_journal(self) -> list[dict[str, Any]]:
        if not self.journal_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        prior_hash: str | None = None
        with self.journal_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise Host02MutexError(
                        f"host02 mutex journal line {line_number} is invalid JSON: {exc}"
                    ) from exc
                if not isinstance(row, dict):
                    raise Host02MutexError(
                        f"host02 mutex journal line {line_number} is not an object"
                    )
                claimed = row.get("event_sha256")
                unsigned = {key: value for key, value in row.items() if key != "event_sha256"}
                actual = hashlib.sha256(_canonical_json(unsigned).encode("utf-8")).hexdigest()
                if claimed != actual or row.get("previous_event_sha256") != prior_hash:
                    raise Host02MutexError(
                        f"host02 mutex journal checksum chain failed at line {line_number}"
                    )
                prior_hash = claimed
                rows.append(row)
        return rows

    def _write_state(self, event: Mapping[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        content = _canonical_json(dict(event)) + "\n"
        descriptor, raw_path = tempfile.mkstemp(
            prefix=f".{self.state_path.name}.", dir=str(self.state_path.parent)
        )
        temporary = Path(raw_path)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o640)
            os.replace(temporary, self.state_path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _append(self, event: str, **payload: Any) -> dict[str, Any]:
        rows = self._read_journal()
        previous = rows[-1] if rows else None
        unsigned = {
            "schema_version": 1,
            "sequence": len(rows) + 1,
            "event": event,
            "timestamp": datetime.now().astimezone().isoformat(),
            "owner_id": self.owner_id,
            "pid": os.getpid(),
            "scenario": self.scenario,
            "lock_path": str(self.lock_path),
            "previous_event_sha256": previous.get("event_sha256") if previous else None,
            "payload": payload,
        }
        row = dict(unsigned)
        row["event_sha256"] = hashlib.sha256(
            _canonical_json(unsigned).encode("utf-8")
        ).hexdigest()
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(self.journal_path, flags, 0o640)
        try:
            os.write(descriptor, (_canonical_json(row) + "\n").encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._write_state(row)
        self._last_event = row
        return row

    def __enter__(self) -> "Host02JobXpMutex":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.lock_path, flags, 0o640)
        self._handle = os.fdopen(descriptor, "a+", encoding="utf-8")
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._handle.close()
            self._handle = None
            raise Host02MutexError(
                f"host02 JOB/XP pressure mutex is busy: {self.lock_path}"
            ) from exc

        try:
            rows = self._read_journal()
            if rows and rows[-1].get("event") not in {
                "lock_released", "acquisition_rejected", "postrun_zero_pressure_failed"
            }:
                self._append(
                    "stale_owner_recovered",
                    stale_owner_id=rows[-1].get("owner_id"),
                    stale_event=rows[-1].get("event"),
                    kernel_lock_reacquired=True,
                )
            self._append("lock_acquired", implementation="posix_flock_exclusive_nonblocking")
            try:
                probe = _validate_probe_payload(
                    self.pressure_probe(self.scenario, "before_pressure"),
                    "before_pressure",
                )
            except Exception as exc:
                self._append("acquisition_rejected", reason=str(exc))
                raise
            self._append("preflight_zero_pressure_passed", probe=probe)
            self._append(
                "handoff_acquired",
                atomic_under_flock=True,
                pressure_scope="before_R0_through_recovery_and_checksum",
            )
            return self
        except Exception:
            if self._handle is not None:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
                self._handle.close()
            self._handle = None
            raise

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if self._handle is None:
            return False
        post_error: Exception | None = None
        try:
            if exc_type is not None:
                self._append(
                    "campaign_aborted",
                    exception_type=getattr(exc_type, "__name__", str(exc_type)),
                    reason=str(exc),
                )
            try:
                probe = _validate_probe_payload(
                    self.pressure_probe(self.scenario, "after_recovery_checksum"),
                    "after_recovery_checksum",
                )
                if exc_type is None:
                    self._append(
                        "recovery_checksum_scope_complete",
                        probe=probe,
                        pressure_scope_complete=True,
                    )
                else:
                    self._append("aborted_scope_zero_pressure_passed", probe=probe)
            except Exception as failure:
                post_error = failure
                self._append("postrun_zero_pressure_failed", reason=str(failure))
            self._append(
                "lock_released",
                release_reason=("complete" if exc_type is None and post_error is None else "failed"),
                kernel_releases_on_close=True,
            )
        finally:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None
        if post_error is not None:
            raise Host02MutexError(str(post_error)) from post_error
        return False


def mutex_from_campaign(
    config: Mapping[str, Any],
    bundle: Mapping[str, Any],
    benchmark: str,
) -> Host02JobXpMutex | Host02IsolatedParallelGuard | None:
    """Build the mandatory shared wrapper for the two host02 launch paths."""
    scenario = HOST02_BENCHMARK_SCENARIO.get(benchmark)
    if scenario is None:
        return None
    raw = config.get("host02_job_xp_mutex")
    if not isinstance(raw, Mapping) or raw.get("required") is not True:
        raise Host02MutexError(
            f"{benchmark} requires host02_job_xp_mutex.required=true"
        )
    if raw.get("scenario") != scenario:
        raise Host02MutexError(
            f"host02 mutex scenario mismatch: expected={scenario} actual={raw.get('scenario')!r}"
        )
    probe = bundle.get("host02_pressure_probe")
    if not callable(probe):
        raise Host02MutexError(
            f"{benchmark} service bundle requires callable host02_pressure_probe"
        )
    mode = str(raw.get("mode") or "full_lifecycle_exclusive")
    if mode == "isolated_parallel_dynamic_gate":
        return Host02IsolatedParallelGuard(
            scenario=scenario,
            pressure_probe=probe,
            journal_path=Path(
                str(raw.get("journal_path") or DEFAULT_HOST02_JOURNAL_PATH)
            ),
        )
    if mode == "full_lifecycle_exclusive":
        return Host02JobXpMutex(
            scenario=scenario,
            pressure_probe=probe,
            lock_path=Path(str(raw.get("lock_path") or DEFAULT_HOST02_LOCK_PATH)),
            journal_path=Path(
                str(raw.get("journal_path") or DEFAULT_HOST02_JOURNAL_PATH)
            ),
        )
    raise Host02MutexError(
        "host02 mode must be full_lifecycle_exclusive or "
        f"isolated_parallel_dynamic_gate; got {mode!r}"
    )
