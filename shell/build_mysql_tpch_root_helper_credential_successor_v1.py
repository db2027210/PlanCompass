#!/usr/bin/env python3
"""Build the immutable TPC-H workload-lifetime successor.

This is an offline, content-addressed publication.  It does not start or stop
containers, workloads, PostgreSQL, or any remote process.  The successor:

* preserves the sealed root-helper, credential, and ``tpch_sf10`` fixes; and
* keeps the SQL workload alive for 181 seconds so the 180-second collector can
  take all 181 samples (t=0 through t=180).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PARENT_ID = "a306d4ca1f7e3b4ca975e78efe313b6d99752b8d433042bf3b798e57b1ec9a91"
PARENT_MANIFEST_SHA256 = "7483c2b51e3efeff46e3d08b325e42a1fe2c7f6ff49273d340db89efe01b8162"
PARENT = ROOT / "artifacts/mysql_tpch_database_duration_successor_v2" / PARENT_ID
OUTPUT_ROOT = ROOT / "artifacts/mysql_tpch_workload_lifetime_successor_v3"
HELPER_SOURCE = ROOT / "shell/mysql_job_host_root_inventory_v1.py"
HELPER_SOURCE_SHA256 = "197d9d5c1385470f61a65965a3298fbcfb328ed8713c57e14c68054eea7888c8"
HELPER_RELATIVE = "qualification/mysql_job_host_root_inventory_v1.py"
COLLECTOR = "qualification/mysql_distributed_live_readback_qualification_v1.py"
AUTHORITY = "mysql_tpch_distributed_v2_authority.py"
FORMAL = "tpch_formal10_contract.json"
BUILDER = "builder/build_mysql_tpch_workload_lifetime_successor_v3.py"
REMEDIATION = "tpch_workload_lifetime_remediation_v3.json"
INVALIDATION = "invalidated_a306_workload_lifetime_contract.json"
DOCKER_SOCKET_URI = "unix:///run/ra-docker-mysql-tpch-v1.sock"


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(payload: bytes, label: str) -> dict[str, Any]:
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise BuildError(label + " is not an object")
    return value


def relative_files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError("symlink prohibited: " + str(path))
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in (
            "bundle_manifest.json", "bundle_manifest.json.sha256"
        ):
            result[relative] = sha_file(path)
    return result


def replace_exact(payload: bytes, old: str, new: str, count: int, label: str) -> bytes:
    text = payload.decode("utf-8")
    if text.count(old) != count:
        raise BuildError(label + " predecessor cardinality differs")
    return text.replace(old, new).encode("utf-8")


def patched_helper() -> bytes:
    if sha_file(HELPER_SOURCE) != HELPER_SOURCE_SHA256:
        raise BuildError("root helper source identity differs")
    payload = HELPER_SOURCE.read_bytes()
    payload = replace_exact(
        payload,
        "HEX64 = re.compile(r\"^[0-9a-f]{64}$\")\n",
        "HEX64 = re.compile(r\"^[0-9a-f]{64}$\")\n"
        "TPC_H_DOCKER_SOCKET_URI = \"unix:///run/ra-docker-mysql-tpch-v1.sock\"\n",
        1, "helper socket constant",
    )
    payload = replace_exact(payload, "def inventory():\n", "def inventory(docker_socket_uri):\n",
                            1, "helper inventory signature")
    payload = replace_exact(
        payload,
        "run([\"docker\", \"ps\", \"--no-trunc\", \"--quiet\"],",
        "run([\"docker\", \"--host\", docker_socket_uri, \"ps\", \"--no-trunc\", \"--quiet\"],",
        1, "helper ps socket binding",
    )
    payload = replace_exact(
        payload,
        "run([\"docker\", \"inspect\"] + identifiers, \"running Docker inspect\")",
        "run([\"docker\", \"--host\", docker_socket_uri, \"inspect\"] + identifiers, "
        "\"running Docker inspect\")",
        1, "helper inspect socket binding",
    )
    payload = replace_exact(
        payload,
        "parser.add_argument(\"--expected-helper-sha256\", required=True)\n",
        "parser.add_argument(\"--expected-helper-sha256\", required=True)\n"
        "    parser.add_argument(\"--docker-socket-uri\", required=True)\n",
        1, "helper socket argument",
    )
    payload = replace_exact(
        payload,
        "if HEX64.fullmatch(args.expected_helper_sha256) is None:\n"
        "        raise InventoryError(\"expected helper SHA-256 differs\")\n",
        "if HEX64.fullmatch(args.expected_helper_sha256) is None:\n"
        "        raise InventoryError(\"expected helper SHA-256 differs\")\n"
        "    if args.docker_socket_uri != TPC_H_DOCKER_SOCKET_URI:\n"
        "        raise InventoryError(\"dedicated TPC-H Docker socket URI differs\")\n",
        1, "helper exact socket validation",
    )
    payload = replace_exact(payload, '"containers": inventory(),',
                            '"containers": inventory(args.docker_socket_uri),',
                            1, "helper inventory call")
    return payload


def patch_collector(payload: bytes) -> bytes:
    payload = replace_exact(
        payload,
        '"--expected-helper-sha256", helper_hash,\n    ]',
        '"--expected-helper-sha256", helper_hash,\n'
        '        "--docker-socket-uri", DOCKER_SOCKET_URI,\n    ]',
        1, "collector helper command",
    )
    payload = replace_exact(
        payload,
        '"--expected-helper-sha256", helper_hash],',
        '"--expected-helper-sha256", helper_hash,\n'
        '                       "--docker-socket-uri", DOCKER_SOCKET_URI],',
        1, "collector helper invocation evidence",
    )
    return payload


def patch_authority(payload: bytes) -> bytes:
    payload = replace_exact(
        payload,
        '        "--duration-seconds=180",\n',
        '        "--duration-seconds=181",\n',
        1, "qualification workload lifetime contract",
    )
    return payload


def verify_parent() -> dict[str, Any]:
    manifest_path = PARENT / "bundle_manifest.json"
    manifest = load(manifest_path.read_bytes(), "parent manifest")
    if (
        manifest.get("bundle_id") != PARENT_ID
        or sha_file(manifest_path) != PARENT_MANIFEST_SHA256
        or relative_files(PARENT) != manifest.get("files")
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("parent identity, closure, scope, or authority differs")
    if HELPER_RELATIVE not in manifest["files"]:
        raise BuildError("parent root helper closure is absent")
    return manifest


def freeze(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def build() -> Path:
    parent = verify_parent()
    content = {
        relative: (PARENT / relative).read_bytes()
        for relative in sorted(parent["files"])
    }
    content[AUTHORITY] = patch_authority(content[AUTHORITY])
    content[BUILDER] = HERE.read_bytes()
    content[REMEDIATION] = canonical({
        "schema_version": "mysql_tpch_workload_lifetime_remediation_v3",
        "status": "OFFLINE_REMEDIATED_FRESH_QUALIFICATION_REQUIRED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "database": "tpch_sf10",
        "collector_duration_seconds": 180,
        "collector_sample_count": 181,
        "workload_duration_seconds": 181,
        "failed_parent_attempt_credit": 0,
        "fresh_qualification_required": True,
        "fresh_r0_required": True,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    })
    content[INVALIDATION] = canonical({
        "schema_version": "mysql_tpch_workload_lifetime_invalidation_v3",
        "invalidated_bundle_id": PARENT_ID,
        "reason": "the SQL workload was configured for 180 seconds and could exit before the collector's t=180 sample",
        "database_or_client_started_by_builder": False,
        "workload_started_by_builder": False,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    })

    component_hashes = dict(parent["component_hashes"])
    component_hashes["bootstrap_authority"] = sha_bytes(content[AUTHORITY])
    formal = load(content[FORMAL], FORMAL)
    qualification_hashes = dict(formal["qualification_hashes"])
    qualification_hashes["bootstrap_authority"] = component_hashes["bootstrap_authority"]
    formal["qualification_hashes"] = qualification_hashes
    content[FORMAL] = canonical(formal)

    files = {relative: sha_bytes(payload)
             for relative, payload in sorted(content.items())}
    identity = {key: value for key, value in parent.items()
                if key not in ("bundle_id", "files")}
    identity.update({
        "successor_generation": "workload_lifetime_contract_v3",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
        "builder_sha256": sha_file(HERE),
        "component_hashes": component_hashes,
        "formal10_contract_sha256": files[FORMAL],
        "workload_lifetime_remediation_sha256": files[REMEDIATION],
        "invalidated_parent_workload_lifetime_sha256": files[INVALIDATION],
        "fresh_qualification_required": True,
        "fresh_r0_required": True,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "offline_build_only": True,
        "remote_actions": 0,
        "files": files,
    })
    bundle_id = sha_bytes(canonical(identity))
    manifest = dict(identity, bundle_id=bundle_id)

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    destination = OUTPUT_ROOT / bundle_id
    if not destination.exists():
        stage = Path(tempfile.mkdtemp(prefix=".tpch-workload-lifetime-v3.",
                                      dir=OUTPUT_ROOT))
        try:
            for relative, payload in sorted(content.items()):
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            raw = canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii"
            )
            freeze(stage)
            os.replace(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)

    actual = relative_files(destination)
    unsigned = {key: value for key, value in manifest.items() if key != "bundle_id"}
    if (actual != files or destination.name != bundle_id
            or sha_bytes(canonical(unsigned)) != bundle_id):
        raise BuildError("published successor identity differs")
    for path in (destination, *destination.rglob("*")):
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError("published successor is mutable")
    print(json.dumps({
        "status": "PASS_IMMUTABLE_TPCH_WORKLOAD_LIFETIME_SUCCESSOR",
        "bundle_id": bundle_id,
        "bundle": str(destination),
        "verified_files": len(files),
        "database": "tpch_sf10",
        "collector_duration_seconds": 180,
        "workload_duration_seconds": 181,
        "fresh_qualification_required": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }, sort_keys=True))
    return destination


if __name__ == "__main__":
    try:
        build()
    except (BuildError, OSError, ValueError, KeyError) as exc:
        raise SystemExit("NOT_READY: " + str(exc))
