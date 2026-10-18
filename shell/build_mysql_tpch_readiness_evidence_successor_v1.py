#!/usr/bin/env python3
"""Build an immutable TPC-H successor with pre-cleanup readiness evidence.

This publisher is offline-only.  It verifies and copies the exact 295f
closure, patches only the sealed bootstrap authority, adds the zero-credit
295f invalidation, and publishes atomically by content address.
"""

from __future__ import annotations

import hashlib
import json
import errno
import os
from pathlib import Path
import secrets
import shutil
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PREDECESSOR_ID = (
    "295f9808ff5b6d81a8760f1feb82eabf5fd0ba8c1d18ea82a2f35b98908ca728"
)
PREDECESSOR_MANIFEST_SHA256 = (
    "2e9490ba3417c35a4e7567d9a95cc8d006820e8f97adf75a21c9358cfea65b12"
)
PREDECESSOR = (
    ROOT / "artifacts/mysql_tpch_effective_authority_successor_v1"
    / PREDECESSOR_ID
)
AUTHORITY_RELATIVE = "mysql_tpch_distributed_v2_authority.py"
INVALIDATION = (
    ROOT
    / "preflight/mysql_tpch_295f_formal_tcp_readiness_invalidation_20260731.json"
)
INVALIDATION_RELATIVE = (
    "invalidated_295f_formal_tcp_readiness_bundle.json"
)
BUILDER_RELATIVE = (
    "builder/build_mysql_tpch_readiness_evidence_successor_v1.py"
)
BUNDLE_ROOT = (
    ROOT / "artifacts/mysql_tpch_readiness_evidence_successor_v1"
)


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict[str, Any]:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise BuildError("absent or symlinked: " + str(path))
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError("JSON object required: " + str(path))
    return value


