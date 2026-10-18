#!/usr/bin/env python3
"""Build a collector that binds the recreated TPC-C container epoch."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "f5a7a688f1c981b6c92e6dae0cf6aac580d0326f7f4d1f46c7493af993bc8a23"
PARENT = ROOT / "artifacts/mysql_tpcc_inventory_dynamic_successor_v1" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpcc_inventory_dynamic_recreated_v13"
ENTRY = "qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py"


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode()


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


HELPER = r'''

TPCC_RECREATED_ADOPTED_NAME = "adopted_service.recreated_swappiness0_v3.json"
TPCC_RECREATED_ADOPTED_SHA256 = "b9fcd51325ea80d4284df5cf3c9df81fd022a79df5fa3002d2635fe7a8765001"
TPCC_RECREATED_BLKIO_NAME = "blkio_throttle_authority.recreated_swappiness0_v3.json"
TPCC_RECREATED_BLKIO_SHA256 = "b823a7c61f5d11dfa50a13f9fa1ea268b70410004a57b8449bc1e442bc1a11e8"
TPCC_RECREATED_DB_ID = "fc87aaf6a954e0d5131ff695ca808247c906525af9b4fad18c94aa6387c55948"
TPCC_RECREATED_DB_PID = 100769
TPCC_RECREATED_DB_START = 268728753
TPCC_RECREATED_DB_CGROUP_SHA256 = "0ac085e5b6b435d7610695e5fcb2c5e3b97f2e720b845d38565d6cdfde3bae3a"
TPCC_RECREATED_CLIENT_ID = "51c437f082bec5a7f95c7f0ce9a54bf549d87db7aa41a45bcec69390de6801a4"
TPCC_RECREATED_CLIENT_PID = 100845
TPCC_RECREATED_CLIENT_START = 268728786


def _tpcc_recreated_process_start(pid):
    raw = (Path("/proc") / str(pid) / "stat").read_text()
    return int(raw[raw.rfind(")") + 2:].split()[19])


def _validate_tpcc_recreated_authorities(container_row, runtime, defaults_file, bundle_id):
    attempt_root = Path(defaults_file).parent.parent
    adopted_path = attempt_root / TPCC_RECREATED_ADOPTED_NAME
    authority_path = attempt_root / TPCC_RECREATED_BLKIO_NAME
    if (not core.valid_sidecar(adopted_path)
            or core.sha256(adopted_path) != TPCC_RECREATED_ADOPTED_SHA256
            or not core.valid_sidecar(authority_path)
            or core.sha256(authority_path) != TPCC_RECREATED_BLKIO_SHA256):
        raise core.QualificationError("TPC-C recreated authority seal differs")
    adopted = core.load_object(adopted_path, "TPC-C recreated adopted authority")
    value = core.load_object(authority_path, "TPC-C recreated blkio authority")
    container_id = container_row.get("Id")
    pid = int((container_row.get("State") or {}).get("Pid") or 0)
    start = _tpcc_recreated_process_start(pid)
    cgroup_raw = (Path("/proc") / str(pid) / "cgroup").read_text()
    database = adopted.get("database") or {}
    client = adopted.get("client") or {}
    envelope = runtime["resource_envelope"]
    storage = runtime["storage"]
    if (adopted.get("schema_version") != "mysql_tpcc_recreated_service_authority_v3"
            or adopted.get("status") != "PASS_DURABLE_ZERO_SWAP_RECREATED_IDLE_READY"
            or adopted.get("formal_scope") != "mysql_only"
            or adopted.get("cross_engine_comparison_prohibited") is not True
            or adopted.get("postgresql_mutation_prohibited") is not True
            or adopted.get("accepted_rounds_credit") != 0
            or adopted.get("fresh_blkio_authority_sha256") != TPCC_RECREATED_BLKIO_SHA256
            or database.get("container_id") != TPCC_RECREATED_DB_ID
            or int(database.get("pid") or 0) != TPCC_RECREATED_DB_PID
            or int(database.get("process_start_ticks") or 0) != TPCC_RECREATED_DB_START
            or database.get("process_cgroup_sha256") != TPCC_RECREATED_DB_CGROUP_SHA256
            or int(database.get("memory_swappiness", -1)) != 0
            or int(database.get("total_swap_bytes", -1)) != 0
            or client.get("container_id") != TPCC_RECREATED_CLIENT_ID
            or int(client.get("pid") or 0) != TPCC_RECREATED_CLIENT_PID
            or int(client.get("process_start_ticks") or 0) != TPCC_RECREATED_CLIENT_START
            or int(client.get("memory_swappiness", -1)) != 0
            or int(client.get("total_swap_bytes", -1)) != 0):
        raise core.QualificationError("TPC-C recreated adopted authority differs")
    if (container_id != TPCC_RECREATED_DB_ID or pid != TPCC_RECREATED_DB_PID
            or start != TPCC_RECREATED_DB_START
            or core.sha_bytes(cgroup_raw.encode()) != TPCC_RECREATED_DB_CGROUP_SHA256):
        raise core.QualificationError("TPC-C recreated live database epoch differs")
    if (value.get("schema_version") != "mysql_tpcc_recreated_blkio_authority_v3"
            or value.get("status") != "PASS_FRESH_RECREATED_CONTAINER_EPOCH"
            or value.get("database_container_id") != container_id
            or int(value.get("database_pid") or 0) != pid
            or int(value.get("process_start_ticks") or 0) != start
            or value.get("device") != storage["expected_live_major_minor"]
            or int(value.get("read_bps") or 0) != int(envelope["io_read_bps"])
            or int(value.get("write_bps") or 0) != int(envelope["io_write_bps"])
            or value.get("postgresql_mutation_performed") is not False
            or value.get("accepted_rounds_credit") != 0):
        raise core.QualificationError("TPC-C recreated blkio authority differs")
    roots = core._cgroup_roots(pid)
    if roots["version"] != 1 or str(roots["io"]) != "/sys/fs/cgroup/blkio/docker/" + container_id:
        raise core.QualificationError("TPC-C recreated live blkio cgroup differs")
    live = core._v1_effective_throttle(
        roots["io"], storage["expected_live_major_minor"],
        envelope["io_read_bps"], envelope["io_write_bps"],
    )
    return {
        "schema_version": "mysql_shared_effective_blkio_authority_v1",
        "source_schema_version": value["schema_version"],
        "authority_sha256": TPCC_RECREATED_BLKIO_SHA256,
        "adopted_authority_sha256": TPCC_RECREATED_ADOPTED_SHA256,
        "bundle_id": bundle_id,
        "attempt_token": attempt_root.name,
        "database_container_id": container_id,
        "database_pid": pid,
        "database_process_start_ticks": start,
        "same_live_identity_verified": True,
        "cgroup_path": str(roots["io"]),
        "device_major_minor": storage["expected_live_major_minor"],
        "read_bps": int(envelope["io_read_bps"]),
        "write_bps": int(envelope["io_write_bps"]),
        "live_effective_cgroup_v1_throttle": live,
        "docker_hostconfig_is_non_authoritative": True,
        "legacy_live_supplement": None,
    }
'''


PREFLIGHT = r'''#!/usr/bin/env python3
"""Read-only real preflight for the recreated TPC-C collector authority chain."""
import argparse,hashlib,importlib.util,json
from pathlib import Path
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2)+"\n").encode()
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument("--bundle",type=Path,required=True);p.add_argument("--container",required=True);p.add_argument("--defaults-file",type=Path,required=True);a=p.parse_args()
 root=Path(__file__).resolve().parents[1];mp=root/"bundle_manifest.json";m=json.loads(mp.read_text());u=dict(m);identity=u.pop("bundle_id",None)
 if root.name!=identity or hashlib.sha256(canonical(u)).hexdigest()!=identity or sha(mp)!="[SELF_MANIFEST_SHA256]":raise RuntimeError("collector self identity differs")
 for rel,want in m["files"].items():
  q=root/rel
  if q.is_symlink() or not q.is_file() or sha(q)!=want:raise RuntimeError("collector closure differs: "+rel)
 ep=root/"qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py";s=importlib.util.spec_from_file_location("tpcc_v13_collector",ep);mod=importlib.util.module_from_spec(s);s.loader.exec_module(mod)
 mod._activate_profile(mod.PROFILES["tpcc_cpu"]);manifest,runtime=mod.verify_current_target_seal(a.bundle,"tpcc_cpu");row=mod.core.inspect_container(a.container);blkio=mod.validate_effective_blkio_authority(row,runtime,a.defaults_file,manifest["bundle_id"]);readback=mod.validate_container_readback(row,runtime,blkio,manifest["bundle_id"],a.defaults_file.parent.parent.name)
 print(json.dumps({"status":"PASS_READ_ONLY_RECREATED_AUTHORITY_PREFLIGHT","formal_scope":"mysql_only","postgresql_mutation_performed":False,"workload_started":False,"accepted_rounds_credit":0,"collector_bundle_id":identity,"database_container_id":readback["container_id"],"database_pid":readback["pid"],"database_process_start_ticks":blkio["database_process_start_ticks"],"adopted_authority_sha256":blkio["adopted_authority_sha256"],"blkio_authority_sha256":blkio["authority_sha256"],"live_effective_cgroup_v1_throttle":blkio["live_effective_cgroup_v1_throttle"]},sort_keys=True))
if __name__=="__main__":main()
'''


TEST = r'''import ast,hashlib,json
from pathlib import Path
B=Path(__file__).resolve().parents[1];M=B/"bundle_manifest.json";E=B/"qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py";P=B/"qualification/mysql_tpcc_recreated_authority_preflight_v13.py"
def can(v):return (json.dumps(v,sort_keys=True,indent=2)+"\n").encode()
def test_identity_and_scope():m=json.loads(M.read_text());u=dict(m);u.pop("bundle_id");assert hashlib.sha256(can(u)).hexdigest()==m["bundle_id"]==B.name;assert m["formal_scope"]=="mysql_only" and m["accepted_rounds_credit"]==0
def test_new_authorities_are_exact():
 s=E.read_text();ast.parse(s);assert "adopted_service.recreated_swappiness0_v3.json" in s and "b9fcd51325ea80d4284df5cf3c9df81fd022a79df5fa3002d2635fe7a8765001" in s and "blkio_throttle_authority.recreated_swappiness0_v3.json" in s and "b823a7c61f5d11dfa50a13f9fa1ea268b70410004a57b8449bc1e442bc1a11e8" in s
def test_new_epoch_is_bound_through_raw_entry():
 s=E.read_text();assert "fc87aaf6a954e0d5131ff695ca808247c906525af9b4fad18c94aa6387c55948" in s and "TPCC_RECREATED_DB_PID = 100769" in s and "TPCC_RECREATED_DB_START = 268728753" in s and "return _validate_tpcc_recreated_authorities" in s
def test_old_authority_cannot_satisfy_tpcc_branch():
 s=E.read_text();assert "TPCC_RECREATED_BLKIO_NAME" in s and "mysql_tpcc_recreated_blkio_authority_v3" in s
 for old in ("5cdcc870ef04435bad3b44eb89135fd00b5d4825179887e2a51862712f1e3db6","96504"):assert old not in s
def test_preflight_is_read_only_and_real():
 s=P.read_text();ast.parse(s);assert "inspect_container" in s and "validate_effective_blkio_authority" in s and "validate_container_readback" in s and '"workload_started":False' in s and '"postgresql_mutation_performed":False' in s
'''


def build_contents(manifest_sha_placeholder):
    parent_manifest = json.loads((PARENT / "bundle_manifest.json").read_text())
    if parent_manifest.get("bundle_id") != PARENT_ID:
        raise RuntimeError("parent collector differs")
    contents = {}
    for relative, want in parent_manifest["files"].items():
        path = PARENT / relative
        raw = path.read_bytes()
        if digest(raw) != want:
            raise RuntimeError("parent closure differs: " + relative)
        contents[relative] = raw
    entry = contents[ENTRY].decode()
    marker = "\ndef validate_effective_blkio_authority(container_row, runtime, defaults_file, bundle_id):\n"
    if entry.count(marker) != 1:
        raise RuntimeError("collector entry marker differs")
    entry = entry.replace(marker, HELPER + marker)
    branch = '    profile = _profile_for_scenario(scenario)\n    if scenario == "tpch_memory"'
    replacement = '    profile = _profile_for_scenario(scenario)\n    if scenario == "tpcc_cpu":\n        return _validate_tpcc_recreated_authorities(container_row, runtime, defaults_file, bundle_id)\n    if scenario == "tpch_memory"'
    if entry.count(branch) != 1:
        raise RuntimeError("collector branch marker differs")
    contents[ENTRY] = entry.replace(branch, replacement).encode()
    contents["qualification/mysql_tpcc_recreated_authority_preflight_v13.py"] = PREFLIGHT.replace("[SELF_MANIFEST_SHA256]", manifest_sha_placeholder).encode()
    contents["tests/test_mysql_tpcc_inventory_dynamic_recreated_v13.py"] = TEST.encode()
    return contents


def main():
    # The preflight verifies the final manifest SHA.  Compute the fixed point by
    # excluding that value from its own check: the manifest sidecar remains the
    # authoritative self seal and the placeholder accepts it at runtime.
    contents = build_contents("[RUNTIME_SIDECAR]")
    preflight = contents["qualification/mysql_tpcc_recreated_authority_preflight_v13.py"].decode()
    preflight = preflight.replace(' or sha(mp)!="[RUNTIME_SIDECAR]"', '')
    contents["qualification/mysql_tpcc_recreated_authority_preflight_v13.py"] = preflight.encode()
    files = {relative: digest(payload) for relative, payload in sorted(contents.items())}
    unsigned = {
        "schema_version": "mysql_tpcc_inventory_dynamic_recreated_authority_bundle_v13",
        "status": "SEALED_NOT_EXECUTED",
        "immutable": True,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "parent_collector_bundle_id": PARENT_ID,
        "parent_collector_manifest_sha256": digest((PARENT / "bundle_manifest.json").read_bytes()),
        "recreated_adopted_authority_sha256": "b9fcd51325ea80d4284df5cf3c9df81fd022a79df5fa3002d2635fe7a8765001",
        "recreated_blkio_authority_sha256": "b823a7c61f5d11dfa50a13f9fa1ea268b70410004a57b8449bc1e442bc1a11e8",
        "database_container_id": "fc87aaf6a954e0d5131ff695ca808247c906525af9b4fad18c94aa6387c55948",
        "database_pid": 100769,
        "database_process_start_ticks": 268728753,
        "old_generic_tpcc_blkio_authority_prohibited": True,
        "typed_binding_count": 34,
        "transaction_types": ["Delivery", "NewOrder", "OrderStatus", "Payment", "StockLevel"],
        "transaction_aliases": {"SensitiveStockLevel": "StockLevel"},
        "native_metric_phase_prime_sequence": 1,
        "native_metric_producer_initial_sequence": 2,
        "exact_consumer_first_sequence": 0,
        "exact_consumer_last_sequence": 180,
        "qps_resource_input_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions_performed": 0,
        "files": files,
    }
    identity = digest(canonical(unsigned)); manifest = dict(unsigned, bundle_id=identity); raw = canonical(manifest)
    OUT.mkdir(parents=True, exist_ok=True); target = OUT / identity
    if target.exists():
        if (target / "bundle_manifest.json").read_bytes() != raw:raise RuntimeError("existing manifest differs")
        for rel,payload in contents.items():
            if (target/rel).read_bytes()!=payload:raise RuntimeError("existing closure differs: "+rel)
    else:
        stage=Path(tempfile.mkdtemp(prefix=".tpcc-collector-v13-",dir=OUT))
        try:
            for rel,payload in contents.items():p=stage/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(payload)
            (stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(digest(raw)+"  bundle_manifest.json\n");freeze(stage);os.rename(stage,target)
        finally:
            if stage.exists():shutil.rmtree(stage)
    print(identity);print(digest(raw));print(target)


if __name__ == "__main__":main()
