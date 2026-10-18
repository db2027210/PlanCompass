#!/usr/bin/env python3
"""Regenerate only the draft MySQL SGD R07--R10 local-code evidence chain.

Dry-run is the default.  ``--execute`` is required for atomic publication.
This tool never changes scenario evidence, bundle_publication, status, launch
authorization, or any READY file.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
DRAFT = ROOT / "config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft"
READINESS = DRAFT / "readiness_manifest.json"
METHOD = DRAFT / "experience_table_sgd_method_manifest.json"
SESSION = DRAFT / "session_adapter_test_evidence.json"
LOGGING = DRAFT / "logging_checkpoint_test_evidence.json"
MUTEX = DRAFT / "host02_job_xp_mutex_test_evidence.json"
GLOBAL_AUDIT = DRAFT / "global_readiness_audit_r07_r10.json"
CONTRACT = ROOT / "preflight/mysql_experience_sgd_eight_algorithm_contract_20260723.sha256"
AUDIT = DRAFT / "r07_r10_evidence_regeneration_audit.json"
GENERATOR_REL = "shell/regenerate_mysql_sgd_r07_r10_evidence.py"

SESSION_TESTS = (
    "tests/test_formal_mysql_database_qps.py",
    "tests/test_formal_mysql_sgd_orchestration.py",
    "tests/test_formal_mysql_sgd_checkpoint.py",
    "tests/test_formal_mysql_continuous_session_factory.py",
    "tests/test_formal_benchbase_continuous_session.py",
    "tests/test_formal_hybench_continuous_session.py",
    "tests/test_formal_job_continuous_session.py",
    "shell/test_mysql_all_subset_integration.py",
    "shell/test_mysql_formal_outer_campaign.py",
)
MUTEX_TESTS = (
    "shell/test_host02_job_xp_mutex.py",
    "shell/test_mysql_formal_outer_campaign.py",
    "shell/test_verify_mysql_sgd_v2_readiness.py",
)
EXPECTED_METHOD_SOURCES = (
    "gradient_descent_optimizer.py", "experience_table_subset_optimizer.py",
    "common_outer_loop.py", "resource_utility.py", "mysql_plan_clustering.py",
    "trained_model_estimator.py", "mysql_parameter_optimizer.py",
    "formal_stability.py", "formal_resource_sampler_20260721.py",
    "formal_mysql_sgd_orchestration.py", "formal_mysql_sgd_checkpoint.py",
    "formal_mysql_sgd_campaign.py", "host02_job_xp_mutex.py",
)
EXPECTED_CONTRACT_PATHS = (
    "AGENTS.md",
    "config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft/FROZEN_CONFIG.md",
    "preflight/mysql_five_benchmark_resource_envelope_and_search_algorithms_20260722.md",
    "gradient_descent_optimizer.py", "experience_table_subset_optimizer.py",
    "common_outer_loop.py", "resource_utility.py", "mysql_plan_clustering.py",
    "trained_model_estimator.py", "mysql_parameter_optimizer.py",
    "formal_stability.py", "formal_resource_sampler_20260721.py",
    "formal_mysql_sgd_orchestration.py", "formal_mysql_database_qps.py",
    "formal_mysql_continuous_session_factory.py", "formal_job_continuous_session.py",
    "formal_hybench_continuous_session.py", "formal_benchbase_continuous_session.py",
    "formal_mysql_sgd_checkpoint.py", "formal_mysql_sgd_campaign.py",
    "host02_job_xp_mutex.py",
    "config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft/experience_table_sgd_formal_config.json",
    "shell/verify_mysql_sgd_v2_readiness.py",
)
PROTECTED_READINESS_FIELDS = (
    "status", "launch_authorized", "scenarios", "policy",
)
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
PYTEST_RE = re.compile(r"(?P<passed>\d+) passed(?:, (?P<skipped>\d+) skipped)?")


class RegenerationError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RegenerationError(f"JSON root must be an object: {path}")
    return value


def require_keys(value: Mapping[str, Any], keys: Iterable[str], label: str) -> None:
    missing = sorted(set(keys) - set(value))
    if missing:
        raise RegenerationError(f"{label} missing required keys: {missing}")


def canonical_json(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def pointer(path: str) -> dict[str, str]:
    target = ROOT / path
    if not target.is_file():
        raise RegenerationError(f"registered source missing: {path}")
    return {"path": path, "sha256": sha256_file(target)}


def update_pointer_collection(value: Any, label: str) -> Any:
    if isinstance(value, list):
        result = []
        for item in value:
            if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
                raise RegenerationError(f"invalid pointer in {label}")
            result.append(pointer(item["path"]))
        return result
    if isinstance(value, Mapping):
        result = {}
        for name, item in value.items():
            if not isinstance(item, Mapping) or item.get("path") != name:
                raise RegenerationError(f"pointer key/path mismatch in {label}: {name}")
            result[name] = pointer(name)
        return result
    raise RegenerationError(f"{label} must be a pointer list or object")


def parse_contract() -> list[str]:
    paths: list[str] = []
    for line_no, line in enumerate(CONTRACT.read_text(encoding="utf-8").splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64})  (\S(?:.*\S)?)", line)
        if not match:
            raise RegenerationError(f"invalid checksum contract line {line_no}")
        rel = match.group(2)
        if Path(rel).is_absolute() or ".." in Path(rel).parts:
            raise RegenerationError(f"unsafe contract path: {rel}")
        if rel in paths:
            raise RegenerationError(f"duplicate contract path: {rel}")
        if not (ROOT / rel).is_file():
            raise RegenerationError(f"contract source missing: {rel}")
        paths.append(rel)
    if not paths:
        raise RegenerationError("empty checksum contract")
    return paths


def run_suite(paths: tuple[str, ...]) -> dict[str, Any]:
    command = [sys.executable, "-m", "pytest", "-q", *paths]
    completed = subprocess.run(
        command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False,
    )
    output = completed.stdout.strip()
    if completed.returncode != 0:
        raise RegenerationError(
            f"frozen test suite failed rc={completed.returncode}:\n{output}"
        )
    matches = list(PYTEST_RE.finditer(output))
    if not matches:
        raise RegenerationError(f"cannot parse pytest result:\n{output}")
    match = matches[-1]
    return {
        "command": " ".join(command),
        "exit_code": completed.returncode,
        "passed": int(match.group("passed")),
        "failed": 0,
        "skipped": int(match.group("skipped") or 0),
        "normalized_result": (
            f"{match.group('passed')} passed"
            + (f", {match.group('skipped')} skipped" if match.group("skipped") else "")
        ),
    }


def deterministic_time(paths: Iterable[str]) -> str:
    epoch = max(int((ROOT / path).stat().st_mtime) for path in paths)
    return datetime.fromtimestamp(epoch, timezone.utc).astimezone().isoformat(timespec="seconds")


def sidecar_bytes(target: Path, payload: bytes) -> bytes:
    return f"{sha256_bytes(payload)}  {target.name}\n".encode()


def atomic_publish(outputs: Mapping[Path, bytes]) -> None:
    DRAFT.mkdir(parents=True, exist_ok=True)
    staged: dict[Path, Path] = {}
    try:
        for target, payload in outputs.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
            temp_path = Path(temporary)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            staged[target] = temp_path
        # The readiness manifest is the final commit marker for this local chain.
        ordered = sorted(outputs, key=lambda p: (p == READINESS, str(p)))
        for target in ordered:
            os.replace(staged.pop(target), target)
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--reason",
        default="eight-algorithm implementation update including automatic 10-to-K replay and frozen top-5 validation",
    )
    args = parser.parse_args(argv)

    readiness = load_object(READINESS)
    method = load_object(METHOD)
    session = load_object(SESSION)
    logging = load_object(LOGGING)
    mutex = load_object(MUTEX)
    global_audit = load_object(GLOBAL_AUDIT)
    require_keys(readiness, [*PROTECTED_READINESS_FIELDS, "global_requirements", "draft_id"], "readiness")
    if not isinstance(
        (readiness.get("global_requirements") or {}).get("bundle_publication"), Mapping
    ):
        raise RegenerationError("readiness missing global bundle_publication object")
    require_keys(method, ["draft_id", "method", "formal_config", "registered_sources", "integrity_contract"], "method manifest")
    require_keys(session, ["passed", "test_sources", "production_sources", "capabilities"], "session evidence")
    require_keys(logging, ["passed", "implementation_sources", "test_result"], "logging evidence")
    require_keys(mutex, ["passed", "sources", "tests", "test_run"], "mutex evidence")
    if readiness["draft_id"] != method["draft_id"]:
        raise RegenerationError("draft identity mismatch")
    if method["method"] != "experience_table_all_subset_sgd":
        raise RegenerationError("unexpected method identity")
    if method.get("formal_launch_authorized") is not False:
        raise RegenerationError("method manifest must remain launch-disabled")
    if readiness.get("status") != "NOT_READY" or readiness.get("launch_authorized") is not False:
        raise RegenerationError("only an unpublished NOT_READY draft may be regenerated")
    if readiness["global_requirements"]["bundle_publication"].get("ready") is not False:
        raise RegenerationError("published/ready bundle cannot be regenerated")
    method_source_paths = tuple(item.get("path") for item in method["registered_sources"])
    if method_source_paths != EXPECTED_METHOD_SOURCES:
        raise RegenerationError("method registered source set/order differs from frozen generator contract")
    formal_config = method.get("formal_config") or {}
    formal_config_path = formal_config.get("path")
    if (
        formal_config_path
        != "config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft/experience_table_sgd_formal_config.json"
        or formal_config.get("sha256") != sha256_file(ROOT / formal_config_path)
    ):
        raise RegenerationError("formal config pointer is missing or already stale")
    if method.get("integrity_contract", {}).get("path") != str(CONTRACT.relative_to(ROOT)):
        raise RegenerationError("unexpected integrity contract path")

    contract_paths = parse_contract()
    if tuple(contract_paths) != EXPECTED_CONTRACT_PATHS:
        raise RegenerationError("integrity contract path set/order differs from frozen generator contract")
    tracked_paths = sorted(set(
        contract_paths
        + [GENERATOR_REL]
        + [item["path"] for item in method["registered_sources"]]
        + [item["path"] for item in session["test_sources"]]
        + [item["path"] for item in session["production_sources"]]
        + [item["path"] for item in logging["implementation_sources"]]
        + [item["path"] for item in mutex["sources"].values()]
    ))
    before_sources = {path: sha256_file(ROOT / path) for path in tracked_paths}
    old_hashes = {
        str(path.relative_to(ROOT)): sha256_file(path)
        for path in (CONTRACT, METHOD, SESSION, LOGGING, MUTEX, GLOBAL_AUDIT, READINESS)
    }

    session_result = run_suite(SESSION_TESTS)
    mutex_result = run_suite(MUTEX_TESTS)
    after_sources = {path: sha256_file(ROOT / path) for path in tracked_paths}
    if before_sources != after_sources:
        changed = [path for path in tracked_paths if before_sources[path] != after_sources[path]]
        raise RegenerationError(f"tracked sources changed during test execution: {changed}")
    generated_at = deterministic_time(tracked_paths)

    contract_payload = "".join(
        f"{after_sources[path]}  {path}\n" for path in contract_paths
    ).encode()

    new_method = copy.deepcopy(method)
    new_method["registered_sources"] = update_pointer_collection(
        method["registered_sources"], "method.registered_sources"
    )
    new_method["integrity_contract"]["sha256"] = sha256_bytes(contract_payload)
    method_payload = canonical_json(new_method)

    new_session = copy.deepcopy(session)
    new_session["tested_at"] = generated_at
    new_session["interpreter"] = f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    version = subprocess.run(
        [sys.executable, "-m", "pytest", "--version"], cwd=ROOT,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True,
    ).stdout.strip()
    new_session["pytest"] = version.removeprefix("pytest ")
    new_session["test_command"] = session_result["command"]
    new_session["test_suite_result"] = {
        "exit_code": 0, "passed": session_result["passed"],
        "failed": 0, "skipped": session_result["skipped"],
    }
    new_session["test_sources"] = update_pointer_collection(session["test_sources"], "session.test_sources")
    new_session["production_sources"] = update_pointer_collection(session["production_sources"], "session.production_sources")
    session_payload = canonical_json(new_session)

    new_logging = copy.deepcopy(logging)
    new_logging["tested_at"] = generated_at
    new_logging["test_result"] = {
        "shared_with": SESSION.name,
        "exit_code": 0,
        "passed": session_result["passed"],
        "failed": 0,
        "skipped": session_result["skipped"],
    }
    new_logging["implementation_sources"] = update_pointer_collection(
        logging["implementation_sources"], "logging.implementation_sources"
    )
    logging_payload = canonical_json(new_logging)

    new_mutex = copy.deepcopy(mutex)
    new_mutex["generated_at"] = generated_at
    new_mutex["sources"] = update_pointer_collection(mutex["sources"], "mutex.sources")
    new_mutex["test_run"] = {
        "command": mutex_result["command"],
        "result": mutex_result["normalized_result"],
        "exit_code": 0,
        "passed": mutex_result["passed"],
        "failed": 0,
        "skipped": mutex_result["skipped"],
    }
    new_mutex.pop("broader_regression", None)
    mutex_payload = canonical_json(new_mutex)

    core_payloads = {
        CONTRACT: contract_payload, METHOD: method_payload, SESSION: session_payload,
        LOGGING: logging_payload, MUTEX: mutex_payload,
    }
    core_unchanged = all(
        target.is_file() and target.read_bytes() == payload
        for target, payload in core_payloads.items()
    )
    if core_unchanged:
        # Avoid the self-referential cycle old -> supersedes -> new global hash.
        # A fully current chain keeps the previously signed audit byte-for-byte.
        global_payload = GLOBAL_AUDIT.read_bytes()
    else:
        new_global = copy.deepcopy(global_audit)
        new_global["audited_at"] = generated_at
        new_global["regeneration_reason"] = args.reason
        new_global["supersedes"] = copy.deepcopy(old_hashes)
        new_global["findings"]["R07.method_bundle"]["basis"] = (
            "Regenerated registered source and integrity-contract hashes after passing frozen local suites."
        )
        new_global["findings"]["R08.session_adapter"]["basis"] = (
            f"Frozen session adapter suite passed: {session_result['normalized_result']}."
        )
        new_global["findings"]["R09.logging_contract"]["basis"] = (
            f"Logging/checkpoint contracts passed in the {session_result['passed']}-test frozen suite."
        )
        new_global["findings"]["R10.host02_job_xp_mutex"]["basis"] = (
            f"Frozen host02 mutex suite passed: {mutex_result['normalized_result']}."
        )
        new_global["launch_authorized"] = False
        new_global["overall_ready"] = False
        global_payload = canonical_json(new_global)

    new_readiness = copy.deepcopy(readiness)
    globals_new = new_readiness["global_requirements"]
    globals_new["method_bundle"]["method_manifest"] = {
        "path": str(METHOD.relative_to(ROOT)), "sha256": sha256_bytes(method_payload),
    }
    globals_new["method_bundle"]["registered_sources"] = copy.deepcopy(
        new_method["registered_sources"]
    )
    globals_new["session_adapter"]["evidence"]["sha256"] = sha256_bytes(session_payload)
    globals_new["logging_contract"]["evidence"]["sha256"] = sha256_bytes(logging_payload)
    globals_new["host02_job_xp_mutex"]["evidence"]["sha256"] = sha256_bytes(mutex_payload)
    for field in PROTECTED_READINESS_FIELDS:
        if new_readiness[field] != readiness[field]:
            raise RegenerationError(f"protected readiness field changed: {field}")
    if (
        new_readiness["global_requirements"]["bundle_publication"]
        != readiness["global_requirements"]["bundle_publication"]
    ):
        raise RegenerationError("protected bundle_publication changed")
    readiness_payload = canonical_json(new_readiness)

    future_hashes = {
        str(path.relative_to(ROOT)): sha256_bytes(payload)
        for path, payload in {
            CONTRACT: contract_payload, METHOD: method_payload, SESSION: session_payload,
            LOGGING: logging_payload, MUTEX: mutex_payload, GLOBAL_AUDIT: global_payload,
            READINESS: readiness_payload,
        }.items()
    }
    fully_unchanged = (
        core_unchanged
        and GLOBAL_AUDIT.read_bytes() == global_payload
        and READINESS.read_bytes() == readiness_payload
        and AUDIT.is_file()
    )
    if fully_unchanged:
        audit_payload = AUDIT.read_bytes()
    else:
        regeneration_audit = {
            "schema_version": 1,
            "generated_at": generated_at,
            "scope": ["R07", "R08", "R09", "R10"],
            "reason": args.reason,
            "mode": "published_artifact",
            "supersedes": old_hashes,
            "replacement_sha256": future_hashes,
            "source_sha256": after_sources,
            "generator": pointer(GENERATOR_REL),
            "test_results": {"session_adapter": session_result, "host02_mutex": mutex_result},
            "protected_readiness_fields_unchanged": True,
            "bundle_publication_unchanged": True,
            "ready_or_launch_authorization_set": False,
        }
        audit_payload = canonical_json(regeneration_audit)

    outputs: dict[Path, bytes] = {
        CONTRACT: contract_payload, METHOD: method_payload, SESSION: session_payload,
        LOGGING: logging_payload, MUTEX: mutex_payload, GLOBAL_AUDIT: global_payload,
        READINESS: readiness_payload, AUDIT: audit_payload,
    }
    for target, payload in list(outputs.items()):
        outputs[target.with_name(target.name + ".sha256")] = sidecar_bytes(target, payload)

    report = {
        "status": "PASS",
        "mode": "execute" if args.execute else "dry-run",
        "would_write": [str(path.relative_to(ROOT)) for path in sorted(outputs)],
        "supersedes": old_hashes,
        "replacement_sha256": future_hashes,
        "test_results": {"session_adapter": session_result, "host02_mutex": mutex_result},
        "launch_authorized": False,
    }
    if args.execute:
        atomic_publish(outputs)
        for target, payload in outputs.items():
            if target.read_bytes() != payload:
                raise RegenerationError(f"post-publication byte verification failed: {target}")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, RegenerationError) as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
