#!/usr/bin/env python3
"""Build the immutable node04-systemd-compatible TPC-H successor offline."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PUBLISHER = ROOT / "shell/build_mysql_tpch_dedicated_daemon_successor_v1.py"
PUBLISHER_SHA256 = (
    "876b168d84f6402890dc250e8e81d6a278494d06100d97ed280e40f7804954ca"
)
PREDECESSOR_ID = (
    "00a0a6e576f23bcb693793c3f407172095380c08b8de17024f825d43c2ea8b27"
)
PREDECESSOR_MANIFEST_SHA256 = (
    "d1d60fc39677e23ad2c3f57bea0bc3a9b1975ef5ca5340f77bfcb4cbb4570eae"
)
PREDECESSOR = (
    ROOT / "artifacts/mysql_tpch_dedicated_daemon_successor_v1"
    / PREDECESSOR_ID
)
BUNDLE_ROOT = ROOT / "artifacts/mysql_tpch_systemd_compat_successor_v1"
AUTHORITY = "mysql_tpch_distributed_v2_authority.py"
RUNTIME = "distributed_runtime_contract.json"
INVALIDATION = (
    ROOT / "preflight/mysql_tpch_00a0_systemd_wait_collect_invalidation_20260731.json"
)
HELP_FIXTURE = ROOT / "preflight/node04_systemd_run_help_fixture_v1.json"
MATERIALIZER = ROOT / "shell/mysql_tpch_systemd_oneshot_materializer_v1.py"
INVALIDATION_RELATIVE = "invalidated_00a0_systemd_wait_collect_bundle.json"
HELP_FIXTURE_RELATIVE = "fixtures/node04_systemd_run_help_fixture_v1.json"
MATERIALIZER_RELATIVE = "launch/mysql_tpch_systemd_oneshot_materializer_v1.py"
BUILDER_RELATIVE = "builder/build_mysql_tpch_systemd_compat_successor_v1.py"


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2,
                   allow_nan=False) + "\n"
    ).encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def load(path: Path) -> dict[str, Any]:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise BuildError("absent or symlinked: " + str(path))
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError("JSON object required: " + str(path))
    return value


def publisher():
    if sha256(PUBLISHER) != PUBLISHER_SHA256:
        raise BuildError("sealed publisher identity differs")
    spec = importlib.util.spec_from_file_location("tpch_atomic_publisher", PUBLISHER)
    if spec is None or spec.loader is None:
        raise BuildError("publisher is not importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_predecessor() -> dict[str, Any]:
    manifest_path = PREDECESSOR / "bundle_manifest.json"
    manifest = load(manifest_path)
    unsigned = {key: value for key, value in manifest.items() if key != "bundle_id"}
    base = publisher()
    prohibited = [
        path for path in PREDECESSOR.rglob("*")
        if path.name == "__pycache__" or path.suffix == ".pyc"
    ]
    if (
        sha256(manifest_path) != PREDECESSOR_MANIFEST_SHA256
        or manifest.get("bundle_id") != PREDECESSOR_ID
        or sha_bytes(canonical(unsigned)) != PREDECESSOR_ID
        or base.base_module().relative_files(PREDECESSOR) != manifest.get("files")
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or prohibited
    ):
        raise BuildError("00a0 predecessor identity/closure differs")
    return manifest


def replace_count(source: str, old: str, new: str, count: int, label: str) -> str:
    if source.count(old) != count:
        raise BuildError("patch point differs: " + label)
    return source.replace(old, new)


OLD_EXECUTION = '''{
        "mode": "direct_host_systemd_transient_unit",
        "unit": expected_bootstrap_unit,
        "ppid": 1,
        "token_transport": "token_file_or_in_process_only",
        "docker_ancestry_prohibited": True,
    }'''
OLD_HELPER_EXECUTION = OLD_EXECUTION.replace(
    "expected_bootstrap_unit", "expected_unit"
)
NEW_EXECUTION = '''{
        "mode": "direct_host_systemd_transient_oneshot",
        "unit": expected_bootstrap_unit,
        "service_type": "oneshot",
        "remain_after_exit": True,
        "ppid": 1,
        "token_transport": "token_file_or_in_process_only",
        "docker_ancestry_prohibited": True,
        "unit_retained_for_audit": True,
    }'''
NEW_HELPER_EXECUTION = NEW_EXECUTION.replace(
    "expected_bootstrap_unit", "expected_unit"
)


def authority_payload() -> bytes:
    source = (PREDECESSOR / AUTHORITY).read_text(encoding="utf-8")
    source = replace_count(
        source, OLD_EXECUTION, NEW_EXECUTION, 1,
        "oneshot execution spec contract",
    )
    source = replace_count(
        source, OLD_HELPER_EXECUTION, NEW_HELPER_EXECUTION, 1,
        "oneshot execution helper contract",
    )
    source = replace_count(
        source,
        '''            "mode", "unit", "ppid", "token_transport",
            "docker_ancestry_prohibited",
''',
        '''            "mode", "unit", "service_type", "remain_after_exit",
            "ppid", "token_transport", "docker_ancestry_prohibited",
            "unit_retained_for_audit",
''',
        1,
        "oneshot spec keys",
    )
    source = replace_count(
        source,
        '''        or properties.get("ActiveState") != "active"
        or properties.get("SubState") not in ("running", "start")
''',
        '''        or (
            properties.get("ActiveState"),
            properties.get("SubState"),
        ) != ("activating", "start")
''',
        1,
        "running oneshot state",
    )
    source = replace_count(
        source,
        '''        "status": "PASS_DIRECT_HOST_TRANSIENT_UNIT_NON_DOCKER",
''',
        '''        "status": "PASS_DIRECT_HOST_TRANSIENT_ONESHOT_NON_DOCKER",
''',
        1,
        "oneshot identity status",
    )
    source = replace_count(
        source,
        '''        "transient": True,
        "fragment_path": properties.get("FragmentPath") or "",
''',
        '''        "transient": True,
        "service_type": "oneshot",
        "remain_after_exit": True,
        "unit_retained_for_audit": True,
        "running_active_state": "activating",
        "running_sub_state": "start",
        "fragment_path": properties.get("FragmentPath") or "",
''',
        1,
        "oneshot identity evidence",
    )
    source = replace_count(
        source,
        '''        != "PASS_DIRECT_HOST_TRANSIENT_UNIT_NON_DOCKER"
''',
        '''        != "PASS_DIRECT_HOST_TRANSIENT_ONESHOT_NON_DOCKER"
''',
        1,
        "oneshot evidence validation",
    )
    return source.encode("utf-8")


def runtime_payload() -> bytes:
    value = load(PREDECESSOR / RUNTIME)
    value["bootstrap_execution"] = {
        "mode": "direct_host_systemd_transient_oneshot",
        "unit_template": "ra-mysql-tpch-bootstrap-<attempt_token>.service",
        "service_type": "oneshot",
        "remain_after_exit": True,
        "ppid": 1,
        "bind_main_pid_startticks_namespaces_cgroup": True,
        "docker_ancestry_prohibited": True,
        "token_transport": "token_file_or_in_process_only",
        "token_in_os_argv_prohibited": True,
        "controller_poll_after_nonblocking_start": True,
        "poll_properties": [
            "ActiveState", "SubState", "ExecMainStatus", "Result",
        ],
        "unit_retained_for_audit": True,
        "wait_option_prohibited": True,
        "collect_option_prohibited": True,
    }
    return canonical(value)


def verify_inputs() -> None:
    invalid = load(INVALIDATION)
    fixture = load(HELP_FIXTURE)
    materializer_spec = importlib.util.spec_from_file_location(
        "tpch_systemd_materializer", MATERIALIZER
    )
    if materializer_spec is None or materializer_spec.loader is None:
        raise BuildError("materializer is not importable")
    module = importlib.util.module_from_spec(materializer_spec)
    materializer_spec.loader.exec_module(module)
    module.validate_help_fixture(fixture)
    if (
        invalid.get("status") != "INVALIDATED_DO_NOT_RUN_OR_CREDIT"
        or invalid.get("bundle_id") != PREDECESSOR_ID
        or invalid.get("bundle_manifest_sha256")
        != PREDECESSOR_MANIFEST_SHA256
        or invalid.get("workload_started") is not False
        or any(invalid.get(key) != 0 for key in (
            "capture_credit", "qualification_credit", "r0_credit",
            "accepted_rounds_credit",
        ))
        or invalid.get("replay_or_rerun_authorized") is not False
    ):
        raise BuildError("00a0 invalidation differs")


def payloads(predecessor: dict[str, Any]) -> dict[str, bytes]:
    result = {
        relative: (PREDECESSOR / relative).read_bytes()
        for relative in sorted(predecessor["files"])
    }
    result[AUTHORITY] = authority_payload()
    result[RUNTIME] = runtime_payload()
    result[INVALIDATION_RELATIVE] = INVALIDATION.read_bytes()
    result[HELP_FIXTURE_RELATIVE] = HELP_FIXTURE.read_bytes()
    result[MATERIALIZER_RELATIVE] = MATERIALIZER.read_bytes()
    result[BUILDER_RELATIVE] = HERE.read_bytes()
    return result


def build() -> dict[str, Any]:
    predecessor = verify_predecessor()
    verify_inputs()
    content = payloads(predecessor)
    files = {name: sha_bytes(data) for name, data in sorted(content.items())}
    identity = {
        key: value for key, value in predecessor.items()
        if key not in ("bundle_id", "files")
    }
    components = dict(identity.get("component_hashes") or {})
    components["bootstrap_authority"] = files[AUTHORITY]
    components["systemd_oneshot_materializer"] = files[MATERIALIZER_RELATIVE]
    identity.update({
        "predecessor_bundle_id": PREDECESSOR_ID,
        "predecessor_manifest_sha256": PREDECESSOR_MANIFEST_SHA256,
        "builder_sha256": sha256(HERE),
        "component_hashes": components,
        "distributed_runtime_contract_sha256": files[RUNTIME],
        "invalidated_00a0_systemd_wait_collect_bundle_sha256":
            sha256(INVALIDATION),
        "node04_systemd_run_help_fixture_sha256": sha256(HELP_FIXTURE),
        "bootstrap_execution_contract": load(
            PREDECESSOR / RUNTIME
        ).get("bootstrap_execution"),
        "systemd_compatibility_successor": {
            "mode": "direct_host_systemd_transient_oneshot",
            "service_type": "oneshot",
            "remain_after_exit": True,
            "systemd_run_wait_prohibited": True,
            "systemd_run_collect_prohibited": True,
            "controller_poll_required": True,
            "unit_retained_for_audit": True,
        },
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "offline_build_only": True,
        "remote_actions": 0,
        "files": files,
    })
    identity["bootstrap_execution_contract"] = json.loads(
        runtime_payload()
    )["bootstrap_execution"]
    bundle_id = sha_bytes(canonical(identity))
    manifest = dict(identity)
    manifest["bundle_id"] = bundle_id
    base = publisher()
    atomic = base.base_module()
    atomic.BUNDLE_ROOT = BUNDLE_ROOT
    bundle = atomic.publish(content, manifest)
    return {
        "schema_version": "mysql_tpch_systemd_compat_successor_build_v1",
        "status": "PASS_IMMUTABLE_SUCCESSOR_PUBLISHED",
        "bundle_id": bundle_id,
        "bundle": str(bundle),
        "bundle_manifest_sha256": sha256(bundle / "bundle_manifest.json"),
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions": 0,
    }


def main() -> int:
    print(json.dumps(build(), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BuildError, OSError, UnicodeError, ValueError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
