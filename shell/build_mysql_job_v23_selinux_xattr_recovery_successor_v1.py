#!/usr/bin/env python3
"""Build JOB v23 staged SELinux-xattr recovery after invalidating offline v22."""
from __future__ import annotations
import hashlib,json,os,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve()
V22_ID="ac79bf9e8640d4390a40b0c7bfc19815110001c94085eb06b715f9127e2ab3f2";V22_SHA="ea94d51c54f86314717fd7eaac94b7461e13d869aa4c4da9704c4cd75d413317";V22=ROOT/"artifacts/mysql_job_v22_mtime_identity_recovery_successor_v1"/V22_ID
V21_ID="f49cefa2dc4a456f3828a7338217c2e6cbc29b11e0528714cbc9c97582b1cd1f";V21_SHA="5be3ac95fe0267f88d793c6a8695321d299c2311a2c7ba19e46d0d55878de20e"
OLD="job-v22-recovery-20260803t114500z-7c3e91a2";NEW="job-v23-recovery-20260803t121500z-91e7c4a6";V21_TXN="job-v21-recovery-20260803t105116z-776fe047"
OUT=ROOT/"artifacts/mysql_job_v23_selinux_xattr_recovery_successor_v1";SCRIPT="transaction/mysql_job_v23_node04_selinux_xattr_recovery_20260803t121500z_91e7c4a6.sh";MATERIALIZER="transaction/mysql_job_v23_input_reference_materializer_v1.py";HELPER="qualification/mysql_job_v23_controlled_selinux_xattr_repair_v1.py";BUILDER="builder/build_mysql_job_v23_selinux_xattr_recovery_successor_v1.py"
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sb(v):return hashlib.sha256(v).hexdigest()
def sf(p):
 h=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):h.update(b)
 return h.hexdigest()
def once(s,o,n,label):
 if s.count(o)!=1:raise RuntimeError(label+" anchor differs "+str(s.count(o)))
 return s.replace(o,n)

