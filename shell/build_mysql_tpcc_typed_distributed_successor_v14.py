#!/usr/bin/env python3
"""Build the immutable TPC-C v14 successor with the portable typed collector.

The v13 active readiness closure still selected collector payload ``032eb2...``.
Its collector implementation is sound, but its bundled test resolves the
binding manifest through a workstation-specific Windows path.  The already
sealed portable payload ``1878a2...`` carries the same collector implementation
plus the binding manifest and a location-independent seven-test closure.

This builder only creates a new content-addressed local bundle.  It grants no
runtime, R0, debug, outer-loop, formal-ten, or accepted-round authority.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "4e87faac238a2ecd1523f9dfc8044593243f732b3f1818a1f4f6fda560d0c8ab"
PARENT = ROOT / "artifacts/mysql_tpcc_typed_distributed_successor_v13" / PARENT_ID
PORTABLE_ID = "1878a2ca8b68e1b3917021499cda396e23f60a23baacd299496c78346e29c86c"
PORTABLE = ROOT / "artifacts/mysql_tpcc_typed_collector_portable_v2" / PORTABLE_ID
OLD_ID = "032eb2c9f4ba01a7edf8a438cdadc7fb115243246ba34923dbb18386802099bd"
OUT = ROOT / "artifacts/mysql_tpcc_typed_distributed_successor_v14"
SCHEMA = "mysql_tpcc_typed_distributed_successor_bundle_v14"
BUILDER_REL = "authority/build_mysql_tpcc_typed_distributed_successor_v14.py"
INVALIDATION_REL = "authority/mysql_tpcc_032_collector_test_portability_invalidation_20260801.json"
TEST_REL = "tests/test_mysql_tpcc_typed_distributed_successor_v14.py"
PUBLISHER_REL = "python_source_closure/shell/publish_mysql_tpcc_typed_collector.py"
ACTIVE_VERIFIERS = (
    "authority/tpcc_v2_formal10_readiness_verifier.py",
    "authority/tpcc_v3_formal10_readiness_verifier.py",
)
ACTIVE_CONTRACTS = (
    "authority/tpcc_v2_formal10_readiness_contract.json",
    "authority/tpcc_v3_formal10_readiness_contract.json",
)


class BuildError(RuntimeError):
    pass


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_path(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def load_manifest(root: Path, expected_schema: str | None = None) -> dict:
    path = root / "bundle_manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("bundle_id") != root.name:
        raise BuildError("bundle directory/manifest identity differs")
    if expected_schema is not None and value.get("schema_version") != expected_schema:
        raise BuildError("bundle schema differs")
    files = value.get("files")
    if not isinstance(files, dict) or not files:
        raise BuildError("bundle file inventory absent")
    actual = set()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = str(path.relative_to(root))
        if relative in ("bundle_manifest.json", "bundle_manifest.json.sha256"):
            continue
        if "__pycache__" in path.parts or ".pytest_cache" in path.parts:
            continue
        actual.add(relative)
    if actual != set(files):
        raise BuildError("bundle exact file inventory differs")
    for relative, expected in files.items():
        path = root / relative
        if path.is_symlink() or sha_path(path) != expected:
            raise BuildError("bundle file identity differs: %s" % relative)
    sidecar = (root / "bundle_manifest.json.sha256").read_text(encoding="ascii").split()[0]
    if sidecar != sha_path(root / "bundle_manifest.json"):
        raise BuildError("bundle manifest sidecar differs")
    return value


def portable_files() -> tuple[dict, dict[str, bytes]]:
    manifest = load_manifest(PORTABLE, "mysql_tpcc_typed_collector_portable_v2")
    if manifest.get("status") != "SEALED_PORTABLE_OFFLINE_TYPED_COLLECTOR":
        raise BuildError("portable collector status differs")
    payload = {
        name: (PORTABLE / name).read_bytes()
        for name in sorted(manifest["files"])
    }
    return manifest, payload


def verifier_with_portable_payload(data: bytes, file_hashes: dict[str, str]) -> bytes:
    text = data.decode("utf-8")
    if text.count(OLD_ID) != 1:
        raise BuildError("active verifier old payload identity cardinality differs")
    text = text.replace(OLD_ID, PORTABLE_ID)
    replacement = "COLLECTOR_FILES = " + json.dumps(
        file_hashes, sort_keys=True, indent=4
    ) + "\nFOUR_SCENE_CONTRACT_SHA256"
    text, count = re.subn(
        r"COLLECTOR_FILES = \{.*?\n\}\nFOUR_SCENE_CONTRACT_SHA256",
        replacement,
        text,
        count=1,
        flags=re.DOTALL,
    )
    if count != 1:
        raise BuildError("active verifier collector file map not found")
    return text.encode("utf-8")


def publisher_source(file_hashes: dict[str, str]) -> bytes:
    files_literal = json.dumps(file_hashes, sort_keys=True, indent=4)
    return ('''#!/usr/bin/env python3
"""Publish the v14-bound portable TPC-C typed collector without overwrite."""
from __future__ import annotations
import argparse,hashlib,os,shutil,tempfile
from pathlib import Path
BUNDLE_ID=%r
FILES=%s
SOURCE=Path(__file__).resolve().parents[2]/"authority"/("collector_payload_"+BUNDLE_ID)
def digest(path):
 h=hashlib.sha256()
 with path.open("rb") as stream:
  for block in iter(lambda:stream.read(1024*1024),b""):h.update(block)
 return h.hexdigest()
def verify(directory):
 actual={p.name for p in directory.iterdir() if p.is_file()}
 if actual!=set(FILES):raise RuntimeError("portable collector exact file set differs")
 for name,expected in FILES.items():
  if digest(directory/name)!=expected:raise RuntimeError("portable collector file identity differs: "+name)
def main():
 parser=argparse.ArgumentParser();parser.add_argument("--destination-root",type=Path,required=True);args=parser.parse_args()
 verify(SOURCE);args.destination_root.mkdir(parents=True,exist_ok=True);destination=args.destination_root/BUNDLE_ID
 if destination.exists():verify(destination);print("ALREADY_PUBLISHED_VERIFIED",destination);return
 stage=Path(tempfile.mkdtemp(prefix="."+BUNDLE_ID+".",dir=str(args.destination_root)))
 try:
  for name in sorted(FILES):shutil.copyfile(SOURCE/name,stage/name);os.chmod(stage/name,0o444)
  verify(stage);os.chmod(stage,0o555)
  try:os.rename(stage,destination)
  except FileExistsError:verify(destination)
  verify(destination)
 finally:
  if stage.exists():os.chmod(stage,0o755);shutil.rmtree(stage)
 print("PUBLISHED_ATOMIC_NOREPLACE_VERIFIED",destination)
if __name__=="__main__":main()
''' % (PORTABLE_ID, files_literal)).encode("utf-8")


def test_source() -> bytes:
    return b'''from __future__ import annotations
import hashlib,json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PORTABLE_ID="1878a2ca8b68e1b3917021499cda396e23f60a23baacd299496c78346e29c86c"
def test_v14_exact_identity_and_portable_collector():
 m=json.loads((ROOT/"bundle_manifest.json").read_text());assert m["schema_version"]=="mysql_tpcc_typed_distributed_successor_bundle_v14";assert m["bundle_id"]==ROOT.name;assert m["portable_collector_payload_id"]==PORTABLE_ID;assert m["accepted_round_authorized"] is False
 for relative,expected in m["files"].items():assert hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()==expected
 payload=ROOT/"authority"/("collector_payload_"+PORTABLE_ID);result=subprocess.run([sys.executable,"-m","pytest","-q","-p","no:cacheprovider",str(payload/"test_tpcc_typed_collector_portable_v2.py")],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True);assert result.returncode==0,result.stdout
 for name in ("tpcc_v2_formal10_readiness_verifier.py","tpcc_v3_formal10_readiness_verifier.py"):
  text=(ROOT/"authority"/name).read_text();assert PORTABLE_ID in text and "032eb2c9f4ba01a7edf8a438cdadc7fb115243246ba34923dbb18386802099bd" not in text
'''


def build() -> Path:
    parent = load_manifest(PARENT, "mysql_tpcc_typed_distributed_successor_bundle_v13")
    portable_manifest, portable_payload = portable_files()
    portable_hashes = dict(portable_manifest["files"])
    payload: dict[str, bytes] = {}
    old_prefix = "authority/collector_payload_%s/" % OLD_ID
    for relative in sorted(parent["files"]):
        if relative.startswith(old_prefix):
            continue
        data = (PARENT / relative).read_bytes()
        if relative in ACTIVE_VERIFIERS:
            data = verifier_with_portable_payload(data, portable_hashes)
        elif relative in ACTIVE_CONTRACTS:
            text = data.decode("utf-8")
            if text.count(OLD_ID) != 1:
                raise BuildError("active contract old payload identity cardinality differs")
            data = text.replace(OLD_ID, PORTABLE_ID).encode("utf-8")
        elif relative == PUBLISHER_REL:
            data = publisher_source(portable_hashes)
        payload[relative] = data
    for name, data in portable_payload.items():
        payload["authority/collector_payload_%s/%s" % (PORTABLE_ID, name)] = data
    payload[BUILDER_REL] = Path(__file__).read_bytes()
    payload[INVALIDATION_REL] = canonical({
        "schema_version": "mysql_tpcc_typed_collector_portability_invalidation_v1",
        "invalidated_payload_id": OLD_ID,
        "reason": "bundled test resolves its manifest through a workstation-specific Windows path and is not portable to the Linux formal host",
        "collector_implementation_sha256": portable_hashes["tpcc_typed_collector.py"],
        "replacement_payload_id": PORTABLE_ID,
        "replacement_test_count": 7,
        "replacement_test_status": "PASS",
        "formal_authorized": False,
        "accepted_round_authorized": False,
    })
    payload[TEST_REL] = test_source()
    files = {relative: sha_bytes(data) for relative, data in sorted(payload.items())}
    identity = {
        "schema_version": SCHEMA,
        "base_bundle_id": PARENT_ID,
        "base_manifest_sha256": sha_path(PARENT / "bundle_manifest.json"),
        "portable_collector_payload_id": PORTABLE_ID,
        "files": files,
    }
    bundle_id = sha_bytes(canonical(identity))
    manifest = {
        **identity,
        "bundle_id": bundle_id,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "status": "SEALED_PORTABLE_TYPED_COLLECTOR_SUCCESSOR_NOT_LAUNCHED",
        "qualification_authorized": False,
        "typed_capture_authorized": False,
        "formal_r0_authorized": False,
        "debug_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "old_payload_retained": False,
    }
    target = OUT / bundle_id
    if target.exists():
        load_manifest(target, SCHEMA)
        return target
    OUT.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".%s." % bundle_id, dir=str(OUT)))
    try:
        for relative, data in payload.items():
            path = stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o444)
        manifest_bytes = json.dumps(manifest, sort_keys=True, indent=2).encode("utf-8") + b"\n"
        (stage / "bundle_manifest.json").write_bytes(manifest_bytes)
        (stage / "bundle_manifest.json.sha256").write_text(
            "%s  bundle_manifest.json\n" % sha_bytes(manifest_bytes), encoding="ascii"
        )
        for path in stage.rglob("*"):
            if path.is_file():
                path.chmod(0o444)
        for path in sorted((p for p in stage.rglob("*") if p.is_dir()), reverse=True):
            path.chmod(0o555)
        stage.chmod(0o555)
        os.rename(stage, target)
    finally:
        if stage.exists():
            stage.chmod(0o755)
            shutil.rmtree(stage)
    load_manifest(target, SCHEMA)
    return target


if __name__ == "__main__":
    print(build())
