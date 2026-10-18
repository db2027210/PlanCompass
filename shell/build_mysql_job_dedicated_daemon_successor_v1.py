#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,os,secrets,shutil,sys
from pathlib import Path
R=Path(__file__).resolve().parents[1];S=Path(__file__).resolve();P=R/"artifacts/mysql_job_v36_measurement_parent_successor_v1/a2766c83b0b3c36841e8825194cf89ef23c6e903b42db7e31fc7454a036adcd8";O=R/"artifacts/mysql_job_dedicated_daemon_successor_v1"
def c(v):return (json.dumps(v,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
def h(p):
 x=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):x.update(b)
 return x.hexdigest()
def main():
 pm=json.loads((P/"bundle_manifest.json").read_text());assert P.name==pm["bundle_id"] and h(P/"bundle_manifest.json")=="4288dc347ee071fa62c167409397872a80cb2b105cf6be437277ef493a5edcaa"
 payload={k:(P/k).read_bytes() for k in pm["files"] if k!="closure.json"};payload["authority/mysql_job_dedicated_daemon_contract_v1.py"]=(R/"shell/mysql_job_dedicated_daemon_contract_v1.py").read_bytes();payload["tests/test_mysql_job_dedicated_daemon_contract_v1.py"]=(R/"shell/test_mysql_job_dedicated_daemon_contract_v1.py").read_bytes();inv={"schema_version":"mysql_job_default_daemon_invalidation_v1","status":"INVALIDATED_DO_NOT_RERUN","predecessor_bundle_id":P.name,"reason":"default daemon DockerRootDir/GraphDriver not proven non-PG","accepted_round_credit":0,"formal_launch_authorized":False,"accepted_round_authorized":False};payload["authority/mysql_job_default_daemon_invalidation_20260731.json"]=c(inv);files={k:hashlib.sha256(v).hexdigest() for k,v in sorted(payload.items())};ident={k:v for k,v in pm.items() if k not in("bundle_id","files")};contract={"socket":"/run/ra-docker-mysql-job-v1.sock","data_root":"/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/docker_ra_job_v1","device_major_minor":"259:3","unit":"ra-docker-mysql-job-v1.service","daemon_cpus":"34-35,70-71","bridge":"none","iptables":False,"db_client_network":"host","mysql_server_port":65102,"all_docker_argv_explicit_host_required":True,"all_graphdriver_paths_non_pg_required":True};ident.update({"schema_version":"mysql_job_dedicated_daemon_successor_bundle_v1","predecessor_bundle_id":P.name,"predecessor_manifest_sha256":h(P/"bundle_manifest.json"),"dedicated_daemon_contract":contract,"default_daemon_invalidation_sha256":hashlib.sha256(payload["authority/mysql_job_default_daemon_invalidation_20260731.json"]).hexdigest(),"formal_launch_authorized":False,"accepted_round_authorized":False,"files":files});bid=hashlib.sha256(c(ident)).hexdigest();ident["bundle_id"]=bid;O.mkdir(parents=True,exist_ok=True);t=O/bid
 if not t.exists():
  d=O/(".stage-"+bid+"-"+secrets.token_hex(6));d.mkdir(mode=0o700)
  try:
   for n,v in payload.items():q=d/n;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(v);os.chmod(q,0o444)
   raw=c(ident);(d/"bundle_manifest.json").write_bytes(raw);(d/"bundle_manifest.json.sha256").write_text(hashlib.sha256(raw).hexdigest()+"  bundle_manifest.json\n");os.rename(d,t)
  except BaseException:
   if d.exists():shutil.rmtree(d)
   raise
 elif json.loads((t/"bundle_manifest.json").read_text())!=ident:raise RuntimeError("existing differs")
 print(json.dumps({"status":"PASS_SEALED_DEDICATED_DAEMON_SUCCESSOR","bundle":str(t),"bundle_id":bid,"manifest_sha256":h(t/"bundle_manifest.json"),"accepted_round_authorized":False},sort_keys=True))
if __name__=="__main__":main()
