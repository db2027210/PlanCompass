"""Exclusive scheduler client for the MySQL Recost formal session.

Only :class:`RecostScheduleLease` mutates the real scheduler, and only while it
owns the common host flock.  Pure transition helpers are exposed for tests so
tests never touch campaign state.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from formal_mysql_recost_spec import ENGINE, METHOD, SESSION_ID, RecostRunSpec, load_recost_schedule
from formal_session_runtime_20260721 import (
    FREEZE_ID,
    GLOBAL_ROOT,
    SHARED_ROOT,
    ProtocolError,
    atomic_replace_json,
    load_json,
    load_schedule,
    sha256_file,
    utc_now,
    validate_all_ready,
    validate_scheduler_state,
)


LOCK_PATH = Path(
    "/home/anonymous/program/resource_adaptive_mysql/history/unified_formal/.host_measurement.lock"
)
SCHEDULER_PATH = SHARED_ROOT / "scheduler_state.json"
MYSQL_OUTPUT_ROOT = GLOBAL_ROOT / "mysql"


class SchedulerNotOurTurn(RuntimeError):
    """The frozen next ordinal belongs to another session."""


def _attempt_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", value):
        raise ProtocolError("attempt_id must be 1..160 safe ASCII characters")
    return value


def _expected_entry(schedule_by_ordinal: Mapping[int, Mapping[str, Any]], ordinal: int) -> Mapping[str, Any]:
    entry = schedule_by_ordinal.get(ordinal)
    if not isinstance(entry, Mapping):
        raise ProtocolError(f"missing frozen schedule ordinal {ordinal}")
    return entry


def claim_transition(
    state: Mapping[str, Any],
    schedule_by_ordinal: Mapping[int, Mapping[str, Any]],
    *,
    attempt_id: str,
    claimed_at_utc: str,
) -> tuple[dict[str, Any], Mapping[str, Any], bool]:
    """Pure claim transition; returns state, entry and whether it was resumed."""

    attempt = _attempt_id(attempt_id)
    result = copy.deepcopy(dict(state))
    ordinal = int(result["next_ordinal"])
    entry = _expected_entry(schedule_by_ordinal, ordinal)
    active = result.get("active")
    if active is not None:
        if not isinstance(active, Mapping):
            raise ProtocolError("scheduler active is malformed")
        for key in ("ordinal", "session_id", "run_id", "repeat_id"):
            if active.get(key) != entry.get(key):
                raise ProtocolError(f"active scheduler {key} differs from frozen entry")
        if active.get("session_id") != SESSION_ID:
            raise SchedulerNotOurTurn(
                f"active ordinal {ordinal} belongs to {active.get('session_id')}"
            )
        # A durable active run must resume its original scheduler attempt id.
        return result, entry, True
    if entry.get("session_id") != SESSION_ID:
        raise SchedulerNotOurTurn(
            f"next ordinal {ordinal} belongs to {entry.get('session_id')}"
        )
    result["active"] = {
        "ordinal": ordinal,
        "session_id": SESSION_ID,
        "run_id": entry["run_id"],
        "repeat_id": entry["repeat_id"],
        "attempt_id": attempt,
        "claimed_at_utc": claimed_at_utc,
    }
    return result, entry, False


def completion_transition(
    state: Mapping[str, Any],
    schedule_by_ordinal: Mapping[int, Mapping[str, Any]],
    *,
    result_manifest_relative_path: str,
    result_manifest_sha256: str,
    completed_at_utc: str,
) -> dict[str, Any]:
    """Pure successful completion transition after manifest validation."""

    result = copy.deepcopy(dict(state))
    active = result.get("active")
    if not isinstance(active, Mapping):
        raise ProtocolError("cannot complete without an active scheduler entry")
    ordinal = int(result["next_ordinal"])
    entry = _expected_entry(schedule_by_ordinal, ordinal)
    for key in ("ordinal", "session_id", "run_id", "repeat_id"):
        if active.get(key) != entry.get(key):
            raise ProtocolError(f"completion active {key} differs from frozen entry")
    if active.get("session_id") != SESSION_ID:
        raise SchedulerNotOurTurn("cannot complete another session's active entry")
    completed = result.get("completed_entries")
    if not isinstance(completed, list) or len(completed) != ordinal - 1:
        raise ProtocolError("completed entries are not contiguous before active ordinal")
    completed.append(
        {
            "ordinal": ordinal,
            "session_id": SESSION_ID,
            "run_id": entry["run_id"],
            "repeat_id": entry["repeat_id"],
            "status": "complete",
            "attempt_id": active["attempt_id"],
            "result_manifest_relative_path": result_manifest_relative_path,
            "result_manifest_sha256": result_manifest_sha256,
            "completed_at_utc": completed_at_utc,
        }
    )
    result["completed_entries"] = completed
    result["completed_entry_count"] = len(completed)
    result["active"] = None
    result["next_ordinal"] = ordinal + 1
    return result


def _manifest_field(document: Mapping[str, Any], name: str) -> Any:
    if name in document:
        return document[name]
    for section_name in ("meta", "result"):
        section = document.get(section_name)
        if isinstance(section, Mapping) and name in section:
            return section[name]
    return None


def validate_completion_manifest(path: Path, spec: RecostRunSpec) -> tuple[str, str]:
    try:
        relative = path.resolve(strict=True).relative_to(MYSQL_OUTPUT_ROOT.resolve(strict=True))
    except (FileNotFoundError, ValueError) as exc:
        raise ProtocolError("result manifest must resolve under the MySQL output root") from exc
    if path.is_symlink() or not path.is_file():
        raise ProtocolError("result manifest must be a regular non-symlink file")
    document = load_json(path)
    if not isinstance(document, Mapping):
        raise ProtocolError("result manifest must contain a JSON object")
    expected = {
        "status": "complete",
        "freeze_id": FREEZE_ID,
        "engine": ENGINE,
        "method": METHOD,
        "run_id": spec.run_id,
        "repeat_id": spec.repeat_id,
    }
    for key, value in expected.items():
        if _manifest_field(document, key) != value:
            raise ProtocolError(f"result manifest {key} does not match the active run")
    return str(Path("mysql") / relative), sha256_file(path)


class RecostScheduleLease(AbstractContextManager["RecostScheduleLease"]):
    """Own the host lock and one current MySQL Recost schedule entry."""

    def __init__(self, attempt_id: str) -> None:
        self.requested_attempt_id = _attempt_id(attempt_id)
        self._lock_handle: Any = None
        self.entry: Mapping[str, Any] | None = None
        self.spec: RecostRunSpec | None = None
        self.resumed = False
        self.completed = False

    def __enter__(self) -> "RecostScheduleLease":
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._lock_handle = LOCK_PATH.open("a+b")
        fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            # These validations intentionally occur after acquiring the same
            # lock that will be held through the entire optimization run.
            validate_all_ready()
            current = validate_scheduler_state()
            _, schedule, _ = load_schedule()
            replacement, entry, resumed = claim_transition(
                current,
                schedule,
                attempt_id=self.requested_attempt_id,
                claimed_at_utc=utc_now(),
            )
            if not resumed:
                atomic_replace_json(SCHEDULER_PATH, replacement)
                current = validate_scheduler_state()
                if current.get("active") != replacement.get("active"):
                    raise ProtocolError("scheduler claim readback differs")
            self.entry = entry
            specs = {(item.run_id, item.repeat_id): item for item in load_recost_schedule()}
            self.spec = specs[(str(entry["run_id"]), int(entry["repeat_id"]))]
            self.resumed = resumed
            return self
        except BaseException:
            self._release()
            raise

    @property
    def scheduler_attempt_id(self) -> str:
        if self._lock_handle is None:
            raise ProtocolError("schedule lease is not active")
        state = load_json(SCHEDULER_PATH)
        active = state.get("active") if isinstance(state, Mapping) else None
        if not isinstance(active, Mapping):
            raise ProtocolError("active scheduler entry disappeared")
        return str(active["attempt_id"])

    def complete(self, result_manifest: Path) -> None:
        if self.completed or self.spec is None:
            raise ProtocolError("schedule lease cannot be completed")
        current = validate_scheduler_state()
        _, schedule, _ = load_schedule()
        relative, digest = validate_completion_manifest(result_manifest, self.spec)
        replacement = completion_transition(
            current,
            schedule,
            result_manifest_relative_path=relative,
            result_manifest_sha256=digest,
            completed_at_utc=utc_now(),
        )
        atomic_replace_json(SCHEDULER_PATH, replacement)
        readback = validate_scheduler_state()
        if int(readback["next_ordinal"]) != self.spec.ordinal + 1:
            raise ProtocolError("scheduler completion readback did not advance exactly once")
        self.completed = True

    def _release(self) -> None:
        if self._lock_handle is not None:
            try:
                fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
            finally:
                self._lock_handle.close()
                self._lock_handle = None

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._release()