def helper_source():
 base=(V22/"qualification/mysql_job_v22_exact_mtime_repair_v1.py").read_text()
 base=base.replace('"""Classify exact portable-tree differences; repair only authoritative mtime_ns."""','"""Audit SELinux xattr injection and restore exact source xattrs on a sealed new stage."""')
 start=base.index("def repair(");end=base.index("def main():",start)
 functions=r'''INJECTED_NAME="security.selinux"
INJECTED_VALUE="dW5jb25maW5lZF91Om9iamVjdF9yOnVubGFiZWxlZF90OnMwAA=="
TXN="job-v23-recovery-20260803t121500z-91e7c4a6"
JOB=Path("/mnt/nvme2n1/anonymous/resource_adaptive_mysql/job")
RUNTIME_PARENT=JOB/"runtime_algorithm_c_successor_20260729/runtime"
STAGES={"template":JOB/(".template.stage-"+TXN),"runtime":RUNTIME_PARENT/(".python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89.stage-"+TXN)}
V21_INPUT=JOB/".relocation_v21"/"job-v21-recovery-20260803t105116z-776fe047"
V23_OUTPUT=JOB/".relocation_v23"/TXN
SOURCES={k:V21_INPUT/k/"source-post.json" for k in STAGES}
OUTPUTS={k:{"pre":V23_OUTPUT/k/"pre-repair.json","audit":V23_OUTPUT/k/"xattr-audit.json","post":V23_OUTPUT/k/"stage.json","receipt":V23_OUTPUT/k/"root-xattr-repair.json"} for k in STAGES}
def load_object(p,label):
 p=Path(p);side=Path(str(p)+".sha256")
 if p.is_symlink() or not p.is_file() or side.is_symlink() or not side.is_file() or side.read_text(encoding="ascii")!=digest(p)+"  "+p.name+"\n":raise Error(label+" seal differs")
 v=json.loads(p.read_text());
 if not isinstance(v,dict):raise Error(label+" object required")
 return v
def exact_injection(source,pre):
 cats,rootdiff=classify(source["identity"],pre["identity"]);nonx=sum(len(cats[k]) for k in cats if k!="xattr")+sum(1 for k in rootdiff if k!="root_xattrs_base64")
 sm={x["path"]:x for x in source["identity"]["entries"]};pm={x["path"]:x for x in pre["identity"]["entries"]}
 expected={INJECTED_NAME:INJECTED_VALUE}
 if nonx or len(cats["xattr"])!=source["entry_count"] or set(rootdiff)!={"root_xattrs_base64"} or source["identity"].get("root_xattrs_base64")!={} or pre["identity"].get("root_xattrs_base64")!=expected or any(sm[p].get("xattrs_base64")!={} or pm[p].get("xattrs_base64")!=expected for p in sm):raise Error("difference is not exact all-entry SELinux auto-injection")
 return cats,rootdiff
def audit(kind,root,source_path,pre_out,audit_out):
 if os.geteuid()!=1008 or os.getegid()!=1008:raise Error("audit requires uid/gid 1008")
 if Path(root).resolve(strict=True)!=STAGES[kind].resolve(strict=True):raise Error("fresh v23 stage path differs")
 if Path(source_path).resolve(strict=True)!=SOURCES[kind].resolve(strict=True) or Path(pre_out)!=OUTPUTS[kind]["pre"] or Path(audit_out)!=OUTPUTS[kind]["audit"]:raise Error("audit input/output path differs from sealed transaction")
 source=load(source_path,"source inventory");pre=inventory(root,kind+"-v23-pre-root-repair");cats,rootdiff=exact_injection(source,pre);write(pre_out,pre);receipt={"schema_version":"mysql_job_v23_selinux_xattr_audit_receipt_v1","status":"PASS_EXACT_ALL_ENTRY_SELINUX_INJECTION_ROOT_REPAIR_NOT_AUTHORIZED","kind":kind,"transaction_id":TXN,"stage":str(Path(root).resolve()),"stage_device":Path(root).stat().st_dev,"stage_inode":Path(root).stat().st_ino,"source_inventory":str(Path(source_path).resolve()),"source_inventory_sha256":digest(source_path),"pre_inventory":str(Path(pre_out).resolve()),"pre_inventory_sha256":digest(pre_out),"source_content_identity_sha256":source["content_identity_sha256"],"pre_content_identity_sha256":pre["content_identity_sha256"],"entry_count":source["entry_count"],"logical_unique_file_bytes":source["logical_unique_file_bytes"],"difference_counts":{k:len(v) for k,v in cats.items()},"root_difference":rootdiff,"injected_xattr_name":INJECTED_NAME,"injected_xattr_value_base64":INJECTED_VALUE,"root_repair_performed":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0};write(audit_out,receipt);print(json.dumps({"status":receipt["status"],"kind":kind,"entry_count":receipt["entry_count"],"audit":str(audit_out)},sort_keys=True))
def restore_attrs(path,expected):
 current=set(os.listxattr(path,follow_symlinks=False));wanted=set(expected)
 for name in sorted(current-wanted):os.removexattr(path,name,follow_symlinks=False)
 for name in sorted(wanted):
  value=base64.b64decode(expected[name].encode("ascii"));actual=os.getxattr(path,name,follow_symlinks=False) if name in current else None
  if actual!=value:os.setxattr(path,name,value,follow_symlinks=False)
def handoff(path):
 path=Path(path);side=Path(str(path)+".sha256")
 for item in (path,side):
  if item.is_symlink() or not item.is_file():raise Error("root repair output absent or symlinked: "+str(item))
  os.chown(item,1008,1008,follow_symlinks=False);os.chmod(item,0o400,follow_symlinks=False)
def root_repair(kind,root,source_path,pre_path,audit_path,post_out,receipt_out):
 if os.geteuid()!=0:raise Error("controlled xattr repair requires root")
 root=Path(root).resolve(strict=True)
 if root!=STAGES[kind].resolve(strict=True):raise Error("root helper target is not exact fresh v23 stage")
 if Path(source_path).resolve(strict=True)!=SOURCES[kind].resolve(strict=True) or Path(pre_path).resolve(strict=True)!=OUTPUTS[kind]["pre"].resolve(strict=True) or Path(audit_path).resolve(strict=True)!=OUTPUTS[kind]["audit"].resolve(strict=True) or Path(post_out)!=OUTPUTS[kind]["post"] or Path(receipt_out)!=OUTPUTS[kind]["receipt"]:raise Error("root helper input/output path differs from sealed transaction")
 source=load(source_path,"source inventory");pre=load(pre_path,"pre inventory");audit_receipt=load_object(audit_path,"audit receipt")
 actual=inventory(root,kind+"-v23-root-helper-pre-readback")
 if audit_receipt.get("status")!="PASS_EXACT_ALL_ENTRY_SELINUX_INJECTION_ROOT_REPAIR_NOT_AUTHORIZED" or audit_receipt.get("stage")!=str(root) or audit_receipt.get("stage_device")!=root.stat().st_dev or audit_receipt.get("stage_inode")!=root.stat().st_ino or audit_receipt.get("source_inventory_sha256")!=digest(source_path) or audit_receipt.get("pre_inventory_sha256")!=digest(pre_path) or audit_receipt.get("injected_xattr_name")!=INJECTED_NAME or audit_receipt.get("injected_xattr_value_base64")!=INJECTED_VALUE:raise Error("root repair authority/audit binding differs")
 if actual["identity"]==pre["identity"]:
  exact_injection(source,actual)
  for row in source["identity"]["entries"]:
   rel=Path(row["path"])
   if rel.is_absolute() or ".." in rel.parts:raise Error("unsafe source inventory path")
   restore_attrs(root/rel,row["xattrs_base64"])
  restore_attrs(root,source["identity"]["root_xattrs_base64"])
 elif actual["identity"]!=source["identity"]:raise Error("stage is neither sealed pre-repair nor exact already-repaired source identity")
 post=inventory(root,kind+"-v23-post-root-repair")
 if post["identity"]!=source["identity"] or post["content_identity_sha256"]!=source["content_identity_sha256"]:raise Error("full portable identity did not close after exact xattr restore")
 write(post_out,post);handoff(post_out);receipt={"schema_version":"mysql_job_v23_controlled_root_xattr_repair_receipt_v1","status":"PASS_ROOT_RESTORED_EXACT_SOURCE_XATTRS_AND_FULL_PORTABLE_IDENTITY","kind":kind,"transaction_id":TXN,"stage":str(root),"stage_device":root.stat().st_dev,"stage_inode":root.stat().st_ino,"audit_receipt":str(Path(audit_path).resolve()),"audit_receipt_sha256":digest(audit_path),"source_inventory_sha256":digest(source_path),"pre_inventory_sha256":digest(pre_path),"post_inventory":str(Path(post_out).resolve()),"post_inventory_sha256":digest(post_out),"content_identity_sha256":post["content_identity_sha256"],"xattr_entries_restored":source["entry_count"]+1,"content_changed":False,"hardlinks_changed":False,"mtime_changed":False,"owner_mode_changed":False,"root_euid":0,"output_owner_uid":1008,"output_owner_gid":1008,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0};write(receipt_out,receipt);handoff(receipt_out);print(json.dumps({"status":receipt["status"],"kind":kind,"content_identity_sha256":post["content_identity_sha256"],"receipt":str(receipt_out)},sort_keys=True))
'''
 main=r'''def main():
 p=argparse.ArgumentParser();sp=p.add_subparsers(dest="mode",required=True);v=sp.add_parser("verify-partial");v.add_argument("--root",type=Path,required=True);v.add_argument("--inventory",type=Path,required=True);a=sp.add_parser("audit");a.add_argument("--kind",choices=sorted(STAGES),required=True);a.add_argument("--root",type=Path,required=True);a.add_argument("--source-inventory",type=Path,required=True);a.add_argument("--pre-output",type=Path,required=True);a.add_argument("--audit-receipt",type=Path,required=True);rr=sp.add_parser("root-repair");rr.add_argument("--kind",choices=sorted(STAGES),required=True);rr.add_argument("--root",type=Path,required=True);rr.add_argument("--source-inventory",type=Path,required=True);rr.add_argument("--pre-inventory",type=Path,required=True);rr.add_argument("--audit-receipt",type=Path,required=True);rr.add_argument("--post-output",type=Path,required=True);rr.add_argument("--repair-receipt",type=Path,required=True);x=p.parse_args()
 if x.mode=="verify-partial":verify_partial(x.root,x.inventory)
 elif x.mode=="audit":audit(x.kind,x.root,x.source_inventory,x.pre_output,x.audit_receipt)
 else:root_repair(x.kind,x.root,x.source_inventory,x.pre_inventory,x.audit_receipt,x.post_output,x.repair_receipt)
'''
 base=base[:start]+functions+base[end:]
 base=base[:base.index("def main():",base.index(functions))]+main+base[base.index('if __name__=="__main__":',base.index(functions)):]
 return base.replace("mysql_job_v22", "mysql_job_v23")

