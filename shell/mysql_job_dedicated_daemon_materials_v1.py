#!/usr/bin/env python3
from __future__ import annotations
import argparse,datetime,hashlib,json,os,secrets
from pathlib import Path
B="49d8ff4b02a4e3aa88d87cc05638b0fb8504805213602209d113943cd3a91d21"
def c(v):return (json.dumps(v,ensure_ascii=True,indent=2,sort_keys=True)+"\n").encode()
def h(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument("--output-root",type=Path,required=True);p.add_argument("--ttl-seconds",type=int,default=900);a=p.parse_args()
 if not 60<=a.ttl_seconds<=1800:raise SystemExit("NOT_READY: TTL")
 n=datetime.datetime.now(datetime.timezone.utc);e=n+datetime.timedelta(seconds=a.ttl_seconds);v={"schema_version":"mysql_job_dedicated_daemon_materials_v1","status":"ISSUED_NOT_EXECUTED","bundle_id":B,"issued_at_utc":n.isoformat(),"expires_at_utc":e.isoformat(),"unit":"ra-docker-mysql-job-v1.service","socket":"/run/ra-docker-mysql-job-v1.sock","data_root":"/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/docker_ra_job_v1","required_prelaunch":["unit active exact cmdline/PID/startticks/cpuset","DockerRootDir on 259:3","every docker argv includes --host unix socket","DB/client host network and mysqld 65102","all GraphDriver paths UpperDir WorkDir MergedDir LowerDir on 259:3 and non-PG","v36 tmpfs parent/attempt readbacks"],"formal_launch_authorized":False,"accepted_round_authorized":False};i=hashlib.sha256(c(v)).hexdigest();v["materials_id"]=i;t=a.output_root/i;t.mkdir(parents=True,mode=0o700);q=t/"materials.json";q.write_bytes(c(v));os.chmod(q,0o400);(t/"materials.json.sha256").write_text(h(q)+"  materials.json\n");print(json.dumps({"status":v["status"],"materials":str(t),"expires_at_utc":v["expires_at_utc"]},sort_keys=True))
if __name__=="__main__":main()
