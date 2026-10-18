#!/usr/bin/env python3
"""Build v4 binding the sealed legacy adopter to the private Docker socket."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PARENT_ID = "c4266df8f10a6a93cb4afe07810c541c216681380e067660590379aee43a74f2"
PARENT_SHA = "a76de2d78c595eaba9cb728b53ea999263619ba1dd6734ec8844873b94ef69c7"
PARENT = ROOT / "artifacts/mysql_tpcc_zero_swap_legacy_before_rehash_successor_v3" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpcc_private_socket_adopt_successor_v4"
CONTROL_REL = "control/mysql_tpcc_zero_swap_recommission_private_socket_v4.py"


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


ANCHOR = '''def adopt(before_path):
 before=load_sealed(before_path);after=snapshot()'''

HELPER_AND_ADOPT = '''def adopt_on_private_socket(module,plan):
 # The sealed legacy adopter invokes plain `docker inspect`; bind only that
 # call window to the already-frozen private daemon and restore the caller's
 # environment exactly, even when the adopter fails.
 marker=object();previous=os.environ.get("DOCKER_HOST",marker)
 os.environ["DOCKER_HOST"]=SOCKET
 try:return module.adopt(plan)
 finally:
  if previous is marker:os.environ.pop("DOCKER_HOST",None)
  else:os.environ["DOCKER_HOST"]=previous
def adopt(before_path):
 before=load_sealed(before_path);after=snapshot()'''

OLD_CALL = '''spec=importlib.util.spec_from_file_location("tpcc_recommission_adopt",module);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);plan=json.loads((ATTEMPT/"plan.json").read_text());value=m.adopt(plan)'''
NEW_CALL = '''spec=importlib.util.spec_from_file_location("tpcc_recommission_adopt",module);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);plan=json.loads((ATTEMPT/"plan.json").read_text());value=adopt_on_private_socket(m,plan)'''


TEST = r'''import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pytest

B = Path(__file__).resolve().parents[1]
C = B / "control/mysql_tpcc_zero_swap_recommission_private_socket_v4.py"


def module():
    spec = importlib.util.spec_from_file_location("tpcc_socket_v4", C)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class FakeSealedAdopter:
    def __init__(self, dedicated):
        self.dedicated = dedicated
        self.seen = []

    def adopt(self, plan):
        selected = os.environ.get("DOCKER_HOST")
        self.seen.append(selected)
        rows = self.dedicated.get(selected, [])
        if len(rows) != 2:
            raise RuntimeError("database/client containers absent")
        return {"status": "PASS", "rows": rows, "plan": plan}


def test_default_socket_empty_private_socket_has_both_and_is_selected(monkeypatch):
    m = module()
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    fake = FakeSealedAdopter({
        "unix:///var/run/docker.sock": [],
        m.SOCKET: ["database", "client"],
    })
    result = m.adopt_on_private_socket(fake, {"attempt": "fresh"})
    assert result["status"] == "PASS"
    assert result["rows"] == ["database", "client"]
    assert fake.seen == [m.SOCKET]
    assert "DOCKER_HOST" not in os.environ


def test_existing_docker_host_is_restored_after_success(monkeypatch):
    m = module()
    monkeypatch.setenv("DOCKER_HOST", "unix:///preexisting.sock")
    fake = FakeSealedAdopter({m.SOCKET: ["database", "client"]})
    m.adopt_on_private_socket(fake, {})
    assert fake.seen == [m.SOCKET]
    assert os.environ["DOCKER_HOST"] == "unix:///preexisting.sock"


def test_environment_is_restored_after_adopter_failure(monkeypatch):
    m = module()
    monkeypatch.setenv("DOCKER_HOST", "unix:///preexisting.sock")
    fake = FakeSealedAdopter({m.SOCKET: []})
    with pytest.raises(RuntimeError, match="containers absent"):
        m.adopt_on_private_socket(fake, {})
    assert os.environ["DOCKER_HOST"] == "unix:///preexisting.sock"


def test_mount_identity_pid_zero_swap_and_pg_guards_unchanged():
    source = C.read_text()
    for required in (
        'canonical_mount_projection(before.get("mounts"),role)',
        'after["container_id"]!=before["container_id"]',
        'after["image_id"]!=before["image_id"]',
        'after["created"]!=before["created"]',
        'after["pid"]==before["pid"]',
        'after["process_start_ticks"]==before["process_start_ticks"]',
        'after["total_swap_bytes"]!=0',
        'after["memory_limit_bytes"]!=after["memory_swap_limit_bytes"]',
        'postgresql_mutation_performed":False',
        'value=adopt_on_private_socket(m,plan)',
    ):
        assert required in source
    assert 'value=m.adopt(plan)' not in source


def test_content_address_scope_and_parent():
    manifest = json.loads((B / "bundle_manifest.json").read_text())
    unsigned = dict(manifest)
    unsigned.pop("bundle_id")
    actual = hashlib.sha256((json.dumps(unsigned, sort_keys=True, indent=2) + "\n").encode()).hexdigest()
    assert actual == manifest["bundle_id"] == B.name
    assert manifest["parent_bundle_id"] == "c4266df8f10a6a93cb4afe07810c541c216681380e067660590379aee43a74f2"
    assert manifest["formal_scope"] == "mysql_only"
    assert manifest["cross_engine_comparison_prohibited"] is True
    assert manifest["postgresql_mutation_prohibited"] is True
'''


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def main():
    verify_parent()
    source = (PARENT / "control/mysql_tpcc_zero_swap_recommission_mount_order_v3.py").read_text()
    for old, new, label in (
        (ANCHOR, HELPER_AND_ADOPT, "private socket helper"),
        (OLD_CALL, NEW_CALL, "sealed adopt call"),
    ):
        if source.count(old) != 1:
            raise RuntimeError(label + " patch anchor differs")
        source = source.replace(old, new)
    runbook = json.loads((PARENT / "runbook/transaction.json").read_text())
    runbook["schema_version"] = "mysql_tpcc_private_socket_adopt_transaction_v4"
    runbook["sealed_adopt_module_docker_transport"] = {
        "docker_host": "unix:///run/ra-docker-mysql-tpcc-v1.sock",
        "binding_scope": "only module.adopt(plan) call under try/finally",
        "prior_environment_restored": True,
        "default_socket_fallback_prohibited": True,
    }
    evidence = json.loads((PARENT / "evidence/mysql_tpcc_ff960_legacy_before_hash_failure_v2.json").read_text())
    evidence.update(
        schema_version="mysql_tpcc_v3_default_socket_adopt_failure_evidence_v1",
        status="OFFLINE_DIAGNOSIS_V3_EXECUTED_ZERO_CREDIT",
        predecessor_bundle_id=PARENT_ID,
        additional_root_cause="sealed legacy adopter used default docker socket while formal containers live on the private TPC-C socket",
        fix="bind DOCKER_HOST to the frozen private socket only during module.adopt(plan)",
        accepted_rounds_credit=0,
    )
    contents = {
        CONTROL_REL: source.encode(),
        "runbook/transaction.json": canonical(runbook),
        "evidence/mysql_tpcc_v3_default_socket_adopt_failure_v1.json": canonical(evidence),
        "tests/test_mysql_tpcc_private_socket_adopt_successor_v4.py": TEST.encode(),
        "builder/build_mysql_tpcc_private_socket_adopt_successor_v4.py": HERE.read_bytes(),
    }
    files = {relative: digest(raw) for relative, raw in sorted(contents.items())}
    unsigned = {
        "schema_version": "mysql_tpcc_private_socket_adopt_successor_bundle_v4",
        "status": "SEALED_NOT_EXECUTED",
        "immutable": True,
        "parent_bundle_id": PARENT_ID,
        "parent_manifest_sha256": PARENT_SHA,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "sealed_adopt_docker_host": "unix:///run/ra-docker-mysql-tpcc-v1.sock",
        "sealed_adopt_docker_host_scope_limited": True,
        "docker_host_environment_restored_try_finally": True,
        "default_docker_socket_fallback_prohibited": True,
        "legacy_before_mount_projection_canonically_rehashed": True,
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
        stage = Path(tempfile.mkdtemp(prefix=".private-socket-adopt-", dir=OUT))
        try:
            for relative, raw in contents.items():
                path = stage / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            (stage / "bundle_manifest.json").write_bytes(manifest_raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                digest(manifest_raw) + "  bundle_manifest.json\n"
            )
            freeze(stage)
            os.rename(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    print(bundle_id)
    print(digest((destination / "bundle_manifest.json").read_bytes()))
    print(destination)


if __name__ == "__main__":
    main()
