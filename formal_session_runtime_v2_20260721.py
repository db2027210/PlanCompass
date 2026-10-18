#!/usr/bin/env python3
"""Fail-closed runtime protocol support for six_session_20260721_v2.

This module deliberately contains no experiment defaults.  Every fixed value is
read from the frozen v2 bundle, and the source bundle is validated before a
campaign copy, READY file, or scheduler state is trusted.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import time
from typing import Any, Iterable, Mapping


FROZEN_ROOT = Path(
    "/home/anonymous/program/resource_adaptive_mysql/preflight/"
    "frozen_formal_config_20260721_v2"
)
GLOBAL_ROOT = Path(
    "/home/anonymous/program/resource_adaptive_mysql/history/unified_formal/"
    "six_session_20260721_v2"
)
SHARED_ROOT = GLOBAL_ROOT / "shared"
SHARED_FROZEN_ROOT = SHARED_ROOT / "frozen_config"
FREEZE_ID = "frozen_formal_config_20260721_v2"
GLOBAL_CAMPAIGN_ID = "six_session_20260721_v2"
RESOURCE_AWARENESS_SHA256 = (
    "1f13306c7910249b23f890df221671e13b914a4e9824a34de56392e7a2d79658"
)
SHARED_READY_FIELDS = {
    "schema_version",
    "status",
    "freeze_id",
    "global_campaign_id",
    "frozen_config_relative_path",
    "checksums_sha256",
    "payload_file_count",
    "resource_awareness_sha256",
    "point_schedule_sha256",
    "publisher_session_id",
    "published_at_utc",
}
ENGINE_READY_FIELDS = {
    "schema_version",
    "status",
    "freeze_id",
    "global_campaign_id",
    "engine",
    "plan_gate_manifest_relative_path",
    "plan_gate_manifest_sha256",
    "environment_readback_relative_path",
    "environment_readback_sha256",
    "publisher_session_id",
    "published_at_utc",
}
SCHEDULER_FIELDS = {
    "schema_version",
    "freeze_id",
    "point_schedule_sha256",
    "next_ordinal",
    "active",
    "completed_entry_count",
    "completed_entries",
}
ACTIVE_FIELDS = {
    "ordinal",
    "session_id",
    "run_id",
    "repeat_id",
    "attempt_id",
    "claimed_at_utc",
}
COMPLETED_FIELDS = {
    "ordinal",
    "session_id",
    "run_id",
    "repeat_id",
    "status",
    "attempt_id",
    "result_manifest_relative_path",
    "result_manifest_sha256",
    "completed_at_utc",
}


class ProtocolError(RuntimeError):
    """A frozen publication or scheduler invariant was violated."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    require_regular(path)
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ProtocolError(f"non-finite JSON constant in {path}: {value}")
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"cannot parse JSON {path}: {exc}") from exc


