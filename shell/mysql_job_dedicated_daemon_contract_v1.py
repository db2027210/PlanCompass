#!/usr/bin/env python3
"""Fail-closed JOB dedicated-Docker-daemon contract and readback validator."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
SOCKET="/run/ra-docker-mysql-job-v1.sock";ROOT="/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/docker_ra_job_v1";DEVICE="259:3";UNIT="ra-docker-mysql-job-v1.service";CPUS="34-35,70-71";PORT=65102
class Error(RuntimeError):pass
def canon(v):return (json.dumps(v,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
def unit_text():return "\n".join(("[Unit]","Description=Dedicated Docker daemon for MySQL JOB","[Service]","ExecStart=/usr/bin/dockerd --host=unix:%s --data-root=%s --iptables=false --bridge=none"%(SOCKET,ROOT),"CPUAffinity=%s"%CPUS,"Delegate=yes","[Install]","WantedBy=multi-user.target",""))
def validate(info,daemon,containers,mount_devices):
 if info.get("DockerRootDir")!=ROOT or info.get("Driver")!="overlay2":raise Error("DockerRootDir/driver differs")
 if daemon.get("unit")!=UNIT or daemon.get("socket")!=SOCKET or daemon.get("cpuset_cpus")!=CPUS or daemon.get("data_root_device")!=DEVICE or not daemon.get("pid") or not daemon.get("startticks") or not daemon.get("cmdline_sha256"):raise Error("daemon identity differs")
 if daemon.get("ppid")!=1 or daemon.get("docker_ancestry") is not False or UNIT not in str(daemon.get("cgroup") or ""):raise Error("daemon is not a direct host systemd process")
 namespaces=daemon.get("namespaces") or {}
 if set(namespaces)!={"mnt","pid","net","user"} or not all(isinstance(v,str) and v for v in namespaces.values()):raise Error("daemon namespace identity differs")
 if set(containers)!={"database","client"}:raise Error("DB/client evidence required")
 for role,row in containers.items():
  if row.get("network_mode")!="host" or row.get("daemon_socket")!=SOCKET or row.get("mysql_server_port")!=PORT or not row.get("container_id") or not row.get("pid") or not row.get("startticks"):raise Error(role+" dedicated daemon identity differs")
  data=row.get("graphdriver_data") or {}
  if set(data)!={"UpperDir","WorkDir","MergedDir","LowerDir"}:raise Error(role+" GraphDriver fields differ")
  for key,path in data.items():
   if not isinstance(path,str) or not path.startswith(ROOT+"/") or mount_devices.get(path)!=DEVICE:raise Error(role+" GraphDriver path is on PG/unknown device")
 return {"schema_version":"mysql_job_dedicated_daemon_readback_v1","status":"PASS_DEDICATED_DAEMON_NON_PG_GRAPHDRIVER","socket":SOCKET,"data_root":ROOT,"device_major_minor":DEVICE,"unit":UNIT,"daemon":daemon,"containers":containers,"accepted_round_authorized":False}
