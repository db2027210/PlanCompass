#!/usr/bin/env python3
"""Build JOB v21 after v20 reflink-always left a sealed partial PREP."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
V20_ID = "10202cbd6a9d00c8370f32e28a4d3c2f0805f6b3ff5b3adfb7d70f68c5a14ed8"
BASE = ROOT / "artifacts/mysql_job_v20_hash_convention_recovery_successor_v1" / V20_ID
OLD_TXN = "job-v20-recovery-20260803t103444z-22dc974a"
NEW_TXN = "job-v21-recovery-20260803t105116z-776fe047"
OUT = ROOT / "artifacts/mysql_job_v21_copy_fallback_recovery_successor_v1"
SCRIPT_NAME = "mysql_job_v21_node04_copy_fallback_recovery_20260803t105116z_776fe047.sh"
MATERIALIZER_NAME = "mysql_job_v21_recovery_input_materializer_v1.py"
PUBLISHER_NAME = "mysql_job_v21_raw_syscall_atomic_tree_publish_v1.py"
EMPTY_SHA = hashlib.sha256(b"").hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError(f"{label} anchor count differs: {text.count(old)}")
    return text.replace(old, new, 1)


def materializer() -> bytes:
    p = BASE / "transaction/mysql_job_v20_recovery_input_materializer_v1.py"
    text = p.read_text(encoding="utf-8").replace(OLD_TXN, NEW_TXN)
    text = text.replace("v20", "v21").replace("V20", "V21")
    text = text.replace(
        'PARENT=JOB/".relocation_v21"\n',
        'FAILED_V20_TXN="job-v20-recovery-20260803t103444z-22dc974a"\n'
        'V20_PARENT=JOB/".relocation_v20"\n'
        'V20_PREP=V20_PARENT/(".prepare-"+FAILED_V20_TXN)\n'
        'V20_VISIBLE=V20_PARENT/FAILED_V20_TXN\n'
        'PARENT=JOB/".relocation_v21"\n', 1)
    anchor = "def verify():\n"
    partial = r'''def v20_partial_authority():
 exact_dir(V20_PARENT,1008,1008,0o700,"v20 transaction parent")
 exact_dir(V20_PREP,1008,1008,0o700,"sealed partial v20 PREP")
 if V20_PARENT.stat().st_dev!=66307 or V20_PREP.stat().st_dev!=66307: raise Error("v20 partial device identity differs")
 if lexists(V20_VISIBLE): raise Error("v20 visible transaction unexpectedly exists")
 if set(x.name for x in V20_PARENT.iterdir())!={V20_PREP.name}: raise Error("v20 parent exact one-entry closure differs")
 expected_dirs={"predecessor_v18":10,"runtime":10,"template":36};rows=[];actual_dirs={};actual_files={}
 root=V20_PREP.stat();rows.append({"path":".","type":"directory","uid":root.st_uid,"gid":root.st_gid,"mode":"0700","dev":root.st_dev})
 for base,dirs,files in os.walk(V20_PREP,topdown=True,followlinks=False):
  for name in dirs:
   q=pathlib.Path(base)/name;rel=str(q.relative_to(V20_PREP));s=q.lstat()
   if q.is_symlink() or not stat.S_ISDIR(s.st_mode): raise Error("non-directory in v20 partial: "+rel)
   actual_dirs[rel]=s.st_size;rows.append({"path":rel,"type":"directory","uid":s.st_uid,"gid":s.st_gid,"mode":format(stat.S_IMODE(s.st_mode),"04o"),"size":s.st_size})
  for name in files:
   q=pathlib.Path(base)/name;rel=str(q.relative_to(V20_PREP));s=q.lstat()
   if q.is_symlink() or not stat.S_ISREG(s.st_mode): raise Error("non-regular in v20 partial: "+rel)
   actual_files[rel]=(s.st_uid,s.st_gid,stat.S_IMODE(s.st_mode),s.st_size,s.st_blocks,digest(q));rows.append({"path":rel,"type":"regular","uid":s.st_uid,"gid":s.st_gid,"mode":format(stat.S_IMODE(s.st_mode),"04o"),"size":s.st_size,"blocks":s.st_blocks,"sha256":digest(q)})
 if actual_dirs!=expected_dirs: raise Error("v20 partial exact directory closure differs")
 if actual_files!={"template/control.sha256":(1008,1008,0o600,0,0,"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")}: raise Error("v20 partial exact empty-file closure differs")
 return hashlib.sha256((json.dumps(sorted(rows,key=lambda x:x["path"]),sort_keys=True,separators=(",",":"))+"\n").encode()).hexdigest()
'''
    text = once(text, anchor, partial + anchor, "v20 partial authority")
    old_verify = (' exact_dir(PARENT,1008,1008,0o700,"v21 transaction parent")\n'
                  ' if V18_PREP.stat().st_dev!=PARENT.stat().st_dev:')
    new_verify = (' partial_identity=v20_partial_authority()\n'
                  ' exact_dir(PARENT,1008,1008,0o700,"v21 transaction parent")\n'
                  ' if V18_PREP.stat().st_dev!=PARENT.stat().st_dev:')
    text = once(text, old_verify, new_verify, "v20 partial verify call")
    text = text.replace(' return v18_authority()\n',
                        ' source_receipt,source_identity=v18_authority()\n'
                        ' fs=os.statvfs(PARENT);available=fs.f_bavail*fs.f_frsize\n'
                        ' if available<3767708926+64*1024*1024: raise Error("insufficient space for explicit full-copy fallback")\n'
                        ' return source_receipt,source_identity,partial_identity\n', 1)
    text = text.replace(
        'subprocess.run(["/bin/cp","--reflink=always","--preserve=all","--no-clobber","--",str(src),str(dst)],check=True)',
        'subprocess.run(["/bin/cp","--reflink=auto","--sparse=always","--preserve=all","--no-clobber","--",str(src),str(dst)],check=True)')
    text = text.replace('def execute(source_receipt,identity,probe_errno):',
                        'def execute(source_receipt,identity,partial_identity,probe_errno):', 1)
    text = text.replace(
        '"copy_method":"cp_reflink_always_unique_inode",',
        '"copy_method":"cp_reflink_auto_sparse_always_full_copy_fallback_unique_inode",'
        '"reflink_always_failure":"Operation not supported",'
        '"reflink_always_failure_errno":"EOPNOTSUPP_ENOTSUP_95",'
        '"fallback_authority":"sealed_v20_first_file_zero_length_partial_on_same_xfs_device_66307",'
        '"v18_regular_files":25,"v18_logical_bytes":3767708926,"v18_allocated_bytes":3767787520,'
        '"v20_partial_prepared_tree_identity_sha256":partial_identity,"v20_partial_entry_count":4,'
        '"v20_partial_reuse_prohibited":True,', 1)
    text = text.replace(
        'source_receipt,identity=verify();number=probe()',
        'source_receipt,identity,partial_identity=verify();number=probe()', 1)
    text = text.replace('else: execute(source_receipt,identity,number)',
                        'else: execute(source_receipt,identity,partial_identity,number)', 1)
    text = text.replace('"prepared_tree_identity_sha256":identity}',
                        '"prepared_tree_identity_sha256":identity,"v20_partial_identity_sha256":partial_identity}', 1)
    text = text.replace(
        'if next(V19_PARENT.iterdir(),None) is not None or lexists(V19_PREP) or lexists(V19_VISIBLE): raise Error("empty v19 parent preservation changed after activation")',
        'if next(V19_PARENT.iterdir(),None) is not None or lexists(V19_PREP) or lexists(V19_VISIBLE): raise Error("empty v19 parent preservation changed after activation")\n'
        ' if not V20_PREP.is_dir() or lexists(V20_VISIBLE): raise Error("partial v20 PREP preservation changed after activation")', 1)
    return text.encode("utf-8")


def recovery_script() -> bytes:
    p = BASE / "transaction/mysql_job_v20_node04_hash_convention_recovery_20260803t103444z_22dc974a.sh"
    text = p.read_text(encoding="utf-8").replace(OLD_TXN, NEW_TXN)
    text = text.replace("v20", "v21").replace("V20", "V21")
    text = text.replace("# Immutable node04 JOB v21 recovery. v17/v18/v19 are never rerun;",
                        "# Immutable JOB v21 recovery. v17-v20 are never rerun; v20 partial is sealed.", 1)
    text = text.replace(
        f"readonly TXN='{NEW_TXN}'\n",
        "readonly FAILED_V20_TXN='job-v20-recovery-20260803t103444z-22dc974a'\n"
        f"readonly TXN='{NEW_TXN}'\n", 1)
    text = text.replace(
        'readonly DST_TXN="${JOB_ROOT}/.relocation_v21/${TXN}"\n',
        'readonly V20_PARENT="${JOB_ROOT}/.relocation_v20"\n'
        'readonly V20_PREP="${V20_PARENT}/.prepare-${FAILED_V20_TXN}"\n'
        'readonly V20_VISIBLE="${V20_PARENT}/${FAILED_V20_TXN}"\n'
        'readonly DST_TXN="${JOB_ROOT}/.relocation_v21/${TXN}"\n', 1)
    text = text.replace("mysql_job_v21_hash_convention_recovery_successor_plan_v1",
                        "mysql_job_v21_copy_fallback_recovery_successor_plan_v1", 1)
    text = text.replace(' or d.get("copy_method")!="cp_reflink_always_unique_inode"\n',
                        ' or d.get("copy_method")!="cp_reflink_auto_sparse_always_full_copy_fallback_unique_inode"\n', 1)
    old = ('  [[ "$TXN" != "$OLD_TXN" && "$TXN" != "$FAILED_V18_TXN" && "$TXN" != "$FAILED_V19_TXN" ]] ||\n'
           "    die 'v17/v18/v19 failed predecessor transaction rerun is prohibited'\n")
    new = ('  [[ "$TXN" != "$OLD_TXN" && "$TXN" != "$FAILED_V18_TXN" && "$TXN" != "$FAILED_V19_TXN" && "$TXN" != "$FAILED_V20_TXN" ]] ||\n'
           "    die 'v17/v18/v19/v20 failed predecessor transaction rerun is prohibited'\n"
           '  [[ -d "$V20_PARENT" && ! -L "$V20_PARENT" && -d "$V20_PREP" && ! -L "$V20_PREP" ]] || die \'partial v20 PREP was not preserved\'\n'
           '  [[ $(stat -c \'%u:%g:%a\' "$V20_PARENT") == \'1008:1008:700\' && $(stat -c \'%u:%g:%a\' "$V20_PREP") == \'1008:1008:700\' ]] || die \'v20 parent/PREP owner mode differs\'\n'
           '  require_absent "$V20_VISIBLE"\n')
    text = once(text, old, new, "recovery v20 preservation")
    # The materializer receipt already binds the exact partial identity; verify its new fields.
    text = text.replace(
        ' or d.get("v19_parent_preserved_empty") is not True or d.get("v19_reuse_prohibited") is not True\n',
        ' or d.get("v19_parent_preserved_empty") is not True or d.get("v19_reuse_prohibited") is not True\n'
        ' or d.get("reflink_always_failure_errno")!="EOPNOTSUPP_ENOTSUP_95"\n'
        ' or d.get("v20_partial_entry_count")!=4 or d.get("v20_partial_reuse_prohibited") is not True\n'
        ' or not isinstance(d.get("v20_partial_prepared_tree_identity_sha256"),str)\n', 1)
    text = text.replace(
        '"$BOOTSTRAP_PY" -B - "$INPUT_REUSE_RECEIPT" "$V18_PREP" "$DST_TXN" <<\'PY\'\n'
        'import hashlib,json,os,pathlib,stat,sys\n'
        'p=pathlib.Path(sys.argv[1]); source=pathlib.Path(sys.argv[2]); new=pathlib.Path(sys.argv[3])\n',
        '"$BOOTSTRAP_PY" -B - "$INPUT_REUSE_RECEIPT" "$V18_PREP" "$DST_TXN" "$V20_PREP" <<\'PY\'\n'
        'import hashlib,json,os,pathlib,stat,sys\n'
        'p=pathlib.Path(sys.argv[1]); source=pathlib.Path(sys.argv[2]); new=pathlib.Path(sys.argv[3]); partial=pathlib.Path(sys.argv[4])\n', 1)
    identity_anchor = 'files=d.get("files")\nif not isinstance(files,dict) or len(files)!=23:\n'
    identity_check = r'''rows=[];actual_dirs={};actual_files={}
s=partial.stat();rows.append({"path":".","type":"directory","uid":s.st_uid,"gid":s.st_gid,"mode":"0700","dev":s.st_dev})
for root,dirs,names in os.walk(partial,topdown=True,followlinks=False):
 for name in dirs:
  x=pathlib.Path(root)/name;rel=str(x.relative_to(partial));s=x.lstat()
  if x.is_symlink() or not stat.S_ISDIR(s.st_mode): raise SystemExit("NOT_READY: non-directory v20 partial member")
  actual_dirs[rel]=s.st_size;rows.append({"path":rel,"type":"directory","uid":s.st_uid,"gid":s.st_gid,"mode":format(stat.S_IMODE(s.st_mode),"04o"),"size":s.st_size})
 for name in names:
  x=pathlib.Path(root)/name;rel=str(x.relative_to(partial));s=x.lstat()
  if x.is_symlink() or not stat.S_ISREG(s.st_mode): raise SystemExit("NOT_READY: non-regular v20 partial member")
  actual_files[rel]=(s.st_uid,s.st_gid,stat.S_IMODE(s.st_mode),s.st_size,s.st_blocks,digest(x));rows.append({"path":rel,"type":"regular","uid":s.st_uid,"gid":s.st_gid,"mode":format(stat.S_IMODE(s.st_mode),"04o"),"size":s.st_size,"blocks":s.st_blocks,"sha256":digest(x)})
if actual_dirs!={"predecessor_v18":10,"runtime":10,"template":36} or actual_files!={"template/control.sha256":(1008,1008,0o600,0,0,"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")}:
 raise SystemExit("NOT_READY: sealed v20 partial closure changed")
partial_identity=hashlib.sha256((json.dumps(sorted(rows,key=lambda x:x["path"]),sort_keys=True,separators=(",",":"))+"\n").encode()).hexdigest()
if d.get("v20_partial_prepared_tree_identity_sha256")!=partial_identity:
 raise SystemExit("NOT_READY: sealed v20 partial identity changed")
'''
    text = once(text, identity_anchor, identity_check + identity_anchor, "recovery partial identity")
    return text.encode("utf-8")


def publisher() -> bytes:
    p = BASE / "qualification/mysql_job_v20_raw_syscall_atomic_tree_publish_v1.py"
    return p.read_text(encoding="utf-8").replace("v20", "v21").replace("V20", "V21").encode("utf-8")


TEST = r'''#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C
B=${1:?bundle required}
S="$B/transaction/mysql_job_v21_node04_copy_fallback_recovery_20260803t105116z_776fe047.sh"
M="$B/transaction/mysql_job_v21_recovery_input_materializer_v1.py"
P="$B/qualification/mysql_job_v21_raw_syscall_atomic_tree_publish_v1.py"
fail(){ printf 'FAIL: %s\n' "$*" >&2; exit 1; }
bash -n "$S"; "$S" --help >/dev/null
python3 -B - "$M" "$P" <<'PY'
import pathlib,sys
for p in map(pathlib.Path,sys.argv[1:]): compile(p.read_bytes(),str(p),"exec")
PY
rg -F 'FAILED_V20_TXN="job-v20-recovery-20260803t103444z-22dc974a"' "$M" >/dev/null
rg -F 'PARENT=JOB/".relocation_v21"' "$M" >/dev/null
rg -F 'v20_partial_authority' "$M" >/dev/null
rg -F 'template/control.sha256":(1008,1008,0o600,0,0,"e3b0c442' "$M" >/dev/null
rg -F '["/bin/cp","--reflink=auto","--sparse=always","--preserve=all","--no-clobber"' "$M" >/dev/null
! rg -F '["/bin/cp","--reflink=always"' "$M" >/dev/null
rg -F 'v20_partial_reuse_prohibited' "$M" "$S" >/dev/null
rg -F 'mysql_job_v21_copy_fallback_recovery_successor_plan_v1' "$S" >/dev/null
[[ $(rg -c 'copy_method.*cp_reflink_auto_sparse_always_full_copy_fallback_unique_inode' "$S") -eq 1 ]] || fail 'fallback copy method check count differs'
rg -F 'sealed v20 partial identity changed' "$S" >/dev/null
if rg -n '(^|[;&|[:space:]])(rm|rmdir|unlink|shred|truncate|mv)([[:space:]]|$)' "$S" "$M" "$P"; then fail 'delete/replace command present'; fi
f=$(mktemp -d /tmp/mysql-job-v21-enotsup.XXXXXX);trap 'rm -rf -- "$f"' EXIT
printf 'explicit fallback regression\n' >"$f/source"
set +e
/bin/cp --reflink=always --preserve=all --no-clobber -- "$f/source" "$f/always" 2>"$f/always.err"
rc=$?
set -e
[[ $rc -ne 0 ]] || fail 'fixture filesystem unexpectedly accepted reflink always'
rg -F 'Operation not supported' "$f/always.err" >/dev/null
[[ -f "$f/always" && ! -L "$f/always" && ! -s "$f/always" ]] || fail 'ENOTSUP partial shape differs'
/bin/cp --reflink=auto --sparse=always --preserve=all --no-clobber -- "$f/source" "$f/auto"
cmp "$f/source" "$f/auto"
[[ $(stat -c %i "$f/source") != $(stat -c %i "$f/auto") ]] || fail 'fallback copy shares inode'
python3 -B - "$f" <<'PY'
import ctypes,errno,os,pathlib,sys
r=pathlib.Path(sys.argv[1]);libc=ctypes.CDLL(None,use_errno=True);fn=libc.syscall
fn.argtypes=[ctypes.c_long,ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint];fn.restype=ctypes.c_long
assert fn(316,-100,os.fsencode(r/"absent-source"),-100,os.fsencode(r/"absent-destination"),1)==-1 and ctypes.get_errno()==errno.ENOENT
PY
printf 'PASS_OFFLINE_V21_FIXTURE: exact_v20_partial ENOTSUP_zero_file reflink_auto_full_copy_fallback metadata no_clobber unique_inode raw_renameat2 no_delete\n'
'''.encode("utf-8")


def main() -> None:
    mat = materializer(); script = recovery_script(); pub = publisher()
    authority = {
        "schema_version": "mysql_job_v21_v20_partial_reflink_failure_contract_v1",
        "status": "PASS_EXACT_V20_PARTIAL_FAILURE_SEALED_NOT_EXECUTED",
        "failed_bundle_id": V20_ID, "failed_transaction": OLD_TXN,
        "failure": "/bin/cp --reflink=always returned Operation not supported",
        "failure_errno": {"number": 95, "names": ["EOPNOTSUPP", "ENOTSUP"]},
        "failure_phase": "first_or_early_file_copy_zero_credit",
        "filesystem": {"device": 66307, "type": "xfs", "mount": "/mnt/nvme2n1",
                       "source": "/dev/nvme2n1", "block_size": 4096,
                       "options": "rw,relatime,seclabel,attr2,inode64,noquota"},
        "cp": "GNU coreutils 8.22",
        "v20_parent": {"uid": 1008, "gid": 1008, "mode": "0700"},
        "v20_partial_root": {"uid": 1008, "gid": 1008, "mode": "0700", "device": 66307},
        "v20_partial_entries": [
            {"path": "predecessor_v18", "type": "directory", "mode": "0700", "size": 10},
            {"path": "runtime", "type": "directory", "mode": "0700", "size": 10},
            {"path": "template", "type": "directory", "mode": "0700", "size": 36},
            {"path": "template/control.sha256", "type": "regular", "mode": "0600",
             "size": 0, "blocks": 0, "sha256": EMPTY_SHA},
        ],
        "v20_partial_entry_count": 4, "v20_visible_absent": True,
        "v20_rerun_reuse_delete_prohibited": True,
        "v18_source": {"regular_files": 25, "logical_bytes": 3767708926,
                       "allocated_bytes": 3767787520, "device": 66307},
        "readback_available_bytes": 4713110016000,
        "remote_actions": 0, "accepted_rounds_credit": 0,
    }
    plan = {
        "schema_version": "mysql_job_v21_copy_fallback_recovery_successor_plan_v1",
        "status": "PASS_IMMUTABLE_RECOVERY_PLAN_NOT_EXECUTED",
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "failed_v20_bundle_id": V20_ID, "failed_v20_transaction": OLD_TXN,
        "invalidated_local_intermediate_bundle_id": "ae41326e89e621fbcbb9b4d22b78b17ec0c3098a1098d8a8ff36272d8c986e7a",
        "failed_v20_rerun_reuse_delete_prohibited": True,
        "new_transaction": NEW_TXN,
        "new_transaction_parent": "/mnt/nvme2n1/anonymous/resource_adaptive_mysql/job/.relocation_v21",
        "authoritative_source": "exact_preserved_v18_PREP",
        "copy_strategy": "cp_--reflink=auto_--sparse=always_--preserve=all_--no-clobber",
        "fallback": "same_xfs_reflink_ENOTSUP_to_verified_full_copy",
        "space_gate_bytes": 3767708926 + 64 * 1024 * 1024,
        "activation": "raw_syscall316_RENAME_NOREPLACE_parent_fsync",
        "publication": "runtime_then_template_raw_syscall316_RENAME_NOREPLACE",
        "remote_actions": 0, "accepted_rounds_credit": 0,
    }
    content = {
        f"transaction/{SCRIPT_NAME}": script,
        f"transaction/{MATERIALIZER_NAME}": mat,
        f"qualification/{PUBLISHER_NAME}": pub,
        "authority/v20_partial_reflink_failure_contract.json": canonical(authority),
        "plan/recovery_plan.json": canonical(plan),
        "tests/test_mysql_job_v21_copy_fallback_recovery_successor_v1.sh": TEST,
        "builder/build_mysql_job_v21_copy_fallback_recovery_successor_v1.py": Path(__file__).read_bytes(),
    }
    files = {name: sha(data) for name, data in sorted(content.items())}
    identity = dict(plan, files=files, script_sha256=sha(script), materializer_sha256=sha(mat),
                    publisher_sha256=sha(pub), authority_sha256=sha(canonical(authority)), immutable=True)
    bundle_id = sha(canonical(identity)); manifest = dict(identity, bundle_id=bundle_id)
    manifest_bytes = canonical(manifest)
    OUT.mkdir(parents=True, exist_ok=True); target = OUT / bundle_id
    if target.exists():
        if sha((target / "bundle_manifest.json").read_bytes()) != sha(manifest_bytes):
            raise SystemExit("existing v21 bundle differs")
        print(json.dumps({"bundle": str(target), "bundle_id": bundle_id,
                          "manifest_sha256": sha(manifest_bytes), "script_sha256": sha(script),
                          "materializer_sha256": sha(mat), "publisher_sha256": sha(pub)}, sort_keys=True)); return
    stage = Path(tempfile.mkdtemp(prefix=".mysql_job_v21_recovery.", dir=OUT))
    for relative, data in content.items():
        path = stage / relative; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data); path.chmod(0o555 if relative.endswith((".sh", ".py")) else 0o444)
    (stage / "bundle_manifest.json").write_bytes(manifest_bytes); (stage / "bundle_manifest.json").chmod(0o444)
    (stage / "bundle_manifest.json.sha256").write_text(sha(manifest_bytes) + "  bundle_manifest.json\n", encoding="ascii")
    (stage / "bundle_manifest.json.sha256").chmod(0o444)
    for directory, dirs, _ in os.walk(stage, topdown=False):
        for name in dirs: os.chmod(Path(directory) / name, 0o555)
    stage.chmod(0o555); os.rename(stage, target)
    print(json.dumps({"bundle": str(target), "bundle_id": bundle_id,
                      "manifest_sha256": sha(manifest_bytes), "script_sha256": sha(script),
                      "materializer_sha256": sha(mat), "publisher_sha256": sha(pub)}, sort_keys=True))


if __name__ == "__main__": main()
