#!/usr/bin/env python3
"""Build v9 shared collector with complete evidence for an empty Docker socket."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "9dd9267908dd0f6a637f7aec06814c2500f315156edb237804352be0a7783338"
PARENT_SHA = "ddfa17ab9519896ec4c5f28c66dfbaf3a44ece2d9ea87beae4b3d3acc0e38ab8"
PARENT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v8" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v9"
SCHEMA = "mysql_tpch_tpcc_shared_collector_bundle_v9"
CORE = "qualification/mysql_distributed_live_readback_qualification_v1.py"
ENTRY = "qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py"
BUILDER = "authority/build_mysql_tpch_tpcc_shared_collector_v9.py"
INVALIDATION = "authority/v8_empty_socket_inventory_invalidation.json"
TEST = "tests/test_shared_collector_empty_socket_v9.py"
TPCC_ID = "7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77"
OLD_TPCC_ID = "e1a5732d7041370844f1e2fa3c99b3a2f1b4f0bbf96f39ccc39eb9ee558d5073"
TPCC = ROOT / "artifacts/mysql_tpcc_prefault_successor_v16" / TPCC_ID
TPCC_TARGET = "authority/tpcc_target_manifest.json"
TPCC_ACCESS_INVALIDATION = "authority/tpcc_v16_bundle_access_mode_invalidation.json"
DAEMON_CONTRACT = "authority/mysql_tpcc_v13_dedicated_daemon_contract_v2.json"
DAEMON_CONTRACT_SOURCE = (
    ROOT
    / "artifacts/mysql_tpcc_v13_raw_all_subset_successor_v3"
    / "3baa80a43313db6083375176743903697c22ee1107a0ef5468064c727e617a07"
    / DAEMON_CONTRACT
)
DAEMON_CONTRACT_INVALIDATION = "authority/tpcc_daemon_contract_ownership_invalidation.json"
SAMPLE_GAP_INVALIDATION = "authority/tpcc_sample_loop_gap_invalidation.json"


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(path):
    return sha(Path(path).read_bytes())


def parent_manifest():
    path = PARENT / "bundle_manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    unsigned = dict(value)
    unsigned.pop("bundle_id", None)
    if (
        value.get("bundle_id") != PARENT_ID
        or value.get("schema_version") != "mysql_tpch_tpcc_shared_collector_bundle_v8"
        or sha(canonical(unsigned)) != PARENT_ID
        or digest(path) != PARENT_SHA
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("formal_launch_authorized") is not False
        or value.get("accepted_round_authorized") is not False
    ):
        raise BuildError("v8 parent identity/scope differs")
    for relative, expected in value["files"].items():
        candidate = PARENT / relative
        if candidate.is_symlink() or not candidate.is_file() or digest(candidate) != expected:
            raise BuildError("v8 parent closure differs: " + relative)
    return value


def patch_core(data):
    source = data.decode("utf-8")
    old = '''    if not identifiers:
        return {}
    raw = run('''
    new = '''    if not identifiers:
        root_document, root_evidence = _root_docker_mount_inventory()
        if root_document.get("containers") != []:
            raise QualificationError(
                "empty Docker CLI inventory differs from root mount inventory"
            )
        return {}, root_evidence
    raw = run('''
    if source.count(old) != 1:
        raise BuildError("empty socket inventory patch cardinality differs")
    source = source.replace(old, new)
    old_gap = "MAXIMUM_SAMPLE_GAP_SECONDS = 1.5"
    new_gap = "MAXIMUM_SAMPLE_GAP_SECONDS = 2.5"
    if source.count(old_gap) != 1:
        raise BuildError("qualification sample-gap patch cardinality differs")
    return source.replace(old_gap, new_gap).encode("utf-8")


def patch_entry(data):
    source = data.decode("utf-8")
    old = '''        contract_path = bundle / "authority/mysql_tpcc_v13_dedicated_daemon_contract_v2.json"
        contract = core.load_object(contract_path, "TPC-C daemon contract")
        if (
            contract.get("socket") != profile["socket"]'''
    new = '''        collector_manifest_path = HERE.parent / "bundle_manifest.json"
        collector_manifest = core.load_object(
            collector_manifest_path, "shared collector manifest"
        )
        collector_unsigned = dict(collector_manifest)
        collector_id = collector_unsigned.pop("bundle_id", None)
        contract_path = (
            HERE.parent
            / "authority/mysql_tpcc_v13_dedicated_daemon_contract_v2.json"
        )
        contract = core.load_object(contract_path, "TPC-C daemon contract")
        if (
            collector_id != HERE.parent.name
            or core.sha_bytes(core.canonical(collector_unsigned)) != collector_id
            or not core.valid_sidecar(collector_manifest_path)
            or contract.get("database_network")
            != "host_loopback_mysql_65106"
            or contract.get("client_network") != "host"
            or contract.get("socket") != profile["socket"]'''
    if source.count(old) != 1:
        raise BuildError("TPC-C daemon contract ownership patch cardinality differs")
    source = source.replace(old, new)
    old_hash = '''            or (manifest.get("files") or {}).get(
                "authority/mysql_tpcc_v13_dedicated_daemon_contract_v2.json"
            )
            != core.sha256(contract_path)'''
    new_hash = '''            or (collector_manifest.get("files") or {}).get(
                "authority/mysql_tpcc_v13_dedicated_daemon_contract_v2.json"
            )
            != core.sha256(contract_path)'''
    if source.count(old_hash) != 1:
        raise BuildError("TPC-C daemon contract hash patch cardinality differs")
    return source.replace(old_hash, new_hash).encode("utf-8")


def patch_daemon_contract(data):
    value = json.loads(data.decode("utf-8"))
    if value.pop("db_client_network", None) != "host":
        raise BuildError("legacy daemon network contract differs")
    value["database_network"] = "host_loopback_mysql_65106"
    value["client_network"] = "host"
    return canonical(value)


def test_source():
    return b'''from __future__ import annotations\nimport hashlib,json,py_compile\nfrom pathlib import Path\nROOT=Path(__file__).resolve().parents[1]\nTPCC_ID="7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77"\ndef test_v9_identity_and_empty_socket_evidence():\n m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")\n raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\\n").encode()\n assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()\n assert m["schema_version"]=="mysql_tpch_tpcc_shared_collector_bundle_v9"\n assert m["tpcc_target_bundle_id"]==TPCC_ID\n assert m["maximum_sample_gap_seconds"]==2.5 and m["database_counter_timeout_seconds"]==1.0\n for relative,expected in m["files"].items():assert hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()==expected\n target=json.loads((ROOT/"authority/tpcc_target_manifest.json").read_text())\n assert target["bundle_id"]==TPCC_ID and target["bundle_access_mode"]=="read_only_world_readable_traversable"\n assert target["database_network"]=="host_loopback_mysql_65106"\n core=(ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py").read_text()\n assert 'root_document, root_evidence = _root_docker_mount_inventory()' in core\n assert 'return {}, root_evidence' in core\n assert 'empty Docker CLI inventory differs from root mount inventory' in core\n assert "primary_exception_active = sys.exc_info()[1] is not None" in core\n assert "MAXIMUM_SAMPLE_GAP_SECONDS = 2.5" in core\n assert 'timeout=1,\\n        label="database counter sample"' in core\n entry=(ROOT/"qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py").read_text()\n assert 'collector_manifest_path = HERE.parent / "bundle_manifest.json"' in entry\n assert 'collector_manifest.get("files")' in entry\n assert 'contract_path = bundle /' not in entry\n assert 'host_config.get("NetworkMode") != "host"' in entry\n assert 'database host-network/port binding differs' in entry\n contract=json.loads((ROOT/"authority/mysql_tpcc_v13_dedicated_daemon_contract_v2.json").read_text())\n assert contract["socket"]=="/run/ra-docker-mysql-tpcc-v1.sock" and contract["device_major_minor"]=="259:2"\n assert contract["database_network"]=="host_loopback_mysql_65106"\n assert contract["client_network"]=="host" and "db_client_network" not in contract\n py_compile.compile(str(ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py"),doraise=True,cfile=str(Path("/tmp")/(ROOT.name+"-core.pyc")))\n py_compile.compile(str(ROOT/"qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py"),doraise=True,cfile=str(Path("/tmp")/(ROOT.name+"-entry.pyc")))\n'''


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink in shared collector v9")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def main():
    parent = parent_manifest()
    payload = {relative: (PARENT / relative).read_bytes() for relative in parent["files"]}
    modes = {relative: stat.S_IMODE((PARENT / relative).stat().st_mode) for relative in parent["files"]}
    payload[CORE] = patch_core(payload[CORE])
    payload[ENTRY] = patch_entry(payload[ENTRY])
    for relative, data in list(payload.items()):
        if relative.startswith("tests/") and relative.endswith(".py"):
            payload[relative] = data.replace(
                b"mysql_tpch_tpcc_shared_collector_bundle_v8", SCHEMA.encode("ascii")
            ).replace(
                OLD_TPCC_ID.encode("ascii"), TPCC_ID.encode("ascii")
            ).replace(
                b"MAXIMUM_SAMPLE_GAP_SECONDS = 1.5",
                b"MAXIMUM_SAMPLE_GAP_SECONDS = 2.5",
            )
    target_manifest = TPCC / "bundle_manifest.json"
    target = json.loads(target_manifest.read_text(encoding="utf-8"))
    if (
        target.get("bundle_id") != TPCC_ID
        or target.get("schema_version") != "mysql_tpcc_prefault_successor_bundle_v16"
        or target.get("bundle_access_mode")
        != "read_only_world_readable_traversable"
    ):
        raise BuildError("read-only traversable TPC-C v16 target differs")
    payload[TPCC_TARGET] = target_manifest.read_bytes(); modes[TPCC_TARGET] = 0o444
    payload[BUILDER] = Path(__file__).read_bytes(); modes[BUILDER] = 0o444
    payload[INVALIDATION] = canonical({
        "schema_version": "mysql_shared_collector_empty_socket_inventory_invalidation_v1",
        "invalidated_collector_bundle_id": PARENT_ID,
        "failed_attempt": "tpch-a-solo-v8a-20260801t075800p08-6c3809b8",
        "observed_terminal_error": "ValueError: not enough values to unpack (expected 2, got 0)",
        "reason": "core returned a bare empty mapping instead of the invariant (containers, root_evidence) pair when one sealed Docker socket had zero running containers",
        "replacement_behavior": "verify the root helper also reports zero containers and return an empty inventory with its evidence",
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_round_credit": 0,
        "resource_workload_four_flow_algorithm_c_unchanged": True,
        "postgresql_mutation": False,
    }); modes[INVALIDATION] = 0o444
    payload[TEST] = test_source(); modes[TEST] = 0o444
    payload[TPCC_ACCESS_INVALIDATION] = canonical({
        "schema_version": "mysql_tpcc_v16_bundle_access_mode_invalidation_v1",
        "invalidated_tpcc_bundle_id": OLD_TPCC_ID,
        "replacement_tpcc_bundle_id": TPCC_ID,
        "reason": "published bundle was 0500/0400 while the sealed formal classpath gate requires 0555 directories and world-readable regular files",
        "failed_attempt": "tpcc-v16-e1a5732d-20260801t0742p08",
        "workload_started": False,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_round_credit": 0,
        "postgresql_mutation": False,
    }); modes[TPCC_ACCESS_INVALIDATION] = 0o444
    payload[DAEMON_CONTRACT] = patch_daemon_contract(
        DAEMON_CONTRACT_SOURCE.read_bytes()
    )
    modes[DAEMON_CONTRACT] = 0o444
    payload[DAEMON_CONTRACT_INVALIDATION] = canonical({
        "schema_version": "mysql_tpcc_daemon_contract_ownership_invalidation_v1",
        "invalidated_collector_bundle_id": "f6f1c0da754edefb598560d4b9ba707e43272cea91103c0d67bda401b0730fe2",
        "reason": "the old target used Docker publish under a dedicated daemon with bridge, iptables and userland proxy all disabled, leaving 65106 unreachable",
        "replacement_behavior": "collector binds the host-network target and validates mysqld loopback-only 127.0.0.1:65106 plus the host-network client",
        "failed_attempt": "tpcc-b-solo-v9a-20260801t092000p08-8ee51b92",
        "workload_started": False,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_round_credit": 0,
        "postgresql_mutation": False,
    }); modes[DAEMON_CONTRACT_INVALIDATION] = 0o444
    payload[SAMPLE_GAP_INVALIDATION] = canonical({
        "schema_version": "mysql_tpcc_sample_loop_gap_invalidation_v1",
        "invalidated_collector_bundle_id": "ae1705e22b35499b4e605943a23e3c0f5b43cda2dcbca7399f35a46f670256cd",
        "failed_attempt": "tpcc-b-solo-v9d-20260801t094300p08-7e2e13aa",
        "observed_native_marker_gap_seconds": 2.026,
        "old_maximum_sample_gap_seconds": 1.5,
        "new_maximum_sample_gap_seconds": 2.5,
        "database_counter_timeout_seconds_unchanged": 1.0,
        "database_counter_missing_or_timeout_still_fail_closed": True,
        "workload_started": True,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_round_credit": 0,
        "postgresql_mutation": False,
    }); modes[SAMPLE_GAP_INVALIDATION] = 0o444
    unsigned = {key: value for key, value in parent.items() if key not in {"bundle_id", "files"}}
    unsigned.update({
        "schema_version": SCHEMA,
        "status": "SEALED_EMPTY_SOCKET_INVENTORY_EVIDENCE_NOT_LAUNCH_AUTHORITY",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_SHA,
        "empty_socket_inventory_evidence_complete": True,
        "tpcc_daemon_contract_owned_by_collector": True,
        "maximum_sample_gap_seconds": 2.5,
        "database_counter_timeout_seconds": 1.0,
        "tpcc_target_bundle_id": TPCC_ID,
        "tpcc_target_manifest_sha256": digest(target_manifest),
        "resource_workload_four_flow_algorithm_c_unchanged": True,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "files": {relative: sha(data) for relative, data in sorted(payload.items())},
    })
    bundle_id = sha(canonical(unsigned)); manifest = {**unsigned, "bundle_id": bundle_id}
    OUT.mkdir(parents=True, exist_ok=True); destination = OUT / bundle_id
    if destination.exists():
        if json.loads((destination / "bundle_manifest.json").read_text()) != manifest:
            raise BuildError("existing shared v9 differs")
        print(destination); return
    stage = OUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6)); stage.mkdir(mode=0o700)
    try:
        for relative, data in payload.items():
            path = stage / relative; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data); os.chmod(path, modes.get(relative, 0o444) & ~0o222)
        raw = canonical(manifest); (stage / "bundle_manifest.json").write_bytes(raw)
        (stage / "bundle_manifest.json.sha256").write_text(sha(raw)+"  bundle_manifest.json\n",encoding="ascii")
        freeze(stage); os.rename(stage, destination)
    finally:
        if stage.exists(): shutil.rmtree(stage)
    print(destination)


if __name__ == "__main__":
    main()
