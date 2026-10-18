#!/usr/bin/env python3
"""Build an immutable successor that canonicalizes Docker Mounts ordering."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PARENT_ID = "c8063f73a3cf7e8fb11e4b7bccd1998cc682ba09b7f1cc17b72811bbe8d72ee5"
PARENT = ROOT / "artifacts/mysql_tpcc_zero_swap_recommission_runbook_v1" / PARENT_ID
PARENT_MANIFEST_SHA256 = "c1af5d882c75de62b1b055b512fa8ced40990de0ba934fb35a3ea4aa723a7c3c"
OUT = ROOT / "artifacts/mysql_tpcc_zero_swap_mount_order_successor_v1"


def canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            indent=2,
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode()


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def verify_parent() -> dict:
    raw = (PARENT / "bundle_manifest.json").read_bytes()
    if sha256(raw) != PARENT_MANIFEST_SHA256:
        raise RuntimeError("parent manifest sha256 differs")
    manifest = json.loads(raw)
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id")
    if bundle_id != PARENT_ID or sha256(canonical(unsigned)) != PARENT_ID:
        raise RuntimeError("parent content address differs")
    for relative, expected in manifest["files"].items():
        path = PARENT / relative
        if not path.is_file() or sha256(path.read_bytes()) != expected:
            raise RuntimeError(f"parent closure differs: {relative}")
    return manifest


OLD = ''' mounts=[{"source":m.get("Source"),"destination":m.get("Destination"),"rw":m.get("RW")} for m in row.get("Mounts") or []]
 return {"role":role,"container_id":cid,"container_name":name,"image_id":row.get("Image"),"created":row.get("Created"),"restart_count":int(row.get("RestartCount") or 0),"mounts":mounts,"mounts_sha256":hashlib.sha256(can(mounts)).hexdigest(),**proc(pid)}'''

NEW = ''' mounts=mount_projection(row.get("Mounts") or [])
 return {"role":role,"container_id":cid,"container_name":name,"image_id":row.get("Image"),"created":row.get("Created"),"restart_count":int(row.get("RestartCount") or 0),"mounts":mounts,"mounts_sha256":mounts_sha256(mounts),**proc(pid)}'''

HELPER = '''def mount_projection(raw_mounts):
 mounts=[{"source":m.get("Source"),"destination":m.get("Destination"),"rw":m.get("RW")} for m in raw_mounts]
 # Docker does not promise Mounts list order.  Sort by the canonical value of
 # every projected field, while retaining duplicates so multiplicity matters.
 return sorted(mounts,key=can)
def mounts_sha256(raw_mounts):
 mounts=mount_projection(raw_mounts) if raw_mounts and "Source" in raw_mounts[0] else list(raw_mounts)
 return hashlib.sha256(can(mounts)).hexdigest()
'''


TEST = r'''import hashlib
import importlib.util
import json
from pathlib import Path

B = Path(__file__).resolve().parents[1]
CONTROL = B / "control/mysql_tpcc_zero_swap_recommission_mount_order_v2.py"


def load_control():
    spec = importlib.util.spec_from_file_location("tpcc_mount_order_control", CONTROL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_id_scope_and_parent():
    manifest = json.loads((B / "bundle_manifest.json").read_text())
    unsigned = dict(manifest)
    unsigned.pop("bundle_id")
    actual = hashlib.sha256((json.dumps(unsigned, sort_keys=True, indent=2) + "\n").encode()).hexdigest()
    assert actual == manifest["bundle_id"] == B.name
    assert manifest["parent_bundle_id"] == "c8063f73a3cf7e8fb11e4b7bccd1998cc682ba09b7f1cc17b72811bbe8d72ee5"
    assert manifest["formal_scope"] == "mysql_only"
    assert manifest["cross_engine_comparison_prohibited"] is True
    assert manifest["postgresql_mutation_prohibited"] is True


def test_mount_reorder_has_identical_projection_and_hash():
    module = load_control()
    mounts = [
        {"Source": "/data/datadir", "Destination": "/var/lib/mysql", "RW": True},
        {"Source": "/etc/timezone", "Destination": "/etc/timezone", "RW": False},
        {"Source": "/scratch", "Destination": "/tmp/mysql", "RW": True},
    ]
    forward = module.mount_projection(mounts)
    reverse = module.mount_projection(list(reversed(mounts)))
    shuffled = module.mount_projection([mounts[1], mounts[2], mounts[0]])
    assert forward == reverse == shuffled
    assert module.mounts_sha256(forward) == module.mounts_sha256(reverse)
    assert module.mounts_sha256(forward) == module.mounts_sha256(shuffled)


def test_mount_change_or_multiplicity_change_still_fails_closed():
    module = load_control()
    base = [
        {"Source": "/data/datadir", "Destination": "/var/lib/mysql", "RW": True},
        {"Source": "/scratch", "Destination": "/tmp/mysql", "RW": True},
    ]
    baseline = module.mounts_sha256(module.mount_projection(base))
    variants = []
    for field, value in (
        ("Source", "/other/datadir"),
        ("Destination", "/var/lib/mysql-other"),
        ("RW", False),
    ):
        changed = [dict(row) for row in base]
        changed[0][field] = value
        variants.append(changed)
    variants.extend((base[:1], base + [dict(base[0])]))
    for changed in variants:
        assert module.mounts_sha256(module.mount_projection(changed)) != baseline


def test_continuity_zero_swap_and_pid_restart_guards_preserved():
    source = CONTROL.read_text()
    assert 'a["container_id"]!=b["container_id"]' in source
    assert 'a["image_id"]!=b["image_id"]' in source
    assert 'a["created"]!=b["created"]' in source
    assert 'a["mounts_sha256"]!=b["mounts_sha256"]' in source
    assert 'a["pid"]==b["pid"]' in source
    assert 'a["process_start_ticks"]==b["process_start_ticks"]' in source
    assert 'a["total_swap_bytes"]!=0' in source
    assert 'a["memory_limit_bytes"]!=a["memory_swap_limit_bytes"]' in source
    assert 'postgresql_mutation_performed":False' in source


def test_runbook_never_mutates_pg_and_preserves_transaction_order():
    runbook = json.loads((B / "runbook/transaction.json").read_text())
    assert runbook["ordered_mutations"][0]["role"] == "client"
    assert runbook["ordered_mutations"][1]["role"] == "database"
    assert runbook["ordered_mutations"][1]["graceful_timeout_seconds"] == 120
    assert runbook["postgresql_mutation_prohibited"] is True
'''


def freeze(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def validate_existing(directory: Path, manifest_raw: bytes, contents: dict[str, bytes]) -> None:
    if (directory / "bundle_manifest.json").read_bytes() != manifest_raw:
        raise RuntimeError("existing successor manifest differs")
    for relative, raw in contents.items():
        if (directory / relative).read_bytes() != raw:
            raise RuntimeError(f"existing successor closure differs: {relative}")


def main() -> None:
    verify_parent()
    parent_control = (PARENT / "control/mysql_tpcc_zero_swap_recommission_v1.py").read_text()
    anchor = "def project(row,role):\n"
    if parent_control.count(anchor) != 1 or parent_control.count(OLD) != 1:
        raise RuntimeError("parent control patch anchor differs")
    control = parent_control.replace(anchor, HELPER + anchor).replace(OLD, NEW).encode()
    runbook = json.loads((PARENT / "runbook/transaction.json").read_text())
    runbook["schema_version"] = "mysql_tpcc_zero_swap_mount_order_transaction_v2"
    runbook["mount_projection_semantics"] = {
        "fields": ["source", "destination", "rw"],
        "ordering": "ascending canonical JSON bytes over the full projected mount record",
        "duplicates_preserved": True,
        "hash": "sha256(canonical(sorted_projection))",
    }
    runbook["postconditions"] = list(runbook["postconditions"]) + [
        "Docker Mounts list reordering alone does not alter the mount projection hash",
        "mount field, set, or multiplicity changes still alter the mount projection hash",
    ]
    evidence = {
        "schema_version": "mysql_tpcc_c806_mount_order_failure_evidence_v1",
        "status": "OFFLINE_DIAGNOSIS_ONLY",
        "parent_bundle_id": PARENT_ID,
        "observed_before_mounts_sha256_prefix": "11120f",
        "observed_after_mounts_sha256_prefix": "ea8c7f",
        "diagnosis": "Docker restart reordered an otherwise identical Mounts projection list",
        "container_id_image_created_datadir_equal": True,
        "database_and_client_pid_start_changed": True,
        "database_and_client_total_swap_bytes": 0,
        "remote_actions_performed_by_builder": 0,
        "accepted_rounds_credit": 0,
    }
    contents = {
        "control/mysql_tpcc_zero_swap_recommission_mount_order_v2.py": control,
        "runbook/transaction.json": canonical(runbook),
        "evidence/mysql_tpcc_c806_mount_order_failure_v1.json": canonical(evidence),
        "tests/test_mysql_tpcc_zero_swap_mount_order_successor_v1.py": TEST.encode(),
        "builder/build_mysql_tpcc_zero_swap_mount_order_successor_v1.py": HERE.read_bytes(),
    }
    files = {relative: sha256(raw) for relative, raw in sorted(contents.items())}
    unsigned = {
        "schema_version": "mysql_tpcc_zero_swap_mount_order_successor_bundle_v1",
        "status": "SEALED_NOT_EXECUTED",
        "immutable": True,
        "parent_bundle_id": PARENT_ID,
        "parent_manifest_sha256": PARENT_MANIFEST_SHA256,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "mount_projection_stably_sorted": True,
        "mount_reorder_equivalent": True,
        "mount_field_set_and_multiplicity_changes_fail_closed": True,
        "same_container_image_created_and_datadir_required": True,
        "database_and_client_pid_start_change_required": True,
        "database_and_client_zero_swap_required": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions_performed": 0,
        "files": files,
    }
    bundle_id = sha256(canonical(unsigned))
    manifest = dict(unsigned, bundle_id=bundle_id)
    manifest_raw = canonical(manifest)
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / bundle_id
    if destination.exists():
        validate_existing(destination, manifest_raw, contents)
    else:
        staging = Path(tempfile.mkdtemp(prefix=".mount-order-", dir=OUT))
        try:
            for relative, raw in contents.items():
                path = staging / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            (staging / "bundle_manifest.json").write_bytes(manifest_raw)
            (staging / "bundle_manifest.json.sha256").write_text(
                sha256(manifest_raw) + "  bundle_manifest.json\n"
            )
            freeze(staging)
            os.rename(staging, destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    print(bundle_id)
    print(sha256((destination / "bundle_manifest.json").read_bytes()))
    print(destination)


if __name__ == "__main__":
    main()