def materializer_source():
 s=(V22/"transaction/mysql_job_v22_input_reference_materializer_v1.py").read_text();s=s.replace(OLD,NEW).replace(".relocation_v22",".relocation_v23").replace("mysql_job_v22","mysql_job_v23").replace("v22", "v23").replace("V22", "V23")
 s=once(s,' a=argparse.ArgumentParser();a.add_argument("--execute",required=True);x=a.parse_args()\n if x.execute!=NEW:raise Error("exact v23 transaction id required")\n',' a=argparse.ArgumentParser();g=a.add_mutually_exclusive_group(required=True);g.add_argument("--check",action="store_true");g.add_argument("--execute");x=a.parse_args()\n if x.execute is not None and x.execute!=NEW:raise Error("exact v23 transaction id required")\n',"materializer parser")
 s=once(s,' if not NEW_PARENT.exists():NEW_PARENT.mkdir(mode=0o700)\n if not NEW_PARENT.is_dir() or NEW_PARENT.is_symlink() or NEW_PARENT.stat().st_uid!=1008 or NEW_PARENT.stat().st_gid!=1008 or stat.S_IMODE(NEW_PARENT.stat().st_mode)!=0o700:raise Error("v23 parent differs")\n',' if NEW_PARENT.exists() and (not NEW_PARENT.is_dir() or NEW_PARENT.is_symlink() or NEW_PARENT.stat().st_uid!=1008 or NEW_PARENT.stat().st_gid!=1008 or stat.S_IMODE(NEW_PARENT.stat().st_mode)!=0o700):raise Error("v23 parent differs")\n',"materializer parent precheck")
 s=once(s,' if (JOB/"template").exists() or (JOB/"runtime_algorithm_c_successor_20260729/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89").exists():raise Error("final target unexpectedly exists")\n NEW_ROOT.mkdir(mode=0o700)\n',' if (JOB/"template").exists() or (JOB/"runtime_algorithm_c_successor_20260729/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89").exists():raise Error("final target unexpectedly exists")\n if x.check:\n  print(json.dumps({"status":"PASS_V23_INPUT_REFERENCE_MATERIALIZER_READ_ONLY","new_parent":str(NEW_PARENT),"new_transaction":str(NEW_ROOT),"new_transaction_absent":True,"v21_partial_stages_preserved":True,"formal_launch_authorized":False,"accepted_rounds_credit":0},sort_keys=True));return\n if not NEW_PARENT.exists():NEW_PARENT.mkdir(mode=0o700)\n if not NEW_PARENT.is_dir() or NEW_PARENT.is_symlink() or NEW_PARENT.stat().st_uid!=1008 or NEW_PARENT.stat().st_gid!=1008 or stat.S_IMODE(NEW_PARENT.stat().st_mode)!=0o700:raise Error("v23 parent differs")\n NEW_ROOT.mkdir(mode=0o700)\n',"materializer check stop")
 return s

