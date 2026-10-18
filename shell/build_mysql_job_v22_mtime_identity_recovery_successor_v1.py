#!/usr/bin/env python3
"""Build JOB v22: preserve v21 partial stages and repair only exact mtime loss."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
V21_ID = "f49cefa2dc4a456f3828a7338217c2e6cbc29b11e0528714cbc9c97582b1cd1f"
V21_SHA = "5be3ac95fe0267f88d793c6a8695321d299c2311a2c7ba19e46d0d55878de20e"
V21 = ROOT / "artifacts/mysql_job_v21_copy_fallback_recovery_successor_v1" / V21_ID
OLD_TXN = "job-v21-recovery-20260803t105116z-776fe047"
NEW_TXN = "job-v22-recovery-20260803t114500z-7c3e91a2"
OUT = ROOT / "artifacts/mysql_job_v22_mtime_identity_recovery_successor_v1"
SCRIPT = "transaction/mysql_job_v22_node04_mtime_identity_recovery_20260803t114500z_7c3e91a2.sh"
MATERIALIZER = "transaction/mysql_job_v22_input_reference_materializer_v1.py"
REPAIR = "qualification/mysql_job_v22_exact_mtime_repair_v1.py"
BUILDER = "builder/build_mysql_job_v22_mtime_identity_recovery_successor_v1.py"


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                       allow_nan=False) + "\n").encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            digest.update(block)
    return digest.hexdigest()


def once(text, old, new, label):
    if text.count(old) != 1:
        raise RuntimeError(label + " anchor differs: " + str(text.count(old)))
    return text.replace(old, new)


REPAIR_SOURCE = r'''#!/usr/bin/env python3
"""Classify exact portable-tree differences; repair only authoritative mtime_ns."""
from __future__ import annotations
import argparse,base64,hashlib,json,os,socket,stat
from datetime import datetime,timezone
from pathlib import Path
class Error(RuntimeError):pass
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def digest_bytes(v):return hashlib.sha256(v).hexdigest()
def digest(p):
 h=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):h.update(b)
 return h.hexdigest()
def attrs(p):return {n:base64.b64encode(os.getxattr(p,n,follow_symlinks=False)).decode("ascii") for n in sorted(os.listxattr(p,follow_symlinks=False))}
def inventory(root,label):
 root=Path(root).resolve(strict=True)
 if root.is_symlink() or not root.is_dir():raise Error("inventory root differs")
 rs=root.lstat();entries=[];links={};logical=0;inodes={(rs.st_dev,rs.st_ino)};allocated=rs.st_blocks*512
 for p in sorted(root.rglob("*"),key=lambda x:x.relative_to(root).as_posix()):
  rel=p.relative_to(root).as_posix();s=p.lstat();key=(s.st_dev,s.st_ino)
  if key not in inodes:inodes.add(key);allocated+=s.st_blocks*512
  common={"path":rel,"mode":"%04o"%stat.S_IMODE(s.st_mode),"uid":s.st_uid,"gid":s.st_gid,"mtime_ns":s.st_mtime_ns,"xattrs_base64":attrs(p)}
  if stat.S_ISDIR(s.st_mode):row=dict(common,type="directory")
  elif stat.S_ISLNK(s.st_mode):row=dict(common,type="symlink",target=os.readlink(p))
  elif stat.S_ISREG(s.st_mode):
   leader=links.get(key)
   if leader is None:links[key]=rel;logical+=s.st_size
   row=dict(common,type="file",size=s.st_size,sha256=digest(p),hardlink_to=leader)
  else:raise Error("special entry prohibited: "+rel)
  entries.append(row)
 identity={"algorithm":"mysql_job_portable_tree_identity_v1","root_mode":"%04o"%stat.S_IMODE(rs.st_mode),"root_uid":rs.st_uid,"root_gid":rs.st_gid,"root_mtime_ns":rs.st_mtime_ns,"root_xattrs_base64":attrs(root),"entries":entries}
 return {"schema_version":"mysql_job_portable_tree_inventory_v1","status":"PASS_EXACT_READ_ONLY_INVENTORY_NOT_COPY_AUTHORITY","label":label,"root":str(root),"hostname":socket.gethostname(),"captured_at_utc":datetime.now(timezone.utc).isoformat(),"content_identity_sha256":digest_bytes(canonical(identity)),"identity":identity,"entry_count":len(entries),"logical_unique_file_bytes":logical,"observed_allocated_bytes_unique_inodes":allocated,"remote_mutation_performed":False,"formal_launch_authorized":False,"accepted_round_authorized":False}
def load(p,label):
 p=Path(p)
 if p.is_symlink() or not p.is_file():raise Error(label+" absent")
 side=Path(str(p)+".sha256")
 if side.is_symlink() or not side.is_file() or side.read_text(encoding="ascii")!=digest(p)+"  "+p.name+"\n":raise Error(label+" seal differs")
 v=json.loads(p.read_text(encoding="utf-8"));identity=v.get("identity") or {}
 if v.get("schema_version")!="mysql_job_portable_tree_inventory_v1" or v.get("content_identity_sha256")!=digest_bytes(canonical(identity)):raise Error(label+" authority differs")
 return v
def write(p,v):
 p=Path(p);side=Path(str(p)+".sha256")
 if p.exists() or p.is_symlink() or side.exists() or side.is_symlink():raise Error("refusing overwrite: "+str(p))
 raw=canonical(v);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw);p.chmod(0o400);side.write_text(digest_bytes(raw)+"  "+p.name+"\n");side.chmod(0o400)
def classify(a,b):
 cats={k:[] for k in ("missing","type","content","hardlink","owner_mode","xattr","mtime","other")};am={x["path"]:x for x in a["entries"]};bm={x["path"]:x for x in b["entries"]}
 for p in sorted(set(am)|set(bm)):
  x=am.get(p);y=bm.get(p)
  if x is None or y is None:cats["missing"].append(p);continue
  if x.get("type")!=y.get("type"):cats["type"].append(p)
  if any(x.get(q)!=y.get(q) for q in ("size","sha256","target")):cats["content"].append(p)
  if x.get("hardlink_to")!=y.get("hardlink_to"):cats["hardlink"].append(p)
  if any(x.get(q)!=y.get(q) for q in ("uid","gid","mode")):cats["owner_mode"].append(p)
  if x.get("xattrs_base64")!=y.get("xattrs_base64"):cats["xattr"].append(p)
  if x.get("mtime_ns")!=y.get("mtime_ns"):cats["mtime"].append(p)
  known={"path","type","size","sha256","target","hardlink_to","uid","gid","mode","xattrs_base64","mtime_ns"}
  if {k:v for k,v in x.items() if k not in known}!={k:v for k,v in y.items() if k not in known}:cats["other"].append(p)
 root={q:[a.get(q),b.get(q)] for q in ("root_mode","root_uid","root_gid","root_mtime_ns","root_xattrs_base64") if a.get(q)!=b.get(q)}
 return cats,root
def verify_partial(root,inventory_path):
 expected=load(inventory_path,"v21 partial inventory");actual=inventory(root,"v21-preserved-partial-readback")
 if actual["identity"]!=expected["identity"] or actual["content_identity_sha256"]!=expected["content_identity_sha256"]:raise Error("sealed v21 partial stage changed")
 print(json.dumps({"status":"PASS_V21_PARTIAL_STAGE_PRESERVED_READ_ONLY","root":str(Path(root)),"inventory_sha256":digest(inventory_path),"content_identity_sha256":actual["content_identity_sha256"],"entry_count":actual["entry_count"],"logical_unique_file_bytes":actual["logical_unique_file_bytes"]},sort_keys=True))
def repair(root,source_path,pre_out,post_out,receipt_out,label):
 source=load(source_path,"source inventory");pre=inventory(root,label+"-pre-mtime-repair");cats,rootdiff=classify(source["identity"],pre["identity"])
 nonmtime=sum(len(cats[k]) for k in cats if k!="mtime")+sum(1 for k in rootdiff if k!="root_mtime_ns")
 if nonmtime or not cats["mtime"] and "root_mtime_ns" not in rootdiff:raise Error("portable identity difference is not exclusively mtime_ns")
 write(pre_out,pre);rows=sorted(source["identity"]["entries"],key=lambda x:(x["path"].count("/"),x["path"]),reverse=True);base=Path(root).resolve(strict=True)
 link_times={}
 for row in rows:
  leader=row.get("hardlink_to") or row["path"];old=link_times.setdefault(leader,row["mtime_ns"])
  if old!=row["mtime_ns"]:raise Error("source hardlink mtime authority is inconsistent")
 for row in rows:
  p=base/row["path"];s=p.lstat();os.utime(p,ns=(s.st_atime_ns,int(row["mtime_ns"])),follow_symlinks=False)
 rs=base.lstat();os.utime(base,ns=(rs.st_atime_ns,int(source["identity"]["root_mtime_ns"])),follow_symlinks=False)
 post=inventory(base,label+"-post-mtime-repair")
 if post["identity"]!=source["identity"] or post["content_identity_sha256"]!=source["content_identity_sha256"]:raise Error("exact source identity did not close after mtime repair")
 write(post_out,post);receipt={"schema_version":"mysql_job_v22_exact_mtime_repair_receipt_v1","status":"PASS_ONLY_MTIME_NS_RESTORED_EXACT_PORTABLE_IDENTITY","source_inventory":str(Path(source_path).resolve()),"source_inventory_sha256":digest(source_path),"pre_inventory":str(Path(pre_out).resolve()),"pre_inventory_sha256":digest(pre_out),"post_inventory":str(Path(post_out).resolve()),"post_inventory_sha256":digest(post_out),"source_content_identity_sha256":source["content_identity_sha256"],"pre_content_identity_sha256":pre["content_identity_sha256"],"post_content_identity_sha256":post["content_identity_sha256"],"difference_counts":{k:len(v) for k,v in cats.items()},"difference_examples":{k:v[:20] for k,v in cats.items() if v},"root_difference":rootdiff,"mtime_entries_restored":len(cats["mtime"]),"content_changed":False,"owner_mode_changed":False,"xattrs_changed":False,"hardlinks_changed":False,"sparse_allocation_observation":{"source":source["observed_allocated_bytes_unique_inodes"],"pre":pre["observed_allocated_bytes_unique_inodes"],"post":post["observed_allocated_bytes_unique_inodes"],"identity_field":False},"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0};write(receipt_out,receipt);print(json.dumps({"status":receipt["status"],"mtime_entries_restored":receipt["mtime_entries_restored"],"content_identity_sha256":post["content_identity_sha256"]},sort_keys=True))
def main():
 p=argparse.ArgumentParser();sp=p.add_subparsers(dest="mode",required=True);v=sp.add_parser("verify-partial");v.add_argument("--root",type=Path,required=True);v.add_argument("--inventory",type=Path,required=True);r=sp.add_parser("repair");r.add_argument("--root",type=Path,required=True);r.add_argument("--source-inventory",type=Path,required=True);r.add_argument("--pre-output",type=Path,required=True);r.add_argument("--post-output",type=Path,required=True);r.add_argument("--receipt",type=Path,required=True);r.add_argument("--label",required=True);a=p.parse_args();verify_partial(a.root,a.inventory) if a.mode=="verify-partial" else repair(a.root,a.source_inventory,a.pre_output,a.post_output,a.receipt,a.label)
if __name__=="__main__":
 try:main()
 except (Error,OSError,ValueError,KeyError,TypeError) as e:raise SystemExit("NOT_READY: "+str(e))
'''


MATERIALIZER_SOURCE = r'''#!/usr/bin/env python3
"""Seal immutable v21 archives/partial stages as v22 inputs; copy no tree data."""
from __future__ import annotations
import argparse,hashlib,json,os,stat
from pathlib import Path
JOB=Path("/mnt/nvme2n1/anonymous/resource_adaptive_mysql/job");OLD="job-v21-recovery-20260803t105116z-776fe047";NEW="job-v22-recovery-20260803t114500z-7c3e91a2";OLD_ROOT=JOB/".relocation_v21"/OLD;NEW_PARENT=JOB/".relocation_v22";NEW_ROOT=NEW_PARENT/NEW;TSTAGE=JOB/(".template.stage-"+OLD);RSTAGE=JOB/"runtime_algorithm_c_successor_20260729/runtime"/(".python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89.stage-"+OLD)
class Error(RuntimeError):pass
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sha(p):
 h=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):h.update(b)
 return h.hexdigest()
def sealed(p,label):
 p=Path(p);s=Path(str(p)+".sha256")
 if p.is_symlink() or not p.is_file() or s.is_symlink() or not s.is_file() or s.read_text(encoding="ascii")!=sha(p)+"  "+p.name+"\n":raise Error(label+" seal differs")
 v=json.loads(p.read_text());
 if not isinstance(v,dict):raise Error(label+" object required")
 return v
def newfile(p,v):
 raw=canonical(v);fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o400)
 try:os.write(fd,raw);os.fsync(fd)
 finally:os.close(fd)
 s=Path(str(p)+".sha256");fd=os.open(s,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o400)
 try:os.write(fd,(hashlib.sha256(raw).hexdigest()+"  "+p.name+"\n").encode());os.fsync(fd)
 finally:os.close(fd)
def main():
 a=argparse.ArgumentParser();a.add_argument("--execute",required=True);x=a.parse_args()
 if x.execute!=NEW:raise Error("exact v22 transaction id required")
 if os.geteuid()!=1008 or os.getegid()!=1008:raise Error("uid/gid 1008 required")
 if NEW_ROOT.exists() or NEW_ROOT.is_symlink():raise Error("fresh v22 transaction required")
 if not NEW_PARENT.exists():NEW_PARENT.mkdir(mode=0o700)
 if not NEW_PARENT.is_dir() or NEW_PARENT.is_symlink() or NEW_PARENT.stat().st_uid!=1008 or NEW_PARENT.stat().st_gid!=1008 or stat.S_IMODE(NEW_PARENT.stat().st_mode)!=0o700:raise Error("v22 parent differs")
 if not OLD_ROOT.is_dir() or OLD_ROOT.is_symlink():raise Error("v21 archive root absent")
 stages={"template":TSTAGE,"runtime":RSTAGE};rows={}
 for kind,root in stages.items():
  inv=OLD_ROOT/kind/"stage.json";v=sealed(inv,"v21 "+kind+" stage inventory")
  if not root.is_dir() or root.is_symlink() or Path(v.get("root","")).resolve(strict=True)!=root.resolve(strict=True):raise Error("v21 "+kind+" partial root differs")
  expected=(193,10509457139,"2487653b") if kind=="template" else (40852,1077311137,"dda1724e")
  if v.get("entry_count")!=expected[0] or v.get("logical_unique_file_bytes")!=expected[1] or not str(v.get("content_identity_sha256","")).startswith(expected[2]):raise Error("v21 "+kind+" partial inventory authority differs")
  for absent in ("source-stage.json","publication.json","final.json","source-final.json"):
   if (OLD_ROOT/kind/absent).exists() or (OLD_ROOT/kind/absent).is_symlink():raise Error("v21 failure progressed unexpectedly")
  rows[kind]={"stage":str(root.resolve()),"stage_device":root.stat().st_dev,"stage_inode":root.stat().st_ino,"stage_inventory":str(inv.resolve()),"stage_inventory_sha256":sha(inv),"content_identity_sha256":v["content_identity_sha256"],"entry_count":v["entry_count"],"logical_unique_file_bytes":v["logical_unique_file_bytes"],"reuse_prohibited":True,"delete_prohibited":True}
 if (JOB/"template").exists() or (JOB/"runtime_algorithm_c_successor_20260729/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89").exists():raise Error("final target unexpectedly exists")
 NEW_ROOT.mkdir(mode=0o700)
 for kind in ("template","runtime"):(NEW_ROOT/kind).mkdir(mode=0o700)
 receipt={"schema_version":"mysql_job_v22_input_reference_receipt_v1","status":"PASS_V21_ARCHIVES_REFERENCED_V21_PARTIALS_SEALED_NOT_REUSED","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"failed_v21_transaction":OLD,"new_v22_transaction":NEW,"archive_root":str(OLD_ROOT),"partial_stages":rows,"v21_partial_stages_preserved":True,"v21_partial_stages_reuse_prohibited":True,"v21_partial_stages_delete_prohibited":True,"archive_segments_referenced_read_only":True,"tree_data_copied":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0};newfile(NEW_ROOT/"input_reference_receipt.json",receipt);print(json.dumps({"status":receipt["status"],"transaction_root":str(NEW_ROOT),"partial_stages":rows},sort_keys=True))
if __name__=="__main__":
 try:main()
 except (Error,OSError,ValueError,KeyError,TypeError) as e:raise SystemExit("NOT_READY: "+str(e))
'''


def recovery_script():
    source = (V21 / "transaction/mysql_job_v21_node04_copy_fallback_recovery_20260803t105116z_776fe047.sh").read_text()
    source = source.replace("# Immutable JOB v21 recovery.", "# Immutable JOB v22 recovery after v21 portable-identity failure.", 1)
    source = once(source, "readonly TXN='" + OLD_TXN + "'", "readonly FAILED_V21_TXN='" + OLD_TXN + "'\nreadonly TXN='" + NEW_TXN + "'", "transaction")
    old_paths = '''readonly DST_TXN="${JOB_ROOT}/.relocation_v21/${TXN}"
readonly INPUT_REUSE_RECEIPT="${DST_TXN}/input_reuse_receipt.json"
readonly TEMPLATE_ARCHIVE="${DST_TXN}/template"
readonly RUNTIME_ARCHIVE="${DST_TXN}/runtime"
'''
    new_paths = '''readonly V21_TXN_ROOT="${JOB_ROOT}/.relocation_v21/${FAILED_V21_TXN}"
readonly DST_TXN="${JOB_ROOT}/.relocation_v22/${TXN}"
readonly INPUT_REUSE_RECEIPT="${DST_TXN}/input_reference_receipt.json"
readonly TEMPLATE_ARCHIVE="${V21_TXN_ROOT}/template"
readonly RUNTIME_ARCHIVE="${V21_TXN_ROOT}/runtime"
readonly TEMPLATE_RECEIPTS="${DST_TXN}/template"
readonly RUNTIME_RECEIPTS="${DST_TXN}/runtime"
'''
    source = once(source, old_paths, new_paths, "archive/receipt paths")
    source = once(source, 'readonly RUNTIME_STAGE="${RUNTIME_PARENT}/.python311-${RUNTIME_ID}.stage-${TXN}"\n', 'readonly RUNTIME_STAGE="${RUNTIME_PARENT}/.python311-${RUNTIME_ID}.stage-${TXN}"\nreadonly V21_TEMPLATE_STAGE="${JOB_ROOT}/.template.stage-${FAILED_V21_TXN}"\nreadonly V21_RUNTIME_STAGE="${RUNTIME_PARENT}/.python311-${RUNTIME_ID}.stage-${FAILED_V21_TXN}"\n', "partial stages")
    source = once(source, 'readonly PUBLISHER="${RECOVERY_BUNDLE}/qualification/mysql_job_v21_raw_syscall_atomic_tree_publish_v1.py"\n', 'readonly PUBLISHER="${RECOVERY_BUNDLE}/qualification/mysql_job_v22_raw_syscall_atomic_tree_publish_v1.py"\nreadonly REPAIR="${RECOVERY_BUNDLE}/qualification/mysql_job_v22_exact_mtime_repair_v1.py"\n', "tools")
    start = source.index("check_failed_predecessor_preserved() {")
    end = source.index("\ncheck_input_reuse_receipt() {", start)
    failed = '''check_failed_predecessor_preserved() {
  [[ "$TXN" != "$OLD_TXN" && "$TXN" != "$FAILED_V18_TXN" && "$TXN" != "$FAILED_V19_TXN" && "$TXN" != "$FAILED_V20_TXN" && "$TXN" != "$FAILED_V21_TXN" ]] || die 'failed transaction rerun prohibited'
  [[ -d "$V21_TEMPLATE_STAGE" && ! -L "$V21_TEMPLATE_STAGE" && -d "$V21_RUNTIME_STAGE" && ! -L "$V21_RUNTIME_STAGE" ]] || die 'v21 partial stages were not preserved'
  require_absent "$TEMPLATE_FINAL"; require_absent "$RUNTIME_FINAL"
  [[ "$TEMPLATE_STAGE" != "$V21_TEMPLATE_STAGE" && "$RUNTIME_STAGE" != "$V21_RUNTIME_STAGE" ]] || die 'v21 partial stage reuse prohibited'
}
'''
    source = source[:start] + failed + source[end:]
    start = source.index("check_input_reuse_receipt() {")
    end = source.index("\ncheck_raw_renameat2_capability() {", start)
    check_input = '''check_input_reuse_receipt() {
  require_evidence_pair "$DST_TXN" input_reference_receipt.json
  "$BOOTSTRAP_PY" -B - "$INPUT_REUSE_RECEIPT" "$V21_TEMPLATE_STAGE" "$V21_RUNTIME_STAGE" <<'PY'
import hashlib,json,pathlib,sys
p=pathlib.Path(sys.argv[1]);v=json.loads(p.read_text());roots={"template":pathlib.Path(sys.argv[2]),"runtime":pathlib.Path(sys.argv[3])}
if v.get("schema_version")!="mysql_job_v22_input_reference_receipt_v1" or v.get("status")!="PASS_V21_ARCHIVES_REFERENCED_V21_PARTIALS_SEALED_NOT_REUSED" or v.get("failed_v21_transaction")!="job-v21-recovery-20260803t105116z-776fe047" or v.get("new_v22_transaction")!="job-v22-recovery-20260803t114500z-7c3e91a2" or v.get("v21_partial_stages_reuse_prohibited") is not True or v.get("v21_partial_stages_delete_prohibited") is not True or v.get("tree_data_copied") is not False:raise SystemExit("NOT_READY: v22 input reference differs")
for k,r in roots.items():
 row=(v.get("partial_stages") or {}).get(k) or {};inv=pathlib.Path(row.get("stage_inventory", ""))
 h=hashlib.sha256(inv.read_bytes()).hexdigest()
 if pathlib.Path(row.get("stage","")).resolve(strict=True)!=r.resolve(strict=True) or h!=row.get("stage_inventory_sha256") or row.get("reuse_prohibited") is not True or row.get("delete_prohibited") is not True:raise SystemExit("NOT_READY: sealed v21 partial binding differs: "+k)
print("PASS: v21 partial stages sealed and prohibited from reuse/delete")
PY
  "$BOOTSTRAP_PY" -B "$REPAIR" verify-partial --root "$V21_TEMPLATE_STAGE" --inventory "$TEMPLATE_ARCHIVE/stage.json"
  "$BOOTSTRAP_PY" -B "$REPAIR" verify-partial --root "$V21_RUNTIME_STAGE" --inventory "$RUNTIME_ARCHIVE/stage.json"
}
'''
    source = source[:start] + check_input + source[end:]
    source = source.replace("v21 recovery bundle", "v22 recovery bundle").replace('mysql_job_v21_copy_fallback_recovery_successor_plan_v1', 'mysql_job_v22_mtime_identity_recovery_successor_plan_v1')
    start = source.index("check_fresh_outputs() {")
    end = source.index("\nvalidate_comparison() {", start)
    fresh = '''check_fresh_outputs() {
  local d path
  require_absent "$TEMPLATE_STAGE"; require_absent "$RUNTIME_STAGE"; require_absent "$TEMPLATE_FINAL"; require_absent "$RUNTIME_FINAL"
  for d in "$TEMPLATE_RECEIPTS" "$RUNTIME_RECEIPTS"; do
    [[ -d "$d" && ! -L "$d" ]] || die "v22 receipt directory differs: $d"
    for path in pre-repair.json pre-repair.json.sha256 mtime-repair.json mtime-repair.json.sha256 stage.json stage.json.sha256 source-stage.json source-stage.json.sha256 publication.json publication.json.sha256 final.json final.json.sha256 source-final.json source-final.json.sha256; do require_absent "$d/$path"; done
  done
}
'''
    source = source[:start] + fresh + source[end:]
    start = source.index("inventory_stage() {")
    end = source.index("\npublish_one() {", start)
    inv = '''inventory_stage() {
  local kind=$1 stage=$2 archive=$3 receipts=$4
  "$BOOTSTRAP_PY" -B "$REPAIR" repair --root "$stage" --source-inventory "$archive/source-post.json" --pre-output "$receipts/pre-repair.json" --post-output "$receipts/stage.json" --receipt "$receipts/mtime-repair.json" --label "${kind}-stage-${TXN}"
  "$BOOTSTRAP_PY" -B "$INVENTORY" --compare-source "$archive/source-post.json" --compare-destination "$receipts/stage.json" --output "$receipts/source-stage.json"
  validate_comparison "$receipts/source-stage.json"
}
'''
    source = source[:start] + inv + source[end:]
    start = source.index("publish_one() {")
    end = source.index("\n[[ $(hostname)", start)
    pub = '''publish_one() {
  local kind=$1 stage=$2 final=$3 archive=$4 receipts=$5
  "$BOOTSTRAP_PY" -B "$PUBLISHER" --kind "$kind" --transaction-id "$TXN" --stage "$stage" --final "$final" --source-pre-inventory "$archive/source-pre.json" --source-inventory "$archive/source-post.json" --stage-inventory "$receipts/stage.json" --source-stability-comparison "$archive/source-stability.json" --comparison "$receipts/source-stage.json" --output "$receipts/publication.json"
  "$BOOTSTRAP_PY" -B "$INVENTORY" --root "$final" --label "${kind}-visible-final-${TXN}" --output "$receipts/final.json"
  "$BOOTSTRAP_PY" -B "$INVENTORY" --compare-source "$archive/source-post.json" --compare-destination "$receipts/final.json" --output "$receipts/source-final.json"
  validate_comparison "$receipts/source-final.json"
}
'''
    source = source[:start] + pub + source[end:]
    source = source.replace('inventory_stage template "$TEMPLATE_STAGE" "$TEMPLATE_ARCHIVE" &', 'inventory_stage template "$TEMPLATE_STAGE" "$TEMPLATE_ARCHIVE" "$TEMPLATE_RECEIPTS" &')
    source = source.replace('inventory_stage runtime "$RUNTIME_STAGE" "$RUNTIME_ARCHIVE" &', 'inventory_stage runtime "$RUNTIME_STAGE" "$RUNTIME_ARCHIVE" "$RUNTIME_RECEIPTS" &')
    source = source.replace('publish_one runtime "$RUNTIME_STAGE" "$RUNTIME_FINAL" "$RUNTIME_ARCHIVE"', 'publish_one runtime "$RUNTIME_STAGE" "$RUNTIME_FINAL" "$RUNTIME_ARCHIVE" "$RUNTIME_RECEIPTS"')
    source = source.replace('publish_one template "$TEMPLATE_STAGE" "$TEMPLATE_FINAL" "$TEMPLATE_ARCHIVE"', 'publish_one template "$TEMPLATE_STAGE" "$TEMPLATE_FINAL" "$TEMPLATE_ARCHIVE" "$TEMPLATE_RECEIPTS"')
    source = source.replace("PASS_V21_RAW_SYSCALL_RECOVERY", "PASS_V22_MTIME_IDENTITY_RECOVERY").replace("v21 input closure", "v22 referenced input closure")
    return source.encode()


TEST_SOURCE = r'''#!/usr/bin/env bash
set -Eeuo pipefail
B=${1:?bundle};S="$B/transaction/mysql_job_v22_node04_mtime_identity_recovery_20260803t114500z_7c3e91a2.sh";M="$B/transaction/mysql_job_v22_input_reference_materializer_v1.py";R="$B/qualification/mysql_job_v22_exact_mtime_repair_v1.py"
bash -n "$S";python3 -B - "$M" "$R" <<'PY'
import pathlib,sys
for p in map(pathlib.Path,sys.argv[1:]):compile(p.read_text(),str(p),"exec")
PY
rg -F 'V21_TEMPLATE_STAGE' "$S" >/dev/null;rg -F 'v21 partial stage reuse prohibited' "$S" >/dev/null;rg -F 'verify-partial' "$S" >/dev/null;rg -F 'portable identity difference is not exclusively mtime_ns' "$R" >/dev/null;rg -F 'hardlinks_changed":False' "$R" >/dev/null;rg -F 'sparse_allocation_observation' "$R" >/dev/null
if rg -n '(^|[;&|[:space:]])(rm|rmdir|unlink|shred|truncate|mv)([[:space:]]|$)' "$S" "$M" "$R";then exit 1;fi
printf 'PASS_OFFLINE_V22: v21_partials_sealed_no_reuse no_delete exact_content_metadata_hardlink_xattr mtime_only sparse_observed raw_noreplace\n'
'''


def main():
    manifest = json.loads((V21 / "bundle_manifest.json").read_text())
    unsigned = dict(manifest); unsigned.pop("bundle_id", None)
    if manifest.get("bundle_id") != V21_ID or file_sha(V21 / "bundle_manifest.json") != V21_SHA or sha(canonical(unsigned)) != V21_ID:
        raise SystemExit("v21 predecessor closure differs")
    script = recovery_script(); materializer = MATERIALIZER_SOURCE.encode(); repair = REPAIR_SOURCE.encode()
    publisher = (V21 / "qualification/mysql_job_v21_raw_syscall_atomic_tree_publish_v1.py").read_text().replace("v21", "v22").replace("V21", "V22").encode()
    authority = {"schema_version":"mysql_job_v22_v21_partial_stage_failure_contract_v1","status":"SEALED_TWO_V21_PARTIAL_STAGES_ZERO_CREDIT_NO_REUSE_DELETE","failed_v21_bundle_id":V21_ID,"failed_v21_manifest_sha256":V21_SHA,"failed_v21_transaction":OLD_TXN,"failure":"source/destination portable tree identity differs","failure_phase":"parallel_stage_inventory_before_any_publication","partial_stages":{"runtime":{"content_identity_sha256_prefix":"dda1724e","entry_count":40852,"logical_unique_file_bytes":1077311137},"template":{"content_identity_sha256_prefix":"2487653b","entry_count":193,"logical_unique_file_bytes":10509457139}},"source_stage_comparison_created":False,"publication_receipts_created":False,"finals_created":False,"cleanup_performed":False,"overwrite_performed":False,"rerun_prohibited":True,"stage_reuse_prohibited":True,"stage_delete_prohibited":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"accepted_rounds_credit":0,"remote_actions":0}
    plan = {"schema_version":"mysql_job_v22_mtime_identity_recovery_successor_plan_v1","status":"SEALED_MTIME_ONLY_REPAIR_RECOVERY_NOT_EXECUTED","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"failed_v21_bundle_id":V21_ID,"failed_v21_transaction":OLD_TXN,"failed_v21_rerun_reuse_delete_prohibited":True,"new_transaction":NEW_TXN,"new_transaction_parent":"/mnt/nvme2n1/anonymous/resource_adaptive_mysql/job/.relocation_v22","archive_strategy":"read_only_reference_to_sealed_v21_segments_and_source_inventories","new_stage_strategy":"fresh_extract_then_classify_all_fields_then_mtime_ns_only_restore","portable_identity_fields_unchanged":True,"hardlink_topology_exact_required":True,"content_exact_required":True,"owner_mode_exact_required":True,"xattrs_exact_required":True,"mtime_ns_exact_required":True,"sparse_allocation_reported_separately":True,"non_mtime_difference_fail_closed":True,"activation":"raw_syscall316_RENAME_NOREPLACE_parent_fsync","publication":"runtime_then_template","formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0}
    content={SCRIPT:script,MATERIALIZER:materializer,REPAIR:repair,"qualification/mysql_job_v22_raw_syscall_atomic_tree_publish_v1.py":publisher,"authority/v21_partial_stage_failure_contract.json":canonical(authority),"plan/recovery_plan.json":canonical(plan),"tests/test_mysql_job_v22_mtime_identity_recovery_successor_v1.sh":TEST_SOURCE.encode(),BUILDER:HERE.read_bytes()}
    files={k:sha(v) for k,v in sorted(content.items())};identity=dict(plan,predecessor_bundle_id=V21_ID,predecessor_manifest_sha256=V21_SHA,authority_sha256=sha(canonical(authority)),script_sha256=sha(script),materializer_sha256=sha(materializer),repair_sha256=sha(repair),publisher_sha256=sha(publisher),immutable=True,files=files);bid=sha(canonical(identity));final=dict(identity,bundle_id=bid);raw=canonical(final);OUT.mkdir(parents=True,exist_ok=True);dst=OUT/bid
    if not dst.exists():
        stage=Path(tempfile.mkdtemp(prefix=".job-v22-mtime-",dir=OUT))
        for rel,data in content.items():p=stage/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);p.chmod(0o555 if rel.endswith((".py",".sh")) else 0o444)
        (stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json").chmod(0o444);(stage/"bundle_manifest.json.sha256").write_text(sha(raw)+"  bundle_manifest.json\n");(stage/"bundle_manifest.json.sha256").chmod(0o444)
        for p in sorted(stage.rglob("*"),reverse=True):
            if p.is_dir():p.chmod(0o555)
        stage.chmod(0o555);os.replace(stage,dst)
    print(json.dumps({"bundle":str(dst),"bundle_id":bid,"manifest_sha256":file_sha(dst/"bundle_manifest.json"),"script_sha256":sha(script),"materializer_sha256":sha(materializer),"repair_sha256":sha(repair),"remote_actions":0},sort_keys=True))


if __name__=="__main__":main()
