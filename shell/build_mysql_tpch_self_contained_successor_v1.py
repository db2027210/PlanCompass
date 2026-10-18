#!/usr/bin/env python3
"""Publish a self-contained immutable successor to invalid TPC-H bundle 313637."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from typing import Any


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PREDECESSOR_ID = (
    "313637860bee5f333c11d8ae92689ef674585d8e7311c82b75c1cb7cc1fd9555"
)
PREDECESSOR_MANIFEST_SHA256 = (
    "58826fc4f32e74fbe5de70095293d22a65dc82c600426525fad4acd383e30cd4"
)
PREDECESSOR = (
    ROOT / "artifacts/mysql_tpch_socket_identity_successor_v1" / PREDECESSOR_ID
)
BUNDLE_ROOT = ROOT / "artifacts/mysql_tpch_self_contained_successor_v1"
VERIFIER_TEMPLATE = ROOT / "shell/mysql_tpch_self_contained_bundle_verifier_v1.py"
SEALED_TEST_TEMPLATE = (
    ROOT / "shell/test_mysql_tpch_sealed_bundle_self_contained_v1.py"
)
INVALIDATION = (
    ROOT / "preflight/mysql_tpch_313_nonselfcontained_invalidation_20260731.json"
)
PROJECTION_TEST = "offline_tests/test_effective_authority_projection_v1.py"
SEALED_TEST = "offline_tests/test_sealed_bundle_self_contained_v1.py"
VERIFIER = "builder/build_mysql_tpch_socket_identity_successor_v1.py"
INVALIDATION_RELATIVE = "invalidated_313_nonselfcontained_bundle.json"
COLLECTOR = "qualification/mysql_distributed_live_readback_qualification_v1.py"


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise BuildError("absent or symlinked: " + str(path))
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError("JSON object required: " + str(path))
    return value


def relative_files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise BuildError("symlink in bundle closure: " + relative)
        if path.is_file():
            if path in (
                root / "bundle_manifest.json",
                root / "bundle_manifest.json.sha256",
            ):
                continue
            result[relative] = sha256(path)
        elif not path.is_dir():
            raise BuildError("special file in bundle closure: " + relative)
    return result


def verify_predecessor() -> dict[str, Any]:
    manifest_path = PREDECESSOR / "bundle_manifest.json"
    manifest = load(manifest_path)
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    sidecar = PREDECESSOR / "bundle_manifest.json.sha256"
    if (
        PREDECESSOR.name != PREDECESSOR_ID
        or sha256(manifest_path) != PREDECESSOR_MANIFEST_SHA256
        or bundle_id != PREDECESSOR_ID
        or sha_bytes(canonical(unsigned)) != PREDECESSOR_ID
        or sidecar.is_symlink()
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii")
        != PREDECESSOR_MANIFEST_SHA256 + "  bundle_manifest.json\n"
        or relative_files(PREDECESSOR) != manifest.get("files")
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("313637 predecessor identity or closure differs")
    return manifest


def replace_once(source: str, old: str, new: str, label: str) -> str:
    if source.count(old) != 1:
        raise BuildError("projection test patch point differs: " + label)
    return source.replace(old, new, 1)


def projection_test_payload() -> bytes:
    source = (PREDECESSOR / PROJECTION_TEST).read_text(encoding="utf-8")
    source = replace_once(
        source,
        '''HERE = Path(__file__).resolve().parent
BUILDER_PATH = HERE / "build_mysql_tpch_effective_authority_successor_v1.py"
SPEC = importlib.util.spec_from_file_location("tpch_effective_builder", BUILDER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("builder import failed")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)
''',
        '''HERE = Path(__file__).resolve().parent
BUNDLE = HERE.parent
COLLECTOR_PATH = (
    BUNDLE / "qualification/mysql_distributed_live_readback_qualification_v1.py"
)
SPEC = importlib.util.spec_from_file_location(
    "tpch_effective_collector", COLLECTOR_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("sealed collector import failed")
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)
''',
        "direct sealed collector import",
    )
    source = replace_once(
        source,
        '''    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "collector.py"
        path.write_bytes(builder.collector_payload())
        spec = importlib.util.spec_from_file_location(
            "tpch_effective_collector", path
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("collector import failed")
        cls.collector = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.collector)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()
''',
        '''    @classmethod
    def setUpClass(cls):
        cls.collector = collector
''',
        "remove generated collector",
    )
    source = replace_once(
        source,
        "        payload = builder.collector_payload()\n",
        "        payload = COLLECTOR_PATH.read_bytes()\n",
        "direct payload inspection",
    )
    if "builder." in source or "PREDECESSOR" in source:
        raise BuildError("projection test retains external builder dependency")
    return source.encode("utf-8")


def collector_payload() -> bytes:
    source = (PREDECESSOR / COLLECTOR).read_text(encoding="utf-8")
    marker = ") -> dict[str, Any]:"
    if source.count(marker) != 2:
        raise BuildError("collector Python 3.6 annotation patch point differs")
    return source.replace(marker, "):").encode("utf-8")


def payloads(predecessor: dict[str, Any]) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for relative, expected in sorted(predecessor["files"].items()):
        if relative.startswith("builder/") or relative.startswith("offline_tests/"):
            continue
        source = PREDECESSOR / relative
        payload = source.read_bytes()
        if sha_bytes(payload) != expected:
            raise BuildError("313637 file differs: " + relative)
        result[relative] = payload
    result[COLLECTOR] = collector_payload()
    result[VERIFIER] = VERIFIER_TEMPLATE.read_bytes()
    result[PROJECTION_TEST] = projection_test_payload()
    result[SEALED_TEST] = SEALED_TEST_TEMPLATE.read_bytes()
    result[INVALIDATION_RELATIVE] = INVALIDATION.read_bytes()
    return result


def chmod_sealed(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file():
            os.chmod(path, 0o444)
        elif path.is_dir():
            os.chmod(path, 0o555)
    os.chmod(root, 0o555)


def verify_published(bundle: Path, manifest: dict[str, Any]) -> None:
    if (
        bundle.name != manifest["bundle_id"]
        or relative_files(bundle) != manifest["files"]
        or sha_bytes(
            canonical(
                {key: value for key, value in manifest.items() if key != "bundle_id"}
            )
        )
        != manifest["bundle_id"]
        or (bundle / "bundle_manifest.json.sha256").read_text(encoding="ascii")
        != sha256(bundle / "bundle_manifest.json") + "  bundle_manifest.json\n"
    ):
        raise BuildError("published self-contained bundle differs")
    for path in [bundle, *bundle.rglob("*")]:
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError("published bundle is mutable: " + str(path))


def publish(content: dict[str, bytes], manifest: dict[str, Any]) -> Path:
    BUNDLE_ROOT.mkdir(parents=True, exist_ok=True)
    destination = BUNDLE_ROOT / manifest["bundle_id"]
    if destination.exists():
        verify_published(destination, manifest)
        return destination
    stage = Path(tempfile.mkdtemp(prefix=".tpch-self-contained.", dir=BUNDLE_ROOT))
    try:
        for relative, payload in sorted(content.items()):
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        manifest_payload = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(manifest_payload)
        (stage / "bundle_manifest.json.sha256").write_text(
            sha_bytes(manifest_payload) + "  bundle_manifest.json\n",
            encoding="ascii",
        )
        chmod_sealed(stage)
        os.replace(stage, destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    verify_published(destination, manifest)
    return destination


def build() -> dict[str, Any]:
    predecessor = verify_predecessor()
    invalidation = load(INVALIDATION)
    if (
        invalidation.get("status") != "INVALIDATED_DO_NOT_RUN_OR_CREDIT"
        or invalidation.get("bundle_id") != PREDECESSOR_ID
        or invalidation.get("bundle_manifest_sha256")
        != PREDECESSOR_MANIFEST_SHA256
        or invalidation.get("accepted_rounds_credit") != 0
        or invalidation.get("container_started") is not False
        or invalidation.get("database_started") is not False
        or invalidation.get("workload_started") is not False
    ):
        raise BuildError("313637 invalidation differs")
    content = payloads(predecessor)
    files = {relative: sha_bytes(payload) for relative, payload in sorted(content.items())}
    identity = {
        key: value
        for key, value in predecessor.items()
        if key not in ("bundle_id", "files")
    }
    components = dict(identity.get("component_hashes") or {})
    components["self_contained_read_only_verifier"] = files[VERIFIER]
    identity.update(
        {
            "predecessor_bundle_id": PREDECESSOR_ID,
            "predecessor_manifest_sha256": PREDECESSOR_MANIFEST_SHA256,
            "builder_sha256": sha256(HERE),
            "component_hashes": components,
            "invalidated_313_nonselfcontained_bundle_sha256": sha256(INVALIDATION),
            "self_contained_sealed_verification": True,
            "sealed_builder_mode": "read_only_current_bundle_verifier",
            "sealed_tests_external_repository_dependency": False,
            "sealed_tests_predecessor_dependency": False,
            "sealed_bundle_pytest_regression_count": 39,
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
        "schema_version": "mysql_tpch_self_contained_successor_build_v1",
        "status": "PASS_IMMUTABLE_SELF_CONTAINED_SUCCESSOR_PUBLISHED",
        "bundle_id": bundle_id,
        "bundle": str(bundle),
        "bundle_manifest_sha256": sha256(bundle / "bundle_manifest.json"),
        "sealed_bundle_pytest_regression_count": 39,
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
