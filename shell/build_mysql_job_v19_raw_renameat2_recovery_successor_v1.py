#!/usr/bin/env python3
"""Build the immutable JOB v19 raw-renameat2 recovery successor."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
V18_BUNDLE = ROOT / "artifacts/mysql_job_v18_recovery_successor_v1/f412531163b4f122d88b4d31f0c42d7b2870cddf9f625a3187a795db2c16db31"
V18_SCRIPT = V18_BUNDLE / "transaction/mysql_job_v18_node04_gzip_destination_recovery_20260803t100018z_35f8668f.sh"
OLD_PUBLISHER = ROOT / "artifacts/mysql_job_node04_disjoint_successor_v18/44c6bda398c8eee5ba5fdf4bef95947cb497b27a3a161448fc084e344baa40ea/qualification/mysql_job_v17_atomic_tree_publish_v1.py"
OUT = ROOT / "artifacts/mysql_job_v19_raw_renameat2_recovery_successor_v1"
V17_TXN = "job-v17-relocate-20260803t154500z-a1b2c3d4"
V18_TXN = "job-v18-recovery-20260803t100018z-35f8668f"
V19_TXN = "job-v19-recovery-20260803t102324z-32e5ff1a"
SCRIPT_NAME = "mysql_job_v19_node04_raw_renameat2_recovery_20260803t102324z_32e5ff1a.sh"
PUBLISHER_NAME = "mysql_job_v19_raw_syscall_atomic_tree_publish_v1.py"
MATERIALIZER_NAME = "mysql_job_v19_recovery_input_materializer_v1.py"

V18_PREP_RECEIPT_SHA = "0b55041736706ad6ec01d2bea09a81d477e4a6a361016e92ef33f57ed21ef5f8"
V18_PREP_SIDECAR_FILE_SHA = "1a4e128451e6ae56831db849069f77dd6978a35fa9fbe74b1ec77ee89b9f778c"
V18_PREP_KEY_LIST_SHA = "e4082666e8b7cda06727ef833ce20b5495c34b5b170e947ec8197accc716f232"
V18_PREP_CLOSURE_SHA = "dfaf1e8656f755f29b3f09bdce58b6141b685689ea4f59e86ca861b44fc0641b"


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def recovery_script() -> bytes:
    text = V18_SCRIPT.read_text(encoding="utf-8")
    text = text.replace(
        "# Immutable node04 JOB recovery transaction. The failed predecessor txn is\n"
        "# never rerun and its empty runtime stage is deliberately retained. Inputs\n"
        "# are atomically materialized in a separate relocation_v18 transaction.\n",
        "# Immutable node04 JOB v19 recovery. v17 and v18 are never rerun; the\n"
        "# complete unactivated v18 PREP is retained and reflinked into a new v19\n"
        "# transaction activated/published only through raw syscall 316.\n",
        1,
    )
    text = text.replace(
        f"readonly OLD_TXN='{V17_TXN}'\nreadonly TXN='{V18_TXN}'\n",
        f"readonly OLD_TXN='{V17_TXN}'\n"
        f"readonly FAILED_V18_TXN='{V18_TXN}'\n"
        f"readonly TXN='{V19_TXN}'\n",
        1,
    )
    text = text.replace(
        'readonly OLD_DST_TXN="${JOB_ROOT}/.relocation_v17/${OLD_TXN}"\n'
        'readonly DST_TXN="${JOB_ROOT}/.relocation_v18/${TXN}"\n',
        'readonly OLD_DST_TXN="${JOB_ROOT}/.relocation_v17/${OLD_TXN}"\n'
        'readonly V18_PREP="${JOB_ROOT}/.relocation_v18/.prepare-${FAILED_V18_TXN}"\n'
        'readonly V18_VISIBLE="${JOB_ROOT}/.relocation_v18/${FAILED_V18_TXN}"\n'
        'readonly DST_TXN="${JOB_ROOT}/.relocation_v19/${TXN}"\n',
        1,
    )
    text = text.replace(
        'readonly PUBLISHER="${BUNDLE}/qualification/mysql_job_v17_atomic_tree_publish_v1.py"\n',
        'readonly SELF_SCRIPT="$(readlink -f -- "$0")"\n'
        'readonly RECOVERY_BUNDLE="$(cd -- "$(dirname -- "$SELF_SCRIPT")/.." && pwd -P)"\n'
        f'readonly PUBLISHER="${{RECOVERY_BUNDLE}}/qualification/{PUBLISHER_NAME}"\n',
        1,
    )
    start = text.index("check_input_reuse_receipt() {")
    end = text.index("check_bundle_closure() {", start)
    replacement = r'''check_input_reuse_receipt() {
  require_evidence_pair "$DST_TXN" input_reuse_receipt.json
  "$BOOTSTRAP_PY" -B - "$INPUT_REUSE_RECEIPT" "$V18_PREP" "$DST_TXN" <<'PY'
import hashlib,json,os,pathlib,stat,sys
p=pathlib.Path(sys.argv[1]); source=pathlib.Path(sys.argv[2]); new=pathlib.Path(sys.argv[3])
R="0b55041736706ad6ec01d2bea09a81d477e4a6a361016e92ef33f57ed21ef5f8"
S="1a4e128451e6ae56831db849069f77dd6978a35fa9fbe74b1ec77ee89b9f778c"
K="e4082666e8b7cda06727ef833ce20b5495c34b5b170e947ec8197accc716f232"
C="dfaf1e8656f755f29b3f09bdce58b6141b685689ea4f59e86ca861b44fc0641b"
def digest(x):
 h=hashlib.sha256()
 with x.open("rb") as f:
  for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
 return h.hexdigest()
d=json.loads(p.read_text(encoding="utf-8"))
if (d.get("schema_version")!="mysql_job_v19_recovery_input_reuse_receipt_v1"
 or d.get("status")!="PASS_ATOMIC_V19_INPUT_REUSE_RAW_SYSCALL_RENAME_NOREPLACE"
 or d.get("copy_method")!="cp_reflink_always_unique_inode"
 or d.get("source_transaction")!="job-v18-recovery-20260803t100018z-35f8668f"
 or d.get("new_transaction")!=new.name or d.get("raw_syscall_number")!=316
 or d.get("precopy_probe_errno")!=2 or d.get("v18_prep_receipt_sha256")!=R
 or d.get("v18_prep_sidecar_file_sha256")!=S
 or d.get("v18_files_sorted_key_list_sha256")!=K
 or d.get("v18_files_sorted_path_sha256_closure_sha256")!=C
 or d.get("v18_prep_preserved") is not True
 or d.get("old_failed_transaction_rerun_prohibited") is not True
 or d.get("new_receipts_isolated_from_old_transaction") is not True):
 raise SystemExit("NOT_READY: v19 recovery input receipt authority differs")
files=d.get("files")
if not isinstance(files,dict) or len(files)!=23:
 raise SystemExit("NOT_READY: v19 input closure must contain exactly 23 inputs")
for rel,row in sorted(files.items()):
 q=pathlib.PurePosixPath(rel)
 if rel.startswith("/") or ".." in q.parts: raise SystemExit("NOT_READY: unsafe v19 input path")
 src=source/q; dst=new/q
 if src.is_symlink() or dst.is_symlink() or not src.is_file() or not dst.is_file():
  raise SystemExit("NOT_READY: v19 input absent or symlinked: "+rel)
 value=digest(dst)
 if value!=digest(src) or value!=row.get("sha256") or src.stat().st_ino==dst.stat().st_ino:
  raise SystemExit("NOT_READY: v19 reflink binding differs: "+rel)
pred=new/"predecessor_v18/input_reuse_receipt.json"
side=pred.with_name(pred.name+".sha256")
if digest(pred)!=R or digest(side)!=S:
 raise SystemExit("NOT_READY: copied v18 PREP evidence identity differs")
rows=[];actual_files=set();actual_dirs=set()
s=source.stat();rows.append({"path":".","type":"directory","uid":s.st_uid,"gid":s.st_gid,"mode":format(stat.S_IMODE(s.st_mode),"04o")})
for root,dirs,names in os.walk(source):
 for name in dirs:
  x=pathlib.Path(root)/name;rel=str(x.relative_to(source));actual_dirs.add(rel);s=x.stat()
  if x.is_symlink(): raise SystemExit("NOT_READY: symlinked directory in v18 PREP")
  rows.append({"path":rel,"type":"directory","uid":s.st_uid,"gid":s.st_gid,"mode":format(stat.S_IMODE(s.st_mode),"04o")})
 for name in names:
  x=pathlib.Path(root)/name;rel=str(x.relative_to(source));actual_files.add(rel);s=x.stat()
  if x.is_symlink() or not x.is_file(): raise SystemExit("NOT_READY: non-regular member in v18 PREP")
  rows.append({"path":rel,"type":"regular","sha256":digest(x),"size":s.st_size,"uid":s.st_uid,"gid":s.st_gid,"mode":format(stat.S_IMODE(s.st_mode),"04o")})
if actual_dirs!={"template","runtime"} or len(actual_files)!=25:
 raise SystemExit("NOT_READY: v18 PREP directory/file closure changed")
identity=hashlib.sha256((json.dumps(sorted(rows,key=lambda x:x["path"]),sort_keys=True,separators=(",",":"))+"\n").encode()).hexdigest()
if d.get("v18_prepared_tree_identity_sha256")!=identity:
 raise SystemExit("NOT_READY: v18 prepared-tree identity changed")
print("PASS: v19 input closure binds and preserves exact failed v18 PREP")
PY
}

check_raw_renameat2_capability() {
  "$BOOTSTRAP_PY" -B "$PUBLISHER" --probe-kind template --transaction-id "$TXN"
  "$BOOTSTRAP_PY" -B "$PUBLISHER" --probe-kind runtime --transaction-id "$TXN"
}

check_recovery_bundle_closure() {
  "$BOOTSTRAP_PY" -B - "$RECOVERY_BUNDLE" <<'PY'
import hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1]); mp=root/"bundle_manifest.json"
def digest(p):
 h=hashlib.sha256()
 with p.open("rb") as f:
  for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
 return h.hexdigest()
if root.is_symlink() or not root.is_dir() or mp.is_symlink() or not mp.is_file():
 raise SystemExit("NOT_READY: v19 recovery bundle absent or symlinked")
m=json.loads(mp.read_text(encoding="utf-8")); actual=digest(mp)
side=mp.with_name(mp.name+".sha256")
if (m.get("schema_version")!="mysql_job_v19_raw_renameat2_recovery_successor_plan_v1"
 or m.get("bundle_id")!=root.name or side.read_text(encoding="ascii")!=actual+"  bundle_manifest.json\n"
 or m.get("formal_scope")!="mysql_only" or m.get("cross_engine_comparison_prohibited") is not True):
 raise SystemExit("NOT_READY: v19 recovery bundle identity differs")
files=m.get("files")
if not isinstance(files,dict) or not files: raise SystemExit("NOT_READY: v19 recovery closure absent")
rr=root.resolve(strict=True)
for rel,expected in sorted(files.items()):
 q=pathlib.PurePosixPath(rel); p=root/q
 if rel.startswith("/") or ".." in q.parts or p.is_symlink() or not p.is_file() or rr not in p.resolve(strict=True).parents or digest(p)!=expected:
  raise SystemExit("NOT_READY: v19 recovery closure differs: "+rel)
print("PASS: v19 raw-syscall recovery bundle closure")
PY
}

'''
    text = text[:start] + replacement + text[end:]
    text = text.replace(
        "  [[ \"$TXN\" != \"$OLD_TXN\" ]] || die 'failed predecessor transaction rerun is prohibited'\n",
        "  [[ \"$TXN\" != \"$OLD_TXN\" && \"$TXN\" != \"$FAILED_V18_TXN\" ]] ||\n"
        "    die 'v17/v18 failed predecessor transaction rerun is prohibited'\n"
        "  [[ -d \"$V18_PREP\" && ! -L \"$V18_PREP\" ]] || die 'failed v18 PREP was not preserved'\n"
        "  [[ $(stat -c '%u:%g:%a' \"$V18_PREP\") == '1008:1008:700' ]] || die 'failed v18 PREP owner/mode differs'\n"
        "  require_absent \"$V18_VISIBLE\"\n",
        1,
    )
    text = text.replace("check_bundle_closure\ncheck_fresh_outputs\n",
                        "check_bundle_closure\ncheck_recovery_bundle_closure\ncheck_raw_renameat2_capability\ncheck_fresh_outputs\n", 1)
    text = text.replace("PASS_RECOVERY_READ_ONLY:", "PASS_V19_RAW_SYSCALL_RECOVERY_READ_ONLY:", 1)
    text = text.replace("PASS_RECOVERY_PUBLISHED_NOT_LAUNCH_AUTHORITY:",
                        "PASS_V19_RAW_SYSCALL_RECOVERY_PUBLISHED_NOT_LAUNCH_AUTHORITY:", 1)
    return text.encode("utf-8")


def publisher() -> bytes:
    text = OLD_PUBLISHER.read_text(encoding="utf-8")
    start = text.index("def rename_noreplace(source, destination):")
    end = text.index("\n\ndef publish(args):", start)
    raw = r'''def raw_renameat2(source, destination):
    if os.uname().machine != "x86_64" or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise PublishError("sealed raw renameat2 syscall 316 requires x86_64")
    libc = ctypes.CDLL(None, use_errno=True)
    syscall = libc.syscall
    syscall.argtypes = [ctypes.c_long, ctypes.c_int, ctypes.c_char_p,
                        ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    syscall.restype = ctypes.c_long
    result = syscall(316, -100, os.fsencode(source), -100,
                     os.fsencode(destination), 1)
    return result, ctypes.get_errno()


def capability_probe(parent, transaction_id, kind):
    source = parent / (".renameat2-probe-absent-source-%s-%s" % (transaction_id, kind))
    destination = parent / (".renameat2-probe-absent-destination-%s-%s" % (transaction_id, kind))
    if os.path.lexists(source) or os.path.lexists(destination):
        raise PublishError("guaranteed-absent raw-syscall probe path exists")
    result, number = raw_renameat2(source, destination)
    if result != -1 or number != errno.ENOENT:
        if number in (errno.ENOSYS, errno.EINVAL):
            raise PublishError("raw syscall 316 RENAME_NOREPLACE unsupported: " + os.strerror(number))
        raise PublishError("raw syscall 316 capability probe did not return ENOENT: " + os.strerror(number))
    if os.path.lexists(source) or os.path.lexists(destination):
        raise PublishError("raw-syscall capability probe mutated filesystem")
    return {"syscall_number": 316, "flags": "RENAME_NOREPLACE", "errno": number}


def rename_noreplace(source, destination):
    result, number = raw_renameat2(source, destination)
    if result != 0:
        if number == errno.EEXIST:
            raise PublishError("final target appeared; no overwrite performed")
        if number in (errno.ENOSYS, errno.EINVAL):
            raise PublishError("raw syscall 316 RENAME_NOREPLACE unsupported: " + os.strerror(number))
        raise PublishError("raw syscall 316 renameat2 failed: " + os.strerror(number))
'''
    text = text[:start] + raw + text[end:]
    text = text.replace(
        "    if os.path.lexists(final):\n        raise PublishError(\"final target already exists; no overwrite allowed\")\n",
        "    if os.path.lexists(final):\n        raise PublishError(\"final target already exists; no overwrite allowed\")\n"
        "    probe = capability_probe(final.parent, args.transaction_id, args.kind)\n",
        1,
    )
    text = text.replace(
        '"schema_version": "mysql_job_v17_atomic_tree_publication_receipt_v1",\n'
        '        "status": "PASS_ATOMIC_RENAME_NOREPLACE_PUBLISHED",',
        '"schema_version": "mysql_job_v19_raw_syscall_atomic_tree_publication_receipt_v1",\n'
        '        "status": "PASS_ATOMIC_RAW_SYSCALL_RENAME_NOREPLACE_PUBLISHED",\n'
        '        "raw_syscall_probe": probe,',
        1,
    )
    old_parser = '''    parser.add_argument("--kind", choices=sorted(ALLOWED), required=True)
    parser.add_argument("--transaction-id", required=True)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--final", type=Path, required=True)
    parser.add_argument("--source-pre-inventory", type=Path, required=True)
    parser.add_argument("--source-inventory", type=Path, required=True)
    parser.add_argument("--stage-inventory", type=Path, required=True)
    parser.add_argument("--source-stability-comparison", type=Path, required=True)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        raise PublishError("publication receipt output already exists")
    result = publish(args)
    write_receipt(args.output, result)
'''
    new_parser = '''    parser.add_argument("--kind", choices=sorted(ALLOWED))
    parser.add_argument("--probe-kind", choices=sorted(ALLOWED))
    parser.add_argument("--transaction-id", required=True)
    parser.add_argument("--stage", type=Path)
    parser.add_argument("--final", type=Path)
    parser.add_argument("--source-pre-inventory", type=Path)
    parser.add_argument("--source-inventory", type=Path)
    parser.add_argument("--stage-inventory", type=Path)
    parser.add_argument("--source-stability-comparison", type=Path)
    parser.add_argument("--comparison", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.probe_kind:
        if args.kind or any((args.stage,args.final,args.source_pre_inventory,args.source_inventory,
                             args.stage_inventory,args.source_stability_comparison,args.comparison,args.output)):
            raise PublishError("probe mode accepts only probe kind and transaction id")
        final = Path(ALLOWED[args.probe_kind]["final"])
        if final.parent.is_symlink() or not final.parent.is_dir():
            raise PublishError("probe final parent absent or symlinked")
        result = capability_probe(final.parent, args.transaction_id, args.probe_kind)
        print(json.dumps({"status":"PASS_RAW_SYSCALL_RENAMEAT2_CAPABILITY_PROBE", **result}, sort_keys=True))
        return
    if not args.kind or any(x is None for x in (args.stage,args.final,args.source_pre_inventory,
            args.source_inventory,args.stage_inventory,args.source_stability_comparison,args.comparison,args.output)):
        raise PublishError("complete publish arguments are required")
    if args.output.exists() or args.output.is_symlink():
        raise PublishError("publication receipt output already exists")
    result = publish(args)
    write_receipt(args.output, result)
'''
    if old_parser not in text:
        raise RuntimeError("publisher parser anchor differs")
    text = text.replace(old_parser, new_parser, 1)
    return text.encode("utf-8")


MATERIALIZER = r'''#!/usr/bin/env python3
"""Reflink exact failed-v18 PREP inputs and atomically activate fresh v19."""
from __future__ import annotations
import argparse,ctypes,errno,hashlib,json,os,pathlib,socket,stat,subprocess

V18_TXN="job-v18-recovery-20260803t100018z-35f8668f"
V19_TXN="job-v19-recovery-20260803t102324z-32e5ff1a"
JOB=pathlib.Path("/mnt/nvme2n1/anonymous/resource_adaptive_mysql/job")
V18_PARENT=JOB/".relocation_v18"
V18_PREP=V18_PARENT/(".prepare-"+V18_TXN)
V18_VISIBLE=V18_PARENT/V18_TXN
PARENT=JOB/".relocation_v19"
PREP=PARENT/(".prepare-"+V19_TXN)
NEW=PARENT/V19_TXN
RUNTIME_ID="3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89"
RUNTIME_PARENT=JOB/"runtime_algorithm_c_successor_20260729/runtime"
V18_RECEIPT_SHA="0b55041736706ad6ec01d2bea09a81d477e4a6a361016e92ef33f57ed21ef5f8"
V18_SIDECAR_FILE_SHA="1a4e128451e6ae56831db849069f77dd6978a35fa9fbe74b1ec77ee89b9f778c"
V18_KEY_LIST_SHA="e4082666e8b7cda06727ef833ce20b5495c34b5b170e947ec8197accc716f232"
V18_CLOSURE_SHA="dfaf1e8656f755f29b3f09bdce58b6141b685689ea4f59e86ca861b44fc0641b"
FAILURE="NOT_READY: renameat2(RENAME_NOREPLACE) unavailable"
PARTS={"template":[f"archive.tar.gz.part-{i:06d}" for i in range(4)],"runtime":["archive.tar.gz.part-000000"]}
COMMON=["control.sha256","segments.list","segments.sha256","source-pre.json","source-pre.json.sha256","source-post.json","source-post.json.sha256","source-stability.json","source-stability.json.sha256"]

class Error(RuntimeError): pass
def lexists(p): return os.path.lexists(p)
def digest(p):
 h=hashlib.sha256()
 with p.open("rb") as f:
  for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
 return h.hexdigest()
def exact_dir(p,uid,gid,mode,label):
 if p.is_symlink() or not p.is_dir(): raise Error(label+" absent or symlinked")
 s=p.stat()
 if (s.st_uid,s.st_gid,stat.S_IMODE(s.st_mode))!=(uid,gid,mode): raise Error(label+" owner/mode differs")
def paths():
 return [pathlib.Path(kind)/name for kind in ("template","runtime") for name in COMMON+PARTS[kind]]
def raw_renameat2(src,dst):
 if os.uname().machine!="x86_64" or ctypes.sizeof(ctypes.c_void_p)!=8: raise Error("syscall 316 requires x86_64")
 libc=ctypes.CDLL(None,use_errno=True); fn=libc.syscall
 fn.argtypes=[ctypes.c_long,ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint];fn.restype=ctypes.c_long
 result=fn(316,-100,os.fsencode(src),-100,os.fsencode(dst),1)
 return result,ctypes.get_errno()
def probe():
 src=PARENT/(".renameat2-probe-absent-source-"+V19_TXN);dst=PARENT/(".renameat2-probe-absent-destination-"+V19_TXN)
 if lexists(src) or lexists(dst): raise Error("guaranteed-absent syscall probe path exists")
 result,number=raw_renameat2(src,dst)
 if result!=-1 or number!=errno.ENOENT:
  if number in (errno.ENOSYS,errno.EINVAL): raise Error("raw syscall 316 RENAME_NOREPLACE unsupported: "+os.strerror(number))
  raise Error("raw syscall capability probe did not return ENOENT: "+os.strerror(number))
 if lexists(src) or lexists(dst): raise Error("raw syscall probe mutated filesystem")
 return number
def v18_authority():
 rp=V18_PREP/"input_reuse_receipt.json";sp=V18_PREP/"input_reuse_receipt.json.sha256"
 if digest(rp)!=V18_RECEIPT_SHA or digest(sp)!=V18_SIDECAR_FILE_SHA: raise Error("exact v18 PREP receipt pair identity differs")
 if sp.read_text(encoding="ascii")!=V18_RECEIPT_SHA+"  input_reuse_receipt.json\n": raise Error("v18 receipt sidecar content differs")
 d=json.loads(rp.read_text(encoding="utf-8"));files=d.get("files")
 if d.get("status")!="PASS_ATOMIC_NEW_TRANSACTION_INPUT_REUSE" or not isinstance(files,dict) or len(files)!=23: raise Error("v18 PREP receipt authority differs")
 key_payload=("\n".join(sorted(files))+"\n").encode()
 closure_payload=("\n".join(k+" "+files[k].get("sha256","") for k in sorted(files))+"\n").encode()
 if hashlib.sha256(key_payload).hexdigest()!=V18_KEY_LIST_SHA: raise Error("v18 files sorted key-list identity differs")
 if hashlib.sha256(closure_payload).hexdigest()!=V18_CLOSURE_SHA: raise Error("v18 sorted path/hash closure identity differs")
 expected=set(str(x) for x in paths())|{"input_reuse_receipt.json","input_reuse_receipt.json.sha256"}
 actual=set();actual_dirs=set();rows=[]
 s=V18_PREP.stat();rows.append({"path":".","type":"directory","uid":s.st_uid,"gid":s.st_gid,"mode":format(stat.S_IMODE(s.st_mode),"04o")})
 for root,dirs,names in os.walk(V18_PREP):
  for name in dirs:
   q=pathlib.Path(root)/name
   if q.is_symlink(): raise Error("symlink in v18 PREP")
   rel=str(q.relative_to(V18_PREP));actual_dirs.add(rel);s=q.stat();rows.append({"path":rel,"type":"directory","uid":s.st_uid,"gid":s.st_gid,"mode":format(stat.S_IMODE(s.st_mode),"04o")})
  for name in names:
   q=pathlib.Path(root)/name;rel=str(q.relative_to(V18_PREP))
   if q.is_symlink() or not q.is_file(): raise Error("non-regular member in v18 PREP: "+rel)
   actual.add(rel);s=q.stat();rows.append({"path":rel,"type":"regular","sha256":digest(q),"size":s.st_size,"uid":s.st_uid,"gid":s.st_gid,"mode":format(stat.S_IMODE(s.st_mode),"04o")})
 if actual!=expected or actual_dirs!={"template","runtime"} or len(actual)!=25: raise Error("v18 PREP exact directory/25-file closure differs")
 identity=hashlib.sha256((json.dumps(sorted(rows,key=lambda x:x["path"]),sort_keys=True,separators=(",",":"))+"\n").encode()).hexdigest()
 return d,identity
def verify():
 if socket.gethostname()!="node04": raise Error("materializer is node04-only")
 if (os.getuid(),os.getgid())!=(1008,1008): raise Error("executor must be uid/gid 1008/1008")
 exact_dir(JOB,1008,1008,0o755,"template parent");exact_dir(RUNTIME_PARENT,1008,1008,0o775,"runtime parent")
 exact_dir(V18_PARENT,1008,1008,0o700,"v18 transaction parent");exact_dir(V18_PREP,1008,1008,0o700,"preserved failed v18 PREP")
 exact_dir(PARENT,1008,1008,0o700,"v19 transaction parent")
 if V18_PREP.stat().st_dev!=PARENT.stat().st_dev: raise Error("v18 PREP and v19 parent are not on one filesystem")
 if lexists(V18_VISIBLE): raise Error("failed v18 transaction unexpectedly activated")
 for p in (PREP,NEW,JOB/"template",RUNTIME_PARENT/("python311-"+RUNTIME_ID),JOB/(".template.stage-"+V19_TXN),RUNTIME_PARENT/(".python311-"+RUNTIME_ID+".stage-"+V19_TXN)):
  if lexists(p): raise Error("fresh no-overwrite path already exists: "+str(p))
 if not pathlib.Path("/bin/cp").is_file() or not os.access("/bin/cp",os.X_OK): raise Error("fixed /bin/cp absent")
 helptext=subprocess.run(["/bin/cp","--help"],check=True,stdout=subprocess.PIPE,text=True).stdout
 if "--reflink" not in helptext or "--no-clobber" not in helptext: raise Error("fixed /bin/cp lacks required flags")
 return v18_authority()
def execute(source_receipt,identity,probe_errno):
 os.umask(0o077);PREP.mkdir(mode=0o700);(PREP/"template").mkdir(mode=0o700);(PREP/"runtime").mkdir(mode=0o700);(PREP/"predecessor_v18").mkdir(mode=0o700)
 files={}
 for rel in paths():
  src=V18_PREP/rel;dst=PREP/rel
  subprocess.run(["/bin/cp","--reflink=always","--preserve=all","--no-clobber","--",str(src),str(dst)],check=True)
  if src.stat().st_ino==dst.stat().st_ino or digest(src)!=digest(dst): raise Error("v19 reflink identity differs: "+str(rel))
  s=src.stat();d=dst.stat();files[str(rel)]={"sha256":digest(dst),"size":d.st_size,"source_inode":s.st_ino,"destination_inode":d.st_ino,"source_blocks":s.st_blocks,"destination_blocks":d.st_blocks}
 for name in ("input_reuse_receipt.json","input_reuse_receipt.json.sha256"):
  src=V18_PREP/name;dst=PREP/"predecessor_v18"/name
  subprocess.run(["/bin/cp","--reflink=always","--preserve=all","--no-clobber","--",str(src),str(dst)],check=True)
  if src.stat().st_ino==dst.stat().st_ino or digest(src)!=digest(dst): raise Error("v18 evidence reflink identity differs")
 receipt={"schema_version":"mysql_job_v19_recovery_input_reuse_receipt_v1","status":"PASS_ATOMIC_V19_INPUT_REUSE_RAW_SYSCALL_RENAME_NOREPLACE","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"source_transaction":V18_TXN,"source_path":str(V18_PREP),"new_transaction":V19_TXN,"copy_method":"cp_reflink_always_unique_inode","raw_syscall_number":316,"raw_syscall_flags":"RENAME_NOREPLACE","precopy_probe_errno":probe_errno,"precopy_probe_expected":"ENOENT","v18_activation_failure":FAILURE,"v18_prep_receipt_sha256":V18_RECEIPT_SHA,"v18_prep_sidecar_file_sha256":V18_SIDECAR_FILE_SHA,"v18_files_sorted_key_list_sha256":V18_KEY_LIST_SHA,"v18_files_sorted_path_sha256_closure_sha256":V18_CLOSURE_SHA,"v18_prepared_tree_identity_sha256":identity,"v18_prep_regular_file_count":25,"v18_input_file_count":23,"v18_prep_preserved":True,"old_failed_transaction_rerun_prohibited":True,"new_receipts_isolated_from_old_transaction":True,"accepted_rounds_credit":0,"files":files}
 payload=(json.dumps(receipt,indent=2,sort_keys=True)+"\n").encode();rp=PREP/"input_reuse_receipt.json";rp.write_bytes(payload);rp.chmod(0o400);sp=rp.with_name(rp.name+".sha256");sp.write_text(hashlib.sha256(payload).hexdigest()+"  input_reuse_receipt.json\n");sp.chmod(0o400)
 fd=os.open(PARENT,os.O_RDONLY|os.O_DIRECTORY)
 try:
  os.fsync(fd);result,number=raw_renameat2(PREP,NEW)
  if result!=0:
   if number in (errno.ENOSYS,errno.EINVAL): raise Error("raw syscall 316 RENAME_NOREPLACE unsupported: "+os.strerror(number))
   if number==errno.EEXIST: raise Error("v19 target appeared; no overwrite performed")
   raise Error("atomic v19 activation failed: "+os.strerror(number))
  os.fsync(fd)
 finally: os.close(fd)
 if not V18_PREP.is_dir() or lexists(V18_VISIBLE): raise Error("v18 PREP preservation changed after activation")
 print(json.dumps({"status":receipt["status"],"transaction":V19_TXN,"path":str(NEW),"prepared_tree_identity_sha256":identity},sort_keys=True))
def main():
 p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument("--check",action="store_true");g.add_argument("--execute",metavar="EXACT_V19_TRANSACTION");a=p.parse_args()
 if a.execute is not None and a.execute!=V19_TXN: raise Error("exact v19 transaction id required")
 source_receipt,identity=verify();number=probe()
 if a.check: print(json.dumps({"status":"PASS_V19_INPUT_READ_ONLY_RAW_SYSCALL_PROBE","transaction":V19_TXN,"probe_errno":number,"prepared_tree_identity_sha256":identity},sort_keys=True))
 else: execute(source_receipt,identity,number)
if __name__=="__main__":
 try: main()
 except (Error,OSError,ValueError,subprocess.SubprocessError) as e: raise SystemExit("NOT_READY: "+str(e))
'''.encode("utf-8")


TEST = r'''#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C
B=${1:?bundle path required}
S="$B/transaction/mysql_job_v19_node04_raw_renameat2_recovery_20260803t102324z_32e5ff1a.sh"
M="$B/transaction/mysql_job_v19_recovery_input_materializer_v1.py"
P="$B/qualification/mysql_job_v19_raw_syscall_atomic_tree_publish_v1.py"
fail(){ printf 'FAIL: %s\n' "$*" >&2; exit 1; }
bash -n "$S"; "$S" --help >/dev/null
python3 -B - "$M" "$P" <<'PY'
import pathlib,sys
for p in sys.argv[1:]: compile(pathlib.Path(p).read_bytes(),p,"exec")
PY
rg -F "readonly FAILED_V18_TXN='job-v18-recovery-20260803t100018z-35f8668f'" "$S" >/dev/null
rg -F "readonly TXN='job-v19-recovery-20260803t102324z-32e5ff1a'" "$S" >/dev/null
rg -F 'readonly V18_PREP="${JOB_ROOT}/.relocation_v18/.prepare-${FAILED_V18_TXN}"' "$S" >/dev/null
rg -F 'readonly DST_TXN="${JOB_ROOT}/.relocation_v19/${TXN}"' "$S" >/dev/null
rg -F 'check_raw_renameat2_capability' "$S" >/dev/null
rg -F '["/bin/cp","--reflink=always","--preserve=all","--no-clobber"' "$M" >/dev/null
rg -F 'result=fn(316,-100' "$M" >/dev/null
rg -F 'result = syscall(316, -100' "$P" >/dev/null
! rg -F 'getattr(libc, "renameat2"' "$P" >/dev/null
! rg -F 'getattr(libc,"renameat2"' "$M" >/dev/null
probe=$(rg -n '^check_raw_renameat2_capability$' "$S" | tail -1 | cut -d: -f1)
mkdirline=$(rg -n '^mkdir -m 0700 -- "\$TEMPLATE_STAGE"$' "$S" | cut -d: -f1)
[[ $probe -lt $mkdirline ]] || fail 'raw syscall probe is not before stage creation'
runtime=$(rg -n '^publish_one runtime ' "$S" | cut -d: -f1);template=$(rg -n '^publish_one template ' "$S" | cut -d: -f1)
[[ $runtime -lt $template ]] || fail 'runtime/template publication order differs'
if rg -n '(^|[;&|[:space:]])(rm|rmdir|unlink|shred|truncate|mv)([[:space:]]|$)' "$S" "$M" "$P"; then fail 'delete/replace command present'; fi
f=$(mktemp -d /tmp/mysql-job-v19-raw-renameat2.XXXXXX)
trap 'rm -rf -- "$f"' EXIT
python3 -B - "$f" <<'PY'
import ctypes,errno,os,pathlib,sys
root=pathlib.Path(sys.argv[1]);libc=ctypes.CDLL(None,use_errno=True);fn=libc.syscall
fn.argtypes=[ctypes.c_long,ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint];fn.restype=ctypes.c_long
def call(a,b):
 r=fn(316,-100,os.fsencode(a),-100,os.fsencode(b),1);return r,ctypes.get_errno()
r,e=call(root/"absent-source",root/"absent-destination")
assert (r,e)==(-1,errno.ENOENT) and not (root/"absent-source").exists() and not (root/"absent-destination").exists()
src=root/"source";dst=root/"destination";src.mkdir();(src/"sentinel").write_text("source\n")
assert call(src,dst)[0]==0 and (dst/"sentinel").read_text()=="source\n"
other=root/"other";other.mkdir();(other/"sentinel").write_text("other\n")
r,e=call(other,dst);assert (r,e)==(-1,errno.EEXIST) and (dst/"sentinel").read_text()=="source\n" and (other/"sentinel").read_text()=="other\n"
PY
printf 'PASS_OFFLINE_V19_FIXTURE: syntax raw_syscall316 ENOENT_probe atomic_success EEXIST_no_overwrite reflink_always_static v18_prep_preserved serial_stages runtime_then_template no_delete\n'
'''.encode("utf-8")


def main() -> None:
    script = recovery_script()
    pub = publisher()
    authority = {
        "schema_version": "mysql_job_v19_v18_failure_and_prepared_tree_contract_v1",
        "status": "PASS_EXACT_V18_FAILURE_SEALED_NOT_EXECUTED",
        "failed_transaction": V18_TXN,
        "failed_prepare_path": f"/mnt/nvme2n1/anonymous/resource_adaptive_mysql/job/.relocation_v18/.prepare-{V18_TXN}",
        "failed_visible_path_must_be_absent": f"/mnt/nvme2n1/anonymous/resource_adaptive_mysql/job/.relocation_v18/{V18_TXN}",
        "exact_failure": "NOT_READY: renameat2(RENAME_NOREPLACE) unavailable",
        "cause": "glibc_symbol_absent_kernel_raw_syscall_supported",
        "remote_raw_syscall_316_probe": {"result": -1, "errno": 2, "errno_name": "ENOENT"},
        "receipt_sha256": V18_PREP_RECEIPT_SHA,
        "sidecar_file_sha256": V18_PREP_SIDECAR_FILE_SHA,
        "receipt_status": "PASS_ATOMIC_NEW_TRANSACTION_INPUT_REUSE",
        "receipt_files_map_count": 23,
        "sorted_key_list_sha256": V18_PREP_KEY_LIST_SHA,
        "sorted_path_sha256_closure_sha256": V18_PREP_CLOSURE_SHA,
        "total_regular_files": 25,
        "preservation_required": True,
        "rerun_prohibited": True,
        "accepted_rounds_credit": 0,
    }
    plan = {
        "schema_version": "mysql_job_v19_raw_renameat2_recovery_successor_plan_v1",
        "status": "PASS_IMMUTABLE_RECOVERY_PLAN_NOT_EXECUTED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "v17_failed_transaction": V17_TXN,
        "v18_failed_transaction": V18_TXN,
        "v18_failed_transaction_rerun_prohibited": True,
        "v18_prepare_tree_preserved": True,
        "new_transaction": V19_TXN,
        "new_transaction_parent": "/mnt/nvme2n1/anonymous/resource_adaptive_mysql/job/.relocation_v19",
        "input_reuse": "cp_--reflink=always_from_exact_v18_PREP_then_raw_syscall316_RENAME_NOREPLACE",
        "invalidated_local_intermediate_bundle_id": "b82e528fe89f7d2f3b95c7da1138cc73850c5bcd7c981d65256bb2e8ff5adbdf",
        "precopy_capability_probe": "guaranteed_absent_source_and_destination_requires_ENOENT",
        "fail_closed_errno": [38, 22],
        "raw_syscall": {"architecture": "x86_64", "number": 316, "flags": 1},
        "logical_unique_file_bytes": 1077311137,
        "stage_creation": "serial_template_then_runtime_fail_fast",
        "publication": "runtime_then_template_each_raw_syscall316_RENAME_NOREPLACE_parent_fsync",
        "remote_actions": 0,
        "accepted_rounds_credit": 0,
    }
    content = {
        f"transaction/{SCRIPT_NAME}": script,
        f"transaction/{MATERIALIZER_NAME}": MATERIALIZER,
        f"qualification/{PUBLISHER_NAME}": pub,
        "authority/v18_failure_and_prepared_tree_contract.json": canonical(authority),
        "plan/recovery_plan.json": canonical(plan),
        "tests/test_mysql_job_v19_raw_renameat2_recovery_successor_v1.sh": TEST,
        "builder/build_mysql_job_v19_raw_renameat2_recovery_successor_v1.py": Path(__file__).read_bytes(),
    }
    files = {name: sha(data) for name, data in sorted(content.items())}
    identity = dict(plan)
    identity.update({"files": files, "script_sha256": sha(script),
                     "materializer_sha256": sha(MATERIALIZER),
                     "publisher_sha256": sha(pub), "authority_sha256": sha(canonical(authority)),
                     "immutable": True})
    bundle_id = sha(canonical(identity))
    manifest = dict(identity, bundle_id=bundle_id)
    manifest_bytes = canonical(manifest)
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / bundle_id
    if target.exists():
        if sha((target / "bundle_manifest.json").read_bytes()) != sha(manifest_bytes):
            raise SystemExit("existing v19 recovery bundle differs")
        print(json.dumps({"bundle": str(target), "bundle_id": bundle_id,
                          "manifest_sha256": sha(manifest_bytes), "script_sha256": sha(script),
                          "materializer_sha256": sha(MATERIALIZER), "publisher_sha256": sha(pub)}, sort_keys=True))
        return
    stage = Path(tempfile.mkdtemp(prefix=".mysql_job_v19_recovery.", dir=OUT))
    for relative, data in content.items():
        path = stage / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o555 if relative.endswith((".sh", ".py")) else 0o444)
    (stage / "bundle_manifest.json").write_bytes(manifest_bytes)
    (stage / "bundle_manifest.json").chmod(0o444)
    (stage / "bundle_manifest.json.sha256").write_text(sha(manifest_bytes) + "  bundle_manifest.json\n", encoding="ascii")
    (stage / "bundle_manifest.json.sha256").chmod(0o444)
    for directory, dirs, _ in os.walk(stage, topdown=False):
        for name in dirs: os.chmod(Path(directory) / name, 0o555)
    stage.chmod(0o555)
    os.rename(stage, target)
    print(json.dumps({"bundle": str(target), "bundle_id": bundle_id,
                      "manifest_sha256": sha(manifest_bytes), "script_sha256": sha(script),
                      "materializer_sha256": sha(MATERIALIZER), "publisher_sha256": sha(pub)}, sort_keys=True))


if __name__ == "__main__":
    main()