def relative_files(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError("symlink in bundle closure: " + str(path))
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            if relative not in (
                "bundle_manifest.json",
                "bundle_manifest.json.sha256",
            ):
                result[relative] = sha256(path)
        elif not path.is_dir():
            raise BuildError("special file in bundle closure: " + str(path))
    return result


def verify_predecessor() -> dict[str, Any]:
    manifest_path = PREDECESSOR / "bundle_manifest.json"
    sidecar = PREDECESSOR / "bundle_manifest.json.sha256"
    manifest = load(manifest_path)
    unsigned = {
        key: value for key, value in manifest.items() if key != "bundle_id"
    }
    if (
        PREDECESSOR.name != PREDECESSOR_ID
        or sha256(manifest_path) != PREDECESSOR_MANIFEST_SHA256
        or sidecar.is_symlink()
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii")
        != PREDECESSOR_MANIFEST_SHA256 + "  bundle_manifest.json\n"
        or manifest.get("bundle_id") != PREDECESSOR_ID
        or sha_bytes(canonical(unsigned)) != PREDECESSOR_ID
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("postgresql_mutation_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or relative_files(PREDECESSOR) != manifest.get("files")
    ):
        raise BuildError("295f predecessor identity/authority differs")
    return manifest


def verify_invalidation() -> None:
    value = load(INVALIDATION)
    required = value.get("required_successor_evidence") or {}
    if (
        value.get("schema_version")
        != "mysql_tpch_295f_formal_tcp_readiness_invalidation_v1"
        or value.get("status") != "INVALIDATED_DO_NOT_RERUN"
        or value.get("bundle_id") != PREDECESSOR_ID
        or value.get("bundle_manifest_sha256")
        != PREDECESSOR_MANIFEST_SHA256
        or value.get("failed_readiness_stage") != "formal_database_tcp"
        or value.get("formal_database_existed_at_failure") is not True
        or value.get("bootstrap_database_already_removed_at_failure") is not True
        or value.get("workload_started") is not False
        or any(
            value.get(key) != 0
            for key in (
                "capture_credit",
                "qualification_credit",
                "r0_credit",
                "accepted_rounds_credit",
            )
        )
        or value.get("replay_or_rerun_authorized") is not False
        or not required
        or any(item is not True for item in required.values())
    ):
        raise BuildError("295f zero-credit invalidation differs")


def _replace_once(payload: str, old: str, new: str, label: str) -> str:
    if payload.count(old) != 1:
        raise BuildError("authority patch point differs: " + label)
    return payload.replace(old, new, 1)


def authority_payload() -> bytes:
    source_path = PREDECESSOR / AUTHORITY_RELATIVE
    before = sha256(source_path)
    source = source_path.read_text(encoding="utf-8")
    if sha256(source_path) != before:
        raise BuildError("295f authority changed during read")
    old_wait = '''def _wait_mysql(container: str, defaults_file: str | None = None) -> None:
    for _ in range(180):
        argv = (
            ["docker", "exec", container, "mysqladmin", "-uroot", "ping", "--silent"]
            if defaults_file is None
            else [
                "/usr/bin/mysql", f"--defaults-file={defaults_file}",
                "--protocol=TCP", "--host=127.0.0.1",
                f"--port={MYSQL_PORT}", "--user=root",
                "--connect-timeout=3", "--ssl-mode=DISABLED",
                "--get-server-public-key", "-NBe", "SELECT 1",
            ]
        )
        if _run(argv, timeout=10, check=False).returncode == 0:
            return
        time.sleep(1)
    raise AuthorityError("MySQL did not become ready")
'''
    new_wait = r'''READINESS_PROBE_TEXT_LIMIT = 4096
READINESS_LOG_TEXT_LIMIT = 16384


def _redacted_bounded_text(raw: str | None, limit: int) -> dict[str, Any]:
    """Return bounded diagnostic text with common secret forms removed."""
    if not isinstance(limit, int) or limit < 256 or limit > 65536:
        raise AuthorityError("diagnostic text limit differs")
    text = str(raw or "")
    substitutions = (
        (
            re.compile(
                r"(?i)(identified(?:\s+with\s+[A-Za-z0-9_]+)?\s+by\s+)"
                r"(?:'[^']*'|\"[^\"]*\"|\S+)"
            ),
            r"\1<redacted>",
        ),
        (
            re.compile(
                r"(?i)((?:password|passwd|token|secret)"
                r"(?:_file|_value)?\s*[:=]\s*)(?:'[^']*'|\"[^\"]*\"|\S+)"
            ),
            r"\1<redacted>",
        ),
        (
            re.compile(r"(?i)(--password(?:=|\s+))\S+"),
            r"\1<redacted>",
        ),
    )
    safe = text
    for pattern, replacement in substitutions:
        safe = pattern.sub(replacement, safe)
    truncated = len(safe) > limit
    safe = safe[-limit:]
    return {
        "text": safe,
        "text_sha256": sha_bytes(safe.encode("utf-8", errors="replace")),
        "truncated": truncated,
        "retained_character_count": len(safe),
        "secret_redaction_applied": True,
    }


def _readiness_trace_document(
    stage: str, records: list[dict[str, Any]], status: str
) -> dict[str, Any]:
    return {
        "schema_version": "mysql_tpch_readiness_probe_trace_v1",
        "status": status,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "stage": stage,
        "probe_limit": 180,
        "probe_timeout_seconds": 10,
        "records": records,
        "secret_content_prohibited": True,
        "workload_started_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
    }


def _wait_mysql(
    container: str,
    defaults_file: str | None = None,
    *,
    stage: str,
    journal_path: Path,
) -> None:
    if stage not in ("bootstrap_database_socket", "formal_database_tcp"):
        raise AuthorityError("MySQL readiness stage differs")
    journal_path = Path(journal_path)
    records: list[dict[str, Any]] = []
    for attempt in range(1, 181):
        argv = (
            ["docker", "exec", container, "mysqladmin", "-uroot", "ping", "--silent"]
            if defaults_file is None
            else [
                "/usr/bin/mysql", f"--defaults-file={defaults_file}",
                "--protocol=TCP", "--host=127.0.0.1",
                f"--port={MYSQL_PORT}", "--user=root",
                "--connect-timeout=3", "--ssl-mode=DISABLED",
                "--get-server-public-key", "-NBe", "SELECT 1",
            ]
        )
        started_epoch = time.time()
        completed = _run(argv, timeout=10, check=False)
        completed_epoch = time.time()
        record = {
            "stage": stage,
            "attempt": attempt,
            "started_at_utc": datetime.fromtimestamp(
                started_epoch, timezone.utc
            ).isoformat(),
            "completed_at_utc": datetime.fromtimestamp(
                completed_epoch, timezone.utc
            ).isoformat(),
            "duration_seconds": max(0.0, completed_epoch - started_epoch),
            "returncode": int(completed.returncode),
            "stdout": _redacted_bounded_text(
                completed.stdout, READINESS_PROBE_TEXT_LIMIT
            ),
            "stderr": _redacted_bounded_text(
                completed.stderr, READINESS_PROBE_TEXT_LIMIT
            ),
            "secret_content_prohibited": True,
        }
        records.append(record)
        probe_path = (
            journal_path.with_name(journal_path.name + ".probes")
            / ("probe-%03d.json" % attempt)
        )
        atomic_json(probe_path, record)
        status = (
            "PASS_READY"
            if completed.returncode == 0
            else (
                "FAIL_NOT_READY_AFTER_180_PROBES"
                if attempt == 180
                else "INFLIGHT_NOT_READY"
            )
        )
        if status != "INFLIGHT_NOT_READY":
            summary = _readiness_trace_document(stage, records, status)
            summary["immutable_probe_record_directory"] = str(
                probe_path.parent
            )
            summary["immutable_probe_record_count"] = len(records)
            summary["every_probe_has_sha256_sidecar"] = True
            atomic_json(journal_path, summary)
        if completed.returncode == 0:
            return
        time.sleep(1)
    raise AuthorityError("MySQL did not become ready")


def _formal_database_failure_projection(
    container: str,
) -> dict[str, Any]:
    inspect_completed = _run(
        ["docker", "inspect", container], timeout=30, check=False
    )
    inspect_projection: dict[str, Any] | None = None
    if inspect_completed.returncode == 0:
        try:
            rows = json.loads(inspect_completed.stdout)
        except json.JSONDecodeError:
            rows = None
        if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict):
            row = rows[0]
            state = row.get("State") or {}
            config = row.get("Config") or {}
            host = row.get("HostConfig") or {}
            inspect_projection = {
                "id": row.get("Id"),
                "name": row.get("Name"),
                "created": row.get("Created"),
                "image": row.get("Image"),
                "state": {
                    key: state.get(key)
                    for key in (
                        "Status", "Running", "Pid", "ExitCode", "OOMKilled",
                        "Error", "StartedAt", "FinishedAt",
                    )
                },
                "restart_count": row.get("RestartCount"),
                "config": {
                    "image": config.get("Image"),
                    "entrypoint": config.get("Entrypoint"),
                    "cmd": config.get("Cmd"),
                    "user": config.get("User"),
                },
                "host_config": {
                    key: host.get(key)
                    for key in (
                        "CpuPeriod", "CpuQuota", "CpusetCpus", "CpusetMems",
                        "Memory", "MemorySwap", "PidsLimit", "PortBindings",
                        "BlkioDeviceReadBps", "BlkioDeviceWriteBps",
                    )
                },
                "mounts": [
                    {
                        "type": mount.get("Type"),
                        "source": mount.get("Source"),
                        "destination": mount.get("Destination"),
                        "rw": mount.get("RW"),
                    }
                    for mount in (row.get("Mounts") or [])
                    if isinstance(mount, dict)
                ],
                "environment_excluded_to_prevent_secret_capture": True,
            }
    logs = _run(
        ["docker", "logs", "--timestamps", "--tail", "500", container],
        timeout=30,
        check=False,
    )
    listeners = _run(["ss", "-ltnp"], timeout=30, check=False)
    listener_lines = "\n".join(
        line for line in (listeners.stdout or "").splitlines()
        if ":65103" in line
    )
    socket_readback = _run(
        [
            "docker", "exec", container, "/bin/sh", "-ceu",
            (
                "if test -S /var/run/mysqld/mysqld.sock; then "
                "stat -c 'socket_present=1 mode=%a uid=%u gid=%g' "
                "/var/run/mysqld/mysqld.sock; "
                "else printf 'socket_present=0\\n'; fi; "
                "if test -d /var/run/mysqld; then "
                "stat -c 'directory_present=1 mode=%a uid=%u gid=%g' "
                "/var/run/mysqld; "
                "else printf 'directory_present=0\\n'; fi"
            ),
        ],
        timeout=30,
        check=False,
    )
    return {
        "inspect_command_returncode": int(inspect_completed.returncode),
        "inspect": inspect_projection,
        "inspect_parse_passed": inspect_projection is not None,
        "logs": {
            "returncode": int(logs.returncode),
            "stdout": _redacted_bounded_text(
                logs.stdout, READINESS_LOG_TEXT_LIMIT
            ),
            "stderr": _redacted_bounded_text(
                logs.stderr, READINESS_PROBE_TEXT_LIMIT
            ),
        },
        "host_tcp_listener_65103": {
            "ss_returncode": int(listeners.returncode),
            "matching_lines": _redacted_bounded_text(
                listener_lines, READINESS_PROBE_TEXT_LIMIT
            ),
        },
        "container_mysql_socket": {
            "returncode": int(socket_readback.returncode),
            "stdout": _redacted_bounded_text(
                socket_readback.stdout, READINESS_PROBE_TEXT_LIMIT
            ),
            "stderr": _redacted_bounded_text(
                socket_readback.stderr, READINESS_PROBE_TEXT_LIMIT
            ),
        },
        "secret_content_prohibited": True,
    }


def _seal_formal_database_readiness_failure(
    path: Path,
    container: str,
    trace_path: Path,
    failure: BaseException,
) -> dict[str, Any]:
    trace_path = Path(trace_path)
    trace = load_object(trace_path, "formal database readiness trace")
    if (
        trace.get("stage") != "formal_database_tcp"
        or trace.get("status") != "FAIL_NOT_READY_AFTER_180_PROBES"
        or len(trace.get("records") or []) != 180
    ):
        raise AuthorityError("formal database readiness trace differs")
    value = {
        "schema_version":
            "mysql_tpch_formal_database_readiness_failure_evidence_v1",
        "status": "FAIL_FORMAL_DATABASE_TCP_READINESS_BEFORE_CLEANUP",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "stage": "formal_database_tcp",
        "failure_type": type(failure).__name__,
        "failure": _redacted_bounded_text(
            str(failure), READINESS_PROBE_TEXT_LIMIT
        ),
        "readiness_trace_path": str(trace_path),
        "readiness_trace_sha256": sha256(trace_path),
        "pre_cleanup_database_evidence":
            _formal_database_failure_projection(container),
        "evidence_sealed_before_cleanup": True,
        "secret_content_prohibited": True,
        "workload_started_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
    }
    atomic_json(path, value)
    return value
'''
    source = _replace_once(
        source, old_wait, new_wait, "per-probe readiness evidence"
    )
    source = _replace_once(
        source,
        '''    _run(plan["bootstrap_database"]["argv"])
    _wait_mysql(bootstrap_name)
''',
        '''    _run(plan["bootstrap_database"]["argv"])
    _wait_mysql(
        bootstrap_name,
        stage="bootstrap_database_socket",
        journal_path=attempt_root / "bootstrap_database_readiness_trace.json",
    )
''',
        "bootstrap readiness call",
    )
    source = _replace_once(
        source,
        '''    _run(plan["database"]["argv"])
    _wait_mysql(spec["containers"]["database"], str(defaults))
    _run(
''',
        '''    _run(plan["database"]["argv"])
    formal_readiness_trace = (
        attempt_root / "formal_database_readiness_trace.json"
    )
    try:
        _wait_mysql(
            spec["containers"]["database"],
            str(defaults),
            stage="formal_database_tcp",
            journal_path=formal_readiness_trace,
        )
    except AuthorityError as exc:
        _seal_formal_database_readiness_failure(
            output.with_name(
                output.name + ".formal_database_readiness_failure.json"
            ),
            spec["containers"]["database"],
            formal_readiness_trace,
            exc,
        )
        raise
    _run(
''',
        "formal database readiness pre-cleanup seal",
    )
    return source.encode("utf-8")


def payloads(predecessor_manifest: dict[str, Any]) -> dict[str, bytes]:
    result = {
        relative: (PREDECESSOR / relative).read_bytes()
        for relative in sorted(predecessor_manifest["files"])
    }
    result[AUTHORITY_RELATIVE] = authority_payload()
    result[INVALIDATION_RELATIVE] = INVALIDATION.read_bytes()
    result[BUILDER_RELATIVE] = HERE.read_bytes()
    return result


def verify_published(bundle: Path, manifest: dict[str, Any]) -> None:
    if bundle.name != manifest.get("bundle_id"):
        raise BuildError("published directory identity differs")
    if relative_files(bundle) != manifest.get("files"):
        raise BuildError("published exact closure differs")
    unsigned = {
        key: value for key, value in manifest.items() if key != "bundle_id"
    }
    if sha_bytes(canonical(unsigned)) != bundle.name:
        raise BuildError("published content address differs")


def fsync_tree(root: Path) -> None:
    directories = [root]
    directories.extend(
        path for path in root.rglob("*") if path.is_dir()
    )
    for directory in sorted(
        directories, key=lambda path: len(path.parts), reverse=True
    ):
        descriptor = os.open(
            str(directory), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def fsync_directory(path: Path) -> None:
    descriptor = os.open(
        str(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish(
    content: dict[str, bytes], manifest: dict[str, Any]
) -> Path:
    target = BUNDLE_ROOT / manifest["bundle_id"]
    BUNDLE_ROOT.mkdir(parents=True, exist_ok=True)
    if target.exists():
        existing = load(target / "bundle_manifest.json")
        if existing != manifest:
            raise BuildError("existing content address has different manifest")
        verify_published(target, existing)
        return target
    stage = BUNDLE_ROOT / (
        ".%s.%s.tmp" % (manifest["bundle_id"], secrets.token_hex(8))
    )
    stage.mkdir(mode=0o700)
    try:
        for relative, data in sorted(content.items()):
            path = stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(path, 0o444)
        manifest_payload = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(manifest_payload)
        (stage / "bundle_manifest.json.sha256").write_text(
            "%s  bundle_manifest.json\n" % sha_bytes(manifest_payload),
            encoding="ascii",
        )
        os.chmod(stage / "bundle_manifest.json", 0o444)
        os.chmod(stage / "bundle_manifest.json.sha256", 0o444)
        fsync_tree(stage)
        fsync_directory(BUNDLE_ROOT)
        try:
            os.rename(stage, target)
        except OSError as exc:
            if (
                exc.errno not in (errno.EEXIST, errno.ENOTEMPTY)
                or not target.is_dir()
            ):
                raise
            shutil.rmtree(stage)
            existing = load(target / "bundle_manifest.json")
            if existing != manifest:
                raise BuildError(
                    "concurrent content address has different manifest"
                )
        fsync_directory(BUNDLE_ROOT)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    verify_published(target, manifest)
    return target


def build() -> dict[str, Any]:
    predecessor = verify_predecessor()
    verify_invalidation()
    content = payloads(predecessor)
    files = {
        relative: sha_bytes(data)
        for relative, data in sorted(content.items())
    }
    identity = {
        key: value
        for key, value in predecessor.items()
        if key not in ("bundle_id", "files")
    }
    components = dict(identity.get("component_hashes") or {})
    components["bootstrap_authority"] = files[AUTHORITY_RELATIVE]
    identity.update(
        {
            "predecessor_bundle_id": PREDECESSOR_ID,
            "predecessor_manifest_sha256":
                PREDECESSOR_MANIFEST_SHA256,
            "builder_sha256": sha256(HERE),
            "component_hashes": components,
            "invalidated_295f_formal_tcp_readiness_bundle_sha256":
                sha256(INVALIDATION),
            "invalidated_predecessor_bundle_ids":
                list(predecessor.get("invalidated_predecessor_bundle_ids") or [])
                + [PREDECESSOR_ID],
            "readiness_evidence_contract": {
                "probe_stages": [
                    "bootstrap_database_socket",
                    "formal_database_tcp",
                ],
                "per_probe_stage_rc_time_bounded_stdout_stderr": True,
                "stdout_stderr_secret_redaction_required": True,
                "formal_database_failure_inspect_logs_port_socket":
                    True,
                "formal_database_failure_evidence_before_cleanup": True,
                "readiness_attempts_unchanged": 180,
                "readiness_probe_timeout_seconds_unchanged": 10,
                "gate_relaxation": False,
                "failure_zero_credit": True,
            },
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
            "offline_build_only": True,
            "remote_actions": 0,
            "files": files,
        }
    )
    bundle_id = sha_bytes(canonical(identity))
    manifest = dict(identity)
    manifest["bundle_id"] = bundle_id
    bundle = publish(content, manifest)
    return {
        "schema_version":
            "mysql_tpch_readiness_evidence_successor_build_result_v1",
        "status": "PASS_IMMUTABLE_SUCCESSOR_PUBLISHED",
        "bundle_id": bundle_id,
        "bundle": str(bundle),
        "bundle_manifest_sha256":
            sha256(bundle / "bundle_manifest.json"),
        "bootstrap_authority_sha256": files[AUTHORITY_RELATIVE],
        "invalidation_sha256": sha256(INVALIDATION),
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions": 0,
    }


if __name__ == "__main__":
    try:
        print(json.dumps(build(), indent=2, sort_keys=True))
    except (BuildError, OSError, ValueError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
