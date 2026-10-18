#!/usr/bin/env python3
"""Seal JOB v36: safe traversable tmpfs parent, private attempt mount.

Offline publisher only; the emitted authority still performs all privileged
mount/container actions only when the main agent later authorizes bootstrap.
"""
from __future__ import annotations
import hashlib,json,os,secrets,shutil,sys
from pathlib import Path
R=Path(__file__).resolve().parents[1];S=Path(__file__).resolve();P=R/"artifacts/mysql_job_v35_server_port_successor/189822803ff9c2721f17fbe6ef92c9af6ec0e4e522b478df627f9b151b5586cb";O=R/"artifacts/mysql_job_v36_measurement_parent_successor_v1";AUTH="qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py";INV="authority/mysql_job_v35_measurement_parent_traversal_invalidation_20260731.json"
def canon(v):return (json.dumps(v,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
def digest(p):
 h=hashlib.sha256()
 with p.open("rb") as f:
  for x in iter(lambda:f.read(1048576),b""):h.update(x)
 return h.hexdigest()
def load(p):return json.loads(Path(p).read_text())
def patched_authority():
 source=(P/AUTH).read_text();old='''    measurement.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    measurement.mkdir(mode=0o700)
''';new='''    measurement_parent = measurement.parent
    if measurement_parent.exists() or measurement_parent.is_symlink():
        parent_stat = measurement_parent.stat(follow_symlinks=False)
        if (measurement_parent.is_symlink() or not measurement_parent.is_dir()
                or parent_stat.st_uid != 0 or parent_stat.st_gid != 0
                or stat.S_IMODE(parent_stat.st_mode) != 0o711):
            raise AuthorityError("measurement tmpfs parent is not root:root 0711")
    else:
        measurement_parent.mkdir(mode=0o711, parents=True, exist_ok=False)
        os.chmod(measurement_parent, 0o711)
        parent_stat = measurement_parent.stat(follow_symlinks=False)
        if (parent_stat.st_uid != 0 or parent_stat.st_gid != 0
                or stat.S_IMODE(parent_stat.st_mode) != 0o711):
            raise AuthorityError("measurement tmpfs parent creation/readback differs")
    # Parent permits traversal only; the mounted attempt directory remains
    # uid/gid 1008 and 0700, so collectors cannot enumerate other attempts.
    measurement.mkdir(mode=0o700)
'''
 if source.count(old)!=1:raise RuntimeError("v35 measurement parent site differs")
 source=source.replace(old,new)
 old='''    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        raise AuthorityError("measurement host tmpfs path is absent or unsafe")
''';new='''    path = Path(path)
    parent = path.parent
    if (parent.is_symlink() or not parent.is_dir()
            or parent.stat(follow_symlinks=False).st_uid != 0
            or parent.stat(follow_symlinks=False).st_gid != 0
            or stat.S_IMODE(parent.stat(follow_symlinks=False).st_mode) != 0o711):
        raise AuthorityError("measurement tmpfs parent traversal readback differs")
    if path.is_symlink() or not path.is_dir():
        raise AuthorityError("measurement host tmpfs path is absent or unsafe")
'''
 if source.count(old)!=1:raise RuntimeError("v35 measurement readback site differs")
 return source.replace(old,new).encode()
def main():
 manifest=load(P/"bundle_manifest.json")
 if P.name!=manifest.get("bundle_id") or digest(P/"bundle_manifest.json")!="a95443f5be6b65e046a2bd8f6a2c6b549eea04da7853cda3eb8266111f4d6de4":raise RuntimeError("v35 predecessor differs")
 payload={k:(P/k).read_bytes() for k in manifest["files"] if k!="closure.json"};payload[AUTH]=patched_authority();invalidation={"schema_version":"mysql_job_v35_measurement_parent_traversal_invalidation_v1","status":"INVALIDATED_DO_NOT_RERUN","predecessor_bundle_id":P.name,"predecessor_manifest_sha256":digest(P/"bundle_manifest.json"),"failure":"/dev/shm/ra_mysql_job_v2 was root:root 0700 so UID 1008 could not traverse its attempt bind","replacement":"root_root_0711_parent_plus_uid1008_gid1008_0700_attempt_tmpfs_v1","accepted_round_credit":0,"formal_launch_authorized":False,"accepted_round_authorized":False};payload[INV]=canon(invalidation)
 hashes={k:hashlib.sha256(v).hexdigest() for k,v in sorted(payload.items())};oldclosure=load(P/"closure.json");closure=dict(oldclosure);closure["files"]=hashes;closure["measurement_tmpfs_parent_contract"]={"path":"/dev/shm/ra_mysql_job_v2","uid":0,"gid":0,"mode":"0711","enumeration_prohibited":True,"attempt_uid":1008,"attempt_gid":1008,"attempt_mode":"0700"};payload["closure.json"]=canon(closure);files={k:hashlib.sha256(v).hexdigest() for k,v in sorted(payload.items())};identity={k:v for k,v in manifest.items() if k not in ("bundle_id","files")};identity.update({"schema_version":"mysql_job_v36_measurement_parent_successor_bundle_v1","predecessor_bundle_id":P.name,"predecessor_manifest_sha256":digest(P/"bundle_manifest.json"),"measurement_tmpfs_parent_contract":closure["measurement_tmpfs_parent_contract"],"v35_parent_traversal_invalidation_sha256":hashlib.sha256(payload[INV]).hexdigest(),"formal_launch_authorized":False,"accepted_round_authorized":False,"files":files});bid=hashlib.sha256(canon(identity)).hexdigest();identity["bundle_id"]=bid;O.mkdir(parents=True,exist_ok=True);t=O/bid
 if t.exists():
  if load(t/"bundle_manifest.json")!=identity:raise RuntimeError("existing differs")
 else:
  d=O/(".stage-"+bid+"-"+secrets.token_hex(6));d.mkdir(mode=0o700)
  try:
   for n,v in payload.items():q=d/n;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(v);os.chmod(q,0o444)
   raw=canon(identity);(d/"bundle_manifest.json").write_bytes(raw);(d/"bundle_manifest.json.sha256").write_text(hashlib.sha256(raw).hexdigest()+"  bundle_manifest.json\n");os.rename(d,t)
  except BaseException:
   if d.exists():shutil.rmtree(d)
   raise
 print(json.dumps({"status":"PASS_SEALED_PARENT_TRAVERSAL_SUCCESSOR","bundle":str(t),"bundle_id":bid,"manifest_sha256":digest(t/"bundle_manifest.json"),"accepted_round_authorized":False},sort_keys=True))
if __name__=="__main__":
 try:main()
 except Exception as e:sys.stderr.write("NOT_READY: %s\n"%e);raise SystemExit(2)
