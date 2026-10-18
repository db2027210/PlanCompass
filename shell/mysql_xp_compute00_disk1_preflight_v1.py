#!/usr/bin/env python3
"""Read-only/fail-closed host preflight for the compute00 /disk1 XP lane."""
from __future__ import annotations
import argparse,hashlib,importlib.util,json,os,re,socket,sys
from pathlib import Path
from typing import Any

HOST="compute00";MOUNT=Path("/disk1");DEVICE="/dev/nvme1n1";MAJOR_MINOR="259:6";DEVICE_NUMA=0
XP_CPUS="184-199,440-455";DB_CPUS="184-191,440-447";CLIENT_CPUS="192-199,448-455";PG_CPUS="32-51,288-307";MEMS="1";PORT=65104
RUNTIME=Path("/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/runtime_algorithm_c_successor_20260803")
TEMPLATE=Path("/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/template_v2")
FORBIDDEN_PATH_PREFIX="/disk2";FORBIDDEN_NAMES=("mysqld","hybench","benchbase","mysql_xp","mysql-xp")
class PreflightError(RuntimeError):pass
def canonical_module():
 p=Path(__file__).with_name("mysql_xp_canonical_template_v2.py")
 spec=importlib.util.spec_from_file_location("mysql_xp_canonical_template_v2",p);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
def cpus(value:str)->set[int]:
 out=set()
 for part in value.split(","):
  lo,*hi=part.split("-");out.update(range(int(lo),int(hi[0])+1) if hi else [int(lo)])
 return out
def sha(p:Path)->str:return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def mount_row(path=Path("/proc/self/mountinfo"))->dict[str,Any]:
 rows=[]
 for line in path.read_text().splitlines():
  f=line.split();i=f.index("-")
  if Path(f[4]).resolve()==MOUNT.resolve():rows.append({"major_minor":f[2],"mount_options":f[5].split(","),"filesystem":f[i+1],"source":f[i+2],"super_options":f[i+3].split(",")})
 if len(rows)!=1:raise PreflightError("/disk1 exact mount identity differs")
 return rows[0]
def ancestor_pids(proc:Path)->set[int]:
 """Exclude the preflight and its launch ancestry from workload matching."""
 out:set[int]=set();pid=os.getpid()
 while pid>0 and pid not in out:
  out.add(pid)
  try:status=(proc/str(pid)/"status").read_text(errors="strict")
  except (OSError,UnicodeError):break
  m=re.search(r"^PPid:\s*(\d+)",status,re.M)
  if not m:break
  pid=int(m.group(1))
 return out
def process_inventory(proc=Path("/proc"),exclude_pids:set[int]|None=None)->list[dict[str,Any]]:
 rows=[];target=cpus(XP_CPUS);excluded=ancestor_pids(proc) if exclude_pids is None and proc==Path("/proc") else set(exclude_pids or ())
 for d in proc.iterdir():
  if not d.name.isdigit() or int(d.name) in excluded:continue
  try:
   status=(d/"status").read_text(errors="strict");cmd=(d/"cmdline").read_bytes().replace(b"\0",b" ").decode(errors="strict").strip()
  except (OSError,UnicodeError):continue
  m=re.search(r"^Cpus_allowed_list:\s*(\S+)",status,re.M)
  if not m or not cmd:continue
  allowed=cpus(m.group(1));lower=cmd.lower()
  if target&allowed and any(name in lower for name in FORBIDDEN_NAMES):rows.append({"pid":int(d.name),"cpus_allowed_list":m.group(1),"cmdline_sha256":hashlib.sha256(cmd.encode()).hexdigest(),"classification":"database_or_benchmark"})
 return sorted(rows,key=lambda x:x["pid"])
