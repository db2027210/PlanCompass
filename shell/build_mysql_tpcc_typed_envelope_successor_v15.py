#!/usr/bin/env python3
"""Build the immutable TPC-C v15 typed/envelope successor.

The v14 portable-collector successor is retained as immutable provenance, but
it cannot be launched: its reduced manifest omitted the scenario and sealed
four-scene-contract binding and used a different manifest canonicalization
from the runtime verifiers.  Separately, the live 8 GiB database cgroup was at
its hard limit for all 181 qualification samples and accumulated 237681 new
memory.failcnt events.  This successor fixes those authority defects and
freezes a 32 GiB database memory+swap limit while leaving the 8 GiB client,
CPU, NUMA, disk, port, pids, I/O, buffer pool, and Algorithm C unchanged.

This builder is local/offline only.  It grants no launch, R0, debug, formal-ten,
or accepted-round authority.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import py_compile
import re
import shutil
import stat
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "17f38a9cd723972cee28319908a3ad52d086edf1ae4db631e4bc715d10867546"
PARENT = ROOT / "artifacts/mysql_tpcc_typed_distributed_successor_v14" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpcc_typed_envelope_successor_v15"
SCHEMA = "mysql_tpcc_typed_envelope_successor_bundle_v15"
DB_MEMORY = 32 * 1024**3
CLIENT_MEMORY = 8 * 1024**3
TYPED_RUNTIME_ID = "9860f0fa7c28c951b2a1f6b210e0fad3a88ab444dbc5b7e658b0ae65259efc75"
STALE_TYPED_RUNTIME_ID = "fe7aaa24d15256f4e771af598ac672cb72ad9106a02c6dea5e30cc679e55d516"
BUILDER_REL = "authority/build_mysql_tpcc_typed_envelope_successor_v15.py"
INVALIDATION_REL = "authority/mysql_tpcc_v14_manifest_and_8g_envelope_invalidation_20260801.json"
TEST_REL = "tests/test_mysql_tpcc_typed_envelope_successor_v15.py"
FOUR_SCENE_REL = "authority/four_scene_successor_contract.json"
RUNTIME_REL = "distributed_runtime_contract.json"
SERVICE_REL = "authority/mysql_tpcc_node04_adopted_service_v1.py"
MATERIALIZER_REL = "authority/mysql_tpcc_materialize_phase_command_v2.py"
VERIFIER_RELS = (
    "authority/tpcc_v2_formal10_readiness_verifier.py",
    "authority/tpcc_v3_formal10_readiness_verifier.py",
)
READINESS_CONTRACT_RELS = (
    "authority/tpcc_v2_formal10_readiness_contract.json",
    "authority/tpcc_v3_formal10_readiness_contract.json",
)


class BuildError(RuntimeError):
    pass


def canonical(value: object) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_parent() -> dict:
    manifest_path = PARENT / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("bundle_id") != PARENT_ID
        or manifest.get("schema_version")
        != "mysql_tpcc_typed_distributed_successor_bundle_v14"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("v14 parent identity/scope differs")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise BuildError("v14 parent file closure absent")
    for relative, expected in files.items():
        path = PARENT / relative
        if path.is_symlink() or not path.is_file() or sha_path(path) != expected:
            raise BuildError("v14 parent closure differs: " + relative)
    return manifest


def load_json_bytes(data: bytes, label: str) -> dict:
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict):
        raise BuildError(label + " must be an object")
    return value


def patch_runtime(data: bytes) -> bytes:
    value = load_json_bytes(data, "runtime contract")
    database = value.get("resource_envelope") or {}
    client = value.get("client_resource_envelope") or {}
    if (
        database.get("memory_bytes") != CLIENT_MEMORY
        or database.get("memory_swap_bytes") != CLIENT_MEMORY
        or client.get("memory_bytes") != CLIENT_MEMORY
        or client.get("memory_swap_bytes") != CLIENT_MEMORY
    ):
        raise BuildError("v14 runtime memory envelope differs")
    database["memory_bytes"] = DB_MEMORY
    database["memory_swap_bytes"] = DB_MEMORY
    value["resource_envelope_recalibration"] = {
        "schema_version": "mysql_tpcc_cpu_memory_envelope_recalibration_v1",
        "source_flow": "B_solo_v5b",
        "source_sample_count": 181,
        "source_memory_limit_bytes": CLIENT_MEMORY,
        "source_memory_current_first_bytes": 8589832192,
        "source_memory_current_last_bytes": 8589910016,
        "source_memory_failcnt_delta": 237681,
        "source_rss_bytes_after": 2725867520,
        "source_cache_bytes_after": 5863612416,
        "new_database_memory_bytes": DB_MEMORY,
        "new_database_memory_swap_bytes": DB_MEMORY,
        "cpu_io_numa_disk_port_pids_buffer_pool_unchanged": True,
        "fresh_solo_and_paired_qualification_required": True,
        "fresh_r0_required": True,
        "accepted_round_credit": 0,
    }
    return canonical(value)


def patch_four_scene(data: bytes) -> bytes:
    value = load_json_bytes(data, "four-scene contract")
    envelope = (((value.get("scenarios") or {}).get("tpcc") or {}).get("resource_envelope") or {})
    if (
        envelope.get("memory_bytes") != CLIENT_MEMORY
        or envelope.get("memory_swap_bytes") != CLIENT_MEMORY
    ):
        raise BuildError("v14 four-scene TPC-C memory envelope differs")
    envelope["memory_bytes"] = DB_MEMORY
    envelope["memory_swap_bytes"] = DB_MEMORY
    return canonical(value)


def patch_readiness_contract(data: bytes) -> bytes:
    value = load_json_bytes(data, "readiness contract")
    database = value.get("database_envelope")
    client = value.get("client_envelope")
    if not isinstance(database, dict) or not isinstance(client, dict):
        raise BuildError("readiness envelopes absent")
    if (
        client.get("memory_bytes") != CLIENT_MEMORY
        or client.get("memory_swap_bytes") != CLIENT_MEMORY
    ):
        raise BuildError("v14 readiness client envelope differs")
    database["memory_bytes"] = DB_MEMORY
    database["memory_swap_bytes"] = DB_MEMORY
    return canonical(value)


def replace_once(source: str, old: str, new: str, label: str) -> str:
    if source.count(old) != 1:
        raise BuildError(label + " patch cardinality differs")
    return source.replace(old, new)


def patch_service(data: bytes) -> bytes:
    source = data.decode("utf-8")
    source = replace_once(
        source,
        "MEMORY = 8 * 1024**3",
        "DB_MEMORY = 32 * 1024**3\nCLIENT_MEMORY = 8 * 1024**3",
        "service memory constants",
    )
    create_memory = '"--memory", str(MEMORY), "--memory-swap", str(MEMORY),'
    if source.count(create_memory) != 2:
        raise BuildError("database/client create memory cardinality differs")
    source = source.replace(
        create_memory,
        '"--memory", str(DB_MEMORY), "--memory-swap", str(DB_MEMORY),',
        1,
    )
    source = replace_once(
        source,
        '"--memory", str(MEMORY), "--memory-swap", str(MEMORY),',
        '"--memory", str(CLIENT_MEMORY), "--memory-swap", str(CLIENT_MEMORY),',
        "client create memory",
    )
    source = replace_once(
        source,
        "    if (\n        host.get(\"CpusetCpus\") != expected[0]",
        "    expected_memory = DB_MEMORY if role == \"database\" else CLIENT_MEMORY\n"
        "    if (\n        host.get(\"CpusetCpus\") != expected[0]",
        "inspect expected memory selection",
    )
    if source.count("int(host.get(\"Memory\") or 0) != MEMORY") != 1:
        raise BuildError("inspect Memory cardinality differs")
    source = source.replace(
        'int(host.get("Memory") or 0) != MEMORY',
        'int(host.get("Memory") or 0) != expected_memory',
    )
    if source.count("int(host.get(\"MemorySwap\") or 0) != MEMORY") != 1:
        raise BuildError("inspect MemorySwap cardinality differs")
    source = source.replace(
        'int(host.get("MemorySwap") or 0) != MEMORY',
        'int(host.get("MemorySwap") or 0) != expected_memory',
    )
    if source.count('"memory_bytes": MEMORY') != 1 or source.count('"memory_swap_bytes": MEMORY') != 1:
        raise BuildError("service readback memory cardinality differs")
    source = source.replace('"memory_bytes": MEMORY', '"memory_bytes": expected_memory')
    source = source.replace('"memory_swap_bytes": MEMORY', '"memory_swap_bytes": expected_memory')
    if re.search(r"\bMEMORY\b", source):
        raise BuildError("unqualified service MEMORY reference remains")
    return source.encode("utf-8")


def patch_materializer(data: bytes) -> bytes:
    source = data.decode("utf-8")
    for field in ("memory_bytes", "memory_swap_bytes"):
        source = replace_once(
            source,
            f'database.get("{field}") != 8 * 1024**3',
            f'database.get("{field}") != 32 * 1024**3',
            "materializer database " + field,
        )
    return source.encode("utf-8")


def patch_verifier(data: bytes, four_scene_sha: str) -> bytes:
    source = data.decode("utf-8")
    source = replace_once(
        source,
        STALE_TYPED_RUNTIME_ID,
        TYPED_RUNTIME_ID,
        "verifier embedded typed-runtime identity",
    )
    source, count = re.subn(
        r'(FOUR_SCENE_CONTRACT_SHA256 = \(\n    ")[0-9a-f]{64}("\n\))',
        r'\g<1>' + four_scene_sha + r'\g<2>',
        source,
        count=1,
    )
    if count != 1:
        raise BuildError("verifier four-scene hash patch cardinality differs")
    return source.encode("utf-8")


def test_source() -> bytes:
    return b'''from __future__ import annotations
import hashlib,importlib.util,json,py_compile,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DB_MEMORY=32*1024**3
CLIENT_MEMORY=8*1024**3
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def test_v15_identity_envelope_and_portable_collector():
 m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")
 raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\\n").encode()
 assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()
 assert m["schema_version"]=="mysql_tpcc_typed_envelope_successor_bundle_v15"
 assert m["scenario"]=="tpcc" and m["formal_scope"]=="mysql_only"
 assert m["cross_engine_comparison_prohibited"] is True
 assert m["formal_launch_authorized"] is False and m["accepted_round_authorized"] is False
 for relative,expected in m["files"].items():assert sha(ROOT/relative)==expected
 runtime=json.loads((ROOT/"distributed_runtime_contract.json").read_text())
 assert runtime["resource_envelope"]["memory_bytes"]==DB_MEMORY
 assert runtime["resource_envelope"]["memory_swap_bytes"]==DB_MEMORY
 assert runtime["client_resource_envelope"]["memory_bytes"]==CLIENT_MEMORY
 assert runtime["client_resource_envelope"]["memory_swap_bytes"]==CLIENT_MEMORY
 four=ROOT/"authority/four_scene_successor_contract.json"
 assert m["four_scene_contract_sha256"]==sha(four)
 assert m["distributed_runtime_contract_sha256"]==sha(ROOT/"distributed_runtime_contract.json")
 for name in ("tpcc_v2_formal10_readiness_contract.json","tpcc_v3_formal10_readiness_contract.json"):
  c=json.loads((ROOT/"authority"/name).read_text());assert c["database_envelope"]["memory_bytes"]==DB_MEMORY;assert c["client_envelope"]["memory_bytes"]==CLIENT_MEMORY
 for name in ("mysql_tpcc_node04_adopted_service_v1.py","mysql_tpcc_node04_adopted_service_v2.py","mysql_tpcc_materialize_phase_command_v2.py","tpcc_v2_formal10_readiness_verifier.py","tpcc_v3_formal10_readiness_verifier.py"):
  py_compile.compile(str(ROOT/"authority"/name),doraise=True,cfile=str(Path("/tmp")/(ROOT.name+"-"+name+".pyc")))
 payload=ROOT/"authority"/"collector_payload_1878a2ca8b68e1b3917021499cda396e23f60a23baacd299496c78346e29c86c"
 result=subprocess.run([sys.executable,"-m","pytest","-q","-p","no:cacheprovider",str(payload/"test_tpcc_typed_collector_portable_v2.py")],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
 assert result.returncode==0,result.stdout
'''


def freeze(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink in v15 bundle")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def build() -> Path:
    parent = load_parent()
    payload = {
        relative: (PARENT / relative).read_bytes()
        for relative in sorted(parent["files"])
    }
    modes = {
        relative: stat.S_IMODE((PARENT / relative).stat().st_mode)
        for relative in parent["files"]
    }
    payload[RUNTIME_REL] = patch_runtime(payload[RUNTIME_REL])
    payload[FOUR_SCENE_REL] = patch_four_scene(payload[FOUR_SCENE_REL])
    for relative in READINESS_CONTRACT_RELS:
        payload[relative] = patch_readiness_contract(payload[relative])
    payload[SERVICE_REL] = patch_service(payload[SERVICE_REL])
    payload[MATERIALIZER_REL] = patch_materializer(payload[MATERIALIZER_REL])
    four_scene_sha = sha_bytes(payload[FOUR_SCENE_REL])
    for relative in VERIFIER_RELS:
        payload[relative] = patch_verifier(payload[relative], four_scene_sha)
    inherited_test = "tests/test_mysql_tpcc_typed_distributed_successor_v14.py"
    if inherited_test in payload:
        text = payload[inherited_test].decode("utf-8")
        text = text.replace(
            "mysql_tpcc_typed_distributed_successor_bundle_v14", SCHEMA
        )
        payload[inherited_test] = text.encode("utf-8")
    payload[BUILDER_REL] = Path(__file__).read_bytes()
    modes[BUILDER_REL] = 0o444
    payload[INVALIDATION_REL] = canonical({
        "schema_version": "mysql_tpcc_v14_manifest_and_8g_envelope_invalidation_v1",
        "invalidated_bundle_id": PARENT_ID,
        "invalidated_local_test_bundle_ids": [
            "9ff95600c128eeb262759186846de802d078702c9282a191321527addb09ab9d",
            "1ec43f654bb90b83a4c95b08ac393ac50dbd978f5be90d94d1e00a1ca67f3afb",
        ],
        "reasons": [
            "manifest omitted scenario and four_scene_contract_sha256 required by active service/readiness verifiers",
            "manifest content address used a canonicalization different from active runtime verifiers",
            "8 GiB database cgroup accumulated 237681 new memory.failcnt events during 181-sample B_solo_v5b",
        ],
        "replacement_database_memory_bytes": DB_MEMORY,
        "replacement_database_memory_swap_bytes": DB_MEMORY,
        "client_memory_bytes_unchanged": CLIENT_MEMORY,
        "fresh_solo_paired_r0_required": True,
        "formal_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "postgresql_mutation": False,
    })
    modes[INVALIDATION_REL] = 0o444
    payload[TEST_REL] = test_source()
    modes[TEST_REL] = 0o444
    files = {relative: sha_bytes(data) for relative, data in sorted(payload.items())}
    unsigned = {
        "schema_version": SCHEMA,
        "status": "SEALED_TYPED_32G_ENVELOPE_SUCCESSOR_NOT_LAUNCHED",
        "scenario": "tpcc",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "base_bundle_id": PARENT_ID,
        "base_manifest_sha256": sha_path(PARENT / "bundle_manifest.json"),
        "portable_collector_payload_id": parent["portable_collector_payload_id"],
        "typed_runtime_bundle_id": TYPED_RUNTIME_ID,
        "four_scene_contract_sha256": four_scene_sha,
        "distributed_runtime_contract_sha256": sha_bytes(payload[RUNTIME_REL]),
        "database_memory_bytes": DB_MEMORY,
        "database_memory_swap_bytes": DB_MEMORY,
        "client_memory_bytes": CLIENT_MEMORY,
        "client_memory_swap_bytes": CLIENT_MEMORY,
        "cpu_io_numa_disk_port_pids_buffer_pool_unchanged": True,
        "immutable": True,
        "offline_build_only": True,
        "remote_actions": 0,
        "qualification_authorized": False,
        "typed_capture_authorized": False,
        "formal_r0_authorized": False,
        "debug_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "fresh_solo_paired_and_r0_required": True,
        "files": files,
    }
    bundle_id = sha_bytes(canonical(unsigned))
    manifest = {**unsigned, "bundle_id": bundle_id}
    target = OUT / bundle_id
    if target.exists():
        current = json.loads((target / "bundle_manifest.json").read_text())
        if current != manifest:
            raise BuildError("existing v15 target differs")
        return target
    OUT.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="." + bundle_id + ".", dir=str(OUT)))
    try:
        for relative, data in payload.items():
            path = stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            os.chmod(path, modes.get(relative, 0o444) & ~0o222)
        raw = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(raw)
        (stage / "bundle_manifest.json.sha256").write_text(
            sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii"
        )
        freeze(stage)
        os.rename(stage, target)
    finally:
        if stage.exists():
            os.chmod(stage, 0o755)
            shutil.rmtree(stage)
    return target


if __name__ == "__main__":
    print(build())
