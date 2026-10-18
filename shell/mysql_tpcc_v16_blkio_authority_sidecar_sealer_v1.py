#!/usr/bin/env python3
"""Validate the live v16 blkio authority and add its missing SHA sidecar."""
from __future__ import annotations
import argparse,fcntl,hashlib,json,os,re,stat,subprocess,sys
from pathlib import Path
from typing import Any
V16_ID="7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77";V16_SHA="e9d5e2213a14d2289c07063c0b24dfb68d6059f068c5c91388862f8a873b4cba";ATTEMPT="tpcc-v16-7e2e13aa-20260801t121500p08";AUTH_SHA="6822e90cc8e6980b6b0030f02f9a2b53a7b5fc23eeb58f214ea26de5a585661d";SOCKET="unix:///run/ra-docker-mysql-tpcc-v1.sock";MM="259:2";BPS=1205862400;HEX=re.compile(r"^[0-9a-f]{64}$")
class Error(RuntimeError):pass
def canonical(v:Any)->bytes:return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sha_bytes(v:bytes)->str:return hashlib.sha256(v).hexdigest()
def regular(p:Path,label:str)->Path:
 p=Path(p)
 if p.is_symlink() or not p.is_file():raise Error(label+" regular file required")
 return p.resolve(strict=True)
def sealed(p:Path,label:str)->tuple[Path,dict[str,Any]]:
 p=regular(p,label);s=regular(Path(str(p)+".sha256"),label+" sidecar");raw=p.read_bytes()
 if s.read_text(encoding="ascii")!=sha_bytes(raw)+"  "+p.name+"\n":raise Error(label+" sidecar differs")
 v=json.loads(raw)
 if not isinstance(v,dict):raise Error(label+" object required")
 return p,v
def bundle(root:Path)->None:
 root=Path(root).resolve(strict=True);p,v=sealed(root/"bundle_manifest.json","v16 manifest")
 if root.name!=V16_ID or v.get("bundle_id")!=V16_ID or sha_bytes(p.read_bytes())!=V16_SHA or v.get("formal_scope")!="mysql_only" or v.get("postgresql_mutation_prohibited") is not True:raise Error("v16 bundle differs")
def proc_ticks(pid:int)->int:
 raw=(Path("/proc")/str(pid)/"stat").read_text();end=raw.rfind(")");return int(raw[end+2:].split()[19])
def rate(path:Path)->int|None:
 found=[]
 for line in regular(path,"blkio throttle").read_text().splitlines():
  parts=line.split()
  if len(parts)==2 and parts[0]==MM:found.append(int(parts[1]))
 if len(found)>1:raise Error("duplicate blkio device rows")
 return found[0] if found else None