def require_regular(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise ProtocolError(f"required file is absent: {path}") from exc
    if not stat.S_ISREG(mode) or path.is_symlink():
        raise ProtocolError(f"required path is not a regular non-symlink file: {path}")


def require_directory(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise ProtocolError(f"required directory is absent: {path}") from exc
    if not stat.S_ISDIR(mode) or path.is_symlink():
        raise ProtocolError(f"required path is not a non-symlink directory: {path}")


def sha256_file(path: Path) -> str:
    require_regular(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _exact_fields(payload: Mapping[str, Any], fields: set[str], label: str) -> None:
    actual = set(payload)
    if actual != fields:
        raise ProtocolError(
            f"{label} field set mismatch: missing={sorted(fields - actual)} "
            f"extra={sorted(actual - fields)}"
        )


def _expect_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(f"{label} must be an integer")
    return value


def _expect_str(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ProtocolError(f"{label} must be a string")
    return value


def _validate_timestamp(value: Any, label: str) -> None:
    text = _expect_str(value, label)
    if not text.endswith("Z"):
        raise ProtocolError(f"{label} must be an RFC3339 UTC timestamp")
    try:
        dt.datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise ProtocolError(f"invalid {label}: {text}") from exc


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def parse_checksums(path: Path) -> dict[str, str]:
    require_regular(path)
    result: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line:
            continue
        pieces = line.split(maxsplit=1)
        if len(pieces) != 2:
            raise ProtocolError(f"malformed checksum line {line_number} in {path}")
        digest, name = pieces
        name = name.lstrip("*")
        relative = PurePosixPath(name)
        if (
            len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            or relative.is_absolute()
            or ".." in relative.parts
            or len(relative.parts) != 1
            or name in result
        ):
            raise ProtocolError(f"unsafe checksum entry on line {line_number}: {line!r}")
        result[name] = digest
    if not result:
        raise ProtocolError(f"empty checksum file: {path}")
    return result


def _validate_checksum_payload(root: Path) -> dict[str, str]:
    entries = parse_checksums(root / "checksums.sha256")
    for name, expected in entries.items():
        actual = sha256_file(root / name)
        if actual != expected:
            raise ProtocolError(
                f"payload checksum mismatch for {root / name}: expected={expected} actual={actual}"
            )
    return entries


def validate_source_bundle(root: Path = FROZEN_ROOT) -> dict[str, Any]:
    require_directory(root)
    entries = _validate_checksum_payload(root)
    ready = load_json(root / "FROZEN_READY.json")
    if not isinstance(ready, dict):
        raise ProtocolError("FROZEN_READY.json must contain an object")
    fixed = {
        "schema_version": 1,
        "freeze_id": FREEZE_ID,
        "status": "ready",
        "immutable": True,
        "payload_file_count": 13,
        "checksums_file": "checksums.sha256",
        "resource_awareness_sha256": RESOURCE_AWARENESS_SHA256,
    }
    for key, expected in fixed.items():
        if ready.get(key) != expected:
            raise ProtocolError(f"FROZEN_READY.{key} mismatch")
    if len(entries) != ready["payload_file_count"]:
        raise ProtocolError("FROZEN_READY payload_file_count mismatch")
    checksums_sha = sha256_file(root / "checksums.sha256")
    if ready.get("checksums_sha256") != checksums_sha:
        raise ProtocolError("FROZEN_READY checksums_sha256 mismatch")
    contract = load_json(root / "freeze_contract.json")
    copies = contract.get("resource_awareness_copies") if isinstance(contract, dict) else None
    if not isinstance(copies, list) or len(copies) != 2:
        raise ProtocolError("freeze contract must name exactly two resource-awareness copies")
    copy_paths = [Path(_expect_str(item, "resource awareness path")) for item in copies]
    raw_bytes = []
    for path in copy_paths:
        if sha256_file(path) != RESOURCE_AWARENESS_SHA256:
            raise ProtocolError(f"resource-awareness SHA mismatch: {path}")
        raw_bytes.append(path.read_bytes())
    if raw_bytes[0] != raw_bytes[1]:
        raise ProtocolError("PG/MySQL resource-awareness files are not byte-identical")
    return {
        "status": "passed",
        "root": str(root),
        "payload_file_count": len(entries),
        "checksums_sha256": checksums_sha,
        "point_schedule_sha256": sha256_file(root / "point_schedule.json"),
        "resource_awareness_sha256": RESOURCE_AWARENESS_SHA256,
        "payloads": entries,
    }


def _validate_shared_ready_object(payload: Any, checksums_sha: str, schedule_sha: str) -> None:
    if not isinstance(payload, dict):
        raise ProtocolError("SHARED_READY must be an object")
    _exact_fields(payload, SHARED_READY_FIELDS, "SHARED_READY")
    fixed = {
        "schema_version": 1,
        "status": "ready",
        "freeze_id": FREEZE_ID,
        "global_campaign_id": GLOBAL_CAMPAIGN_ID,
        "frozen_config_relative_path": "frozen_config",
        "checksums_sha256": checksums_sha,
        "payload_file_count": 13,
        "resource_awareness_sha256": RESOURCE_AWARENESS_SHA256,
        "point_schedule_sha256": schedule_sha,
        "publisher_session_id": "pg_brute_force",
    }
    for key, expected in fixed.items():
        if payload.get(key) != expected:
            raise ProtocolError(f"SHARED_READY.{key} mismatch")
    _validate_timestamp(payload["published_at_utc"], "SHARED_READY.published_at_utc")


def validate_shared_bundle(
    source_root: Path = FROZEN_ROOT, shared_root: Path = SHARED_ROOT
) -> dict[str, Any]:
    source = validate_source_bundle(source_root)
    target = shared_root / "frozen_config"
    require_directory(shared_root)
    require_directory(target)
    expected_names = set(source["payloads"]) | {"checksums.sha256", "FROZEN_READY.json"}
    actual_names: set[str] = set()
    for child in target.iterdir():
        if child.is_symlink() or not stat.S_ISREG(child.lstat().st_mode):
            raise ProtocolError(f"non-regular entry in copied frozen bundle: {child}")
        actual_names.add(child.name)
    if actual_names != expected_names:
        raise ProtocolError(
            f"copied frozen bundle file set mismatch: "
            f"missing={sorted(expected_names - actual_names)} extra={sorted(actual_names - expected_names)}"
        )
    _validate_checksum_payload(target)
    for name in expected_names:
        if (source_root / name).read_bytes() != (target / name).read_bytes():
            raise ProtocolError(f"shared/source byte mismatch: {name}")
    checksums_sha = sha256_file(target / "checksums.sha256")
    schedule_sha = sha256_file(target / "point_schedule.json")
    ready_path = shared_root / "SHARED_READY.json"
    ready = load_json(ready_path)
    _validate_shared_ready_object(ready, checksums_sha, schedule_sha)
    return {
        "status": "passed",
        "shared_ready": str(ready_path),
        "checksums_sha256": checksums_sha,
        "point_schedule_sha256": schedule_sha,
        "payload_file_count": len(source["payloads"]),
    }


def safe_campaign_file(relative_text: str, global_root: Path = GLOBAL_ROOT) -> Path:
    relative = PurePosixPath(relative_text)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ProtocolError(f"unsafe campaign-relative path: {relative_text!r}")
    candidate = global_root.joinpath(*relative.parts)
    resolved_root = global_root.resolve(strict=False)
    resolved = candidate.resolve(strict=False)
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ProtocolError(f"campaign path escapes global root: {relative_text!r}")
    require_regular(candidate)
    return candidate


def _manifest_common(payload: Any, engine: str, label: str) -> Mapping[str, Any]:
    if not isinstance(payload, dict):
        raise ProtocolError(f"{label} must contain an object")
    fixed = {
        "schema_version": 1,
        "freeze_id": FREEZE_ID,
        "global_campaign_id": GLOBAL_CAMPAIGN_ID,
        "engine": engine,
        "status": "passed",
    }
    for key, expected in fixed.items():
        if payload.get(key) != expected:
            raise ProtocolError(f"{label}.{key} mismatch")
    return payload


def _validate_plan_gate(payload: Any, engine: str) -> None:
    gate = _manifest_common(payload, engine, "plan gate")
    expected_points = ["P0", *[f"G{index}" for index in range(11)]]
    points = gate.get("point_set")
    if points != expected_points and set(points or []) != set(expected_points):
        raise ProtocolError("plan gate point_set is not exact P0 plus G0..G10")
    benchmark = load_json(SHARED_FROZEN_ROOT / "benchmark_matrix.json")
    expected_cases = [
        item["case_id"]
        for item in benchmark.get("cases", [])
        if item.get("engine") == engine
    ]
    actual_cases = gate.get("case_ids")
    if not isinstance(actual_cases, list):
        results = gate.get("case_results")
        if isinstance(results, list):
            actual_cases = [item.get("case_id") for item in results if isinstance(item, dict)]
    if set(actual_cases or []) != set(expected_cases) or len(actual_cases or []) != len(expected_cases):
        raise ProtocolError(f"plan gate case_ids mismatch for {engine}")
    results = gate.get("case_results")
    if not isinstance(results, list) or len(results) != len(expected_cases):
        raise ProtocolError("plan gate must contain one case_result per engine case")
    for result in results:
        if not isinstance(result, dict) or result.get("status") != "passed":
            raise ProtocolError("every plan-gate case_result must be passed")
        fingerprints = result.get("distinct_structural_plan_fingerprints")
        if _expect_int(fingerprints, "distinct structural fingerprints") < 2:
            raise ProtocolError("plan gate requires at least two structural fingerprints")
        if result.get("correctness_passed") is not True:
            raise ProtocolError("plan gate correctness did not pass")
        completion = result.get("single_query_completion_seconds_max")
        if isinstance(completion, bool) or not isinstance(completion, (int, float)) or completion > 600:
            raise ProtocolError("plan gate single-query completion exceeds 600 seconds")
        if result.get("resource_effect_passed") is not True:
            raise ProtocolError("plan gate resource-effect threshold did not pass")


def _validate_environment(payload: Any, engine: str) -> None:
    env = _manifest_common(payload, engine, "environment readback")
    required_sections = {
        "resource_profiles",
        "fixed_database_settings",
        "machine",
        "images",
        "artifacts",
        "templates",
        "cost_apply_protocol",
    }
    missing = required_sections - set(env)
    if missing:
        raise ProtocolError(f"environment readback missing required sections: {sorted(missing)}")
    for key in required_sections:
        section = env.get(key)
        if not isinstance(section, dict) or section.get("status") != "passed":
            raise ProtocolError(f"environment readback section did not pass: {key}")


def engine_ready_path(engine: str) -> Path:
    if engine == "postgresql":
        return SHARED_ROOT / "PG_ENGINE_READY.json"
    if engine == "mysql":
        return SHARED_ROOT / "MYSQL_ENGINE_READY.json"
    raise ProtocolError(f"unsupported engine: {engine}")


def validate_engine_ready(engine: str) -> dict[str, Any]:
    validate_shared_bundle()
    path = engine_ready_path(engine)
    ready = load_json(path)
    if not isinstance(ready, dict):
        raise ProtocolError(f"{path.name} must contain an object")
    _exact_fields(ready, ENGINE_READY_FIELDS, path.name)
    fixed_by_engine = {
        "postgresql": {
            "engine": "postgresql",
            "plan_gate_manifest_relative_path": "postgresql/preflight/plan_gate.json",
            "environment_readback_relative_path": "postgresql/preflight/environment_readback.json",
            "publisher_session_id": "pg_brute_force",
        },
        "mysql": {
            "engine": "mysql",
            "plan_gate_manifest_relative_path": "mysql/preflight/plan_gate.json",
            "environment_readback_relative_path": "mysql/preflight/environment_readback.json",
            "publisher_session_id": "mysql_brute_force",
        },
    }
    fixed = {
        "schema_version": 1,
        "status": "ready",
        "freeze_id": FREEZE_ID,
        "global_campaign_id": GLOBAL_CAMPAIGN_ID,
        **fixed_by_engine[engine],
    }
    for key, expected in fixed.items():
        if ready.get(key) != expected:
            raise ProtocolError(f"{path.name}.{key} mismatch")
    _validate_timestamp(ready["published_at_utc"], f"{path.name}.published_at_utc")
    plan_path = safe_campaign_file(ready["plan_gate_manifest_relative_path"])
    env_path = safe_campaign_file(ready["environment_readback_relative_path"])
    if sha256_file(plan_path) != ready["plan_gate_manifest_sha256"]:
        raise ProtocolError(f"{path.name} plan-gate SHA mismatch")
    if sha256_file(env_path) != ready["environment_readback_sha256"]:
        raise ProtocolError(f"{path.name} environment SHA mismatch")
    _validate_plan_gate(load_json(plan_path), engine)
    _validate_environment(load_json(env_path), engine)
    return {
        "status": "passed",
        "engine": engine,
        "ready_path": str(path),
        "plan_gate_manifest_sha256": ready["plan_gate_manifest_sha256"],
        "environment_readback_sha256": ready["environment_readback_sha256"],
    }


def validate_all_ready() -> dict[str, Any]:
    shared = validate_shared_bundle()
    pg = validate_engine_ready("postgresql")
    mysql = validate_engine_ready("mysql")
    return {"status": "passed", "shared": shared, "postgresql": pg, "mysql": mysql}


def load_schedule() -> tuple[dict[str, Any], dict[int, dict[str, Any]], str]:
    schedule_path = SHARED_FROZEN_ROOT / "point_schedule.json"
    schedule = load_json(schedule_path)
    if not isinstance(schedule, dict) or schedule.get("total_entries") != 84:
        raise ProtocolError("static point schedule is invalid")
    entries = schedule.get("entries")
    if not isinstance(entries, list) or len(entries) != 84:
        raise ProtocolError("static point schedule must contain 84 entries")
    by_ordinal: dict[int, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ProtocolError("schedule entry must be an object")
        ordinal = _expect_int(entry.get("ordinal"), "schedule ordinal")
        if ordinal in by_ordinal:
            raise ProtocolError(f"duplicate schedule ordinal: {ordinal}")
        by_ordinal[ordinal] = entry
    if sorted(by_ordinal) != list(range(1, 85)):
        raise ProtocolError("schedule ordinals are not contiguous 1..84")
    return schedule, by_ordinal, sha256_file(schedule_path)


def _matching_schedule_entry(state_entry: Mapping[str, Any], schedule_entry: Mapping[str, Any]) -> None:
    for key in ("ordinal", "session_id", "run_id", "repeat_id"):
        if state_entry.get(key) != schedule_entry.get(key):
            raise ProtocolError(f"scheduler entry {key} does not match static schedule")


def _result_field(payload: Mapping[str, Any], key: str) -> Any:
    if key in payload:
        return payload[key]
    meta = payload.get("meta")
    if isinstance(meta, dict) and key in meta:
        return meta[key]
    result = payload.get("result")
    if isinstance(result, dict) and key in result:
        return result[key]
    return None


def validate_scheduler_state() -> dict[str, Any]:
    validate_all_ready()
    _, schedule, schedule_sha = load_schedule()
    state_path = SHARED_ROOT / "scheduler_state.json"
    state = load_json(state_path)
    if not isinstance(state, dict):
        raise ProtocolError("scheduler state must contain an object")
    _exact_fields(state, SCHEDULER_FIELDS, "scheduler state")
    if state.get("schema_version") != 1 or state.get("freeze_id") != FREEZE_ID:
        raise ProtocolError("scheduler state fixed values mismatch")
    if state.get("point_schedule_sha256") != schedule_sha:
        raise ProtocolError("scheduler state schedule SHA mismatch")
    next_ordinal = _expect_int(state.get("next_ordinal"), "scheduler next_ordinal")
    if not 1 <= next_ordinal <= 85:
        raise ProtocolError("scheduler next_ordinal is outside 1..85")
    completed_count = _expect_int(
        state.get("completed_entry_count"), "scheduler completed_entry_count"
    )
    completed = state.get("completed_entries")
    if not isinstance(completed, list) or completed_count != len(completed):
        raise ProtocolError("scheduler completed-entry count mismatch")
    if completed_count != next_ordinal - 1:
        raise ProtocolError("scheduler completed entries are not contiguous before next_ordinal")
    seen: set[int] = set()
    for expected_ordinal, entry in enumerate(completed, 1):
        if not isinstance(entry, dict):
            raise ProtocolError("completed scheduler entry must be an object")
        _exact_fields(entry, COMPLETED_FIELDS, "completed scheduler entry")
        ordinal = _expect_int(entry.get("ordinal"), "completed ordinal")
        if ordinal != expected_ordinal or ordinal in seen:
            raise ProtocolError("completed scheduler ordinals are not unique contiguous values")
        seen.add(ordinal)
        _matching_schedule_entry(entry, schedule[ordinal])
        if entry.get("status") != "complete":
            raise ProtocolError("completed scheduler status must be complete")
        _validate_timestamp(entry.get("completed_at_utc"), "completed_at_utc")
        manifest = safe_campaign_file(entry.get("result_manifest_relative_path"))
        if sha256_file(manifest) != entry.get("result_manifest_sha256"):
            raise ProtocolError("completed result manifest SHA mismatch")
        result = load_json(manifest)
        if not isinstance(result, dict) or _result_field(result, "status") != "complete":
            raise ProtocolError("completed result manifest is not status=complete")
        expected = schedule[ordinal]
        expected_values = {
            "freeze_id": FREEZE_ID,
            "engine": expected["engine"],
            "method": expected["method"],
            "run_id": expected["run_id"],
            "repeat_id": expected["repeat_id"],
        }
        for key, value in expected_values.items():
            if _result_field(result, key) != value:
                raise ProtocolError(f"result manifest {key} does not match schedule")
    active = state.get("active")
    if active is not None:
        if not isinstance(active, dict):
            raise ProtocolError("scheduler active must be null or an object")
        _exact_fields(active, ACTIVE_FIELDS, "scheduler active")
        ordinal = _expect_int(active.get("ordinal"), "active ordinal")
        if ordinal != next_ordinal or ordinal not in schedule:
            raise ProtocolError("scheduler active ordinal mismatch")
        _matching_schedule_entry(active, schedule[ordinal])
        _validate_timestamp(active.get("claimed_at_utc"), "active claimed_at_utc")
    return state


def claim_scheduler_entry(session_id: str, attempt_id: int) -> dict[str, Any]:
    """Claim or resume exactly next_ordinal; caller must already hold the common host flock."""
    if isinstance(attempt_id, bool) or not isinstance(attempt_id, int) or attempt_id <= 0:
        raise ProtocolError("scheduler claim attempt_id must be a positive integer")
    state = validate_scheduler_state()
    _, schedule, _ = load_schedule()
    next_ordinal = int(state["next_ordinal"])
    if next_ordinal == 85:
        return {"status": "campaign_complete", "next_ordinal": 85}
    expected = schedule[next_ordinal]
    active = state["active"]
    if active is not None:
        if active["session_id"] != session_id:
            return {
                "status": "owned_by_other_session", "next_ordinal": next_ordinal,
                "expected_session_id": expected["session_id"], "active": active,
            }
        _matching_schedule_entry(active, expected)
        active = {
            "ordinal": next_ordinal,
            "session_id": session_id,
            "run_id": expected["run_id"],
            "repeat_id": expected["repeat_id"],
            "attempt_id": attempt_id,
            "claimed_at_utc": utc_now(),
        }
        replacement = dict(state)
        replacement["active"] = active
        atomic_replace_json(SHARED_ROOT / "scheduler_state.json", replacement)
        validate_scheduler_state()
        return {"status": "resumed", "schedule_entry": expected, "active": active}
    if expected["session_id"] != session_id:
        return {
            "status": "not_this_session", "next_ordinal": next_ordinal,
            "expected_session_id": expected["session_id"], "schedule_entry": expected,
        }
    active = {
        "ordinal": next_ordinal,
        "session_id": session_id,
        "run_id": expected["run_id"],
        "repeat_id": expected["repeat_id"],
        "attempt_id": attempt_id,
        "claimed_at_utc": utc_now(),
    }
    replacement = dict(state)
    replacement["active"] = active
    atomic_replace_json(SHARED_ROOT / "scheduler_state.json", replacement)
    validate_scheduler_state()
    return {"status": "claimed", "schedule_entry": expected, "active": active}


def complete_scheduler_entry(
    session_id: str, result_manifest_relative_path: str,
) -> dict[str, Any]:
    """Advance exactly one active ordinal after a complete manifest is durable."""
    state = validate_scheduler_state()
    active = state["active"]
    if not isinstance(active, dict) or active.get("session_id") != session_id:
        raise ProtocolError("scheduler completion requires this session's active entry")
    _, schedule, _ = load_schedule()
    ordinal = int(active["ordinal"])
    expected = schedule[ordinal]
    manifest = safe_campaign_file(result_manifest_relative_path)
    engine_root = GLOBAL_ROOT / expected["engine"]
    resolved = manifest.resolve()
    if engine_root.resolve() not in resolved.parents:
        raise ProtocolError("result manifest is outside the matching engine output root")
    result = load_json(manifest)
    required = {
        "status": "complete", "freeze_id": FREEZE_ID,
        "engine": expected["engine"], "method": expected["method"],
        "run_id": expected["run_id"], "repeat_id": expected["repeat_id"],
    }
    for key, value in required.items():
        if _result_field(result, key) != value:
            raise ProtocolError(f"result manifest {key} does not match active schedule entry")
    completed = {
        "ordinal": ordinal,
        "session_id": session_id,
        "run_id": expected["run_id"],
        "repeat_id": expected["repeat_id"],
        "status": "complete",
        "attempt_id": active["attempt_id"],
        "result_manifest_relative_path": result_manifest_relative_path,
        "result_manifest_sha256": sha256_file(manifest),
        "completed_at_utc": utc_now(),
    }
    replacement = dict(state)
    replacement["completed_entries"] = [*state["completed_entries"], completed]
    replacement["completed_entry_count"] = int(state["completed_entry_count"]) + 1
    replacement["next_ordinal"] = ordinal + 1
    replacement["active"] = None
    atomic_replace_json(SHARED_ROOT / "scheduler_state.json", replacement)
    validate_scheduler_state()
    return {"status": "advanced", "completed": completed, "next_ordinal": ordinal + 1}


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _serialize_json(payload: Any) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")


def _new_temp_path(directory: Path, basename: str) -> Path:
    nonce = os.urandom(12).hex()
    return directory / f".{basename}.tmp.{os.getpid()}.{nonce}"


def _write_exclusive_fsynced(path: Path, data: bytes, mode: int = 0o644) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def _rename_noreplace(source: Path, target: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise ProtocolError("renameat2(RENAME_NOREPLACE) is unavailable")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    at_fdcwd = -100
    rename_noreplace = 1
    result = renameat2(
        at_fdcwd,
        os.fsencode(source),
        at_fdcwd,
        os.fsencode(target),
        rename_noreplace,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise ProtocolError(f"atomic no-replace rename failed for {target}: {os.strerror(error)}")


def atomic_publish_new_json(path: Path, payload: Any) -> None:
    require_directory(path.parent)
    temporary = _new_temp_path(path.parent, path.name)
    _write_exclusive_fsynced(temporary, _serialize_json(payload))
    try:
        _rename_noreplace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_replace_json(path: Path, payload: Any) -> None:
    require_directory(path.parent)
    temporary = _new_temp_path(path.parent, path.name)
    _write_exclusive_fsynced(temporary, _serialize_json(payload))
    try:
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def publish_mysql_engine_ready(plan_path: Path, environment_path: Path) -> dict[str, Any]:
    validate_shared_bundle()
    expected_plan = GLOBAL_ROOT / "mysql/preflight/plan_gate.json"
    expected_environment = GLOBAL_ROOT / "mysql/preflight/environment_readback.json"
    if plan_path.resolve() != expected_plan.resolve() or environment_path.resolve() != expected_environment.resolve():
        raise ProtocolError("MySQL engine manifests must use the frozen protocol paths")
    _validate_plan_gate(load_json(plan_path), "mysql")
    _validate_environment(load_json(environment_path), "mysql")
    payload = {
        "schema_version": 1,
        "status": "ready",
        "freeze_id": FREEZE_ID,
        "global_campaign_id": GLOBAL_CAMPAIGN_ID,
        "engine": "mysql",
        "plan_gate_manifest_relative_path": "mysql/preflight/plan_gate.json",
        "plan_gate_manifest_sha256": sha256_file(plan_path),
        "environment_readback_relative_path": "mysql/preflight/environment_readback.json",
        "environment_readback_sha256": sha256_file(environment_path),
        "publisher_session_id": "mysql_brute_force",
        "published_at_utc": utc_now(),
    }
    atomic_publish_new_json(engine_ready_path("mysql"), payload)
    return validate_engine_ready("mysql")


def wait_for_regular(path: Path, timeout_seconds: float, poll_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            require_regular(path)
            return
        except ProtocolError:
            if time.monotonic() >= deadline:
                raise ProtocolError(f"timed out waiting for {path}")
            time.sleep(poll_seconds)


def _print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate-source")
    subparsers.add_parser("validate-shared")
    wait_parser = subparsers.add_parser("wait-shared")
    wait_parser.add_argument("--timeout-seconds", type=float, default=86400)
    wait_parser.add_argument("--poll-seconds", type=float, default=5)
    engine_parser = subparsers.add_parser("validate-engine")
    engine_parser.add_argument("engine", choices=("postgresql", "mysql"))
    subparsers.add_parser("validate-all-ready")
    subparsers.add_parser("validate-scheduler")
    publish_parser = subparsers.add_parser("publish-mysql-engine-ready")
    publish_parser.add_argument(
        "--plan-gate", type=Path, default=GLOBAL_ROOT / "mysql/preflight/plan_gate.json"
    )
    publish_parser.add_argument(
        "--environment",
        type=Path,
        default=GLOBAL_ROOT / "mysql/preflight/environment_readback.json",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "validate-source":
            result = validate_source_bundle()
        elif args.command == "validate-shared":
            result = validate_shared_bundle()
        elif args.command == "wait-shared":
            wait_for_regular(
                SHARED_ROOT / "SHARED_READY.json", args.timeout_seconds, args.poll_seconds
            )
            result = validate_shared_bundle()
        elif args.command == "validate-engine":
            result = validate_engine_ready(args.engine)
        elif args.command == "validate-all-ready":
            result = validate_all_ready()
        elif args.command == "validate-scheduler":
            result = validate_scheduler_state()
        elif args.command == "publish-mysql-engine-ready":
            result = publish_mysql_engine_ready(args.plan_gate, args.environment)
        else:  # pragma: no cover
            raise ProtocolError(f"unknown command: {args.command}")
        _print(result)
        return 0
    except ProtocolError as exc:
        print(f"PROTOCOL_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
