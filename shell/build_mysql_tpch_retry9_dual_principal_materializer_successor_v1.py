#!/usr/bin/env python3
"""Build the immutable Retry9 dual-principal defaults materializer successor."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
OLD_ID = "819640d73e8b85a4547b2f4a09b5477eac4bcd19f425d46cac9764b01e3a57af"
OLD_SHA = "fef5a7cbd3a93a5bd190036240492ffa5dabe495a8a701f77bc3e05bca978480"
OLD = ROOT / "artifacts/mysql_tpch_qualification_retry5_completion_materializer_v1" / OLD_ID
OUT = ROOT / "artifacts/mysql_tpch_retry9_dual_principal_materializer_successor_v1"
MATERIALIZER = "materializer/mysql_tpch_qualification_retry5_completion_materializer_v1.py"
BUILDER = "builder/build_mysql_tpch_retry9_dual_principal_materializer_successor_v1.py"
TEST = "tests/test_mysql_tpch_retry9_dual_principal_materializer_successor_v1.py"


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_map(root):
    return {path.relative_to(root).as_posix(): sha_file(path)
            for path in sorted(Path(root).rglob("*")) if path.is_file()
            and path.relative_to(root).as_posix() not in
            ("bundle_manifest.json", "bundle_manifest.json.sha256")}


def verify_old():
    manifest_path = OLD / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    unsigned = dict(manifest); unsigned.pop("bundle_id", None)
    if (OLD.name != OLD_ID or manifest.get("bundle_id") != OLD_ID
            or sha_file(manifest_path) != OLD_SHA
            or sha_bytes(canonical(unsigned)) != OLD_ID
            or file_map(OLD) != manifest.get("files")
            or manifest.get("formal_scope") != "mysql_only"
            or manifest.get("cross_engine_comparison_prohibited") is not True):
        raise BuildError("old materializer closure differs")
    return manifest


def patch_materializer(source):
    anchor = "def attempt():return \"tpch-qualification-retry5-\"+datetime.now(timezone.utc).strftime(\"%Y%m%dT%H%M%SZ\")+\"-\"+secrets.token_hex(16)\n"
    helper = r'''def _defaults_metadata(state,parent_state):
 if (not stat.S_ISREG(state.st_mode) or state.st_uid!=1008 or state.st_gid!=0 or stat.S_IMODE(state.st_mode)!=0o440):raise Error("MySQL defaults dual-principal file identity differs")
 if (not stat.S_ISDIR(parent_state.st_mode) or parent_state.st_uid!=1008 or parent_state.st_gid!=0 or stat.S_IMODE(parent_state.st_mode)!=0o750):raise Error("MySQL defaults dual-principal parent identity differs")
def validate_defaults(path,target,bootstrap_attempt):
 raw=Path(path)
 expected=target.parent.parent/"attempts"/bootstrap_attempt/"credentials/mysql.cnf"
 if not raw.is_absolute() or raw!=expected or raw.name!="mysql.cnf" or raw.parent.name!="credentials":raise Error("MySQL defaults attempt/path binding differs")
 try:state=os.lstat(raw);parent_state=os.lstat(raw.parent)
 except OSError as exc:raise Error("MySQL defaults path is absent") from exc
 if stat.S_ISLNK(state.st_mode) or stat.S_ISLNK(parent_state.st_mode) or raw.resolve(strict=True)!=raw:raise Error("MySQL defaults path contains a symlink")
 _defaults_metadata(state,parent_state)
 flags=os.O_RDONLY|getattr(os,"O_CLOEXEC",0)|getattr(os,"O_NOFOLLOW",0)
 descriptor=os.open(raw,flags)
 try:
  opened=os.fstat(descriptor)
  if (opened.st_dev,opened.st_ino)!=(state.st_dev,state.st_ino):raise Error("MySQL defaults inode changed during readback")
  _defaults_metadata(opened,parent_state)
  digest=hashlib.sha256()
  while True:
   block=os.read(descriptor,1048576)
   if not block:break
   digest.update(block)
 finally:os.close(descriptor)
 return {"schema_version":"mysql_tpch_dual_principal_defaults_binding_v1","bootstrap_attempt_token":bootstrap_attempt,"path":str(raw),"sha256":digest.hexdigest(),"device":opened.st_dev,"inode":opened.st_ino,"uid":opened.st_uid,"gid":opened.st_gid,"mode":"%04o"%stat.S_IMODE(opened.st_mode),"parent_uid":parent_state.st_uid,"parent_gid":parent_state.st_gid,"parent_mode":"%04o"%stat.S_IMODE(parent_state.st_mode),"regular_file":True,"symlink":False,"accepted_round_authorized":False}
'''
    if source.count(anchor) != 1:
        raise BuildError("materializer helper anchor differs")
    source = source.replace(anchor, anchor + helper)
    old = 'defaults=a.mysql_defaults_file.resolve(strict=True)\n if defaults.is_symlink() or not defaults.is_file() or stat.S_IMODE(defaults.stat().st_mode)&0o077:raise Error("MySQL defaults file must be private")'
    new = 'defaults=a.mysql_defaults_file\n defaults_contract=validate_defaults(defaults,target,bootstrap_attempt)'
    if source.count(old) != 1:
        raise BuildError("old defaults predicate anchor differs")
    source = source.replace(old, new)
    old_material = '"predecessor_reuse_prohibited":True,"formal_launch_authorized":False'
    new_material = '"mysql_defaults_binding":defaults_contract,"predecessor_reuse_prohibited":True,"formal_launch_authorized":False'
    if source.count(old_material) != 1:
        raise BuildError("material receipt anchor differs")
    return source.replace(old_material, new_material)


TEST_SOURCE = r'''from __future__ import annotations
import hashlib,importlib.util,json,stat
from pathlib import Path
from types import SimpleNamespace

BUNDLE=Path(__file__).resolve().parents[1]
SOURCE=BUNDLE/"materializer/mysql_tpch_qualification_retry5_completion_materializer_v1.py"
spec=importlib.util.spec_from_file_location("dual_materializer",SOURCE);M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)

def state(kind,uid,gid,mode):
 bits=stat.S_IFREG if kind=="file" else stat.S_IFDIR
 return SimpleNamespace(st_mode=bits|mode,st_uid=uid,st_gid=gid,st_dev=7,st_ino=11)

def test_manifest_and_predecessor_are_content_addressed():
 manifest=json.loads((BUNDLE/"bundle_manifest.json").read_text());unsigned=dict(manifest);unsigned.pop("bundle_id")
 assert hashlib.sha256((json.dumps(unsigned,sort_keys=True,indent=2)+"\n").encode()).hexdigest()==manifest["bundle_id"]==BUNDLE.name
 assert manifest["predecessor_bundle_id"]=="819640d73e8b85a4547b2f4a09b5477eac4bcd19f425d46cac9764b01e3a57af"

def test_exact_dual_principal_metadata_is_accepted():
 M._defaults_metadata(state("file",1008,0,0o440),state("dir",1008,0,0o750))

def test_private_but_contract_wrong_modes_are_rejected():
 for mode in (0o400,0o600,0o640,0o444):
  try:M._defaults_metadata(state("file",1008,0,mode),state("dir",1008,0,0o750))
  except M.Error:pass
  else:raise AssertionError("wrong mode accepted")

def test_wrong_owner_parent_or_type_is_rejected():
 bad=[(state("file",0,0,0o440),state("dir",1008,0,0o750)),(state("file",1008,0,0o440),state("dir",0,0,0o750)),(state("dir",1008,0,0o440),state("dir",1008,0,0o750))]
 for file_state,parent in bad:
  try:M._defaults_metadata(file_state,parent)
  except M.Error:pass
  else:raise AssertionError("wrong identity accepted")

def test_materialization_receipt_binds_defaults_and_old_predicate_is_gone():
 source=SOURCE.read_text()
 assert 'mysql_defaults_binding":defaults_contract' in source
 assert 'target.parent.parent/"attempts"/bootstrap_attempt/"credentials/mysql.cnf"' in source
 assert "MySQL defaults file must be private" not in source
 assert "O_NOFOLLOW" in source and "st_ino" in source and '"sha256":digest.hexdigest()' in source
'''


def freeze(root):
    for path in sorted(Path(root).rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def build():
    old = verify_old()
    content = {}
    for relative in sorted(old["files"]):
        if relative in (
                "builder/build_mysql_tpch_qualification_retry5_completion_materializer_v1.py",
                "tests/test_mysql_tpch_qualification_retry5_completion_materializer_v1.py"):
            continue
        payload = (OLD / relative).read_bytes()
        if relative == MATERIALIZER:
            payload = patch_materializer(payload.decode("utf-8")).encode("utf-8")
        content[relative] = payload
    content[BUILDER] = HERE.read_bytes()
    content[TEST] = TEST_SOURCE.encode("utf-8")
    files = {name: sha_bytes(payload) for name, payload in sorted(content.items())}
    identity = {
        "schema_version": "mysql_tpch_retry9_dual_principal_materializer_successor_bundle_v1",
        "status": "PASS_IMMUTABLE_DUAL_PRINCIPAL_DEFAULTS_MATERIALIZER_NOT_EXECUTED",
        "immutable": True, "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "predecessor_bundle_id": OLD_ID,
        "predecessor_manifest_sha256": OLD_SHA,
        "mysql_defaults_file_identity": {"uid": 1008, "gid": 0, "mode": "0440", "regular_file": True, "symlink": False},
        "mysql_defaults_parent_identity": {"uid": 1008, "gid": 0, "mode": "0750", "directory": True, "symlink": False},
        "fresh_bootstrap_attempt_path_and_sha256_binding_required": True,
        "bootstrap_dual_principal_credential_mutation_prohibited": True,
        "formal_launch_authorized": False, "accepted_round_authorized": False,
        "accepted_rounds_credit": 0, "remote_actions": 0, "files": files,
    }
    bundle_id = sha_bytes(canonical(identity)); manifest = dict(identity, bundle_id=bundle_id)
    destination = OUT / bundle_id
    if destination.exists():
        if sha_file(destination / "bundle_manifest.json") != sha_bytes(canonical(manifest)):
            raise BuildError("existing successor differs")
        return destination
    OUT.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".dual-principal-materializer-", dir=OUT))
    try:
        for relative, payload in sorted(content.items()):
            target = stage / relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(payload)
        raw = canonical(manifest); (stage / "bundle_manifest.json").write_bytes(raw)
        (stage / "bundle_manifest.json.sha256").write_text(sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii")
        freeze(stage); os.replace(stage, destination)
    finally:
        if stage.exists(): shutil.rmtree(stage)
    if file_map(destination) != files: raise BuildError("successor closure differs")
    return destination


if __name__ == "__main__":
    result = build()
    print(json.dumps({"status": "PASS_IMMUTABLE_DUAL_PRINCIPAL_DEFAULTS_MATERIALIZER_NOT_EXECUTED", "bundle": str(result), "bundle_id": result.name, "manifest_sha256": sha_file(result / "bundle_manifest.json"), "remote_actions": 0}, sort_keys=True))