def recovery_source():
 s=(V22/"transaction/mysql_job_v22_node04_mtime_identity_recovery_20260803t114500z_7c3e91a2.sh").read_text();s=s.replace(OLD,NEW).replace(".relocation_v22",".relocation_v23").replace("mysql_job_v22","mysql_job_v23").replace("v22", "v23").replace("V22", "V23")
 s=s.replace("mysql_job_v23_exact_mtime_repair_v1.py","mysql_job_v23_controlled_selinux_xattr_repair_v1.py")
 s=once(s,'    "  $0 --execute ${TXN}" \\\n', '    "  $0 --execute ${TXN}" \\\n    "  $0 --resume-publish ${TXN}" \\\n',"usage")
 s=s.replace('"them, and publishes runtime first and template second with RENAME_NOREPLACE." \\\n    "All transaction paths and bundle identities are hard-coded; no cleanup exists."','"and audits them without root mutation or publication. After the explicit sealed root" \\\n    "repair, --resume-publish revalidates full identity and uses RENAME_NOREPLACE."')
 s=s.replace("[[ -n \"$ACTION\" ]] || die 'one of --check or --execute is required'","[[ -n \"$ACTION\" ]] || die 'one of --check, --execute, or --resume-publish is required'")
 s=once(s,"      ACTION='execute'\n      shift 2\n      ;;\n", "      ACTION='execute'\n      shift 2\n      ;;\n    --resume-publish)\n      [[ -z \"$ACTION\" && ${2-} == \"$TXN\" ]] || die 'resume requires exact fresh v23 transaction'\n      ACTION='resume'\n      shift 2\n      ;;\n", "resume parser")
 start=s.index("inventory_stage() {");end=s.index("\npublish_one() {",start)
 inv='''inventory_stage() {
  local kind=$1 stage=$2 archive=$3 receipts=$4
  "$BOOTSTRAP_PY" -B "$REPAIR" audit --kind "$kind" --root "$stage" --source-inventory "$archive/source-post.json" --pre-output "$receipts/pre-repair.json" --audit-receipt "$receipts/xattr-audit.json"
}

validate_root_repair() {
  local kind=$1 archive=$2 receipts=$3
  require_evidence_pair "$receipts" stage.json; require_evidence_pair "$receipts" root-xattr-repair.json; require_evidence_pair "$receipts" xattr-audit.json; require_evidence_pair "$receipts" pre-repair.json
  "$BOOTSTRAP_PY" -B "$INVENTORY" --compare-source "$archive/source-post.json" --compare-destination "$receipts/stage.json" --output "$receipts/source-stage.json"
  validate_comparison "$receipts/source-stage.json"
}
'''
 s=s[:start]+inv+s[end:]
 oldfresh='''check_fresh_outputs() {
  local d path
  require_absent "$TEMPLATE_STAGE"; require_absent "$RUNTIME_STAGE"; require_absent "$TEMPLATE_FINAL"; require_absent "$RUNTIME_FINAL"
  for d in "$TEMPLATE_RECEIPTS" "$RUNTIME_RECEIPTS"; do
    [[ -d "$d" && ! -L "$d" ]] || die "v23 receipt directory differs: $d"
    for path in pre-repair.json pre-repair.json.sha256 mtime-repair.json mtime-repair.json.sha256 stage.json stage.json.sha256 source-stage.json source-stage.json.sha256 publication.json publication.json.sha256 final.json final.json.sha256 source-final.json source-final.json.sha256; do require_absent "$d/$path"; done
  done
}
'''
 newfresh='''check_fresh_outputs() {
  local d path
  require_absent "$TEMPLATE_STAGE"; require_absent "$RUNTIME_STAGE"; require_absent "$TEMPLATE_FINAL"; require_absent "$RUNTIME_FINAL"
  for d in "$TEMPLATE_RECEIPTS" "$RUNTIME_RECEIPTS"; do [[ -d "$d" && ! -L "$d" ]] || die "v23 receipt directory differs: $d"; for path in pre-repair.json pre-repair.json.sha256 xattr-audit.json xattr-audit.json.sha256 stage.json stage.json.sha256 root-xattr-repair.json root-xattr-repair.json.sha256 source-stage.json source-stage.json.sha256 publication.json publication.json.sha256 final.json final.json.sha256 source-final.json source-final.json.sha256;do require_absent "$d/$path";done;done
}
check_resume_outputs() {
  [[ -d "$TEMPLATE_STAGE" && ! -L "$TEMPLATE_STAGE" && -d "$RUNTIME_STAGE" && ! -L "$RUNTIME_STAGE" ]] || die 'fresh v23 audited stages absent'
  require_absent "$TEMPLATE_FINAL";require_absent "$RUNTIME_FINAL"
  for d in "$TEMPLATE_RECEIPTS" "$RUNTIME_RECEIPTS";do for path in pre-repair.json xattr-audit.json stage.json root-xattr-repair.json;do require_evidence_pair "$d" "$path";done;for path in source-stage.json source-stage.json.sha256 publication.json publication.json.sha256 final.json final.json.sha256 source-final.json source-final.json.sha256;do require_absent "$d/$path";done;done
}
'''
 s=once(s,oldfresh,newfresh,"fresh/resume")
 marker='''check_raw_renameat2_capability
check_fresh_outputs

if [[ "$ACTION" == 'check' ]]; then
'''
 repl='''check_raw_renameat2_capability
if [[ "$ACTION" == 'resume' ]];then check_resume_outputs;validate_root_repair runtime "$RUNTIME_ARCHIVE" "$RUNTIME_RECEIPTS";validate_root_repair template "$TEMPLATE_ARCHIVE" "$TEMPLATE_RECEIPTS";else check_fresh_outputs;fi

if [[ "$ACTION" == 'check' ]]; then
'''
 s=once(s,marker,repl,"action preflight")
 marker='''[[ $inventory_rc -eq 0 ]] || die 'parallel stage inventory failed; stages retained, finals untouched'

# Deliberate order: runtime first, then its final readback; template second.
'''
 repl='''[[ $inventory_rc -eq 0 ]] || die 'parallel xattr audit failed; stages retained, finals untouched'
printf 'PASS_V23_STAGE_AUDIT_ROOT_REPAIR_REQUIRED: txn=%s stages=new_v23_only root_mutation=false finals=absent accepted=0\n' "$TXN"
exit 0

# Deliberate order: runtime first, then its final readback; template second.
'''
 s=once(s,marker,repl,"audit stop")
 # Resume must jump around extraction/audit into publication; insert a branch before stage creation.
 anchor="# All immutable inputs and every no-overwrite target passed before first write.\n"
 s=once(s,anchor,"if [[ \"$ACTION\" == 'resume' ]];then publish_one runtime \"$RUNTIME_STAGE\" \"$RUNTIME_FINAL\" \"$RUNTIME_ARCHIVE\" \"$RUNTIME_RECEIPTS\";publish_one template \"$TEMPLATE_STAGE\" \"$TEMPLATE_FINAL\" \"$TEMPLATE_ARCHIVE\" \"$TEMPLATE_RECEIPTS\";printf 'PASS_V23_XATTR_RECOVERY_PUBLISHED_NOT_LAUNCH_AUTHORITY: txn=%s accepted=0\\n' \"$TXN\";exit 0;fi\n\n"+anchor,"resume publish")
 s=s.replace("PASS_V23_MTIME_IDENTITY_RECOVERY_READ_ONLY","PASS_V23_SELINUX_XATTR_RECOVERY_READ_ONLY")
 unreachable="# Deliberate order: runtime first, then its final readback; template second.\n"
 if s.count(unreachable)!=1:raise RuntimeError("unreachable publication tail differs")
 s=s[:s.index(unreachable)]
 return s

