#!/usr/bin/env python3
"""Transactionally publish and verify the manifest-bound XP JDK on node02.

No SSH is used.  An existing exact content-addressed runtime is adopted
idempotently; a differing target is never overwritten.
"""

import sys
sys.dont_write_bytecode = True

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile


HOST = "node02"
SOURCE = Path("/usr/lib/jvm/jdk-23.0.2-oracle-x64")
TARGET = Path(
    "/mnt/mysql_nvme0n1p1/anonymous/resource_adaptive_mysql/runtime/"
    "jdk-23.0.2-oracle-x64-"
    "578395e883d31c692a1c01c37910d22dbdafbcf95e373c7e0f6776a26f5c844c"
)
TREE_SHA256 = (
    "578395e883d31c692a1c01c37910d22dbdafbcf95e373c7e0f6776a26f5c844c"
)
JAVA_SHA256 = (
    "c8f339ba5d433ef5ecacef4de0640b0a35c99381804413bfbc24375a0410e51c"
)


class PublishError(RuntimeError):
    pass


def canonical(value):
    return (
        json.dumps(
            value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    sidecar = Path(str(path) + ".sha256")
    if (
        path.exists()
        or path.is_symlink()
        or sidecar.exists()
        or sidecar.is_symlink()
    ):
        raise PublishError("refusing to overwrite publication evidence")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical(value)
    temporary = path.with_name(".%s.%d.tmp" % (path.name, os.getpid()))
    temporary.write_bytes(payload)
    with temporary.open("rb") as stream:
        os.fsync(stream.fileno())
    os.replace(str(temporary), str(path))
    sidecar.write_text(
        "%s  %s\n" % (hashlib.sha256(payload).hexdigest(), path.name),
        encoding="ascii",
    )


def load_helper(bundle):
    path = bundle / "runtime/runtime_tree_content_identity_v3.py"
    spec = importlib.util.spec_from_file_location("xp_runtime_identity_v3", path)
    if spec is None or spec.loader is None:
        raise PublishError("sealed runtime identity helper cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path


def verify(bundle, target):
    helper, helper_path = load_helper(bundle)
    manifest_path = bundle / "runtime/jdk-23.0.2-tree-manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise PublishError("sealed JDK tree manifest is absent")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    result = helper.verify_tree_manifest(target, manifest)
    observed = result.get("tree_content_sha256")
    if observed != TREE_SHA256:
        raise PublishError("JDK tree content identity differs")
    java = target / "bin/java"
    if java.is_symlink() or not java.is_file() or sha256(java) != JAVA_SHA256:
        raise PublishError("JDK java binary identity differs")
    return {
        "tree_manifest_sha256": sha256(manifest_path),
        "runtime_helper_sha256": sha256(helper_path),
        "tree_content_sha256": observed,
        "java_binary_sha256": sha256(java),
    }


def publish(bundle, output):
    if socket.gethostname().split(".")[0] != HOST:
        raise PublishError("JDK publication is host-bound to node02")
    bundle = Path(bundle).resolve(strict=True)
    output = Path(output).resolve()
    if TARGET.exists() or TARGET.is_symlink():
        if TARGET.is_symlink() or not TARGET.is_dir():
            raise PublishError("runtime target exists with unsafe type")
        identity = verify(bundle, TARGET)
        mode = "ADOPTED_IDENTICAL"
    else:
        if SOURCE.is_symlink() or not SOURCE.is_dir():
            raise PublishError("sealed JDK source tree is absent")
        TARGET.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(
            tempfile.mkdtemp(prefix=".xp-jdk-publish.", dir=str(TARGET.parent))
        )
        try:
            copied_root = stage / "runtime"
            completed = subprocess.run(
                ["cp", "-a", "--", str(SOURCE) + "/.", str(copied_root)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
            )
            if completed.returncode != 0:
                raise PublishError(
                    "JDK physical copy failed: " + completed.stderr.strip()
                )
            identity = verify(bundle, copied_root)
            os.rename(str(copied_root), str(TARGET))
            descriptor = os.open(
                str(TARGET.parent), os.O_RDONLY | os.O_DIRECTORY
            )
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            identity = verify(bundle, TARGET)
            mode = "PUBLISHED_NEW"
        finally:
            if stage.exists():
                shutil.rmtree(str(stage))
    document = {
        "schema_version": "mysql_xp_node02_jdk_runtime_publication_v1",
        "status": "PUBLISHED_EXACT_MANIFEST_BOUND",
        "host": HOST,
        "runtime_root": str(TARGET),
        "deployment_source": str(SOURCE),
        "content_addressed": True,
        "atomic_publish": True,
        "non_overwriting": True,
        "idempotent_identical_adoption": True,
        "tree_modes_preserved_as_manifested": True,
        "exact_readback_required_before_every_launch": True,
        "client_mount_read_only_required": True,
        "publish_mode": mode,
        "tree_content_sha256": identity["tree_content_sha256"],
        "java_binary_sha256": identity["java_binary_sha256"],
        "tree_manifest_sha256": identity["tree_manifest_sha256"],
        "runtime_helper_sha256": identity["runtime_helper_sha256"],
        "publish_gate_passed": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }
    atomic_json(output, document)
    return document


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = publish(args.bundle, args.output)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
