#!/usr/bin/env python3
"""Build TPC-C v7 with the sealed idle-Python identity and socket inventory."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PARENT_ID = "72d665b50fae3b3fe257fa140b44531940a41a35156e52fad0581af22bd03cf3"
PARENT_SHA = "efeb93956adf51b8d4f43a4d97270ed5bd9b0a534c8e6026e9d00ab1d4c789ae"
PARENT = ROOT / "artifacts/mysql_tpcc_blkio_reapply_successor_v6" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpcc_idle_python_successor_v7"
CONTROL_REL = "control/mysql_tpcc_zero_swap_recommission_idle_python_v7.py"

def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                       allow_nan=False) + "\n").encode()

def digest(raw): return hashlib.sha256(raw).hexdigest()

def verify_parent():
    raw = (PARENT / "bundle_manifest.json").read_bytes()
    if digest(raw) != PARENT_SHA: raise RuntimeError("parent manifest sha256 differs")
    manifest = json.loads(raw); unsigned = dict(manifest); bundle_id = unsigned.pop("bundle_id")
    if bundle_id != PARENT_ID or digest(canonical(unsigned)) != PARENT_ID:
        raise RuntimeError("parent content address differs")
    for relative, expected in manifest["files"].items():
        path = PARENT / relative
        if not path.is_file() or digest(path.read_bytes()) != expected:
            raise RuntimeError("parent closure differs: " + relative)
    return manifest

OLD_IDLE = '''def client_idle_evidence(client,proc_root=Path("/proc")):
 if client.get("container_id")!=CLIENT_ID or client.get("container_name")!=CLIENT or int(client.get("pid") or 0)<=1:raise E("client live identity differs before blkio reapply")
 needle="/docker/"+CLIENT_ID;members=[]
 for row in proc_cgroup_rows(proc_root):
  if any(path==needle for controllers,path in row["cgroups"]):members.append(row)
 if len(members)!=1 or members[0]["pid"]!=client["pid"]:raise E("client cgroup workload absence differs")
 cmd=members[0]["cmdline"]
 if len(cmd)!=2 or Path(cmd[0]).name!="sleep" or cmd[1]!="infinity":raise E("client is not the sealed idle sleep process")
 return {"client_container_id":CLIENT_ID,"client_pid":client["pid"],"member_pids":[members[0]["pid"]],"idle_command":["sleep","infinity"],"workload_absent":True}
'''

NEW_IDLE = r'''IDLE_PYTHON="/opt/mysql-tpcc-python/bin/python3.11"
IDLE_ENTRYPOINT=[IDLE_PYTHON];IDLE_CMD=["-c","import time; time.sleep(315360000)"]
def private_socket_inspects():
 r=subprocess.run(["/usr/bin/docker","--host",SOCKET,"ps","--no-trunc","--format","{{.ID}}"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=30)
 if r.returncode:raise E("private socket container inventory failed")
 ids=[line.strip() for line in r.stdout.splitlines() if line.strip()]
 if not ids or len(ids)!=len(set(ids)) or any(len(value)!=64 or any(c not in "0123456789abcdef" for c in value) for value in ids):raise E("private socket container ID inventory differs")
 return [inspect(value) for value in ids]
def proc_start_ticks(proc_dir):
 raw=(Path(proc_dir)/"stat").read_text();end=raw.rfind(") ")
 if end<0:raise E("idle client proc stat differs")
 fields=raw[end+2:].split()
 if len(fields)<=19:raise E("idle client proc stat is short")
 return int(fields[19])
def private_socket_process_inventory(inspects,proc_root=Path("/proc")):
 if not isinstance(inspects,list) or not inspects:raise E("private socket inspect inventory differs")
 processes=proc_cgroup_rows(proc_root);seen=set();inventory=[]
 for raw in inspects:
  cid=raw.get("Id");state=raw.get("State") or {};pid=int(state.get("Pid") or 0);name=str(raw.get("Name") or "").lstrip("/")
  if not isinstance(cid,str) or len(cid)!=64 or any(c not in "0123456789abcdef" for c in cid) or cid in seen or state.get("Running") is not True or pid<=1 or not name:raise E("private socket live container inventory differs")
  seen.add(cid);needle="/docker/"+cid
  members=[row for row in processes if any(path==needle or path.startswith(needle+"/") for controllers,path in row["cgroups"])]
  if not members or pid not in [row["pid"] for row in members]:raise E("private socket process membership differs: "+name)
  projected=[]
  for row in sorted(members,key=lambda value:value["pid"]):
   cmd=row["cmdline"];base=Path(cmd[0]).name.lower() if cmd else "";joined=" ".join(cmd).lower();comm=row["comm"].lower()
   if comm=="java" or base=="java" or "dbworkload" in joined or base in ("mysql","mysqlsh","mariadb"):
    raise E("workload or mysql query client exists on private socket: "+name)
   projected.append({"pid":row["pid"],"comm":row["comm"],"cmdline":cmd})
  inventory.append({"container_id":cid,"container_name":name,"host_pid":pid,"processes":projected})
 if DB_ID not in seen or CLIENT_ID not in seen:raise E("required private socket containers missing")
 return sorted(inventory,key=lambda value:value["container_id"])
def sealed_idle_python_evidence(client,proc_root=Path("/proc"),inspects=None):
 if client.get("container_id")!=CLIENT_ID or client.get("container_name")!=CLIENT or int(client.get("pid") or 0)<=1 or int(client.get("process_start_ticks") or 0)<=0:raise E("client live identity differs before blkio reapply")
 inventory=private_socket_process_inventory(inspects,proc_root);matches=[row for row in inspects if row.get("Id")==CLIENT_ID]
 if len(matches)!=1:raise E("sealed idle client inspect differs")
 raw=matches[0];config=raw.get("Config") or {};state=raw.get("State") or {}
 if str(raw.get("Name") or "").lstrip("/")!=CLIENT or state.get("Running") is not True or int(state.get("Pid") or 0)!=client["pid"] or config.get("Entrypoint")!=IDLE_ENTRYPOINT or config.get("Cmd")!=IDLE_CMD:raise E("sealed idle Python container Config differs")
 members=[row for row in inventory if row["container_id"]==CLIENT_ID][0]["processes"]
 if len(members)!=1 or members[0]["pid"]!=client["pid"]:raise E("idle Python client has additional container processes")
 d=Path(proc_root)/str(client["pid"])
 try:exe=os.readlink(str(d/"exe"));cmd=[x.decode("utf-8","surrogateescape") for x in (d/"cmdline").read_bytes().split(b"\0") if x];start=proc_start_ticks(d);children=(d/"task"/str(client["pid"])/"children").read_text().split()
 except (FileNotFoundError,ProcessLookupError,PermissionError):raise E("idle Python process disappeared")
 if exe!=IDLE_PYTHON or cmd!=IDLE_ENTRYPOINT+IDLE_CMD or start!=client["process_start_ticks"] or children:raise E("sealed idle Python /proc identity differs")
 return {"schema_version":"mysql_tpcc_idle_python_identity_v1","client_container_id":CLIENT_ID,"client_pid":client["pid"],"process_start_ticks":start,"entrypoint":IDLE_ENTRYPOINT,"cmd":IDLE_CMD,"proc_exe":exe,"proc_cmdline":cmd,"children":[],"private_socket_container_process_inventory":inventory,"forbidden_dbworkload_java_mysql_query_processes":0,"workload_absent":True}
'''

OLD_VALUE_SIG = '''def blkio_reapply_value(plan,database,client,read_root=BLKIO_ROOT,proc_root=Path("/proc"),writer=cgroup_write):'''
NEW_VALUE_SIG = '''def blkio_reapply_value(plan,database,client,read_root=BLKIO_ROOT,proc_root=Path("/proc"),writer=cgroup_write,inspect_rows_fn=private_socket_inspects):'''
OLD_IDLE_CALL = '''idle=client_idle_evidence(client,proc_root);root=Path(read_root);cgroup=root/DB_ID'''
NEW_IDLE_CALL = '''idle=sealed_idle_python_evidence(client,proc_root,inspect_rows_fn());root=Path(read_root);cgroup=root/DB_ID'''
OLD_SCHEMA = '''"schema_version":"mysql_tpcc_blkio_v1_throttle_reapply_receipt_v1"'''
NEW_SCHEMA = '''"schema_version":"mysql_tpcc_blkio_v1_throttle_reapply_receipt_v2"'''
OLD_SEAL_SIG = '''def seal_blkio_reapply(plan,database,client,path=REAPPLY_RECEIPT,read_root=BLKIO_ROOT,proc_root=Path("/proc"),writer=cgroup_write):'''
NEW_SEAL_SIG = '''def seal_blkio_reapply(plan,database,client,path=REAPPLY_RECEIPT,read_root=BLKIO_ROOT,proc_root=Path("/proc"),writer=cgroup_write,inspect_rows_fn=private_socket_inspects):'''
OLD_SEAL_CALL = '''value=blkio_reapply_value(plan,database,client,read_root,proc_root,writer)'''
NEW_SEAL_CALL = '''value=blkio_reapply_value(plan,database,client,read_root,proc_root,writer,inspect_rows_fn)'''

TEST = r'''import hashlib
import importlib.util
import json
from pathlib import Path
import pytest

B=Path(__file__).resolve().parents[1]
C=B/"control/mysql_tpcc_zero_swap_recommission_idle_python_v7.py"
def module():
 spec=importlib.util.spec_from_file_location("tpcc_v7",C);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def plan(m):return {"bundle_id":"7e2e13aa","attempt_id":m.ATTEMPT.name,"database":{"name":m.DB}}
def live(m,role,pid=222,start=777):return {"container_id":m.DB_ID if role=="database" else m.CLIENT_ID,"container_name":m.DB if role=="database" else m.CLIENT,"pid":pid,"process_start_ticks":start}
def proc(root,pid,comm,cmd,cgroup,start=777,exe=None,children=""):
 d=root/str(pid);(d/"task"/str(pid)).mkdir(parents=True);(d/"comm").write_text(comm+"\n");(d/"cmdline").write_bytes(b"\0".join(x.encode() for x in cmd)+b"\0");(d/"cgroup").write_text("5:blkio:"+cgroup+"\n4:memory:"+cgroup+"\n")
 fields=["S"]+["0"]*30;fields[19]=str(start);(d/"stat").write_text(str(pid)+" ("+comm+") "+" ".join(fields)+"\n");(d/"task"/str(pid)/"children").write_text(children)
 if exe:(d/"exe").symlink_to(exe)
def inspect_rows(m,client_config=True,extra=None):
 rows=[{"Id":m.DB_ID,"Name":"/"+m.DB,"State":{"Running":True,"Pid":111},"Config":{"Entrypoint":["mysqld"],"Cmd":[]}},
       {"Id":m.CLIENT_ID,"Name":"/"+m.CLIENT,"State":{"Running":True,"Pid":222},"Config":{"Entrypoint":m.IDLE_ENTRYPOINT if client_config else ["sleep"],"Cmd":m.IDLE_CMD}}]
 if extra:rows.append(extra)
 return rows
def fixture(m,tmp_path,extra_proc=None,client_config=True,children="",start=777):
 cg=tmp_path/"blkio/docker"/m.DB_ID;cg.mkdir(parents=True);rp=cg/"blkio.throttle.read_bps_device";wp=cg/"blkio.throttle.write_bps_device";rp.write_text("");wp.write_text("")
 pr=tmp_path/"proc";pr.mkdir();proc(pr,111,"mysqld",["mysqld"],"/docker/"+m.DB_ID,start=1);proc(pr,222,"python3.11",m.IDLE_ENTRYPOINT+m.IDLE_CMD,"/docker/"+m.CLIENT_ID,start=start,exe=m.IDLE_PYTHON,children=children)
 rows=inspect_rows(m,client_config);calls=[]
 if extra_proc:
  pid,comm,cmd,cid=extra_proc;proc(pr,pid,comm,cmd,"/docker/"+cid,start=9);rows.append({"Id":cid,"Name":"/extra","State":{"Running":True,"Pid":pid},"Config":{"Entrypoint":[cmd[0]],"Cmd":cmd[1:]}})
 def writer(path,payload):
  calls.append((Path(path),payload));Path(path).write_text(payload)
 return tmp_path/"blkio/docker",pr,rows,calls,writer
def run(m,tmp_path,**kwargs):
 root,pr,rows,calls,writer=fixture(m,tmp_path,**kwargs);value=m.blkio_reapply_value(plan(m),live(m,"database",111,1),live(m,"client"),root,pr,writer,lambda:rows);return value,calls
def test_real_idle_python_identity_passes_before_exact_two_writes(tmp_path):
 m=module();value,calls=run(m,tmp_path)
 assert [payload for _,payload in calls]==["259:2 1205862400\n"]*2
 idle=value["client_idle_evidence"];assert idle["proc_exe"]==m.IDLE_PYTHON and idle["proc_cmdline"]==m.IDLE_ENTRYPOINT+m.IDLE_CMD and idle["children"]==[]
 assert len(idle["private_socket_container_process_inventory"])==2 and idle["workload_absent"] is True
 assert value["accepted_rounds_credit"]==0 and value["postgresql_mutation_performed"] is False
@pytest.mark.parametrize("change",["config","children","start"])
def test_idle_identity_mismatch_fails_before_write(tmp_path,change):
 m=module();kwargs={"client_config":change!="config","children":"333" if change=="children" else "","start":778 if change=="start" else 777};root,pr,rows,calls,writer=fixture(m,tmp_path,**kwargs)
 with pytest.raises(m.E):m.blkio_reapply_value(plan(m),live(m,"database",111,1),live(m,"client"),root,pr,writer,lambda:rows)
 assert calls==[]
@pytest.mark.parametrize("comm,cmd",[("java",["java","DBWorkload"]),("mysql",["mysql","-e","select 1"]),("python",["python","DBWorkload.py"])])
def test_private_socket_workload_inventory_rejects_before_write(tmp_path,comm,cmd):
 m=module();cid="a"*64;root,pr,rows,calls,writer=fixture(m,tmp_path,extra_proc=(333,comm,cmd,cid))
 with pytest.raises(m.E,match="workload or mysql query client"):m.blkio_reapply_value(plan(m),live(m,"database",111,1),live(m,"client"),root,pr,writer,lambda:rows)
 assert calls==[]
def test_additional_client_process_rejected(tmp_path):
 m=module();root,pr,rows,calls,writer=fixture(m,tmp_path);proc(pr,223,"python",["python","idle.py"],"/docker/"+m.CLIENT_ID,start=8)
 with pytest.raises(m.E,match="additional container processes"):m.blkio_reapply_value(plan(m),live(m,"database",111,1),live(m,"client"),root,pr,writer,lambda:rows)
 assert calls==[]
def test_v6_cgroup_pg_receipt_and_adopt_gates_preserved():
 source=C.read_text()
 for required in ('relative in pg_paths','apply_throttle_file(cgroup/"blkio.throttle.read_bps_device",writer)','non-target blkio throttle rows changed','seal_blkio_reapply(plan,after["database"],after["client"]);fresh_blkio=seal_fresh_blkio_authority','value=adopt_with_fresh_blkio','accepted_rounds_credit":0'):
  assert required in source
def test_content_address_parent_and_scope():
 manifest=json.loads((B/"bundle_manifest.json").read_text());unsigned=dict(manifest);unsigned.pop("bundle_id");actual=hashlib.sha256((json.dumps(unsigned,sort_keys=True,indent=2)+"\n").encode()).hexdigest()
 assert actual==manifest["bundle_id"]==B.name and manifest["parent_bundle_id"]=="72d665b50fae3b3fe257fa140b44531940a41a35156e52fad0581af22bd03cf3"
 assert manifest["formal_scope"]=="mysql_only" and manifest["cross_engine_comparison_prohibited"] is True and manifest["postgresql_mutation_prohibited"] is True
'''

def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True): os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)

def main():
    verify_parent()
    source=(PARENT/"control/mysql_tpcc_zero_swap_recommission_blkio_reapply_v6.py").read_text()
    for old,new,label in ((OLD_IDLE,NEW_IDLE,"idle identity"),(OLD_VALUE_SIG,NEW_VALUE_SIG,"value signature"),(OLD_IDLE_CALL,NEW_IDLE_CALL,"idle call"),(OLD_SCHEMA,NEW_SCHEMA,"receipt schema"),(OLD_SEAL_SIG,NEW_SEAL_SIG,"seal signature"),(OLD_SEAL_CALL,NEW_SEAL_CALL,"seal forwarding")):
        if source.count(old)!=1: raise RuntimeError(label+" patch anchor differs")
        source=source.replace(old,new)
    runbook=json.loads((PARENT/"runbook/transaction.json").read_text());runbook["schema_version"]="mysql_tpcc_idle_python_transaction_v7";runbook["client_idle_gate"]={"container_entrypoint":["/opt/mysql-tpcc-python/bin/python3.11"],"container_cmd":["-c","import time; time.sleep(315360000)"],"proc_exe":"/opt/mysql-tpcc-python/bin/python3.11","proc_cmdline_exact":True,"process_start_ticks_current_snapshot_required":True,"children_required_empty":True,"private_socket_all_running_container_processes_inventoried":True,"dbworkload_java_mysql_query_clients_rejected":True}
    evidence={"schema_version":"mysql_tpcc_v6_idle_gate_correction_v1","status":"OFFLINE_SUCCESSOR_ZERO_CREDIT","parent_bundle_id":PARENT_ID,"observed_pid":21060,"observed_entrypoint":["/opt/mysql-tpcc-python/bin/python3.11"],"observed_cmd":["-c","import time; time.sleep(315360000)"],"observed_exe":"/opt/mysql-tpcc-python/bin/python3.11","observed_children":[],"root_cause":"v6 encoded sleep infinity instead of the frozen idle Python process","v7_fix":"bind Docker Config and current /proc identity and inventory every private-socket container process before any cgroup write","postgresql_mutation_prohibited":True,"accepted_rounds_credit":0}
    contents={CONTROL_REL:source.encode(),"runbook/transaction.json":canonical(runbook),"evidence/mysql_tpcc_v6_idle_gate_correction_v1.json":canonical(evidence),"tests/test_mysql_tpcc_idle_python_successor_v7.py":TEST.encode(),"builder/build_mysql_tpcc_idle_python_successor_v7.py":HERE.read_bytes()}
    files={relative:digest(raw) for relative,raw in sorted(contents.items())}
    unsigned={"schema_version":"mysql_tpcc_idle_python_successor_bundle_v7","status":"SEALED_NOT_EXECUTED","immutable":True,"parent_bundle_id":PARENT_ID,"parent_manifest_sha256":PARENT_SHA,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"idle_python_entrypoint":"/opt/mysql-tpcc-python/bin/python3.11","idle_python_cmd":["-c","import time; time.sleep(315360000)"],"idle_proc_exe_cmdline_start_children_bound":True,"private_socket_container_process_inventory_required":True,"dbworkload_java_mysql_query_clients_rejected":True,"v6_dedicated_db_cgroup_reapply_preserved":True,"v6_receipt_fresh_authority_adopt_order_preserved":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions_performed":0,"files":files}
    bundle_id=digest(canonical(unsigned));manifest_raw=canonical(dict(unsigned,bundle_id=bundle_id));OUT.mkdir(parents=True,exist_ok=True);destination=OUT/bundle_id
    if destination.exists():
        if (destination/"bundle_manifest.json").read_bytes()!=manifest_raw:raise RuntimeError("existing manifest differs")
        for relative,raw in contents.items():
            if (destination/relative).read_bytes()!=raw:raise RuntimeError("existing closure differs: "+relative)
    else:
        stage=Path(tempfile.mkdtemp(prefix=".idle-python-",dir=OUT))
        try:
            for relative,raw in contents.items():path=stage/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
            (stage/"bundle_manifest.json").write_bytes(manifest_raw);(stage/"bundle_manifest.json.sha256").write_text(digest(manifest_raw)+"  bundle_manifest.json\n");freeze(stage);os.rename(stage,destination)
        finally:
            if stage.exists():shutil.rmtree(stage)
    print(bundle_id);print(digest((destination/"bundle_manifest.json").read_bytes()));print(destination)

if __name__=="__main__":main()