def validate_live(authority:Path,adopted:dict[str,Any])->tuple[bytes,dict[str,Any]]:
 authority=regular(authority,"blkio authority");expected=Path(str((adopted.get("database") or {}).get("datadir") or "")).parent.parent/"blkio_throttle_authority.json"
 if authority!=expected.resolve(strict=True):raise Error("blkio authority path differs")
 with authority.open("rb") as stream:
  fcntl.flock(stream.fileno(),fcntl.LOCK_SH);before=os.fstat(stream.fileno());raw=stream.read();after=os.fstat(stream.fileno())
 if (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):raise Error("blkio authority changed while hashing")
 try:v=json.loads(raw)
 except json.JSONDecodeError as e:raise Error("blkio authority JSON differs") from e
 if sha_bytes(raw)!=AUTH_SHA:raise Error("blkio authority exact observed SHA differs")
 db=adopted.get("database") or {};cid=db.get("container_id");r=subprocess.run(["/usr/bin/docker","--host",SOCKET,"inspect","--format","{{json .}}",str(cid)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=20)
 try:live=json.loads(r.stdout)
 except json.JSONDecodeError as e:raise Error("live TPC-C inspect JSON differs") from e
 state=live.get("State") or {};pid=int(state.get("Pid") or 0);cgroup=Path("/sys/fs/cgroup/blkio/docker")/str(cid);read=cgroup/"blkio.throttle.read_bps_device";write=cgroup/"blkio.throttle.write_bps_device"
 checks=(r.returncode==0 and HEX.fullmatch(str(cid or "")) is not None and live.get("Id")==cid and state.get("Running") is True and pid>0 and v.get("schema_version")=="mysql_tpcc_blkio_v1_throttle_authority_v1" and v.get("status")=="PASS" and v.get("bundle_id")==V16_ID and v.get("attempt_id")==ATTEMPT and v.get("database_container_id")==cid and int(v.get("database_pid") or 0)==pid and v.get("cgroup_path")==str(cgroup) and v.get("device_major_minor")==MM and int(v.get("read_bps") or 0)==BPS and int(v.get("write_bps") or 0)==BPS and v.get("read_throttle_file")==str(read) and v.get("write_throttle_file")==str(write) and v.get("docker_hostconfig_readback_not_authoritative") is True and v.get("workload_started") is False and v.get("formal_launch_authorized") is False and v.get("accepted_round_authorized") is False and rate(read)==BPS and rate(write)==BPS)
 if not checks:raise Error("live blkio authority fields/readback differ")
 return raw,{"database_container_id":cid,"database_pid":pid,"database_pid_start_ticks":proc_ticks(pid),"cgroup_path":str(cgroup),"device_major_minor":MM,"read_bps":BPS,"write_bps":BPS}
def write_sidecar(authority:Path,raw:bytes)->tuple[Path,bool]:
 side=Path(str(authority)+".sha256");payload=(sha_bytes(raw)+"  "+authority.name+"\n").encode("ascii")
 if side.exists() or side.is_symlink():
  if side.is_symlink() or not side.is_file() or side.read_bytes()!=payload:raise Error("existing blkio sidecar differs")
  return side,False
 fd=os.open(side,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o400)
 try:os.write(fd,payload);os.fsync(fd);os.fchown(fd,authority.stat().st_uid,authority.stat().st_gid);os.fchmod(fd,0o400)
 finally:os.close(fd)
 if authority.read_bytes()!=raw or side.read_bytes()!=payload:raise Error("blkio sidecar post-write closure differs")
 return side,True
def seal_output(path:Path,value:dict[str,Any]):
 if path.exists():raise Error("fresh receipt required")
 path.parent.mkdir(parents=True,exist_ok=True);raw=canonical(value);path.write_bytes(raw);os.chmod(path,0o400);s=Path(str(path)+".sha256");s.write_text(sha_bytes(raw)+"  "+path.name+"\n");os.chmod(s,0o400)
def main()->int:
 p=argparse.ArgumentParser();p.add_argument("--v16-bundle",type=Path,required=True);p.add_argument("--adopted-service",type=Path,required=True);p.add_argument("--authority",type=Path,required=True);p.add_argument("--output",type=Path,required=True);a=p.parse_args();bundle(a.v16_bundle);_,adopted=sealed(a.adopted_service,"adopted service")
 if adopted.get("schema_version")!="mysql_tpcc_node04_adopted_service_authority_v2" or adopted.get("status")!="PASS" or adopted.get("bundle_id")!=V16_ID or adopted.get("attempt_id")!=ATTEMPT:raise Error("adopted service differs")
 raw,live=validate_live(a.authority,adopted);side,created=write_sidecar(a.authority,raw);receipt={"schema_version":"mysql_tpcc_v16_blkio_authority_sidecar_seal_v1","status":"PASS_VALIDATED_LIVE_BLKIO_AUTHORITY_SIDECAR","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"authority_path":str(a.authority.resolve()),"authority_sha256":sha_bytes(raw),"sidecar_path":str(side.resolve()),"sidecar_content":side.read_text(),"sidecar_created":created,"authority_content_modified":False,"database_restarted":False,"client_restarted":False,"workload_started":False,"live_identity":live,"accepted_round_authorized":False,"accepted_rounds_credit":0};seal_output(a.output,receipt);print(json.dumps({"status":receipt["status"],"authority_sha256":receipt["authority_sha256"],"sidecar_created":created,"output":str(a.output)},sort_keys=True));return 0
if __name__=="__main__":
 try:raise SystemExit(main())
 except (Error,OSError,UnicodeError,ValueError,KeyError,subprocess.SubprocessError) as e:print("NOT_READY: "+str(e),file=sys.stderr);raise SystemExit(2)
