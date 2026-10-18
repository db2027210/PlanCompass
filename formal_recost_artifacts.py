"""Durable artifact contract for one complete formal MySQL Recost run."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
from typing import Any, Mapping


REQUIRED_ATTEMPT_FILES = (
    "round_manifest.json",
    "events.jsonl",
    "inner_iterations.jsonl",
    "params_before.json",
    "params_candidate.json",
    "params_applied.json",
    "plan.json",
    "plan_fingerprint.json",
    "resource_samples.jsonl",
    "database_samples.jsonl",
    "throughput_windows.jsonl",
    "benchmark_result.json",
    "stability.json",
    "decision.json",
    "round_summary.json",
)
RUN_LEVEL_FILES = ("run_manifest.json", "run_summary.json", "workload_raw.log")
CHECKSUM_FILE = "checksums.sha256"
RESULT_MANIFEST = "result_manifest.json"
EXCLUDED_FROM_CHECKSUMS = {CHECKSUM_FILE, RESULT_MANIFEST}
TERMINAL_ATTEMPT_STATUSES = {"complete", "failed", "timeout", "oom", "interrupted"}
ROUND_KINDS = {"baseline", "candidate", "default_validation", "final_validation"}


class RecostArtifactError(RuntimeError):
    """A formal Run tree is unsafe, malformed, incomplete, or tampered."""


def _directory(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise RecostArtifactError(f"missing directory: {path}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise RecostArtifactError(f"expected non-symlink directory: {path}")


def _regular(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise RecostArtifactError(f"missing regular file: {path}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise RecostArtifactError(f"expected regular non-symlink file: {path}")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def sha256_file(path: Path) -> str:
    _regular(path)
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    digest = hashlib.sha256()
    try:
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                return digest.hexdigest()
            digest.update(block)
    finally:
        os.close(descriptor)


def create_attempt_directory(run_root: Path, round_index: int, attempt_index: int) -> Path:
    _directory(run_root)
    if round_index < 0 or attempt_index < 1:
        raise RecostArtifactError("round/attempt indexes are outside the formal range")
    round_path = run_root / f"round_{round_index:03d}"
    round_path.mkdir(mode=0o755, exist_ok=True)
    _directory(round_path)
    attempt = round_path / f"attempt_{attempt_index:02d}"
    try:
        attempt.mkdir(mode=0o755)
    except FileExistsError as exc:
        raise RecostArtifactError(f"attempt already exists and cannot be overwritten: {attempt}") from exc
    _fsync_directory(round_path)
    return attempt


def _strict_bytes(value: Any, *, jsonl: bool = False) -> bytes:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            indent=None if jsonl else 2,
            separators=(",", ":") if jsonl else None,
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise RecostArtifactError(f"non-strict JSON value: {exc}") from exc
    return (text + "\n").encode("utf-8")


def write_new_bytes(path: Path, payload: bytes, *, mode: int = 0o644) -> None:
    _directory(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    descriptor = os.open(path, flags, mode)
    try:
        view = memoryview(payload)
        while view:
            count = os.write(descriptor, view)
            if count <= 0:
                raise RecostArtifactError(f"short write: {path}")
            view = view[count:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def write_new_json(path: Path, value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise RecostArtifactError("JSON artifact must be an object")
    write_new_bytes(path, _strict_bytes(dict(value)))


def write_empty_new(path: Path) -> None:
    write_new_bytes(path, b"")


def append_jsonl_fsync(path: Path, value: Mapping[str, Any]) -> None:
    _directory(path.parent)
    if path.exists() or path.is_symlink():
        _regular(path)
    payload = _strict_bytes(dict(value), jsonl=True)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o644)
    try:
        view = memoryview(payload)
        while view:
            count = os.write(descriptor, view)
            if count <= 0:
                raise RecostArtifactError(f"short JSONL append: {path}")
            view = view[count:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RecostArtifactError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_json(path: Path) -> Any:
    _regular(path)
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                RecostArtifactError(f"non-finite JSON token {token} in {path}")
            ),
        )
    except RecostArtifactError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RecostArtifactError(f"malformed JSON {path}: {exc}") from exc


def _parse_jsonl(path: Path) -> list[Mapping[str, Any]]:
    _regular(path)
    rows: list[Mapping[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise RecostArtifactError(f"blank JSONL line {line_number}: {path}")
        try:
            value = json.loads(
                line,
                object_pairs_hook=_reject_pairs,
                parse_constant=lambda token: (_ for _ in ()).throw(
                    RecostArtifactError(f"non-finite JSONL token {token} in {path}")
                ),
            )
        except RecostArtifactError:
            raise
        except json.JSONDecodeError as exc:
            raise RecostArtifactError(f"malformed JSONL {path}:{line_number}: {exc}") from exc
        if not isinstance(value, Mapping):
            raise RecostArtifactError(f"JSONL row is not an object: {path}:{line_number}")
        rows.append(value)
    return rows


def validate_attempt(attempt: Path) -> Mapping[str, Any]:
    _directory(attempt)
    for name in REQUIRED_ATTEMPT_FILES:
        _regular(attempt / name)
    manifest = _parse_json(attempt / "round_manifest.json")
    summary = _parse_json(attempt / "round_summary.json")
    if not isinstance(manifest, Mapping) or not isinstance(summary, Mapping):
        raise RecostArtifactError("round manifest/summary must be objects")
    kind = manifest.get("kind")
    if kind not in ROUND_KINDS:
        raise RecostArtifactError(f"invalid round kind: {kind}")
    status = summary.get("status")
    if status not in TERMINAL_ATTEMPT_STATUSES:
        raise RecostArtifactError(f"attempt has no terminal status: {attempt}")

    inner = _parse_jsonl(attempt / "inner_iterations.jsonl")
    if kind == "candidate":
        if not inner:
            raise RecostArtifactError("Recost candidate has no Inner iterations")
        for row in inner:
            if row.get("label") != "inner_step_accepted":
                raise RecostArtifactError("Recost Inner row has the wrong label")
            if "outer_candidate_accepted" in row:
                raise RecostArtifactError("Recost Inner row contains an Outer decision")
    elif inner:
        raise RecostArtifactError(f"non-candidate round has Inner iterations: {attempt}")

    events = _parse_jsonl(attempt / "events.jsonl")
    if not events or events[0].get("event") != "round_start" or events[-1].get("event") != "round_end":
        raise RecostArtifactError(f"attempt lacks round_start/round_end bracketing: {attempt}")
    for name in (
        "params_before.json", "params_candidate.json", "params_applied.json",
        "plan.json", "plan_fingerprint.json", "benchmark_result.json",
        "stability.json", "decision.json",
    ):
        if not isinstance(_parse_json(attempt / name), Mapping):
            raise RecostArtifactError(f"{name} must contain an object")
    resource_rows = _parse_jsonl(attempt / "resource_samples.jsonl")
    database_rows = _parse_jsonl(attempt / "database_samples.jsonl")
    _parse_jsonl(attempt / "throughput_windows.jsonl")
    if status == "complete" and (not resource_rows or not database_rows):
        raise RecostArtifactError(f"complete attempt lacks raw one-second samples: {attempt}")
    return {"kind": kind, "status": status, "inner_iterations": len(inner)}


def _relative(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise RecostArtifactError(f"artifact escapes Run root: {path}") from exc
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise RecostArtifactError(f"unsafe relative artifact: {relative}")
    return relative.as_posix()


def covered_files(run_root: Path) -> tuple[Path, ...]:
    _directory(run_root)
    files: list[Path] = []
    for path in run_root.rglob("*"):
        relative = _relative(run_root, path)
        if any(part.startswith(".") and ".tmp." in part for part in Path(relative).parts):
            raise RecostArtifactError(f"temporary file remains: {relative}")
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise RecostArtifactError(f"symlink is forbidden: {relative}")
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise RecostArtifactError(f"non-regular artifact is forbidden: {relative}")
        if relative not in EXCLUDED_FROM_CHECKSUMS:
            files.append(path)
    return tuple(sorted(files, key=lambda path: _relative(run_root, path)))


def write_checksums(run_root: Path) -> Mapping[str, Any]:
    entries = [(sha256_file(path), _relative(run_root, path)) for path in covered_files(run_root)]
    payload = "".join(f"{digest}  {relative}\n" for digest, relative in entries).encode("utf-8")
    write_new_bytes(run_root / CHECKSUM_FILE, payload)
    result = validate_checksums(run_root)
    return {"path": CHECKSUM_FILE, "sha256": sha256_file(run_root / CHECKSUM_FILE), **result}


def validate_checksums(run_root: Path) -> Mapping[str, Any]:
    checksum = run_root / CHECKSUM_FILE
    _regular(checksum)
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(checksum.read_text(encoding="utf-8").splitlines(), 1):
        fields = line.split("  ", 1)
        if len(fields) != 2 or len(fields[0]) != 64:
            raise RecostArtifactError(f"malformed checksum line {line_number}")
        digest, relative_text = fields
        try:
            int(digest, 16)
        except ValueError as exc:
            raise RecostArtifactError(f"invalid digest at checksum line {line_number}") from exc
        relative = Path(relative_text)
        if relative.is_absolute() or ".." in relative.parts or relative_text in EXCLUDED_FROM_CHECKSUMS:
            raise RecostArtifactError(f"unsafe/excluded checksum path: {relative_text}")
        normalized = relative.as_posix()
        if normalized in seen:
            raise RecostArtifactError(f"duplicate checksum path: {normalized}")
        seen.add(normalized)
        target = run_root / relative
        if sha256_file(target) != digest:
            raise RecostArtifactError(f"checksum mismatch: {normalized}")
        entries.append((digest, normalized))
    expected = {_relative(run_root, path) for path in covered_files(run_root)}
    if seen != expected:
        raise RecostArtifactError(
            f"checksum coverage mismatch: missing={sorted(expected - seen)} extra={sorted(seen - expected)}"
        )
    if [name for _, name in entries] != sorted(seen):
        raise RecostArtifactError("checksum entries are not sorted")
    return {"status": "passed", "entry_count": len(entries)}


def validate_complete_run(run_root: Path, *, expected_candidate_count: int) -> Mapping[str, Any]:
    _directory(run_root)
    if not 6 <= expected_candidate_count <= 10:
        raise RecostArtifactError("complete Recost candidate count must be 6..10")
    for name in RUN_LEVEL_FILES + (CHECKSUM_FILE, RESULT_MANIFEST):
        _regular(run_root / name)
    result_manifest = _parse_json(run_root / RESULT_MANIFEST)
    if not isinstance(result_manifest, Mapping) or result_manifest.get("status") != "complete":
        raise RecostArtifactError("result manifest is not status complete")
    round_paths = sorted(path for path in run_root.glob("round_[0-9][0-9][0-9]") if path.is_dir())
    expected_round_count = 1 + expected_candidate_count + 3 + 3
    if len(round_paths) != expected_round_count:
        raise RecostArtifactError(
            f"Run round count differs: expected={expected_round_count} actual={len(round_paths)}"
        )
    kinds: list[str] = []
    for index, round_path in enumerate(round_paths):
        if round_path.name != f"round_{index:03d}":
            raise RecostArtifactError("Run rounds are not contiguous from round_000")
        attempts = sorted(path for path in round_path.glob("attempt_[0-9][0-9]") if path.is_dir())
        if not attempts:
            raise RecostArtifactError(f"round has no attempts: {round_path}")
        validations = [validate_attempt(attempt) for attempt in attempts]
        kinds.append(str(validations[-1]["kind"]))
        if index == 0 and validations[-1]["status"] != "complete":
            raise RecostArtifactError("baseline latest attempt is not complete")
    expected_kinds = (
        ["baseline"]
        + ["candidate"] * expected_candidate_count
        + ["default_validation"] * 3
        + ["final_validation"] * 3
    )
    if kinds != expected_kinds:
        raise RecostArtifactError(f"round kind sequence differs: {kinds}")
    for result in [validate_attempt(sorted(path for path in round_path.glob("attempt_[0-9][0-9]") if path.is_dir())[-1]) for round_path in round_paths[-6:]]:
        if result["status"] != "complete":
            raise RecostArtifactError("default/final validation latest attempt is not complete")
    checksum_result = validate_checksums(run_root)
    return {
        "status": "passed",
        "candidate_count": expected_candidate_count,
        "round_count": expected_round_count,
        "checksum": checksum_result,
    }
