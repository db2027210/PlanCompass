#!/usr/bin/env python3
"""Build the immutable JOB v18 recovery transaction successor."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile


ROOT = Path(__file__).resolve().parents[1]
OLD_SCRIPT = ROOT / "shell/mysql_job_v18_node04_gzip_destination_txn154500.sh"
OUT = ROOT / "artifacts/mysql_job_v18_recovery_successor_v1"
OLD_TXN = "job-v17-relocate-20260803t154500z-a1b2c3d4"
NEW_TXN = "job-v18-recovery-20260803t100018z-35f8668f"
SCRIPT_NAME = "mysql_job_v18_node04_gzip_destination_recovery_20260803t100018z_35f8668f.sh"


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def recovery_script() -> bytes:
    text = OLD_SCRIPT.read_text(encoding="utf-8")
    text = text.replace(
        "# Fixed node04 destination transaction for the already-created txn154500\n"
        "# gzip segmented JOB relocation.  This script never removes or overwrites\n"
        "# an archive, stage, final tree, or evidence file.\n",
        "# Immutable node04 JOB recovery transaction. The failed predecessor txn is\n"
        "# never rerun and its empty runtime stage is deliberately retained. Inputs\n"
        "# are atomically materialized in a separate relocation_v18 transaction.\n",
        1,
    )
    text = text.replace(
        f"readonly TXN='{OLD_TXN}'\n",
        f"readonly OLD_TXN='{OLD_TXN}'\n"
        f"readonly TXN='{NEW_TXN}'\n"
        "readonly EXPECTED_UID='1008'\n"
        "readonly EXPECTED_GID='1008'\n",
        1,
    )
    text = text.replace(
        'readonly DST_TXN="${JOB_ROOT}/.relocation_v17/${TXN}"\n',
        'readonly OLD_DST_TXN="${JOB_ROOT}/.relocation_v17/${OLD_TXN}"\n'
        'readonly DST_TXN="${JOB_ROOT}/.relocation_v18/${TXN}"\n'
        'readonly INPUT_REUSE_RECEIPT="${DST_TXN}/input_reuse_receipt.json"\n',
        1,
    )
    text = text.replace(
        'readonly RUNTIME_PARENT="${JOB_ROOT}/runtime_algorithm_c_successor_20260729/runtime"\n',
        'readonly RUNTIME_PARENT="${JOB_ROOT}/runtime_algorithm_c_successor_20260729/runtime"\n'
        'readonly OLD_RUNTIME_STAGE="${RUNTIME_PARENT}/.python311-${RUNTIME_ID}.stage-${OLD_TXN}"\n',
        1,
    )
    anchor = '''require_evidence_pair() {
  local directory=$1
  local name=$2
  require_plain_file "${directory}/${name}"
  require_plain_file "${directory}/${name}.sha256"
  (
    cd "$directory"
    sha256sum --strict -c "${name}.sha256"
  )
}
'''
    injected = anchor + r'''
check_exact_parent_contracts() {
  local template_stat runtime_stat
  [[ $(id -u) == "$EXPECTED_UID" && $(id -g) == "$EXPECTED_GID" ]] ||
    die 'recovery transaction executor must be uid/gid 1008/1008'
  [[ -d "$JOB_ROOT" && ! -L "$JOB_ROOT" ]] || die 'template parent is absent or symlinked'
  [[ -d "$RUNTIME_PARENT" && ! -L "$RUNTIME_PARENT" ]] || die 'runtime parent is absent or symlinked'
  template_stat=$(stat -c '%u:%g:%a' "$JOB_ROOT")
  runtime_stat=$(stat -c '%u:%g:%a' "$RUNTIME_PARENT")
  [[ "$template_stat" == '1008:1008:755' ]] ||
    die "template parent owner/mode differs: ${template_stat}"
  [[ "$runtime_stat" == '1008:1008:775' ]] ||
    die "runtime parent owner/mode differs: ${runtime_stat}"
  [[ -w "$JOB_ROOT" && -x "$JOB_ROOT" ]] || die 'template parent is not writable/searchable by executor'
  [[ -w "$RUNTIME_PARENT" && -x "$RUNTIME_PARENT" ]] || die 'runtime parent is not writable/searchable by executor'
  [[ $(stat -c '%d' "$JOB_ROOT") == $(stat -c '%d' "$RUNTIME_PARENT") ]] ||
    die 'template/runtime final parents are not on one filesystem'
}

check_failed_predecessor_preserved() {
  [[ "$TXN" != "$OLD_TXN" ]] || die 'failed predecessor transaction rerun is prohibited'
  require_absent "${JOB_ROOT}/.template.stage-${OLD_TXN}"
  require_absent "$TEMPLATE_FINAL"
  require_absent "$RUNTIME_FINAL"
  [[ -d "$OLD_RUNTIME_STAGE" && ! -L "$OLD_RUNTIME_STAGE" ]] ||
    die 'failed predecessor empty runtime stage was not preserved'
  [[ $(stat -c '%u:%g:%a' "$OLD_RUNTIME_STAGE") == '1008:1008:700' ]] ||
    die 'failed predecessor empty runtime stage identity differs'
  [[ -z $(find "$OLD_RUNTIME_STAGE" -mindepth 1 -maxdepth 1 -print -quit) ]] ||
    die 'failed predecessor runtime stage is no longer empty'
}

check_input_reuse_receipt() {
  require_evidence_pair "$DST_TXN" input_reuse_receipt.json
  "$BOOTSTRAP_PY" -B - "$INPUT_REUSE_RECEIPT" "$OLD_DST_TXN" "$DST_TXN" <<'PY'
import hashlib,json,pathlib,sys
p=pathlib.Path(sys.argv[1]); old=pathlib.Path(sys.argv[2]); new=pathlib.Path(sys.argv[3])
d=json.loads(p.read_text(encoding="utf-8"))
if (d.get("schema_version")!="mysql_job_v18_recovery_input_reuse_receipt_v1"
        or d.get("status")!="PASS_ATOMIC_NEW_TRANSACTION_INPUT_REUSE"
        or d.get("copy_method")!="cp_reflink_auto_unique_inode"
        or d.get("old_transaction")!=old.name or d.get("new_transaction")!=new.name
        or d.get("old_failed_transaction_rerun_prohibited") is not True
        or d.get("new_receipts_isolated_from_old_transaction") is not True):
    raise SystemExit("NOT_READY: recovery input reuse receipt authority differs")
files=d.get("files")
if not isinstance(files,dict) or not files:
    raise SystemExit("NOT_READY: recovery input reuse file closure is empty")
for rel,row in sorted(files.items()):
    if not isinstance(rel,str) or rel.startswith("/") or ".." in pathlib.PurePosixPath(rel).parts:
        raise SystemExit("NOT_READY: unsafe recovery input path")
    src=old/rel; dst=new/rel
    if src.is_symlink() or dst.is_symlink() or not src.is_file() or not dst.is_file():
        raise SystemExit("NOT_READY: recovery input is absent or symlinked: "+rel)
    def digest(x):
        value=hashlib.sha256()
        with x.open("rb") as stream:
            for block in iter(lambda: stream.read(1024*1024),b""): value.update(block)
        return value.hexdigest()
    actual=digest(dst)
    if (actual!=digest(src) or actual!=row.get("sha256")
            or dst.stat().st_ino==src.stat().st_ino):
        raise SystemExit("NOT_READY: recovery reflink binding differs: "+rel)
print("PASS: atomically materialized isolated recovery input closure")
PY
}
'''
    if anchor not in text:
        raise RuntimeError("function insertion anchor differs")
    text = text.replace(anchor, injected, 1)
    old_checks = '''[[ -d "$JOB_ROOT" && ! -L "$JOB_ROOT" ]] || die 'JOB destination root is absent or symlinked'
[[ -d "$RUNTIME_PARENT" && ! -L "$RUNTIME_PARENT" ]] || die 'runtime final parent is absent or symlinked'
[[ -d "$DST_TXN" && ! -L "$DST_TXN" ]] || die 'fixed destination transaction directory is absent or symlinked'
[[ $(stat -c '%d' "$JOB_ROOT") == $(stat -c '%d' "$RUNTIME_PARENT") ]] || die 'template/runtime final parents are not on one filesystem'
'''
    new_checks = '''check_exact_parent_contracts
[[ -d "$DST_TXN" && ! -L "$DST_TXN" ]] || die 'fixed recovery transaction directory is absent or symlinked'
check_failed_predecessor_preserved
check_input_reuse_receipt
'''
    if old_checks not in text:
        raise RuntimeError("parent check replacement anchor differs")
    text = text.replace(old_checks, new_checks, 1)
    old_mkdir = 'mkdir -m 0700 "$TEMPLATE_STAGE" "$RUNTIME_STAGE"\n'
    new_mkdir = '''mkdir -m 0700 -- "$TEMPLATE_STAGE"
[[ $(stat -c '%u:%g:%a' "$TEMPLATE_STAGE") == '1008:1008:700' ]] ||
  die 'fresh template stage owner/mode differs immediately after creation'
mkdir -m 0700 -- "$RUNTIME_STAGE"
[[ $(stat -c '%u:%g:%a' "$RUNTIME_STAGE") == '1008:1008:700' ]] ||
  die 'fresh runtime stage owner/mode differs immediately after creation'
'''
    if old_mkdir not in text:
        raise RuntimeError("stage mkdir replacement anchor differs")
    text = text.replace(old_mkdir, new_mkdir, 1)
    text = text.replace(
        'printf \'PASS_READ_ONLY: txn=%s gzip_segments=template:4,runtime:1 finals=absent bundle=%s\\n\'',
        'printf \'PASS_RECOVERY_READ_ONLY: txn=%s gzip_segments=template:4,runtime:1 finals=absent bundle=%s\\n\'',
        1,
    )
    text = text.replace(
        'printf \'PASS_PUBLISHED_NOT_LAUNCH_AUTHORITY: txn=%s order=runtime,template exact_final_readback=true\\n\'',
        'printf \'PASS_RECOVERY_PUBLISHED_NOT_LAUNCH_AUTHORITY: txn=%s order=runtime,template exact_final_readback=true old_txn_rerun=false\\n\'',
        1,
    )
    return text.encode("utf-8")


MATERIALIZER = r'''#!/usr/bin/env python3
"""Atomically materialize isolated JOB recovery inputs on node04, no overwrite."""
from __future__ import annotations
import argparse,ctypes,errno,hashlib,json,os,pathlib,socket,stat,subprocess

OLD_TXN="job-v17-relocate-20260803t154500z-a1b2c3d4"
NEW_TXN="job-v18-recovery-20260803t100018z-35f8668f"
JOB=pathlib.Path("/mnt/nvme2n1/anonymous/resource_adaptive_mysql/job")
OLD=JOB/".relocation_v17"/OLD_TXN
PARENT=JOB/".relocation_v18"
NEW=PARENT/NEW_TXN
PREP=PARENT/(".prepare-"+NEW_TXN)
RUNTIME_ID="3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89"
RUNTIME_PARENT=JOB/"runtime_algorithm_c_successor_20260729/runtime"
OLD_RUNTIME_STAGE=RUNTIME_PARENT/(".python311-"+RUNTIME_ID+".stage-"+OLD_TXN)
TEMPLATE_PARTS=[f"archive.tar.gz.part-{i:06d}" for i in range(4)]
RUNTIME_PARTS=["archive.tar.gz.part-000000"]
COMMON=["control.sha256","segments.list","segments.sha256","source-pre.json","source-pre.json.sha256","source-post.json","source-post.json.sha256","source-stability.json","source-stability.json.sha256"]

class Error(RuntimeError): pass
def digest(p):
 d=hashlib.sha256()
 with p.open("rb") as f:
  for b in iter(lambda:f.read(1024*1024),b""): d.update(b)
 return d.hexdigest()
def lexists(p): return os.path.lexists(p)
def exact_dir(p,uid,gid,mode,label):
 if p.is_symlink() or not p.is_dir(): raise Error(label+" absent or symlinked")
 s=p.stat()
 if (s.st_uid,s.st_gid,stat.S_IMODE(s.st_mode))!=(uid,gid,mode): raise Error(label+" owner/mode differs")
def rename_noreplace(src,dst):
 libc=ctypes.CDLL(None,use_errno=True); fn=getattr(libc,"renameat2",None)
 if fn is None: raise Error("renameat2(RENAME_NOREPLACE) unavailable")
 fn.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
 fn.restype=ctypes.c_int
 if fn(-100,os.fsencode(src),-100,os.fsencode(dst),1)!=0:
  n=ctypes.get_errno()
  raise Error("atomic recovery transaction activation failed: "+os.strerror(n))
def input_paths():
 result=[]
 for kind,parts in (("template",TEMPLATE_PARTS),("runtime",RUNTIME_PARTS)):
  for name in COMMON+parts: result.append(pathlib.Path(kind)/name)
 return result
def verify_preconditions():
 if socket.gethostname()!="node04": raise Error("materializer is node04-only")
 if (os.getuid(),os.getgid())!=(1008,1008): raise Error("executor must be uid/gid 1008/1008")
 exact_dir(JOB,1008,1008,0o755,"template parent")
 exact_dir(RUNTIME_PARENT,1008,1008,0o775,"runtime parent")
 exact_dir(PARENT,1008,1008,0o700,"recovery transaction parent")
 if not pathlib.Path("/bin/cp").is_file() or not os.access("/bin/cp",os.X_OK): raise Error("fixed /bin/cp absent or not executable")
 cp_help=subprocess.run(["/bin/cp","--help"],check=True,stdout=subprocess.PIPE,text=True).stdout
 if "--reflink" not in cp_help or "--no-clobber" not in cp_help: raise Error("fixed /bin/cp lacks reflink/no-clobber support")
 exact_dir(OLD_RUNTIME_STAGE,1008,1008,0o700,"preserved failed runtime stage")
 if next(OLD_RUNTIME_STAGE.iterdir(),None) is not None: raise Error("preserved failed runtime stage is not empty")
 for p in (NEW,PREP,JOB/"template",RUNTIME_PARENT/("python311-"+RUNTIME_ID),JOB/(".template.stage-"+NEW_TXN),RUNTIME_PARENT/(".python311-"+RUNTIME_ID+".stage-"+NEW_TXN)):
  if lexists(p): raise Error("fresh no-overwrite path already exists: "+str(p))
 for rel in input_paths():
  p=OLD/rel
  if p.is_symlink() or not p.is_file() or p.stat().st_size==0: raise Error("old verified input absent/empty/symlinked: "+str(rel))
 for kind,parts in (("template",TEMPLATE_PARTS),("runtime",RUNTIME_PARTS)):
  d=OLD/kind
  if d.joinpath("segments.list").read_text().splitlines()!=parts: raise Error(kind+" fixed segment list differs")
  for control in ("segments.sha256","control.sha256"):
   for line in d.joinpath(control).read_text().splitlines():
    expected,name=line.split(None,1); name=name.strip().lstrip("*")
    if digest(d/name)!=expected: raise Error(kind+" checksum differs: "+name)
 required=sum((OLD/rel).stat().st_size for rel in input_paths())+64*1024*1024
 fs=os.statvfs(PARENT);available=fs.f_bavail*fs.f_frsize
 if available<required: raise Error("insufficient space for conservative full-copy fallback")
def activate():
 os.umask(0o077)
 PREP.mkdir(mode=0o700)
 files={}
 for kind in ("template","runtime"): (PREP/kind).mkdir(mode=0o700)
 for rel in input_paths():
  src=OLD/rel; dst=PREP/rel
  subprocess.run(["/bin/cp","--reflink=auto","--preserve=all","--no-clobber","--",str(src),str(dst)],check=True)
  if src.stat().st_ino==dst.stat().st_ino: raise Error("recovery input unexpectedly shares inode: "+str(rel))
  value=digest(dst)
  if value!=digest(src): raise Error("recovery input content differs: "+str(rel))
  files[str(rel)]={"sha256":value,"source_inode":src.stat().st_ino,"destination_inode":dst.stat().st_ino}
 receipt={"schema_version":"mysql_job_v18_recovery_input_reuse_receipt_v1","status":"PASS_ATOMIC_NEW_TRANSACTION_INPUT_REUSE","copy_method":"cp_reflink_auto_unique_inode","reflink_preferred_full_copy_fallback_allowed":True,"old_transaction":OLD_TXN,"new_transaction":NEW_TXN,"old_failed_transaction_rerun_prohibited":True,"new_receipts_isolated_from_old_transaction":True,"old_failed_runtime_stage_preserved":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"accepted_rounds_credit":0,"files":files}
 payload=(json.dumps(receipt,indent=2,sort_keys=True)+"\n").encode()
 rp=PREP/"input_reuse_receipt.json"; rp.write_bytes(payload); rp.chmod(0o400)
 side=PREP/"input_reuse_receipt.json.sha256"; side.write_text(hashlib.sha256(payload).hexdigest()+"  input_reuse_receipt.json\n"); side.chmod(0o400)
 fd=os.open(PARENT,os.O_RDONLY|os.O_DIRECTORY)
 try: os.fsync(fd);rename_noreplace(PREP,NEW);os.fsync(fd)
 finally: os.close(fd)
 print(json.dumps({"status":"PASS_ATOMIC_NEW_TRANSACTION_INPUT_REUSE","transaction":NEW_TXN,"path":str(NEW)},sort_keys=True))
def main():
 p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument("--check",action="store_true");g.add_argument("--execute",metavar="EXACT_NEW_TRANSACTION");a=p.parse_args()
 if a.execute is not None and a.execute!=NEW_TXN: raise Error("exact new transaction id required")
 verify_preconditions()
 if a.check: print(json.dumps({"status":"PASS_RECOVERY_INPUT_MATERIALIZER_READ_ONLY","transaction":NEW_TXN},sort_keys=True))
 else: activate()
if __name__=="__main__":
 try: main()
 except (Error,OSError,ValueError,subprocess.SubprocessError) as e: raise SystemExit("NOT_READY: "+str(e))
'''.encode("utf-8")


TEST = r'''#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C
B=${1:?bundle path required}
S="$B/transaction/mysql_job_v18_node04_gzip_destination_recovery_20260803t100018z_35f8668f.sh"
M="$B/transaction/mysql_job_v18_recovery_input_materializer_v1.py"
fail(){ printf 'FAIL: %s\n' "$*" >&2; exit 1; }
bash -n "$S"
"$S" --help >/dev/null
python3 -B - "$M" <<'PY'
import pathlib,sys
compile(pathlib.Path(sys.argv[1]).read_bytes(),sys.argv[1],"exec")
PY
rg -F "readonly OLD_TXN='job-v17-relocate-20260803t154500z-a1b2c3d4'" "$S" >/dev/null
rg -F "readonly TXN='job-v18-recovery-20260803t100018z-35f8668f'" "$S" >/dev/null
rg -F 'readonly DST_TXN="${JOB_ROOT}/.relocation_v18/${TXN}"' "$S" >/dev/null
rg -F "post.get(\"logical_unique_file_bytes\") != 1077311137" "$S" >/dev/null
rg -F 'check_exact_parent_contracts' "$S" >/dev/null
rg -F "template_stat == '1008:1008:755'" "$S" >/dev/null || rg -F "template_stat\" == '1008:1008:755'" "$S" >/dev/null
rg -F "runtime_stat == '1008:1008:775'" "$S" >/dev/null || rg -F "runtime_stat\" == '1008:1008:775'" "$S" >/dev/null
rg -F 'mkdir -m 0700 -- "$TEMPLATE_STAGE"' "$S" >/dev/null
rg -F 'mkdir -m 0700 -- "$RUNTIME_STAGE"' "$S" >/dev/null
! rg -F 'mkdir -m 0700 "$TEMPLATE_STAGE" "$RUNTIME_STAGE"' "$S" >/dev/null
p=$(rg -n '^check_exact_parent_contracts$' "$S" | tail -1 | cut -d: -f1)
t=$(rg -n '^mkdir -m 0700 -- "\$TEMPLATE_STAGE"$' "$S" | cut -d: -f1)
r=$(rg -n '^mkdir -m 0700 -- "\$RUNTIME_STAGE"$' "$S" | cut -d: -f1)
[[ $p -lt $t && $t -lt $r ]] || fail 'parent gate or serial mkdir order differs'
rg -F 'cp_reflink_auto_unique_inode' "$M" >/dev/null
rg -F 'rename_noreplace(PREP,NEW)' "$M" >/dev/null
rg -F 'new_receipts_isolated_from_old_transaction' "$M" >/dev/null
if rg -n '(^|[;&|[:space:]])(rm|rmdir|unlink|shred|truncate|mv)([[:space:]]|$)' "$S" "$M"; then fail 'delete/replace command present'; fi
f=$(mktemp -d /tmp/mysql-job-v18-recovery-fixture.XXXXXX)
trap 'rm -rf -- "$f"' EXIT
printf 'reflink fixture\n' >"$f/source"
/bin/cp --reflink=auto --preserve=all --no-clobber -- "$f/source" "$f/destination"
cmp "$f/source" "$f/destination"
[[ $(stat -c %i "$f/source") != $(stat -c %i "$f/destination") ]] || fail 'reflink fixture shares inode'
mkdir "$f/blocked" "$f/writable";chmod 0500 "$f/blocked"
if mkdir "$f/blocked/template-stage" 2>/dev/null; then fail 'blocked first serial mkdir succeeded'; fi
[[ ! -e "$f/writable/runtime-stage" ]] || fail 'second stage appeared after first serial failure'
printf 'PASS_OFFLINE_RECOVERY_FIXTURE: bash_n python_compile new_txn old_txn_prohibited isolated_receipts exact_parent_gate serial_fail_fast reflink_unique_inode rename_noreplace no_delete\n'
'''.encode("utf-8")


def main() -> None:
    script = recovery_script()
    plan = {
        "schema_version": "mysql_job_v18_recovery_successor_plan_v1",
        "status": "PASS_IMMUTABLE_RECOVERY_PLAN_NOT_EXECUTED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "old_failed_transaction": OLD_TXN,
        "old_failed_transaction_rerun_prohibited": True,
        "old_empty_runtime_stage_must_be_preserved": True,
        "new_transaction": NEW_TXN,
        "new_transaction_parent": "/mnt/nvme2n1/anonymous/resource_adaptive_mysql/job/.relocation_v18",
        "input_reuse": "cp_--reflink=auto_unique_inode_then_renameat2_RENAME_NOREPLACE",
        "reflink_preferred_full_copy_fallback_allowed": True,
        "hardlink_alternative": "not_recommended_old_inputs_are_owner_writable_and_inode_aliasing_weakens_isolation",
        "invalidated_local_intermediate_bundle_id": "991386f577fcb786fad4a24c3e3d21869d8efaa16c88b296fe8b2ef3f6d9e72c",
        "invalidated_local_intermediate_bundle_id_2": "6a2fde5f8e0c1d73bdc81628c235a290b210e1563154e46fc7a3b3c45b5f25c0",
        "invalidated_local_intermediate_bundle_id_3": "4a6895617f2c5879ea0d3db722772124431d9af6223c3cd98aab27a5f5ab85fc",
        "new_receipts_isolated_from_old_transaction": True,
        "template_parent": {"path": "/mnt/nvme2n1/anonymous/resource_adaptive_mysql/job", "uid": 1008, "gid": 1008, "mode": "0755", "executor_writable": True},
        "runtime_parent": {"path": "/mnt/nvme2n1/anonymous/resource_adaptive_mysql/job/runtime_algorithm_c_successor_20260729/runtime", "uid": 1008, "gid": 1008, "mode": "0775", "executor_writable": True},
        "recovery_transaction_parent": {"uid": 1008, "gid": 1008, "mode": "0700"},
        "logical_unique_file_bytes": 1077311137,
        "stage_creation": "serial_template_then_runtime_fail_fast",
        "publication": "runtime_then_template_each_RENAME_NOREPLACE_parent_fsync",
        "remote_actions": 0,
        "accepted_rounds_credit": 0,
    }
    content = {
        f"transaction/{SCRIPT_NAME}": script,
        "transaction/mysql_job_v18_recovery_input_materializer_v1.py": MATERIALIZER,
        "plan/recovery_plan.json": canonical(plan),
        "tests/test_mysql_job_v18_recovery_successor_v1.sh": TEST,
        "builder/build_mysql_job_v18_recovery_successor_v1.py": Path(__file__).read_bytes(),
    }
    files = {name: sha(data) for name, data in sorted(content.items())}
    identity = dict(plan)
    identity.update({"files": files, "script_sha256": sha(script),
                     "materializer_sha256": sha(MATERIALIZER), "immutable": True})
    bundle_id = sha(canonical(identity))
    manifest = dict(identity, bundle_id=bundle_id)
    manifest_bytes = canonical(manifest)
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / bundle_id
    if target.exists():
        if hashlib.sha256((target / "bundle_manifest.json").read_bytes()).hexdigest() != sha(manifest_bytes):
            raise SystemExit("existing recovery bundle differs")
        print(json.dumps({"bundle": str(target), "bundle_id": bundle_id,
                          "manifest_sha256": sha(manifest_bytes), "script_sha256": sha(script)}, sort_keys=True))
        return
    stage = Path(tempfile.mkdtemp(prefix=".mysql_job_v18_recovery.", dir=OUT))
    for relative, data in content.items():
        path = stage / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o555 if relative.endswith((".sh", ".py")) else 0o444)
    (stage / "bundle_manifest.json").write_bytes(manifest_bytes)
    (stage / "bundle_manifest.json").chmod(0o444)
    sidecar = sha(manifest_bytes) + "  bundle_manifest.json\n"
    (stage / "bundle_manifest.json.sha256").write_text(sidecar, encoding="ascii")
    (stage / "bundle_manifest.json.sha256").chmod(0o444)
    for directory, dirs, _ in os.walk(stage, topdown=False):
        for name in dirs:
            os.chmod(Path(directory) / name, 0o555)
    stage.chmod(0o555)
    os.rename(stage, target)
    print(json.dumps({"bundle": str(target), "bundle_id": bundle_id,
                      "manifest_sha256": sha(manifest_bytes), "script_sha256": sha(script)}, sort_keys=True))


if __name__ == "__main__":
    main()