TEST=r'''#!/usr/bin/env bash
set -Eeuo pipefail;B=${1:?};S="$B/transaction/mysql_job_v23_node04_selinux_xattr_recovery_20260803t121500z_91e7c4a6.sh";H="$B/qualification/mysql_job_v23_controlled_selinux_xattr_repair_v1.py";M="$B/transaction/mysql_job_v23_input_reference_materializer_v1.py";bash -n "$S";python3 -B - "$H" "$M" <<'PY'
import pathlib,sys
for p in map(pathlib.Path,sys.argv[1:]):compile(p.read_text(),str(p),"exec")
PY
rg -F 'PASS_V23_STAGE_AUDIT_ROOT_REPAIR_REQUIRED' "$S" >/dev/null;rg -F 'PASS_V23_INPUT_REFERENCE_MATERIALIZER_READ_ONLY' "$M" >/dev/null;rg -F 'controlled xattr repair requires root' "$H" >/dev/null;rg -F 'difference is not exact all-entry SELinux auto-injection' "$H" >/dev/null;rg -F 'full portable identity did not close' "$H" >/dev/null;rg -F 'security.selinux' "$H" >/dev/null;rg -F -- '--resume-publish' "$S" >/dev/null
if rg -n '(^|[;&|[:space:]])(rm|rmdir|unlink|shred|truncate|mv)([[:space:]]|$)' "$S" "$M";then exit 1;fi
printf 'PASS_OFFLINE_V23: audit_before_root exact_selinux_only controlled_root_new_stage full_identity resume_noreplace v21_preserved\n'
'''
def main():
 m=json.loads((V22/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")
 if m.get("bundle_id")!=V22_ID or sf(V22/"bundle_manifest.json")!=V22_SHA or sb(canonical(u))!=V22_ID:raise SystemExit("v22 closure differs")
 script=recovery_source().encode();material=materializer_source().encode();helper=helper_source().encode();publisher=(V22/"qualification/mysql_job_v22_raw_syscall_atomic_tree_publish_v1.py").read_text().replace(OLD,NEW).replace("v22","v23").replace("V22","V23").encode()
 invalid={"schema_version":"mysql_job_v23_v22_offline_invalidation_v1","status":"INVALIDATED_OFFLINE_NEVER_PUBLISH_OR_EXECUTE","v22_bundle_id":V22_ID,"reason":"real v21 difference is exact all-entry security.selinux injection, not mtime","evidence":{"runtime":{"entry_count":40852,"source_xattrs_empty_count":40852,"stage_injected_xattr_count":40852,"allocation_bytes":[1140178944,1141305344]},"template":{"entry_count":193,"source_xattrs_empty_count":193,"stage_injected_xattr_count":193,"allocation_bytes_equal":True},"root_same_injection":True,"all_content_hardlink_mtime_owner_mode_type_missing_differences":0,"injected_name":"security.selinux","injected_value_base64":"dW5jb25maW5lZF91Om9iamVjdF9yOnVubGFiZWxlZF90OnMwAA=="},"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"accepted_rounds_credit":0,"remote_actions":0}
 plan={"schema_version":"mysql_job_v23_selinux_xattr_recovery_successor_plan_v1","status":"SEALED_THREE_PHASE_XATTR_RECOVERY_NOT_EXECUTED","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"predecessor_v22_bundle_id":V22_ID,"v22_publish_execute_prohibited":True,"failed_v21_transaction":V21_TXN,"v21_stages_preserved_no_reuse_delete":True,"new_transaction":NEW,"phases":["uid1008_check_and_new_stage_audit_no_root_mutation","explicit_root_exact_source_xattr_restore_on_new_stage_only","uid1008_full_identity_recheck_and_raw_noreplace_publish"],"all_non_xattr_difference_fail_closed":True,"exact_injected_selinux_value_required":True,"source_xattrs_restored_not_ignored":True,"full_portable_identity_required":True,"root_helper_exact_stage_inode_audit_binding_required":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0}
 content={SCRIPT:script,MATERIALIZER:material,HELPER:helper,"qualification/mysql_job_v23_raw_syscall_atomic_tree_publish_v1.py":publisher,"authority/v22_offline_invalidation.json":canonical(invalid),"plan/recovery_plan.json":canonical(plan),"tests/test_mysql_job_v23_selinux_xattr_recovery_successor_v1.sh":TEST.encode(),BUILDER:HERE.read_bytes()};files={k:sb(v) for k,v in sorted(content.items())};identity=dict(plan,predecessor_bundle_id=V22_ID,predecessor_manifest_sha256=V22_SHA,v21_archive_bundle_id=V21_ID,v21_archive_manifest_sha256=V21_SHA,invalidated_v22_sha256=sb(canonical(invalid)),script_sha256=sb(script),materializer_sha256=sb(material),helper_sha256=sb(helper),publisher_sha256=sb(publisher),immutable=True,files=files);bid=sb(canonical(identity));manifest=dict(identity,bundle_id=bid);raw=canonical(manifest);OUT.mkdir(parents=True,exist_ok=True);dst=OUT/bid
 if not dst.exists():
  stage=Path(tempfile.mkdtemp(prefix=".job-v23-xattr-",dir=OUT))
  for rel,data in content.items():p=stage/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);p.chmod(0o555 if rel.endswith((".py",".sh")) else 0o444)
  (stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json").chmod(0o444);(stage/"bundle_manifest.json.sha256").write_text(sb(raw)+"  bundle_manifest.json\n");(stage/"bundle_manifest.json.sha256").chmod(0o444)
  for p in sorted(stage.rglob("*"),reverse=True):
   if p.is_dir():p.chmod(0o555)
  stage.chmod(0o555);os.replace(stage,dst)
 print(json.dumps({"bundle":str(dst),"bundle_id":bid,"manifest_sha256":sf(dst/"bundle_manifest.json"),"script_sha256":sb(script),"materializer_sha256":sb(material),"helper_sha256":sb(helper),"remote_actions":0},sort_keys=True))
if __name__=="__main__":main()
