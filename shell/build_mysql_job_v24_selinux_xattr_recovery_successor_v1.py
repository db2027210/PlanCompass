#!/usr/bin/env python3
"""Build the immutable JOB v24 SELinux-xattr recovery successor."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

V23_ID = "d328ac108dda22acf0cca1caa6c74afb58e6c415176fa15acd1276b90f400a98"
V23_MANIFEST_SHA = "be4e93f475b9ae5eaf779febc5d144156c504fbe516afbdfb8e2aeebae08eeff"
V21_ID = "f49cefa2dc4a456f3828a7338217c2e6cbc29b11e0528714cbc9c97582b1cd1f"
V21_MANIFEST_SHA = "5be3ac95fe0267f88d793c6a8695321d299c2311a2c7ba19e46d0d55878de20e"
OLD_TXN = "job-v23-recovery-20260803t121500z-91e7c4a6"
NEW_TXN = "job-v24-recovery-20260803t123500z-4d6f8a21"
V21_TXN = "job-v21-recovery-20260803t105116z-776fe047"
SCHEMA = "mysql_job_v24_selinux_xattr_recovery_successor_plan_v1"
OUT_NAME = "mysql_job_v24_selinux_xattr_recovery_successor_v1"
SCRIPT_REL = "transaction/mysql_job_v24_node04_selinux_xattr_recovery_20260803t123500z_4d6f8a21.sh"
MATERIALIZER_REL = "transaction/mysql_job_v24_input_reference_materializer_v1.py"
HELPER_REL = "qualification/mysql_job_v24_controlled_selinux_xattr_repair_v1.py"
PUBLISHER_REL = "qualification/mysql_job_v24_raw_syscall_atomic_tree_publish_v1.py"
CLOSURE_REL = "qualification/mysql_job_v24_recovery_bundle_closure_v1.py"
TEST_REL = "tests/test_mysql_job_v24_selinux_xattr_recovery_successor_v1.sh"
BUILDER_REL = "builder/build_mysql_job_v24_selinux_xattr_recovery_successor_v1.py"


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


def locate_workspace(explicit):
    candidates = []
    if explicit is not None:
        candidates.append(Path(explicit))
    candidates.extend((Path.cwd(), *Path(__file__).resolve().parents))
    for candidate in candidates:
        candidate = candidate.resolve()
        predecessor = candidate / "artifacts/mysql_job_v23_selinux_xattr_recovery_successor_v1" / V23_ID
        if predecessor.is_dir():
            return candidate
    raise SystemExit("v23 predecessor workspace not found")


def transform(raw):
    value = raw.replace(OLD_TXN, NEW_TXN)
    value = value.replace(".relocation_v23", ".relocation_v24")
    value = value.replace("mysql_job_v23", "mysql_job_v24")
    value = value.replace("V23", "V24").replace("v23", "v24")
    return value


def replace_once(value, old, new, label):
    count = value.count(old)
    if count != 1:
        raise RuntimeError(f"{label} anchor count differs: {count}")
    return value.replace(old, new)


CLOSURE_SOURCE = r'''#!/usr/bin/env python3
"""Verify the exact immutable JOB v24 recovery bundle closure."""
from __future__ import annotations
import argparse,hashlib,json,os,stat
from pathlib import Path
SCHEMA="mysql_job_v24_selinux_xattr_recovery_successor_plan_v1"
EXPECTED_PREDECESSOR="d328ac108dda22acf0cca1caa6c74afb58e6c415176fa15acd1276b90f400a98"
class Error(RuntimeError):pass
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def digest(p):
 h=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):h.update(b)
 return h.hexdigest()
def main():
 a=argparse.ArgumentParser();a.add_argument("--bundle",type=Path,required=True);x=a.parse_args();root=x.bundle
 if root.is_symlink() or not root.is_dir():raise Error("v24 recovery bundle absent or symlinked")
 root=root.resolve(strict=True);mp=root/"bundle_manifest.json";side=root/"bundle_manifest.json.sha256"
 if mp.is_symlink() or not mp.is_file() or side.is_symlink() or not side.is_file():raise Error("v24 manifest or sidecar absent/symlinked")
 actual=digest(mp)
 if side.read_text(encoding="ascii")!=actual+"  bundle_manifest.json\n":raise Error("v24 manifest sidecar differs")
 m=json.loads(mp.read_text(encoding="utf-8"));bid=m.get("bundle_id");identity=dict(m);identity.pop("bundle_id",None)
 if (m.get("schema_version")!=SCHEMA or bid!=root.name or hashlib.sha256(canonical(identity)).hexdigest()!=bid
  or m.get("formal_scope")!="mysql_only" or m.get("cross_engine_comparison_prohibited") is not True
  or m.get("postgresql_mutation_prohibited") is not True or m.get("formal_launch_authorized") is not False
  or m.get("accepted_round_authorized") is not False or m.get("accepted_rounds_credit")!=0
  or m.get("predecessor_v23_bundle_id")!=EXPECTED_PREDECESSOR or m.get("v23_execute_prohibited") is not True):
  raise Error("v24 recovery bundle identity/scope differs")
 files=m.get("files")
 if not isinstance(files,dict) or not files:raise Error("v24 recovery file closure absent")
 expected=set(files)|{"bundle_manifest.json","bundle_manifest.json.sha256"};observed=set()
 for p in root.rglob("*"):
  rel=p.relative_to(root).as_posix()
  if p.is_symlink():raise Error("symlink prohibited in v24 closure: "+rel)
  mode=p.lstat().st_mode
  if stat.S_ISDIR(mode):continue
  if not stat.S_ISREG(mode):raise Error("special member prohibited in v24 closure: "+rel)
  observed.add(rel)
 if observed!=expected:raise Error("v24 closure member set differs")
 for rel,expected_sha in sorted(files.items()):
  q=Path(rel)
  if q.is_absolute() or ".." in q.parts or "\\" in rel:raise Error("unsafe v24 closure member: "+rel)
  p=root/q
  if p.is_symlink() or not p.is_file() or root not in p.resolve(strict=True).parents or digest(p)!=expected_sha:
   raise Error("v24 closure hash/path differs: "+rel)
 print(json.dumps({"status":"PASS_V24_RECOVERY_BUNDLE_CLOSURE","bundle_id":bid,"manifest_sha256":actual,"file_count":len(files),"formal_scope":"mysql_only","accepted_rounds_credit":0},sort_keys=True))
if __name__=="__main__":
 try:main()
 except (Error,OSError,ValueError,KeyError,TypeError) as e:raise SystemExit("NOT_READY: "+str(e))
'''


TEST_SOURCE = r'''#!/usr/bin/env bash
set -Eeuo pipefail
B=${1:?bundle required}
S="$B/transaction/mysql_job_v24_node04_selinux_xattr_recovery_20260803t123500z_4d6f8a21.sh"
H="$B/qualification/mysql_job_v24_controlled_selinux_xattr_repair_v1.py"
M="$B/transaction/mysql_job_v24_input_reference_materializer_v1.py"
C="$B/qualification/mysql_job_v24_recovery_bundle_closure_v1.py"
bash -n "$S"
python3 -B - "$H" "$M" "$C" <<'PY'
import pathlib,sys
for p in map(pathlib.Path,sys.argv[1:]):compile(p.read_text(),str(p),"exec")
PY
closure=$(python3 -B "$C" --bundle "$B")
python3 -B - "$closure" <<'PY'
import json,sys
v=json.loads(sys.argv[1])
assert v["status"]=="PASS_V24_RECOVERY_BUNDLE_CLOSURE"
assert v["formal_scope"]=="mysql_only" and v["accepted_rounds_credit"]==0
PY
rg -F '"$BOOTSTRAP_PY" -B "$CLOSURE_VERIFIER" --bundle "$RECOVERY_BUNDLE"' "$S" >/dev/null
rg -F 'PASS_V24_STAGE_AUDIT_ROOT_REPAIR_REQUIRED' "$S" >/dev/null
rg -F 'PASS_V24_INPUT_REFERENCE_MATERIALIZER_READ_ONLY' "$M" >/dev/null
rg -F 'controlled xattr repair requires root' "$H" >/dev/null
rg -F 'difference is not exact all-entry SELinux auto-injection' "$H" >/dev/null
rg -F 'full portable identity did not close' "$H" >/dev/null
rg -F 'security.selinux' "$H" >/dev/null
rg -F -- '--resume-publish' "$S" >/dev/null
if rg -n '(^|[;&|[:space:]])(rm|rmdir|unlink|shred|truncate|mv)([[:space:]]|$)' "$S" "$M";then exit 1;fi
printf 'PASS_OFFLINE_V24: production_bundle_closure_executed deterministic_identity exact_selinux_only full_identity resume_noreplace\n'
'''


def expected_content(workspace):
    predecessor = workspace / "artifacts/mysql_job_v23_selinux_xattr_recovery_successor_v1" / V23_ID
    if sha_file(predecessor / "bundle_manifest.json") != V23_MANIFEST_SHA:
        raise SystemExit("v23 predecessor manifest differs")
    old_manifest = json.loads((predecessor / "bundle_manifest.json").read_text())
    old_identity = dict(old_manifest)
    old_identity.pop("bundle_id")
    if old_manifest.get("bundle_id") != V23_ID or sha_bytes(canonical(old_identity)) != V23_ID:
        raise SystemExit("v23 predecessor content address differs")

    script = transform((predecessor / "transaction/mysql_job_v23_node04_selinux_xattr_recovery_20260803t121500z_91e7c4a6.sh").read_text())
    script = script.replace(
        "readonly REPAIR=\"${RECOVERY_BUNDLE}/qualification/mysql_job_v24_controlled_selinux_xattr_repair_v1.py\"",
        "readonly REPAIR=\"${RECOVERY_BUNDLE}/qualification/mysql_job_v24_controlled_selinux_xattr_repair_v1.py\"\nreadonly CLOSURE_VERIFIER=\"${RECOVERY_BUNDLE}/qualification/mysql_job_v24_recovery_bundle_closure_v1.py\"",
    )
    start = script.index("check_recovery_bundle_closure() {")
    end = script.index("\n}\n\ncheck_bundle_closure()", start) + 2
    script = script[:start] + '''check_recovery_bundle_closure() {
  "$BOOTSTRAP_PY" -B "$CLOSURE_VERIFIER" --bundle "$RECOVERY_BUNDLE"
}''' + script[end:]
    if "mysql_job_v24_mtime_identity_recovery_successor_plan_v1" in script:
        raise RuntimeError("stale mismatched closure schema survived")

    materializer = transform((predecessor / "transaction/mysql_job_v23_input_reference_materializer_v1.py").read_text())
    helper = transform((predecessor / "qualification/mysql_job_v23_controlled_selinux_xattr_repair_v1.py").read_text())
    publisher = transform((predecessor / "qualification/mysql_job_v23_raw_syscall_atomic_tree_publish_v1.py").read_text())
    invalidation = {
        "schema_version": "mysql_job_v24_v23_offline_invalidation_v1",
        "status": "INVALID_SCHEMA_CLOSURE_NEVER_EXECUTE",
        "v23_bundle_id": V23_ID,
        "v23_manifest_sha256": V23_MANIFEST_SHA,
        "reason": "manifest schema is selinux_xattr but production recovery closure required stale mtime_identity schema",
        "detected_by": "direct_runtime_branch_review",
        "v23_publish_state_not_assumed": True,
        "v23_transaction_and_stages_preserve_if_present": True,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
    }
    plan = {
        "schema_version": SCHEMA,
        "status": "SEALED_V24_THREE_PHASE_XATTR_RECOVERY_NOT_EXECUTED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "predecessor_v23_bundle_id": V23_ID,
        "predecessor_v23_manifest_sha256": V23_MANIFEST_SHA,
        "v23_execute_prohibited": True,
        "v23_transaction_and_stages_preserve_if_present": True,
        "failed_v21_transaction": V21_TXN,
        "v21_stages_preserved_no_reuse_delete": True,
        "new_transaction": NEW_TXN,
        "phases": [
            "uid1008_check_and_new_stage_audit_no_root_mutation",
            "explicit_root_exact_source_xattr_restore_on_new_stage_only",
            "uid1008_full_identity_recheck_and_raw_noreplace_publish",
        ],
        "production_bundle_closure_shared_with_offline_test": True,
        "all_non_xattr_difference_fail_closed": True,
        "exact_injected_selinux_value_required": True,
        "source_xattrs_restored_not_ignored": True,
        "full_portable_identity_required": True,
        "root_helper_exact_stage_inode_audit_binding_required": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
    }
    return {
        SCRIPT_REL: script.encode(),
        MATERIALIZER_REL: materializer.encode(),
        HELPER_REL: helper.encode(),
        PUBLISHER_REL: publisher.encode(),
        CLOSURE_REL: CLOSURE_SOURCE.encode(),
        "authority/v23_offline_invalidation.json": canonical(invalidation),
        "plan/recovery_plan.json": canonical(plan),
        TEST_REL: TEST_SOURCE.encode(),
        BUILDER_REL: Path(__file__).read_bytes(),
    }, plan, invalidation


def verify_existing(destination, content, manifest_raw):
    if destination.is_symlink() or not destination.is_dir():
        raise SystemExit("existing v24 destination is not an immutable directory")
    expected = set(content) | {"bundle_manifest.json", "bundle_manifest.json.sha256"}
    observed = {p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file()}
    if observed != expected:
        raise SystemExit("existing v24 destination member set differs")
    for relative, payload in content.items():
        if (destination / relative).read_bytes() != payload:
            raise SystemExit("existing v24 member differs: " + relative)
    if (destination / "bundle_manifest.json").read_bytes() != manifest_raw:
        raise SystemExit("existing v24 manifest differs")
    side = sha_bytes(manifest_raw) + "  bundle_manifest.json\n"
    if (destination / "bundle_manifest.json.sha256").read_text(encoding="ascii") != side:
        raise SystemExit("existing v24 sidecar differs")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", type=Path)
    args = parser.parse_args()
    workspace = locate_workspace(args.workspace_root)
    content, plan, invalidation = expected_content(workspace)
    files = {relative: sha_bytes(payload) for relative, payload in sorted(content.items())}
    identity = dict(
        plan,
        predecessor_bundle_id=V23_ID,
        predecessor_manifest_sha256=V23_MANIFEST_SHA,
        v21_archive_bundle_id=V21_ID,
        v21_archive_manifest_sha256=V21_MANIFEST_SHA,
        invalidated_v23_sha256=sha_bytes(canonical(invalidation)),
        script_sha256=sha_bytes(content[SCRIPT_REL]),
        materializer_sha256=sha_bytes(content[MATERIALIZER_REL]),
        helper_sha256=sha_bytes(content[HELPER_REL]),
        publisher_sha256=sha_bytes(content[PUBLISHER_REL]),
        closure_verifier_sha256=sha_bytes(content[CLOSURE_REL]),
        immutable=True,
        files=files,
    )
    bundle_id = sha_bytes(canonical(identity))
    manifest = dict(identity, bundle_id=bundle_id)
    manifest_raw = canonical(manifest)
    output = workspace / "artifacts" / OUT_NAME
    output.mkdir(parents=True, exist_ok=True)
    destination = output / bundle_id
    if destination.exists() or destination.is_symlink():
        verify_existing(destination, content, manifest_raw)
    else:
        stage = Path(tempfile.mkdtemp(prefix=".job-v24-xattr-", dir=output))
        for relative, payload in content.items():
            path = stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            path.chmod(0o555 if relative.endswith((".py", ".sh")) else 0o444)
        (stage / "bundle_manifest.json").write_bytes(manifest_raw)
        (stage / "bundle_manifest.json").chmod(0o444)
        (stage / "bundle_manifest.json.sha256").write_text(
            sha_bytes(manifest_raw) + "  bundle_manifest.json\n", encoding="ascii")
        (stage / "bundle_manifest.json.sha256").chmod(0o444)
        for path in sorted(stage.rglob("*"), reverse=True):
            if path.is_dir():
                path.chmod(0o555)
        stage.chmod(0o555)
        os.replace(stage, destination)
    print(json.dumps({
        "bundle": str(destination),
        "bundle_id": bundle_id,
        "manifest_sha256": sha_file(destination / "bundle_manifest.json"),
        "schema_version": SCHEMA,
        "transaction": NEW_TXN,
        "remote_actions": 0,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