def validate(*,hostname:str,row:dict[str,Any],device_dev:str,device_numa:int,node1:set[int],online:set[int],conflicts:list[dict[str,Any]],runtime_exists:bool,template_exists:bool,template_identity:dict[str,Any]|None,port_free:bool,require_ready:bool=True)->dict[str,Any]:
 target,db,client,pg=map(cpus,(XP_CPUS,DB_CPUS,CLIENT_CPUS,PG_CPUS))
 errors=[]
 if hostname!=HOST:errors.append("hostname")
 if row.get("source")!=DEVICE or row.get("major_minor")!=MAJOR_MINOR or row.get("filesystem")!="ext4":errors.append("disk1_device")
 opts=set(row.get("mount_options",[]))|set(row.get("super_options",[]))
 if "rw" not in opts:errors.append("disk1_read_only")
 if device_dev!=MAJOR_MINOR or device_numa!=DEVICE_NUMA:errors.append("device_sysfs")
 if target!=db|client or db&client or target&pg:errors.append("cpu_partition")
 if not target<=node1 or not target<=online:errors.append("cpu_topology")
 if conflicts:errors.append("database_or_benchmark_cpu_conflict")
 if runtime_exists:errors.append("runtime_root_not_fresh")
 if not template_exists:errors.append("xp_template_absent")
 elif not isinstance(template_identity,dict) or template_identity.get("status")!="PASS_EXACT_CANONICAL_TEMPLATE":errors.append("xp_template_canonical_identity")
 if not port_free:errors.append("mysql_port_busy")
 result={"schema_version":"mysql_xp_compute00_disk1_preflight_v2","status":"PASS_READY_NOT_LAUNCHED" if not errors else "NOT_READY","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"disk2_accessed":False,"host":hostname,"storage":{"mount":str(MOUNT),"device":DEVICE,"major_minor":MAJOR_MINOR,"mount_options":sorted(opts),"device_numa_node":device_numa,"whole_disk_filesystem_and_throttle_identity_same":True},"cpu":{"reserved":XP_CPUS,"database":DB_CPUS,"client":CLIENT_CPUS,"postgresql_reserved":PG_CPUS,"overlap":sorted(target&pg),"mems":MEMS},"conflicts":conflicts,"runtime_root":str(RUNTIME),"runtime_root_exists":runtime_exists,"template":str(TEMPLATE),"template_exists":template_exists,"template_identity":template_identity,"mysql_port":PORT,"mysql_port_free":port_free,"errors":errors,"formal_envelope":{"cpu_cores":5.3,"read_bps":29360128,"write_bps":524288},"capture_only_io_bps_per_direction":1205862400,"capture_is_r0":False,"capture_accepted_round_credit":0,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0}
 if require_ready and errors:raise PreflightError(",".join(errors))
 return result
def observe(require_ready=True):
 row=mount_row();node1=cpus(Path("/sys/devices/system/node/node1/cpulist").read_text().strip());online=cpus(Path("/sys/devices/system/cpu/online").read_text().strip())
 probe=socket.socket();probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,0)
 try:probe.bind(("127.0.0.1",PORT));free=True
 except OSError:free=False
 finally:probe.close()
 exists=TEMPLATE.is_dir();identity=canonical_module().verify_template(TEMPLATE) if exists else None
 return validate(hostname=socket.gethostname(),row=row,device_dev=Path("/sys/class/block/nvme1n1/dev").read_text().strip(),device_numa=int(Path("/sys/class/block/nvme1n1/device/numa_node").read_text()),node1=node1,online=online,conflicts=process_inventory(),runtime_exists=RUNTIME.exists(),template_exists=exists,template_identity=identity,port_free=free,require_ready=require_ready)
def main():
 p=argparse.ArgumentParser();p.add_argument("--observe-not-ready",action="store_true");p.add_argument("--output",type=Path);a=p.parse_args();v=observe(not a.observe_not_ready)
 raw=(json.dumps(v,indent=2,sort_keys=True)+"\n").encode()
 if a.output:
  if a.output.exists():raise PreflightError("refusing to overwrite output")
  a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_bytes(raw);Path(str(a.output)+".sha256").write_text(hashlib.sha256(raw).hexdigest()+"  "+a.output.name+"\n")
 print(raw.decode(),end="");return 0 if v["status"].startswith("PASS") else 3
if __name__=="__main__":
 try:raise SystemExit(main())
 except (PreflightError,OSError,ValueError) as e:sys.stderr.write("NOT_READY: %s\n"%e);raise SystemExit(2)
