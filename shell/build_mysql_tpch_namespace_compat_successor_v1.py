#!/usr/bin/env python3
"""Build the immutable node04 namespace-compatible TPC-H successor."""

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
PUBLISHER = ROOT / "shell/build_mysql_tpch_systemd_compat_successor_v1.py"
PUBLISHER_SHA256 = (
    "3c1f7f84d24a41468ae721f3413f6b9b01aa9c38dbfd62ef26cda2d606750ff5"
)
PREDECESSOR_ID = (
    "08f1bf171d27d74b3f68949df0210c0512e2c4b63a95b8e73c6be9bfa0e827c0"
)
PREDECESSOR_MANIFEST_SHA256 = (
    "6d0d6b8b99890074ceb29d804b8b5d31b9b68b4b04dbc7a961532991cd0e2443"
)
PREDECESSOR = (
    ROOT / "artifacts/mysql_tpch_systemd_compat_successor_v1"
    / PREDECESSOR_ID
)
BUNDLE_ROOT = ROOT / "artifacts/mysql_tpch_namespace_compat_successor_v1"
AUTHORITY = "mysql_tpch_distributed_v2_authority.py"
RUNTIME = "distributed_runtime_contract.json"
INVALIDATION = (
    ROOT
    / "preflight/mysql_tpch_08f1_missing_cgroup_namespace_invalidation_20260731.json"
)
NAMESPACE_FIXTURE = (
    ROOT / "preflight/node04_kernel_namespace_capability_fixture_v1.json"
)
INVALIDATION_RELATIVE = "invalidated_08f1_missing_cgroup_namespace_bundle.json"
NAMESPACE_FIXTURE_RELATIVE = (
    "fixtures/node04_kernel_namespace_capability_fixture_v1.json"
)
BUILDER_RELATIVE = "builder/build_mysql_tpch_namespace_compat_successor_v1.py"


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
    spec = importlib.util.spec_from_file_location("tpch_namespace_publisher", PUBLISHER)
    if spec is None or spec.loader is None:
        raise BuildError("publisher is not importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def atomic_publisher():
    return publisher().publisher().base_module()


def verify_predecessor() -> dict[str, Any]:
    manifest_path = PREDECESSOR / "bundle_manifest.json"
    manifest = load(manifest_path)
    unsigned = {key: value for key, value in manifest.items() if key != "bundle_id"}
    prohibited = [
        path for path in PREDECESSOR.rglob("*")
        if path.name == "__pycache__" or path.suffix == ".pyc"
    ]
    if (
        sha256(manifest_path) != PREDECESSOR_MANIFEST_SHA256
        or manifest.get("bundle_id") != PREDECESSOR_ID
        or sha_bytes(canonical(unsigned)) != PREDECESSOR_ID
        or atomic_publisher().relative_files(PREDECESSOR)
        != manifest.get("files")
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or prohibited
    ):
        raise BuildError("08f1 predecessor identity/closure differs")
    return manifest


NAMESPACE_HELPER = r'''
def _namespace_identity_readback() -> dict[str, Any]:
    mandatory_names = ("ipc", "mnt", "net", "pid", "user", "uts")
    mandatory = {}
    init_mandatory = {}
    for name in mandatory_names:
        self_path = "/proc/self/ns/" + name
        init_path = "/proc/1/ns/" + name
        try:
            mandatory[name] = os.readlink(self_path)
            init_mandatory[name] = os.readlink(init_path)
        except OSError as exc:
            raise AuthorityError(
                "mandatory namespace readback failed: " + name
            ) from exc
        if (
            not mandatory[name].startswith(name + ":[")
            or mandatory[name] != init_mandatory[name]
        ):
            raise AuthorityError(
                "mandatory namespace identity differs: " + name
            )
    self_cgroup = "/proc/self/ns/cgroup"
    init_cgroup = "/proc/1/ns/cgroup"
    self_supported = os.path.lexists(self_cgroup)
    init_supported = os.path.lexists(init_cgroup)
    if self_supported != init_supported:
        raise AuthorityError("cgroup namespace capability is asymmetric")
    optional_cgroup = {
        "supported": self_supported,
        "path_absent": not self_supported,
        "self_path": self_cgroup,
        "init_path": init_cgroup,
        "self_identity": None,
        "init_identity": None,
    }
    if self_supported:
        try:
            optional_cgroup["self_identity"] = os.readlink(self_cgroup)
            optional_cgroup["init_identity"] = os.readlink(init_cgroup)
        except OSError as exc:
            raise AuthorityError(
                "supported cgroup namespace readback failed"
            ) from exc
        if (
            not optional_cgroup["self_identity"].startswith("cgroup:[")
            or optional_cgroup["self_identity"]
            != optional_cgroup["init_identity"]
        ):
            raise AuthorityError("cgroup namespace identity differs")
    return {
        "schema_version": "mysql_tpch_namespace_identity_readback_v1",
        "mandatory_names": list(mandatory_names),
        "mandatory": mandatory,
        "init_mandatory": init_mandatory,
        "mandatory_complete": True,
        "optional": {"cgroup": optional_cgroup},
        "cgroup_binding_fallback":
            "/proc/self/cgroup_plus_systemd_ControlGroup",
    }
'''


def replace_once(source: str, old: str, new: str, label: str) -> str:
    if source.count(old) != 1:
        raise BuildError("patch point differs: " + label)
    return source.replace(old, new, 1)


def authority_payload() -> bytes:
    source = (PREDECESSOR / AUTHORITY).read_text(encoding="utf-8")
    source = replace_once(
        source,
        "\ndef _direct_host_bootstrap_readback("
        "spec: Mapping[str, Any]) -> dict[str, Any]:\n",
        "\n" + NAMESPACE_HELPER
        + "\ndef _direct_host_bootstrap_readback("
        "spec: Mapping[str, Any]) -> dict[str, Any]:\n",
        "namespace helper",
    )
    source = replace_once(
        source,
        '''    cgroup_lines = Path("/proc/self/cgroup").read_text(
        encoding="ascii"
    ).splitlines()
''',
        '''    try:
        cgroup_lines = Path("/proc/self/cgroup").read_text(
            encoding="ascii"
        ).splitlines()
        init_cgroup_lines = Path("/proc/1/cgroup").read_text(
            encoding="ascii"
        ).splitlines()
    except (OSError, UnicodeError) as exc:
        raise AuthorityError("process cgroup readback failed") from exc
''',
        "cgroup exception closure",
    )
    source = replace_once(
        source,
        '''    namespaces = {
        name: os.readlink("/proc/self/ns/" + name)
        for name in ("pid", "mnt", "net", "user", "ipc", "uts", "cgroup")
    }
    init_namespaces = {
        name: os.readlink("/proc/1/ns/" + name)
        for name in namespaces
    }
    ancestry_text = "\\n".join(
        cgroup_lines
        + Path("/proc/1/cgroup").read_text(encoding="ascii").splitlines()
    ).lower()
''',
        '''    namespace_identity = _namespace_identity_readback()
    namespaces = namespace_identity["mandatory"]
    init_namespaces = namespace_identity["init_mandatory"]
    ancestry_text = "\\n".join(
        cgroup_lines + init_cgroup_lines
    ).lower()
''',
        "namespace capability policy",
    )
    source = replace_once(
        source,
        '''        "namespaces": namespaces,
        "init_namespaces": init_namespaces,
''',
        '''        "namespaces": namespaces,
        "init_namespaces": init_namespaces,
        "namespace_identity": namespace_identity,
''',
        "namespace evidence",
    )
    return source.encode("utf-8")


def runtime_payload() -> bytes:
    value = load(PREDECESSOR / RUNTIME)
    value["namespace_capability"] = {
        "fixture_sha256": sha256(NAMESPACE_FIXTURE),
        "kernel_release": "3.10.0-1160.71.1.el7",
        "mandatory": ["ipc", "mnt", "net", "pid", "user", "uts"],
        "optional_cgroup": {
            "supported": False,
            "path_absent": True,
        },
        "optional_cgroup_binding_fallback":
            "/proc/self/cgroup_plus_systemd_ControlGroup",
        "mandatory_missing_policy": "fail_closed_AuthorityError",
        "optional_cgroup_missing_policy": "record_absent_and_continue",
    }
    return canonical(value)


def verify_inputs() -> None:
    invalid = load(INVALIDATION)
    fixture = load(NAMESPACE_FIXTURE)
    stdout = fixture.get("stdout")
    if (
        invalid.get("status") != "INVALIDATED_DO_NOT_RUN_OR_CREDIT"
        or invalid.get("bundle_id") != PREDECESSOR_ID
        or invalid.get("bundle_manifest_sha256")
        != PREDECESSOR_MANIFEST_SHA256
        or invalid.get("container_started") is not False
        or invalid.get("database_started") is not False
        or invalid.get("workload_started") is not False
        or any(invalid.get(key) != 0 for key in (
            "capture_credit", "qualification_credit", "r0_credit",
            "accepted_rounds_credit",
        ))
        or invalid.get("replay_or_rerun_authorized") is not False
        or fixture.get("schema_version")
        != "node04_kernel_namespace_capability_fixture_v1"
        or fixture.get("host_id") != "node04"
        or fixture.get("kernel_release") != "3.10.0-1160.71.1.el7"
        or hashlib.sha256(str(stdout).encode("utf-8")).hexdigest()
        != "544486c6bdaab05ad05efa950b6afa40eb11b98d3dbfa4f7fa7aa795cd63c215"
        or fixture.get("mandatory_namespaces")
        != ["ipc", "mnt", "net", "pid", "user", "uts"]
        or (fixture.get("optional_namespaces") or {}).get("cgroup")
        != {"supported": False, "path_absent": True}
    ):
        raise BuildError("namespace invalidation/fixture differs")


def payloads(predecessor: dict[str, Any]) -> dict[str, bytes]:
    result = {
        relative: (PREDECESSOR / relative).read_bytes()
        for relative in sorted(predecessor["files"])
    }
    result[AUTHORITY] = authority_payload()
    result[RUNTIME] = runtime_payload()
    result[INVALIDATION_RELATIVE] = INVALIDATION.read_bytes()
    result[NAMESPACE_FIXTURE_RELATIVE] = NAMESPACE_FIXTURE.read_bytes()
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
    namespace_contract = json.loads(runtime_payload())["namespace_capability"]
    identity.update({
        "predecessor_bundle_id": PREDECESSOR_ID,
        "predecessor_manifest_sha256": PREDECESSOR_MANIFEST_SHA256,
        "builder_sha256": sha256(HERE),
        "component_hashes": components,
        "distributed_runtime_contract_sha256": files[RUNTIME],
        "invalidated_08f1_missing_cgroup_namespace_bundle_sha256":
            sha256(INVALIDATION),
        "node04_kernel_namespace_capability_fixture_sha256":
            sha256(NAMESPACE_FIXTURE),
        "namespace_capability_contract": namespace_contract,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "offline_build_only": True,
        "remote_actions": 0,
        "files": files,
    })
    bundle_id = sha_bytes(canonical(identity))
    manifest = dict(identity)
    manifest["bundle_id"] = bundle_id
    atomic = atomic_publisher()
    atomic.BUNDLE_ROOT = BUNDLE_ROOT
    bundle = atomic.publish(content, manifest)
    return {
        "schema_version": "mysql_tpch_namespace_compat_successor_build_v1",
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
