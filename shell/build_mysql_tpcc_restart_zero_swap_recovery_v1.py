#!/usr/bin/env python3
"""Build an immutable main-agent-only TPC-C restart/reseal recovery bundle."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/mysql_tpcc_restart_zero_swap_recovery_v1"


CONTROL = r'''#!/usr/bin/env python3
"""Restart only the exact TPC-C DB container and seal its fresh zero-swap epoch."""
from __future__ import annotations
import argparse,hashlib,json,os,socket,stat,subprocess,time
from pathlib import Path

DOCKER=["/usr/bin/docker","--host","unix:///run/ra-docker-mysql-tpcc-v1.sock"]
DB_NAME="ra_mysql_tpcc_v2_db_tpcc-v16-7e2e13aa-20260801t121500p08"
DB_ID="fc87aaf6a954e0d5131ff695ca808247c906525af9b4fad18c94aa6387c55948"
DB_PID=100769;DB_START=268728753;DB_CGROUP_SHA="0ac085e5b6b435d7610695e5fcb2c5e3b97f2e720b845d38565d6cdfde3bae3a"
CLIENT_NAME="ra_mysql_tpcc_v2_client_tpcc-v16-7e2e13aa-20260801t121500p08"
CLIENT_ID="51c437f082bec5a7f95c7f0ce9a54bf549d87db7aa41a45bcec69390de6801a4"
CLIENT_PID=100845;CLIENT_START=268728786
FAILED_EVENT="tpcc_v14_explicit_failure_observed_2026-08-04T16:39:11+08:00"
OLD_ADOPTED_SHA="b9fcd51325ea80d4284df5cf3c9df81fd022a79df5fa3002d2635fe7a8765001"
OLD_BLKIO_SHA="b823a7c61f5d11dfa50a13f9fa1ea268b70410004a57b8449bc1e442bc1a11e8"
MEMORY=Path("/sys/fs/cgroup/memory/docker");BLKIO=Path("/sys/fs/cgroup/blkio/docker")
DEVICE="259:2";BPS=1205862400;EXPECTED_SWAP=106496

class NotReady(RuntimeError):pass
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sha_bytes(v):return hashlib.sha256(v).hexdigest()
def sha(path):return sha_bytes(Path(path).read_bytes())
def seal(path,value):
 path=Path(path);path.parent.mkdir(parents=True,mode=0o700,exist_ok=True);side=Path(str(path)+".sha256")
 if path.exists() or path.is_symlink() or side.exists() or side.is_symlink():raise NotReady("fresh output required: "+str(path))
 raw=canonical(value);fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o400)
 try:os.write(fd,raw);os.fsync(fd)
 finally:os.close(fd)
 side.write_text(sha_bytes(raw)+"  "+path.name+"\n",encoding="ascii");os.chmod(side,0o400)
 return sha_bytes(raw)
def load_sealed(path,label):
 path=Path(path).resolve(strict=True);side=Path(str(path)+".sha256").resolve(strict=True)
 if path.is_symlink() or side.is_symlink() or side.read_text()!=sha(path)+"  "+path.name+"\n":raise NotReady(label+" seal differs")
 value=json.loads(path.read_text())
 if not isinstance(value,dict):raise NotReady(label+" object differs")
 return path,value
def docker(*args,timeout=30):
 row=subprocess.run(DOCKER+list(args),text=True,capture_output=True,timeout=timeout)
 if row.returncode:raise NotReady("docker command failed: "+" ".join(args)+": "+row.stderr.strip())
 return row
def inspect(identity):
 values=json.loads(docker("inspect",identity).stdout)
 if len(values)!=1:raise NotReady("inspect cardinality differs")
 return values[0]
def start_ticks(pid):
 raw=(Path("/proc")/str(pid)/"stat").read_text();return int(raw[raw.rfind(") ")+2:].split()[19])
def memory_state(identity):
 root=MEMORY/identity;values={}
 for line in (root/"memory.stat").read_text().splitlines():
  key,value=line.split();values[key]=int(value)
 return {"memory_swappiness":int((root/"memory.swappiness").read_text()),"memory_limit_bytes":int((root/"memory.limit_in_bytes").read_text()),"memory_swap_limit_bytes":int((root/"memory.memsw.limit_in_bytes").read_text()),"total_swap_bytes":int(values.get("total_swap",-1))}
def proc(identity,row):
 state=row.get("State") or {};pid=int(state.get("Pid") or 0)
 if row.get("Id")!=identity or state.get("Running") is not True or pid<=1:raise NotReady("live container identity differs")
 raw=(Path("/proc")/str(pid)/"cgroup").read_text()
 return {"container_id":identity,"pid":pid,"process_start_ticks":start_ticks(pid),"process_cgroup_sha256":sha_bytes(raw.encode()),**memory_state(identity),"started_at":state.get("StartedAt")}
def projection(row):
 c=row.get("Config") or {};h=row.get("HostConfig") or {}
 return {"image":row.get("Image"),"created":row.get("Created"),"hostname":c.get("Hostname"),"user":c.get("User"),"env":c.get("Env"),"cmd":c.get("Cmd"),"entrypoint":c.get("Entrypoint"),"labels":c.get("Labels"),"working_dir":c.get("WorkingDir"),"network_mode":h.get("NetworkMode"),"cgroupns_mode":h.get("CgroupnsMode"),"ipc_mode":h.get("IpcMode"),"runtime":h.get("Runtime"),"restart_policy":h.get("RestartPolicy"),"readonly_rootfs":h.get("ReadonlyRootfs"),"memory":h.get("Memory"),"memory_swap":h.get("MemorySwap"),"memory_swappiness":h.get("MemorySwappiness"),"pids_limit":h.get("PidsLimit"),"cpu_period":h.get("CpuPeriod"),"cpu_quota":h.get("CpuQuota"),"cpuset_cpus":h.get("CpusetCpus"),"cpuset_mems":h.get("CpusetMems"),"mounts":sorted([{k:m.get(k) for k in ("Type","Name","Source","Destination","RW","Propagation")} for m in row.get("Mounts") or []],key=lambda v:(v["Destination"] or "",v["Source"] or ""))}
def resource_gate(row,role):
 h=row.get("HostConfig") or {};db=role=="database"
 want={"Memory":34359738368 if db else 8589934592,"MemorySwap":34359738368 if db else 8589934592,"MemorySwappiness":0,"PidsLimit":512 if db else 1024,"CpuPeriod":100000,"CpuQuota":400000 if db else 1600000,"CpusetCpus":"21-24,57-60" if db else "8-15,44-51","CpusetMems":"1" if db else "0"}
 if any(h.get(k)!=v for k,v in want.items()):raise NotReady(role+" Docker resource projection differs")
def client_idle(row,state):
 c=row.get("Config") or {};want=["/opt/mysql-tpcc-python/bin/python3.11","-c","import time; time.sleep(315360000)"]
 cmd=[x.decode("utf-8","surrogateescape") for x in (Path("/proc")/str(state["pid"])/"cmdline").read_bytes().split(b"\0") if x]
 children=(Path("/proc")/str(state["pid"])/"task"/str(state["pid"])/"children").read_text().split()
 if (c.get("Entrypoint") or [])+(c.get("Cmd") or [])!=want or cmd!=want or children:raise NotReady("client is not idle")
 return {"pid":state["pid"],"process_start_ticks":state["process_start_ticks"],"cmdline":cmd,"children":[],"workload_absent":True}
def pg_cgroups():
 values=set()
 for p in Path("/proc").glob("[0-9]*"):
  try:
   if (p/"comm").read_text().strip()=="postgres":values.update(line.split(":",2)[2] for line in (p/"cgroup").read_text().splitlines())
  except (FileNotFoundError,PermissionError,ProcessLookupError):pass
 return sorted(values)
def read_rate(path):
 return {parts[0]:int(parts[1]) for line in Path(path).read_text().splitlines() if len(parts:=line.split())==2}.get(DEVICE)
def set_rate(path):
 path=Path(path);before=read_rate(path)
 with path.open("w") as out:out.write(DEVICE+" "+str(BPS)+"\n")
 after=read_rate(path)
 if after!=BPS:raise NotReady("blkio readback differs")
 return {"path":str(path),"before":before,"after":after,"payload":DEVICE+" "+str(BPS)}
def socket_ready(deadline):
 while time.monotonic()<deadline:
  try:
   with socket.create_connection(("127.0.0.1",65106),timeout=2):return
  except OSError:time.sleep(2)
 raise NotReady("MySQL readiness socket timeout")
def preflight(output):
 dbrow=inspect(DB_ID);clientrow=inspect(CLIENT_ID);resource_gate(dbrow,"database");resource_gate(clientrow,"client")
 db=proc(DB_ID,dbrow);client=proc(CLIENT_ID,clientrow)
 if db["pid"]!=DB_PID or db["process_start_ticks"]!=DB_START or db["process_cgroup_sha256"]!=DB_CGROUP_SHA or db["memory_swappiness"]!=0 or db["memory_limit_bytes"]!=db["memory_swap_limit_bytes"] or db["total_swap_bytes"]!=EXPECTED_SWAP:raise NotReady("database failed epoch differs")
 if client["pid"]!=CLIENT_PID or client["process_start_ticks"]!=CLIENT_START or client["memory_swappiness"]!=0 or client["memory_limit_bytes"]!=client["memory_swap_limit_bytes"] or client["total_swap_bytes"]!=0:raise NotReady("client epoch differs")
 idle=client_idle(clientrow,client);pg=pg_cgroups()
 if "/docker/"+DB_ID in pg:raise NotReady("TPC-C database is a PostgreSQL cgroup")
 value={"schema_version":"mysql_tpcc_restart_zero_swap_preflight_v1","status":"PASS_EXACT_FAILED_EPOCH_RESTART_REQUIRED","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"failed_event_id":FAILED_EVENT,"failed_attempt_token":None,"failed_attempt_rerun_prohibited":True,"old_adopted_authority_sha256":OLD_ADOPTED_SHA,"old_blkio_authority_sha256":OLD_BLKIO_SHA,"database":db,"client":client,"database_projection":projection(dbrow),"database_projection_sha256":sha_bytes(canonical(projection(dbrow))),"client_projection_sha256":sha_bytes(canonical(projection(clientrow))),"client_idle":idle,"observed_postgresql_cgroups":pg,"mutation_performed":False,"workload_started":False,"accepted_rounds_credit":0}
 seal(output,value);return value
def restart(preflight_path,output):
 pp,before=load_sealed(preflight_path,"restart preflight")
 if before.get("status")!="PASS_EXACT_FAILED_EPOCH_RESTART_REQUIRED" or before.get("failed_attempt_rerun_prohibited") is not True or before.get("accepted_rounds_credit")!=0:raise NotReady("preflight authority differs")
 dbrow=inspect(DB_ID);old=proc(DB_ID,dbrow)
 if old!=before["database"] or projection(dbrow)!=before["database_projection"]:raise NotReady("database changed since preflight")
 clientrow=inspect(CLIENT_ID);client=proc(CLIENT_ID,clientrow);client_idle(clientrow,client)
 docker("restart","--time","120",DB_ID,timeout=180)
 socket_ready(time.monotonic()+180)
 dbrow2=inspect(DB_ID);resource_gate(dbrow2,"database");fresh=proc(DB_ID,dbrow2)
 if fresh["pid"]==old["pid"] or fresh["process_start_ticks"]==old["process_start_ticks"] or projection(dbrow2)!=before["database_projection"]:raise NotReady("fresh database epoch/config differs")
 cg=BLKIO/DB_ID
 if not cg.is_dir() or cg.is_symlink():raise NotReady("database blkio cgroup differs")
 read=set_rate(cg/"blkio.throttle.read_bps_device");write=set_rate(cg/"blkio.throttle.write_bps_device")
 samples=[]
 for index in range(3):
  current=proc(DB_ID,inspect(DB_ID));samples.append({"sequence":index,"epoch":time.time(),"pid":current["pid"],"process_start_ticks":current["process_start_ticks"],"memory_swappiness":current["memory_swappiness"],"total_swap_bytes":current["total_swap_bytes"]})
  if current["pid"]!=fresh["pid"] or current["process_start_ticks"]!=fresh["process_start_ticks"] or current["memory_swappiness"]!=0 or current["total_swap_bytes"]!=0 or current["memory_limit_bytes"]!=current["memory_swap_limit_bytes"]:raise NotReady("zero-swap stabilization differs")
  if index<2:time.sleep(10)
 client_after=proc(CLIENT_ID,inspect(CLIENT_ID))
 if client_after!=client:raise NotReady("idle client epoch changed")
 client_idle(inspect(CLIENT_ID),client_after)
 pg=pg_cgroups()
 if "/docker/"+DB_ID in pg:raise NotReady("restarted target became PostgreSQL cgroup")
 parent=Path(output).parent;parent.mkdir(parents=True,exist_ok=True)
 blkio={"schema_version":"mysql_tpcc_restart_zero_swap_blkio_authority_v1","status":"PASS_FRESH_RESTART_EPOCH","formal_scope":"mysql_only","database_container_id":DB_ID,"database_pid":fresh["pid"],"database_process_start_ticks":fresh["process_start_ticks"],"database_process_cgroup_sha256":fresh["process_cgroup_sha256"],"device":DEVICE,"read_bps":BPS,"write_bps":BPS,"read_reapply":read,"write_reapply":write,"preflight_sha256":sha(pp),"postgresql_mutation_performed":False,"workload_started":False,"accepted_rounds_credit":0}
 bp=parent/"blkio_throttle_authority.restart_zero_swap_v1.json";bsha=seal(bp,blkio)
 adopted={"schema_version":"mysql_tpcc_restart_zero_swap_adopted_service_v1","status":"PASS_DURABLE_ZERO_SWAP_RESTARTED_IDLE_READY","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"predecessor_adopted_authority_sha256":OLD_ADOPTED_SHA,"predecessor_blkio_authority_sha256":OLD_BLKIO_SHA,"preflight_sha256":sha(pp),"database":fresh,"client":client_after,"database_projection":projection(dbrow2),"database_projection_sha256":sha_bytes(canonical(projection(dbrow2))),"client_idle":client_idle(inspect(CLIENT_ID),client_after),"zero_swap_samples":samples,"zero_swap_stability_seconds":20,"fresh_blkio_authority_sha256":bsha,"same_container_id":True,"database_restarted":True,"container_recreated":False,"workload_started":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0}
 ap=parent/"adopted_service.restart_zero_swap_v1.json";asha=seal(ap,adopted)
 completion={"schema_version":"mysql_tpcc_restart_zero_swap_completion_v1","status":"PASS_RESTARTED_RESEALED_NEW_EPOCH_NO_WORKLOAD","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"failed_event_id":FAILED_EVENT,"failed_attempt_token":None,"failed_attempt_rerun_prohibited":True,"old_database_pid":old["pid"],"new_database_pid":fresh["pid"],"new_database_process_start_ticks":fresh["process_start_ticks"],"new_database_process_cgroup_sha256":fresh["process_cgroup_sha256"],"adopted_authority_path":str(ap),"adopted_authority_sha256":asha,"blkio_authority_path":str(bp),"blkio_authority_sha256":bsha,"observed_postgresql_cgroups":pg,"postgresql_actions":[],"workload_started":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0}
 seal(output,completion);return completion
def main():
 if socket.gethostname().split(".")[0]!="node04" or os.geteuid()!=0:raise NotReady("node04 root required")
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest="action",required=True);a=sub.add_parser("preflight");a.add_argument("--output",type=Path,required=True);b=sub.add_parser("restart");b.add_argument("--preflight",type=Path,required=True);b.add_argument("--output",type=Path,required=True);args=p.parse_args()
 value=preflight(args.output) if args.action=="preflight" else restart(args.preflight,args.output);print(json.dumps({"status":value["status"],"output":str(args.output)},sort_keys=True))
if __name__=="__main__":
 try:main()
 except (NotReady,OSError,ValueError,subprocess.SubprocessError) as exc:print("NOT_READY: "+str(exc),file=__import__("sys").stderr);raise SystemExit(2)
'''


TEST = r'''import ast,hashlib,json
from pathlib import Path
B=Path(__file__).resolve().parents[1];M=B/"bundle_manifest.json";C=B/"control/mysql_tpcc_restart_zero_swap_recovery_v1.py";R=B/"runbook/recovery.json"
def can(v):return (json.dumps(v,sort_keys=True,indent=2)+"\n").encode()
def test_identity_scope_zero_credit():m=json.loads(M.read_text());u=dict(m);u.pop("bundle_id");assert hashlib.sha256(can(u)).hexdigest()==m["bundle_id"]==B.name and m["formal_scope"]=="mysql_only" and m["cross_engine_comparison_prohibited"] is True and m["accepted_rounds_credit"]==0
def test_only_exact_database_restart():
 s=C.read_text();ast.parse(s);assert 'docker("restart","--time","120",DB_ID' in s and 'DB_ID="fc87aaf6a954e0d5131ff695ca808247c906525af9b4fad18c94aa6387c55948"' in s
 for forbidden in ("swapoff","memory.force_empty",'docker("stop"','docker("rm"','docker("kill"'):assert forbidden not in s
def test_strict_zero_swap_and_new_epoch():s=C.read_text();assert 'current["total_swap_bytes"]!=0' in s and 'fresh["pid"]==old["pid"]' in s and "range(3)" in s and "time.sleep(10)" in s
def test_config_and_idle_client_preserved():s=C.read_text();assert 'projection(dbrow2)!=before["database_projection"]' in s and "client_after!=client" in s and "client_idle" in s
def test_blkio_reapplied_only_to_tpcc_db():s=C.read_text();assert "BLKIO/DB_ID" in s and 'DEVICE="259:2"' in s and "BPS=1205862400" in s
def test_pg_prohibited_and_no_workload():s=C.read_text();assert 'postgresql_mutation_prohibited":True' in s and '"postgresql_actions":[]' in s and '"workload_started":False' in s
def test_failed_attempt_zero_credit_no_rerun():r=json.loads(R.read_text());assert r["failed_attempt_rerun_prohibited"] is True and r["accepted_rounds_credit"]==0 and r["main_agent_serial_execution_required"] is True
def test_new_authorities_and_controller_rebuild_required():r=json.loads(R.read_text());assert r["new_authority_outputs"]["adopted"].endswith("adopted_service.restart_zero_swap_v1.json") and r["post_recovery"][0]=="build_new_collector_bound_to_new_authority_epoch" and r["post_recovery"][1]=="build_new_explicit_controller_bound_to_new_collector_and_epoch"
'''


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def main():
    runtime = "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729"
    attempt = runtime + "/tpcc-v16-7e2e13aa-20260801t121500p08"
    output = attempt + "/recovery/v15-restart-zero-swap"
    python = runtime + "/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89/bin/python3.11"
    runbook = {
        "schema_version": "mysql_tpcc_restart_zero_swap_recovery_runbook_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "main_agent_serial_execution_required": True,
        "workload_start_prohibited": True,
        "failed_event_id": "tpcc_v14_explicit_failure_observed_2026-08-04T16:39:11+08:00",
        "failed_attempt_token": None,
        "failed_attempt_rerun_prohibited": True,
        "accepted_rounds_credit": 0,
        "preflight_argv": [python, "-B", "[THIS_PUBLISHED_BUNDLE]/control/mysql_tpcc_restart_zero_swap_recovery_v1.py", "preflight", "--output", output + "/preflight.json"],
        "restart_argv": [python, "-B", "[THIS_PUBLISHED_BUNDLE]/control/mysql_tpcc_restart_zero_swap_recovery_v1.py", "restart", "--preflight", output + "/preflight.json", "--output", output + "/completion.json"],
        "new_authority_outputs": {"adopted": output + "/adopted_service.restart_zero_swap_v1.json", "blkio": output + "/blkio_throttle_authority.restart_zero_swap_v1.json", "completion": output + "/completion.json"},
        "post_recovery": ["build_new_collector_bound_to_new_authority_epoch", "build_new_explicit_controller_bound_to_new_collector_and_epoch", "run_read_only_preflight", "start_fresh_qualification_then_fresh_r0"],
        "estimated_minutes": {"publish_and_preflight": 3, "controlled_restart_mysql_recovery_and_zero_swap_stability": 4, "new_collector_and_controller_build_publish_test": 8, "fresh_qualification_and_r0": 8},
    }
    failure = {"schema_version": "mysql_tpcc_v14_explicit_zero_swap_failure_v1", "status": "FAIL_CLOSED_DATABASE_TOTAL_SWAP_NONZERO", "failed_event_id": "tpcc_v14_explicit_failure_observed_2026-08-04T16:39:11+08:00", "failed_attempt_token": None, "workload_started": False, "real_workload_pid": None, "output_absent": True, "database_container_id": "fc87aaf6a954e0d5131ff695ca808247c906525af9b4fad18c94aa6387c55948", "database_pid": 100769, "cgroup_total_swap_bytes": 106496, "client_total_swap_bytes": 0, "rerun_prohibited": True, "reusable": False, "accepted_rounds_credit": 0, "postgresql_mutation_performed": False}
    contents = {"control/mysql_tpcc_restart_zero_swap_recovery_v1.py": CONTROL.encode(), "runbook/recovery.json": canonical(runbook), "evidence/v14_explicit_zero_swap_failure.json": canonical(failure), "tests/test_mysql_tpcc_restart_zero_swap_recovery_v1.py": TEST.encode(), "builder/build_mysql_tpcc_restart_zero_swap_recovery_v1.py": Path(__file__).read_bytes()}
    files = {relative: sha(raw) for relative, raw in sorted(contents.items())}
    unsigned = {"schema_version": "mysql_tpcc_restart_zero_swap_recovery_bundle_v1", "status": "SEALED_NOT_PUBLISHED_NOT_EXECUTED", "immutable": True, "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True, "postgresql_mutation_prohibited": True, "exact_database_container_id": "fc87aaf6a954e0d5131ff695ca808247c906525af9b4fad18c94aa6387c55948", "restart_strategy": "same_container_exact_id_graceful_restart", "host_swapoff_prohibited": True, "container_recreation_not_required_first": True, "new_authority_epoch_required": True, "new_collector_and_controller_required": True, "formal_launch_authorized": False, "accepted_round_authorized": False, "accepted_rounds_credit": 0, "remote_actions_performed": 0, "data_plane_actions_performed": 0, "files": files}
    identity = sha(canonical(unsigned));manifest = dict(unsigned, bundle_id=identity);raw = canonical(manifest)
    OUT.mkdir(parents=True, exist_ok=True);target = OUT / identity
    if target.exists():
        if (target / "bundle_manifest.json").read_bytes() != raw:raise RuntimeError("existing manifest differs")
        for relative, payload in contents.items():
            if (target / relative).read_bytes() != payload:raise RuntimeError("existing closure differs: " + relative)
    else:
        stage = Path(tempfile.mkdtemp(prefix=".tpcc-restart-zero-swap-", dir=OUT))
        try:
            for relative, payload in contents.items():
                path=stage/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(payload)
            (stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(sha(raw)+"  bundle_manifest.json\n");freeze(stage);os.rename(stage,target)
        finally:
            if stage.exists():shutil.rmtree(stage)
    print(identity);print(sha(raw));print(target)


if __name__ == "__main__":main()
