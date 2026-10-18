#!/usr/bin/env python3
"""Build v3: rehash legacy sealed-before Mounts using canonical order."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PARENT_ID = "ff96016a63bfb7ec4977e8f1fde73f6da920985418828603ef3f55a861fbc12d"
PARENT_SHA = "5070ab14e94aea267f9e99721d236c232ad31504e0a0e0b370d9caaf6c04a305"
PARENT = ROOT / "artifacts/mysql_tpcc_zero_swap_mount_order_successor_v1" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpcc_zero_swap_legacy_before_rehash_successor_v3"
CONTROL_REL = "control/mysql_tpcc_zero_swap_recommission_mount_order_v3.py"


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                       allow_nan=False) + "\n").encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def verify_parent():
    raw = (PARENT / "bundle_manifest.json").read_bytes()
    if digest(raw) != PARENT_SHA:
        raise RuntimeError("parent manifest sha256 differs")
    manifest = json.loads(raw)
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id")
    if bundle_id != PARENT_ID or digest(canonical(unsigned)) != PARENT_ID:
        raise RuntimeError("parent content address differs")
    for relative, expected in manifest["files"].items():
        path = PARENT / relative
        if not path.is_file() or digest(path.read_bytes()) != expected:
            raise RuntimeError("parent closure differs: " + relative)
    return manifest


OLD_HELPERS = '''def mount_projection(raw_mounts):
 mounts=[{"source":m.get("Source"),"destination":m.get("Destination"),"rw":m.get("RW")} for m in raw_mounts]
 # Docker does not promise Mounts list order.  Sort by the canonical value of
 # every projected field, while retaining duplicates so multiplicity matters.
 return sorted(mounts,key=can)
def mounts_sha256(raw_mounts):
 mounts=mount_projection(raw_mounts) if raw_mounts and "Source" in raw_mounts[0] else list(raw_mounts)
 return hashlib.sha256(can(mounts)).hexdigest()
'''

NEW_HELPERS = '''def canonical_mount_projection(projected,role):
 if not isinstance(projected,list):raise E(role+" sealed mounts list differs")
 mounts=[]
 for index,m in enumerate(projected):
  if not isinstance(m,dict) or set(m)!={"source","destination","rw"}:raise E(role+" sealed mount fields differ at "+str(index))
  if not isinstance(m["source"],str) or not m["source"] or not isinstance(m["destination"],str) or not m["destination"] or not isinstance(m["rw"],bool):raise E(role+" sealed mount values differ at "+str(index))
  mounts.append({"source":m["source"],"destination":m["destination"],"rw":m["rw"]})
 # Sorting does not collapse duplicate records: multiplicity remains hashed.
 return sorted(mounts,key=can)
def mount_projection(raw_mounts):
 if not isinstance(raw_mounts,list):raise E("live Docker mounts list differs")
 mounts=[]
 for index,m in enumerate(raw_mounts):
  if not isinstance(m,dict) or not {"Source","Destination","RW"}.issubset(set(m)):raise E("live Docker mount fields differ at "+str(index))
  mounts.append({"source":m.get("Source"),"destination":m.get("Destination"),"rw":m.get("RW")})
 return canonical_mount_projection(mounts,"live Docker")
def mounts_sha256(projected):
 return hashlib.sha256(can(projected)).hexdigest()
def verify_recommission_continuity(after,before,role):
 before_mounts=canonical_mount_projection(before.get("mounts"),role)
 before_mounts_sha256=mounts_sha256(before_mounts)
 if after["container_id"]!=before["container_id"] or after["image_id"]!=before["image_id"] or after["created"]!=before["created"] or after["mounts_sha256"]!=before_mounts_sha256:raise E(role+" immutable continuity differs")
 if after["pid"]==before["pid"] or after["process_start_ticks"]==before["process_start_ticks"]:raise E(role+" cgroup was not recommissioned")
 if after["total_swap_bytes"]!=0 or after["memory_limit_bytes"]!=after["memory_swap_limit_bytes"]:raise E(role+" zero-swap postcondition failed")
 return before_mounts_sha256
'''

OLD_ADOPT = ''' for role in ("database","client"):
  a=after[role];b=before[role]
  if a["container_id"]!=b["container_id"] or a["image_id"]!=b["image_id"] or a["created"]!=b["created"] or a["mounts_sha256"]!=b["mounts_sha256"]:raise E(role+" immutable continuity differs")
  if a["pid"]==b["pid"] or a["process_start_ticks"]==b["process_start_ticks"]:raise E(role+" cgroup was not recommissioned")
  if a["total_swap_bytes"]!=0 or a["memory_limit_bytes"]!=a["memory_swap_limit_bytes"]:raise E(role+" zero-swap postcondition failed")
'''

NEW_ADOPT = ''' recomputed_before_mount_hashes={}
 for role in ("database","client"):
  recomputed_before_mount_hashes[role]=verify_recommission_continuity(after[role],before[role],role)
'''

OLD_RECEIPT = '''"same_container_ids":True,"same_datadir_mount":True,"postgresql_mutation_performed":False}'''
NEW_RECEIPT = '''"same_container_ids":True,"same_datadir_mount":True,"legacy_before_mounts_rehashed_canonically":True,"recomputed_before_mount_hashes":recomputed_before_mount_hashes,"postgresql_mutation_performed":False}'''


TEST = r'''import hashlib
import importlib.util
import json
from pathlib import Path
import pytest

B = Path(__file__).resolve().parents[1]
C = B / "control/mysql_tpcc_zero_swap_recommission_mount_order_v3.py"


def module():
    spec = importlib.util.spec_from_file_location("tpcc_mount_v3", C)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def record(mounts, *, pid, start, legacy_hash="legacy-order-sensitive-hash"):
    return {
        "container_id": "same-container",
        "image_id": "same-image",
        "created": "same-created",
        "mounts": mounts,
        "mounts_sha256": legacy_hash,
        "pid": pid,
        "process_start_ticks": start,
        "total_swap_bytes": 0,
        "memory_limit_bytes": 1024,
        "memory_swap_limit_bytes": 1024,
    }


def test_c806_legacy_before_order_and_v2_after_order_pass():
    m = module()
    legacy_before = [
        {"source": "/runtime", "destination": "/runtime", "rw": False},
        {"source": "/data/datadir", "destination": "/var/lib/mysql", "rw": True},
        {"source": "/etc/localtime", "destination": "/etc/localtime", "rw": False},
    ]
    canonical_after = m.canonical_mount_projection(list(reversed(legacy_before)), "after")
    before = record(legacy_before, pid=101, start=1001)
    after = record(canonical_after, pid=202, start=2002,
                   legacy_hash=m.mounts_sha256(canonical_after))
    recomputed = m.verify_recommission_continuity(after, before, "database")
    assert recomputed == after["mounts_sha256"]
    assert recomputed != before["mounts_sha256"]


@pytest.mark.parametrize("mutation", ["source", "destination", "rw", "remove", "duplicate"])
def test_field_count_or_duplicate_multiplicity_change_is_rejected(mutation):
    m = module()
    before_mounts = [
        {"source": "/data/datadir", "destination": "/var/lib/mysql", "rw": True},
        {"source": "/runtime", "destination": "/runtime", "rw": False},
    ]
    after_mounts = [dict(row) for row in before_mounts]
    if mutation == "source":
        after_mounts[0]["source"] = "/other"
    elif mutation == "destination":
        after_mounts[0]["destination"] = "/other"
    elif mutation == "rw":
        after_mounts[0]["rw"] = False
    elif mutation == "remove":
        after_mounts.pop()
    else:
        after_mounts.append(dict(after_mounts[0]))
    after_mounts = m.canonical_mount_projection(after_mounts, "after")
    before = record(before_mounts, pid=101, start=1001)
    after = record(after_mounts, pid=202, start=2002,
                   legacy_hash=m.mounts_sha256(after_mounts))
    with pytest.raises(m.E, match="immutable continuity differs"):
        m.verify_recommission_continuity(after, before, "database")


@pytest.mark.parametrize("bad", [
    [{"source": "/x", "destination": "/y", "rw": True, "extra": 1}],
    [{"source": "/x", "destination": "/y"}],
    [{"source": "/x", "destination": "/y", "rw": 1}],
    "not-a-list",
])
def test_legacy_before_shape_is_strictly_validated(bad):
    m = module()
    with pytest.raises(m.E, match="sealed mount"):
        m.canonical_mount_projection(bad, "database")


def test_pid_start_zero_swap_and_pg_guards_remain_fail_closed():
    m = module()
    mounts = [{"source": "/x", "destination": "/y", "rw": True}]
    canon = m.canonical_mount_projection(mounts, "before")
    base = record(mounts, pid=101, start=1001)
    good = record(canon, pid=202, start=2002,
                  legacy_hash=m.mounts_sha256(canon))
    for field, value, message in (
        ("pid", 101, "cgroup was not recommissioned"),
        ("process_start_ticks", 1001, "cgroup was not recommissioned"),
        ("total_swap_bytes", 1, "zero-swap postcondition failed"),
        ("memory_swap_limit_bytes", 2048, "zero-swap postcondition failed"),
    ):
        changed = dict(good)
        changed[field] = value
        with pytest.raises(m.E, match=message):
            m.verify_recommission_continuity(changed, base, "database")
    source = C.read_text()
    assert 'postgresql_mutation_performed":False' in source
    assert 'before.get("mounts")' in source
    assert 'before["mounts_sha256"]' not in source


def test_content_address_scope_and_parent():
    manifest = json.loads((B / "bundle_manifest.json").read_text())
    unsigned = dict(manifest)
    unsigned.pop("bundle_id")
    actual = hashlib.sha256((json.dumps(unsigned, sort_keys=True, indent=2) + "\n").encode()).hexdigest()
    assert actual == manifest["bundle_id"] == B.name
    assert manifest["parent_bundle_id"] == "ff96016a63bfb7ec4977e8f1fde73f6da920985418828603ef3f55a861fbc12d"
    assert manifest["formal_scope"] == "mysql_only"
    assert manifest["cross_engine_comparison_prohibited"] is True
    assert manifest["postgresql_mutation_prohibited"] is True
'''


def frozen(root):
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def main():
    verify_parent()
    source = (PARENT / "control/mysql_tpcc_zero_swap_recommission_mount_order_v2.py").read_text()
    for old, new, label in (
        (OLD_HELPERS, NEW_HELPERS, "mount helpers"),
        (OLD_ADOPT, NEW_ADOPT, "adopt continuity"),
        (OLD_RECEIPT, NEW_RECEIPT, "receipt"),
    ):
        if source.count(old) != 1:
            raise RuntimeError(label + " patch anchor differs")
        source = source.replace(old, new)
    runbook = json.loads((PARENT / "runbook/transaction.json").read_text())
    runbook["schema_version"] = "mysql_tpcc_zero_swap_legacy_before_rehash_transaction_v3"
    runbook["legacy_before_snapshot_policy"] = {
        "trust_stored_mounts_sha256": False,
        "required_fields_exactly": ["source", "destination", "rw"],
        "canonical_sort_before_hash": True,
        "duplicates_and_multiplicity_preserved": True,
    }
    evidence = json.loads((PARENT / "evidence/mysql_tpcc_c806_mount_order_failure_v1.json").read_text())
    evidence.update(
        schema_version="mysql_tpcc_ff960_legacy_before_hash_failure_evidence_v2",
        status="OFFLINE_DIAGNOSIS_FF960_NOT_EXECUTED",
        predecessor_bundle_id=PARENT_ID,
        additional_root_cause="ff960 compared canonical after hash to c806 stored order-sensitive before hash",
        fix="strictly validate and canonically rehash sealed before mounts projection",
    )
    contents = {
        CONTROL_REL: source.encode(),
        "runbook/transaction.json": canonical(runbook),
        "evidence/mysql_tpcc_ff960_legacy_before_hash_failure_v2.json": canonical(evidence),
        "tests/test_mysql_tpcc_zero_swap_legacy_before_rehash_successor_v3.py": TEST.encode(),
        "builder/build_mysql_tpcc_zero_swap_legacy_before_rehash_successor_v3.py": HERE.read_bytes(),
    }
    files = {name: digest(raw) for name, raw in sorted(contents.items())}
    unsigned = {
        "schema_version": "mysql_tpcc_zero_swap_legacy_before_rehash_successor_bundle_v3",
        "status": "SEALED_NOT_EXECUTED",
        "immutable": True,
        "parent_bundle_id": PARENT_ID,
        "parent_manifest_sha256": PARENT_SHA,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "legacy_before_stored_mount_hash_trusted": False,
        "legacy_before_mount_projection_strictly_validated": True,
        "legacy_before_mount_projection_canonically_rehashed": True,
        "mount_duplicates_and_multiplicity_preserved": True,
        "mount_field_set_and_count_changes_fail_closed": True,
        "same_container_image_created_and_datadir_required": True,
        "database_and_client_pid_start_change_required": True,
        "database_and_client_zero_swap_required": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions_performed": 0,
        "files": files,
    }
    bundle_id = digest(canonical(unsigned))
    manifest_raw = canonical(dict(unsigned, bundle_id=bundle_id))
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / bundle_id
    if destination.exists():
        if (destination / "bundle_manifest.json").read_bytes() != manifest_raw:
            raise RuntimeError("existing manifest differs")
        for relative, raw in contents.items():
            if (destination / relative).read_bytes() != raw:
                raise RuntimeError("existing closure differs: " + relative)
    else:
        stage = Path(tempfile.mkdtemp(prefix=".legacy-before-rehash-", dir=OUT))
        try:
            for relative, raw in contents.items():
                path = stage / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            (stage / "bundle_manifest.json").write_bytes(manifest_raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                digest(manifest_raw) + "  bundle_manifest.json\n"
            )
            frozen(stage)
            os.rename(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    print(bundle_id)
    print(digest((destination / "bundle_manifest.json").read_bytes()))
    print(destination)


if __name__ == "__main__":
    main()
