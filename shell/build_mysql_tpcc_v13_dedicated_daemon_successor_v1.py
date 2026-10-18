#!/usr/bin/env python3
"""Offline, clean-room v13 TPC-C dedicated-daemon publisher."""
from __future__ import annotations
import hashlib,json,os,re,secrets,shutil,sys
from pathlib import Path
R=Path(__file__).resolve().parents[1]; P=R/'artifacts/mysql_tpcc_typed_distributed_successor_v13/4e87faac238a2ecd1523f9dfc8044593243f732b3f1818a1f4f6fda560d0c8ab'; O=R/'artifacts/mysql_tpcc_v13_dedicated_daemon_successor_v1'; SOCK='unix:///run/ra-docker-mysql-tpcc-v1.sock'; SHA='2172eae52c4736ba1afbf4a1342f8e02d20db6fa771828839db14cc9e5a17e90'
def c(v):return (json.dumps(v,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+'\n').encode()
def h(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def patch(n,s):
 s=s.replace('"docker",','"docker", "--host", "'+SOCK+'",').replace("['docker',","['docker', '--host', '"+SOCK+"',")
 s=re.sub(r'(?<![A-Za-z0-9_-])docker (?=(?:run|exec|inspect|ps|stop|rm|image|info|create|start)\b)','docker --host '+SOCK+' ',s)
 s=s.replace('--publish", f"127.0.0.1:{PORT}:3306"','--network", "host"').replace('"--publish", f"127.0.0.1:{PORT}:3306"','"--network", "host"')
 s=s.replace('"--publish", f"127.0.0.1:{args.port}:3306"','"--network", "host"')
 s=s.replace('"--main-agent-token"','"--main-agent-token-file"')
 return s
def main():
 m=json.loads((P/'bundle_manifest.json').read_text()); assert P.name==m['bundle_id'] and h(P/'bundle_manifest.json')==SHA
 q={}
 for n in m['files']:
  if n=='closure.json':continue
  v=(P/n).read_bytes();q[n]=patch(n,v.decode()).encode() if n.endswith('.py') else v
 q['authority/mysql_tpcc_v13_dedicated_daemon_validator_v1.py']=(R/'shell/mysql_tpcc_v13_dedicated_daemon_validator_v1.py').read_bytes()
 q['materials/mysql_tpcc_v13_dedicated_issuer_v1.py']=(R/'shell/mysql_tpcc_v13_dedicated_issuer_v1.py').read_bytes()
 q['tests/test_mysql_tpcc_v13_dedicated_issuer_v1.py']=(R/'shell/test_mysql_tpcc_v13_dedicated_issuer_v1.py').read_bytes()
 contract={'socket':'/run/ra-docker-mysql-tpcc-v1.sock','data_root':'/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/docker_ra_tpcc_v1','device_major_minor':'259:2','unit':'ra-docker-mysql-tpcc-v1.service','db_client_network':'host','mysql_server_port':65106,'all_docker_argv_explicit_host_required':True,'direct_host_daemon_required':True,'daemon_identity':['unit','MainPID','startticks','ppid=1','cgroup','namespaces','no_docker_ancestry'],'graphdriver_fields':['UpperDir','WorkDir','MergedDir','LowerDir'],'failed_quiescence_required':True,'token_file_only':True,'typed_trace_contract':['34_bindings','Delivery','NewOrder','OrderStatus','Payment','StockLevel','L2_L3','QPS_resource_input_prohibited']}
 q['authority/mysql_tpcc_v13_dedicated_daemon_contract_v1.json']=c(contract)
 q['authority/mysql_tpcc_v13_default_daemon_invalidation_20260731.json']=c({'status':'INVALIDATED_DO_NOT_USE','reason':'default socket/daemon cannot prove direct-host identity or non-PG GraphDriver','accepted_round_credit':0,'formal_launch_authorized':False,'accepted_round_authorized':False})
 files={n:hashlib.sha256(v).hexdigest() for n,v in sorted(q.items())}; close={'schema_version':'mysql_tpcc_v13_dedicated_daemon_closure_v1','files':files,'dedicated_daemon_contract':contract};q['closure.json']=c(close);files={n:hashlib.sha256(v).hexdigest() for n,v in sorted(q.items())}
 x={k:v for k,v in m.items() if k not in ('bundle_id','files')};x.update({'schema_version':'mysql_tpcc_v13_dedicated_daemon_successor_bundle_v1','predecessor_bundle_id':P.name,'predecessor_manifest_sha256':SHA,'dedicated_daemon_contract':contract,'formal_launch_authorized':False,'accepted_round_authorized':False,'files':files});bid=hashlib.sha256(c(x)).hexdigest();x['bundle_id']=bid;O.mkdir(parents=True,exist_ok=True);t=O/bid
 if not t.exists():
  d=O/('.stage-'+secrets.token_hex(8));d.mkdir(mode=0o700)
  try:
   for n,v in q.items():p=d/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v);os.chmod(p,0o444)
   raw=c(x);(d/'bundle_manifest.json').write_bytes(raw);(d/'bundle_manifest.json.sha256').write_text(hashlib.sha256(raw).hexdigest()+'  bundle_manifest.json\n');os.rename(d,t)
  except BaseException:
   if d.exists():shutil.rmtree(d)
   raise
 print(json.dumps({'status':'PASS_SEALED_TPCC_DEDICATED_DAEMON_SUCCESSOR','bundle':str(t),'bundle_id':bid,'manifest_sha256':h(t/'bundle_manifest.json'),'formal_launch_authorized':False,'accepted_round_authorized':False},sort_keys=True))
if __name__=='__main__':main()
