#!/usr/bin/env python3
"""Execute one guarded host01 AP v5 17-endpoint estimator collection session."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import socket
import subprocess
import tempfile
import time
from typing import Any
import sys
import signal


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HOST = "anonymous@192.0.2.3"
DATADIR = "/mnt/mysql_nvme0n1/anonymous/resource_adaptive_mysql/hybench_ap/production_binding_trace_20260723_0630/datadir"
TEMPLATE = "/mnt/mysql_nvme0n1/anonymous/resource_adaptive_mysql/hybench_ap/mysql_template_data"
PROJECT = "/mnt/mysql_nvme0n1/anonymous/resource_adaptive_mysql/hybench_ap"
IMAGE = "sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
V5 = ROOT / "config/formal/frozen_mysql_workloads_and_parameters_20260723_v5_estimator_t"
REQUEST = V5 / "ap.collection_request.json"
RUNTIME = V5 / "ap.runtime_config.template.json"
REQUEST_SHA = "62bbab29f7987a2cae271fcc3884aeb13704895ed5b0ba2ec736fee2cf178fd7"
RUNTIME_SHA = "b464d195e1a53c1b9648199aa2e5a445c339bfa3e1405c78f20c248ee4ae795c"
RELEASE = ROOT / "history/mysql_sgd_env_agents_20260723/ap/ap_v5_endpoint_main_controller_release.json"
DRY = ROOT / "history/mysql_sgd_env_agents_20260723/ap/ap_17_endpoint_session_v5_bound_20260723_0715.json"
DRY_SHA = "4897a4a0c5faf6ccd4a740dc96f7eb8b73078cbfc65fe9230008289e4fb3d30b"
GUARD = ROOT / "shell/verify_host01_ap_native_mysqld_guard.py"
COLLECTOR = ROOT / "shell/collect_mysql_trained_estimator_endpoint_evidence.py"
FREEZER = ROOT / "shell/freeze_mysql_estimator_experience_table.py"
MODEL_PYTHON = Path("/home/anonymous/miniconda3/envs/resource_adaptive/bin/python")
MODEL_VERIFY = Path("/home/anonymous/program/resource_adaptive/mysql_plan_models/verify_all.py")
RUN_RE = re.compile(r"ap_v5_endpoint_(?:failure|real)_[0-9]{8}T[0-9]{6}Z\Z")


REMOTE_SESSION = r'''
set -Eeuo pipefail
umask 077
run="$1"; remote_port="$2"; data="$3"; template="$4"; image="$5"
rm -f -- "$0"
root=/mnt/mysql_nvme0n1/anonymous/resource_adaptive_mysql/hybench_ap
ev="$root/endpoint_sessions/$run"; locks="$root/locks"
lane="$locks/host01_mysql_lane.lock"; serial="$locks/ap_estimator_t_endpoint.lock"
owner="$locks/host01_mysql_lane.owner.json"; serial_owner="$locks/ap_estimator_t_endpoint.owner.json"
name="ra_mysql_ap01_endpoint_${run}"; net="${name}_net"; socket_dir="$ev/socket"
manifest_name="${name}_template_manifest"
cid=""; net_created=0; cleanup_done=0; template_before=""; remote_defaults=""
lane_owner_created=0; serial_owner_created=0
template_manifest(){ docker run --rm --name "$manifest_name" --network none --cpus 0.25 --cpuset-cpus 27 --cpuset-mems 1 --memory 134217728 --memory-swap 134217728 --pids-limit 64 --device-read-bps /dev/nvme0n1:10485760 --read-only -v "$template:/template:ro" --entrypoint /bin/bash "$image" -c "find /template -xdev -printf '%P|%y|%i|%s|%T@|%C@|%m|%u|%g\\n' | LC_ALL=C sort"; }
owner_matches(){ python3 - "$1" "$run" "$$" <<'PY'
import json,sys
p,run,pid=sys.argv[1:]
try: value=json.load(open(p))
except FileNotFoundError: raise SystemExit(1)
raise SystemExit(0 if value=={'run_id':run,'pid':int(pid),'scope':'ap_v5_17_endpoint'} else 1)
PY
}
remove_own_owner(){ path="$1"; created="$2"; if test "$created" = 1 && test -e "$path"; then owner_matches "$path" && rm -f -- "$path" || return 1; fi; }
cleanup(){
 rc=$?; trap - EXIT HUP INT TERM
 if test "$cleanup_done" = 0; then
  docker rm -f "$manifest_name" >/dev/null 2>&1 || true
  if test -n "$cid" && docker inspect "$cid" >/dev/null 2>&1; then
   docker stop --time 120 "$cid" >"$ev/container_stop.txt" 2>&1 || true
   docker inspect "$cid" >"$ev/container_inspect_stopped.json" 2>/dev/null || true
   docker logs "$cid" >"$ev/mysql.log" 2>&1 || true
   docker rm -f "$cid" >"$ev/container_remove.txt" 2>&1 || true
  fi
  cid=""
  if test "$net_created" = 1; then docker network rm "$net" >"$ev/network_remove.txt" 2>&1 || true; fi
  if test -n "$remote_defaults"; then rm -f -- "$remote_defaults"; fi
  rm -rf -- "$socket_dir"
  template_unchanged=false
  if test -n "$template_before"; then template_manifest >"$ev/template_recursive_after.tsv" && template_after=$(sha256sum "$ev/template_recursive_after.tsv"|awk '{print $1}') && printf '%s\n' "$template_after" >"$ev/template_recursive_after.sha256" && test "$template_after" = "$template_before" && template_unchanged=true || rc=91; fi
  work_copy_retained=false; test -d "$data" && work_copy_retained=true || rc=92
  container_removed=false; test -z "$(docker ps -a --format '{{.Names}}'|grep -x "$name"||true)" && container_removed=true || rc=93
  network_removed=false; test -z "$(docker network ls --format '{{.Name}}'|grep -x "$net"||true)" && network_removed=true || rc=94
  manifest_helper_removed=false; test -z "$(docker ps -a --format '{{.Names}}'|grep -x "$manifest_name"||true)" && manifest_helper_removed=true || rc=100
  socket_removed=false; test ! -e "$socket_dir" && socket_removed=true || rc=95
  remove_own_owner "$serial_owner" "$serial_owner_created" || rc=96
  remove_own_owner "$owner" "$lane_owner_created" || rc=97
  owners_removed=false; test ! -e "$owner" && test ! -e "$serial_owner" && owners_removed=true || rc=98
  defaults_removed=false; { test -z "$remote_defaults" || test ! -e "$remote_defaults"; } && defaults_removed=true || rc=99
  printf '{"event":"remote_cleanup","rc":%s,"container_removed":%s,"network_removed":%s,"manifest_helper_removed":%s,"socket_removed":%s,"owners_removed":%s,"defaults_removed":%s,"work_copy_retained":%s,"template_recursive_manifest_unchanged":%s}\n' "$rc" "$container_removed" "$network_removed" "$manifest_helper_removed" "$socket_removed" "$owners_removed" "$defaults_removed" "$work_copy_retained" "$template_unchanged" >>"$ev/events.jsonl" || true
  cleanup_done=1
 fi
 exit "$rc"
}
mkdir -p "$root/endpoint_sessions"; test ! -e "$ev"; mkdir "$ev"; chmod 0700 "$ev"
trap cleanup EXIT HUP INT TERM
exec 8>>"$lane"; flock -n 8
exec 9>>"$serial"; flock -n 9
test ! -e "$owner"; test ! -e "$serial_owner"
printf '{"run_id":"%s","pid":%s,"scope":"ap_v5_17_endpoint"}\n' "$run" "$$" >"$owner.partial.$$"; chmod 0600 "$owner.partial.$$"; lane_owner_created=1; mv -T "$owner.partial.$$" "$owner"; owner_matches "$owner"
printf '{"run_id":"%s","pid":%s,"scope":"ap_v5_17_endpoint"}\n' "$run" "$$" >"$serial_owner.partial.$$"; chmod 0600 "$serial_owner.partial.$$"; serial_owner_created=1; mv -T "$serial_owner.partial.$$" "$serial_owner"; owner_matches "$serial_owner"
printf '{"network":"none","cpus":0.25,"cpuset_cpus":"27","cpuset_mems":"1","memory_bytes":134217728,"memsw_bytes":134217728,"pids":64,"read_bps":10485760,"template_mount":"ro","read_only_rootfs":true}\n' >"$ev/template_manifest_helper_envelope.json"
test "$(hostname)" = node01; test -d "$data"; test -d "$template"; test "$(realpath "$data")" != "$(realpath "$template")"
template_manifest >"$ev/template_recursive_before.tsv"; template_before=$(sha256sum "$ev/template_recursive_before.tsv"|awk '{print $1}'); printf '%s\n' "$template_before" >"$ev/template_recursive_before.sha256"
test "$(docker image inspect "$image" --format '{{.Id}}')" = "$image"
test -z "$(docker ps --format '{{.Names}}'|grep -E '^ra_mysql_'||true)"
test -z "$(docker network ls --format '{{.Name}}'|grep -E '^ra_mysql_ap01_endpoint_'||true)"
! pgrep -x sysbench >/dev/null; ! pgrep -x mysqlslap >/dev/null; ! pgrep -x java >/dev/null
test "$(systemctl show mysqld.service -p MainPID|cut -d= -f2)" = 2809
test "$(systemctl is-active mysqld.service)" = active
test "$(cat /sys/fs/cgroup/cpuset/ra_ap_guard_mysqld/cpuset.cpus)" = '0-17,36-53'
test "$(cat /sys/fs/cgroup/cpuset/ra_ap_guard_mysqld/cpuset.mems)" = 0
test "$(cat /sys/fs/cgroup/memory/system.slice/mysqld.service/ra_ap_guard/memory.limit_in_bytes)" = 2147483648
test "$(cat /sys/fs/cgroup/memory/system.slice/mysqld.service/ra_ap_guard/memory.memsw.limit_in_bytes)" = 2147483648
connections=$(netstat -nt|awk '$6=="ESTABLISHED"&&($4~/:3306$/||$5~/:3306$/||$4~/:33060$/||$5~/:33060$/){n++}END{print n+0}'); test "$connections" = 0
cpu=/sys/fs/cgroup/cpu,cpuacct/system.slice/mysqld.service/cpuacct.usage
io=/sys/fs/cgroup/blkio/system.slice/mysqld.service/blkio.throttle.io_service_bytes
ca=$(cat "$cpu"); ia=$(awk '$2=="Read"||$2=="Write"{n+=$3}END{print n+0}' "$io"); sleep 3; cb=$(cat "$cpu"); ib=$(awk '$2=="Read"||$2=="Write"{n+=$3}END{print n+0}' "$io")
test $((cb-ca)) -le 300000000; test $((ib-ia)) -le 3145728
if netstat -lnt 2>/dev/null | awk 'NR>2{print $4}' | grep -Eq "(^|:)${remote_port}$"; then exit 73; fi
mkdir "$socket_dir"; chmod 0777 "$socket_dir"
docker network create "$net" >"$ev/network_create.txt"; net_created=1
cid=$(docker run -d --name "$name" --network "$net" --cpus 3 --cpuset-cpus 24-26,60-62 --cpuset-mems 1 --memory 6442450944 --memory-swap 6442450944 --pids-limit 512 --device-read-bps /dev/nvme0n1:1205862400 --device-write-bps /dev/nvme0n1:1205862400 -p "127.0.0.1:${remote_port}:3306" -v "$data:/var/lib/mysql:rw" -v "$socket_dir:/var/run/mysqld:rw" "$image" mysqld --bind-address=0.0.0.0 --innodb-buffer-pool-size=4294967296 --innodb-flush-method=O_DIRECT --innodb-flush-log-at-trx-commit=2 --skip-log-bin --max-connections=64 --performance-schema=ON '--performance-schema-instrument=memory/%=ON')
docker inspect "$cid" >"$ev/container_inspect_running.json"
ready=0; for _ in $(seq 1 120); do if docker exec "$cid" mysqladmin -uroot ping --silent >/dev/null 2>&1; then ready=1; break; fi; sleep 2; done; test "$ready" = 1
docker exec "$cid" mysql -uroot --batch --skip-column-names -e 'SELECT @@GLOBAL.skip_networking, @@GLOBAL.port, CURRENT_USER()' >"$ev/mysql_socket_preflight.tsv" 2>"$ev/mysql_socket_preflight.stderr"
awk -F '\t' 'NR==1 && $1==0 && $2==3306 && $3!=""{ok=1} END{exit !ok}' "$ev/mysql_socket_preflight.tsv"
docker exec "$cid" mysql -uroot --batch --skip-column-names -e "SELECT COALESCE(SUM(CURRENT_NUMBER_OF_BYTES_USED),0)/1048576 FROM performance_schema.memory_summary_global_by_event_name" >"$ev/mysql_memory_model_baseline_mb.tsv" 2>"$ev/mysql_memory_model_baseline_mb.stderr"
awk 'NR==1 && $1+0>0{ok=1} END{exit !ok}' "$ev/mysql_memory_model_baseline_mb.tsv"
remote_defaults="/tmp/.${run}.mysql.defaults.cnf"
test ! -e "$remote_defaults"
printf '[client]\nuser=root\nprotocol=tcp\nssl-mode=DISABLED\n' >"$remote_defaults"
chmod 0600 "$remote_defaults"
printf '{"mode":"0600","user":"root","protocol":"tcp","ssl_mode":"DISABLED","password_field":false}\n' >"$ev/host_client_defaults.audit.json"
host_tcp_ready=0
for _ in $(seq 1 60); do
 if mysql --defaults-file="$remote_defaults" -h127.0.0.1 -P"$remote_port" --batch --skip-column-names -e 'SELECT 1, CURRENT_USER()' >"$ev/host_published_port_select1.stdout" 2>"$ev/host_published_port_select1.stderr"; then host_tcp_ready=1; break; fi
 sleep 1
done
test "$host_tcp_ready" = 1
awk -F '\t' 'NR==1 && $1==1 && $2!=""{ok=1} END{exit !ok}' "$ev/host_published_port_select1.stdout"
python3 - "$cid" "$remote_port" >"$ev/effective_envelope.json" <<'PY'
import json,subprocess,sys
d=json.loads(subprocess.check_output(['docker','inspect',sys.argv[1]],universal_newlines=True))[0];h=d['HostConfig']
assert d['State']['Running'] and (h['NanoCpus'],h['Memory'],h['MemorySwap'],h['PidsLimit'],h['CpusetCpus'],h['CpusetMems'])==(3000000000,6442450944,6442450944,512,'24-26,60-62','1')
assert h['PortBindings']['3306/tcp']==[{'HostIp':'127.0.0.1','HostPort':sys.argv[2]}]
assert any(x['Source'].endswith('/production_binding_trace_20260723_0630/datadir') and x['Destination']=='/var/lib/mysql' and x['RW'] for x in d['Mounts'])
assert not any(x['Source'].endswith('/mysql_template_data') for x in d['Mounts'])
assert any(x['Path']=='/dev/nvme0n1' and x['Rate']==1205862400 for x in h['BlkioDeviceReadBps'])
assert any(x['Path']=='/dev/nvme0n1' and x['Rate']==1205862400 for x in h['BlkioDeviceWriteBps'])
print(json.dumps({'status':'PASS','container_id':d['Id'],'cpu_cores':3,'memory_bytes':h['Memory'],'memsw_bytes':h['MemorySwap'],'pids':h['PidsLimit'],'cpuset_cpus':h['CpusetCpus'],'cpuset_mems':h['CpusetMems'],'remote_port':int(sys.argv[2]),'template_mounted':False},sort_keys=True))
PY
printf '{"event":"endpoint_ready","container_id":"%s","remote_port":%s}\n' "$cid" "$remote_port" >>"$ev/events.jsonl"
container_ip=$(docker inspect "$cid" --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'); test -n "$container_ip"
printf '{"status":"REMOTE_READY","container_id":"%s","container_ip":"%s","remote_pid":%s,"remote_evidence":"%s"}\n' "$cid" "$container_ip" "$$" "$ev"
IFS= read -r command; test "$command" = FINISH
cleanup_done=0
exit 0
'''


def sha(path: Path) -> str:
    if not path.is_file() or path.is_symlink(): raise ValueError(f"missing/unsafe file: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try: os.fsync(fd)
    finally: os.close(fd)


def write_json(path: Path, value: Any, mode: int = 0o600) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle: handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.chmod(raw, mode); os.replace(raw, path)
        fsync_dir(path.parent)
    finally:
        if os.path.exists(raw): os.unlink(raw)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    sidecar = path.with_name(path.name + ".sha256")
    fd, raw = tempfile.mkstemp(prefix=f".{sidecar.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(f"{digest}  {path.name}\n"); handle.flush(); os.fsync(handle.fileno())
        os.chmod(raw, mode); os.replace(raw, sidecar)
        fsync_dir(path.parent)
    finally:
        if os.path.exists(raw): os.unlink(raw)
    return digest


def seal_run(staging: Path, published: Path) -> str:
    entries: list[dict[str, Any]] = []
    for path in sorted(p for p in staging.rglob("*") if p.is_file() and p.name not in {"MANIFEST.json", "MANIFEST.json.sha256", "SEALED"}):
        with path.open("rb") as handle: os.fsync(handle.fileno())
        entries.append({"path": str(path.relative_to(staging)), "bytes": path.stat().st_size, "sha256": sha(path)})
    manifest_sha = write_json(staging / "MANIFEST.json", {"schema_version": "sealed_run_manifest_v1", "files": entries})
    sealed = staging / "SEALED"
    with sealed.open("x", encoding="ascii") as handle:
        handle.write(f"{manifest_sha}  MANIFEST.json\n"); handle.flush(); os.fsync(handle.fileno())
    os.chmod(sealed, 0o600)
    fsync_dir(staging)
    os.replace(staging, published)
    fsync_dir(published.parent)
    return manifest_sha


def exact_gates(args: argparse.Namespace, local_root: Path) -> dict[str, Any]:
    if args.confirm_host != HOST or args.confirm_datadir != DATADIR or args.confirm_request_sha256 != REQUEST_SHA or args.confirm_runtime_sha256 != RUNTIME_SHA:
        raise ValueError("exact host/datadir/v5 SHA confirmations required")
    release_sha = sha(RELEASE)
    if args.main_controller_release_sha256 != release_sha:
        raise ValueError("main-controller release SHA mismatch")
    if sha(REQUEST) != REQUEST_SHA or sha(RUNTIME) != RUNTIME_SHA or sha(DRY) != DRY_SHA:
        raise ValueError("v5 request/runtime/dry-run identity changed")
    verified = subprocess.run(["python3", str(ROOT / "shell/verify_mysql_ap_estimator_t_inputs_v5.py")], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if verified.returncode or json.loads(verified.stdout)["status"] != "PASS": raise ValueError("v5 independent verifier failed")
    guard_path = local_root / "native_guard.json"
    guard = subprocess.run(["python3", str(GUARD), "--output", str(guard_path), "--sample-seconds", "5"], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if guard.returncode or json.loads(guard_path.read_text())["status"] != "PASS_PREPARATION_ISOLATION": raise ValueError("fresh native guard failed")
    model_env = subprocess.run([str(MODEL_PYTHON), "-c", "import json,joblib,numpy,pandas,sklearn,sys;print(json.dumps({'executable':sys.executable,'joblib':joblib.__version__,'numpy':numpy.__version__,'pandas':pandas.__version__,'sklearn':sklearn.__version__},sort_keys=True))"], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    model_verify = subprocess.run([str(MODEL_PYTHON), str(MODEL_VERIFY)], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if model_env.returncode or model_verify.returncode or json.loads(model_verify.stdout).get("all_passed") is not True: raise ValueError("frozen model runtime gate failed")
    model_gate = {"runtime": json.loads(model_env.stdout), "verification": json.loads(model_verify.stdout), "model_python_sha256": sha(MODEL_PYTHON.resolve()), "verify_script_sha256": sha(MODEL_VERIFY)}
    write_json(local_root / "model_runtime_gate.json", model_gate)
    return {"request_sha256": REQUEST_SHA, "runtime_sha256": RUNTIME_SHA, "dry_run_sha256": DRY_SHA, "release_sha256": release_sha, "fresh_native_guard_sha256": sha(guard_path), "model_runtime_gate_sha256": sha(local_root / "model_runtime_gate.json"), "v5_verifier": json.loads(verified.stdout)}


def port_free(port: int) -> bool:
    with socket.socket() as sock:
        try: sock.bind(("127.0.0.1", port)); return True
        except OSError: return False


def start_remote(run: str, remote_port: int) -> tuple[subprocess.Popen[str], dict[str, Any]]:
    remote_script = f"/tmp/.{run}.session.sh"
    upload = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "--", HOST,
         f"umask 077; test ! -e {remote_script}; cat > {remote_script}; chmod 600 {remote_script}"],
        input=REMOTE_SESSION, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if upload.returncode:
        raise RuntimeError("remote session-script upload failed: " + upload.stderr.strip())
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3", "--", HOST, "bash", remote_script, run, str(remote_port), DATADIR, TEMPLATE, IMAGE]
    proc = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    assert proc.stdout is not None
    ready, _, _ = select.select([proc.stdout], [], [], 300)
    if not ready:
        proc.kill(); raise RuntimeError("remote endpoint readiness timeout")
    line = proc.stdout.readline().strip()
    if not line:
        stderr = proc.stderr.read() if proc.stderr else ""
        raise RuntimeError(f"remote endpoint failed before ready: {stderr[-2000:]}")
    row = json.loads(line)
    if row.get("status") != "REMOTE_READY": raise RuntimeError(f"unexpected remote readiness: {row}")
    return proc, row


def stop_remote(proc: subprocess.Popen[str]) -> tuple[int, str]:
    if proc.poll() is None and proc.stdin is not None:
        proc.stdin.write("FINISH\n"); proc.stdin.flush()
    try: _, stderr = proc.communicate(timeout=240)
    except subprocess.TimeoutExpired:
        proc.kill(); _, stderr = proc.communicate(); return 124, stderr
    return int(proc.returncode or 0), stderr


def emergency_cleanup(run: str, expected_pid: int | None) -> None:
    name = "ra_mysql_ap01_endpoint_" + run; net = name + "_net"
    script = r'''set -euo pipefail; name="$1"; net="$2"; run="$3"; root=/mnt/mysql_nvme0n1/anonymous/resource_adaptive_mysql/hybench_ap
manifest_name="${name}_template_manifest"
lane="$root/locks/host01_mysql_lane.lock"; serial="$root/locks/ap_estimator_t_endpoint.lock"
exec 8>>"$lane"; flock -w 240 8
exec 9>>"$serial"; flock -w 240 9
for owner in "$root/locks/host01_mysql_lane.owner.json" "$root/locks/ap_estimator_t_endpoint.owner.json"; do
 if test -e "$owner"; then python3 - "$owner" "$run" "$4" <<'PY'
import json,sys
p,run,expected=sys.argv[1:]
value=json.load(open(p))
valid=value.get('run_id')==run and value.get('scope')=='ap_v5_17_endpoint' and isinstance(value.get('pid'),int) and value['pid']>1
if expected: valid=valid and value['pid']==int(expected)
if not valid: raise SystemExit(95)
PY
 fi
done
docker stop --time 120 "$name" >/dev/null 2>&1 || true
docker rm -f "$name" >/dev/null 2>&1 || true
docker rm -f "$manifest_name" >/dev/null 2>&1 || true
docker network rm "$net" >/dev/null 2>&1 || true
rm -rf -- "$root/endpoint_sessions/$run/socket"
rm -f -- "/tmp/.${run}.mysql.defaults.cnf"
for owner in "$root/locks/host01_mysql_lane.owner.json" "$root/locks/ap_estimator_t_endpoint.owner.json"; do if test -e "$owner"; then rm -f -- "$owner"; fi; done
test -z "$(docker ps -a --format '{{.Names}}'|grep -x "$name"||true)"
test -z "$(docker ps -a --format '{{.Names}}'|grep -x "$manifest_name"||true)"
test -z "$(docker network ls --format '{{.Name}}'|grep -x "$net"||true)"
test ! -e "$root/endpoint_sessions/$run/socket"
test ! -e "/tmp/.${run}.mysql.defaults.cnf"
test ! -e "$root/locks/host01_mysql_lane.owner.json"
test ! -e "$root/locks/ap_estimator_t_endpoint.owner.json"'''
    result = subprocess.run(["ssh", "-o", "BatchMode=yes", "--", HOST, "bash", "-s", "--", name, net, run, str(expected_pid or "")], input=script, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=360)
    if result.returncode:
        raise RuntimeError("emergency cleanup refused mismatched owner or failed")


def materialize_runtime(path: Path, defaults: Path, local_lock: Path, local_port: int) -> dict[str, Any]:
    runtime = json.loads(RUNTIME.read_text())
    runtime["ready"] = True; runtime["database_connection_started"] = True
    runtime["mysql_client"]["defaults_extra_file"] = str(defaults)
    runtime["mysql_client"]["defaults_file_only"] = True
    runtime["mysql_client"]["port"] = local_port
    runtime["serialization_lock_file"] = str(local_lock)
    runtime["runtime_gate"]["satisfied"] = True
    write_json(path, runtime)
    return runtime


def verify_result(endpoint_dir: Path, t_path: Path) -> dict[str, Any]:
    manifest = json.loads((endpoint_dir / "collection_manifest.json").read_text())
    evidence = json.loads((endpoint_dir / "endpoint_evidence.json").read_text())
    if manifest["endpoint_evidence"]["sha256"] != sha(endpoint_dir / "endpoint_evidence.json") or evidence.get("query_coverage", {}).get("coverage_ratio") != 1.0:
        raise ValueError("endpoint evidence verification failed")
    count = 1 + 2 * len(evidence.get("active_params", []))
    if count != 17: raise ValueError("endpoint count is not 17")
    sys_path = str(ROOT / "shell")
    code = "import json,sys;sys.path.insert(0,sys.argv[1]);from calibrate_mysql_experience_table import _load_json;from freeze_mysql_estimator_experience_table import build_estimator_artifact;from pathlib import Path;x=build_estimator_artifact(_load_json(Path(sys.argv[2])),Path(sys.argv[2]));print(json.dumps(x,sort_keys=True,separators=(',',':')),end='')"
    rebuilt = subprocess.run(["python3", "-c", code, sys_path, str(endpoint_dir / "freeze_request.json")], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if rebuilt.returncode or hashlib.sha256(rebuilt.stdout.encode()).hexdigest() != sha(t_path): raise ValueError("independent T rebuild mismatch")
    t = json.loads(t_path.read_text())
    if t.get("calibration", {}).get("real_measurement") is not False or t.get("provenance", {}).get("source") != "trained_estimator_experience": raise ValueError("T provenance mismatch")
    return {"status": "PASS", "endpoint_count": count, "coverage_ratio": 1.0, "t_sha256": sha(t_path), "independent_rebuild_equal": True, "real_measurement": False}


def run(args: argparse.Namespace) -> dict[str, Any]:
    base = (ROOT / "history/mysql_sgd_env_agents_20260723/ap").resolve()
    published = base / args.run_id
    staging = base / f".{args.run_id}.staging.{os.getpid()}"
    if published.exists() or staging.exists(): raise ValueError("run output already exists")
    staging.mkdir(parents=True, mode=0o700); fsync_dir(base)
    run_root = staging
    stage = "exact_gates"; stage_at_failure: str | None = None
    gates: dict[str, Any] = {}
    local_port = args.local_port; remote_port = args.remote_port
    secret_root: Path | None = None; defaults: Path | None = None
    remote: subprocess.Popen[str] | None = None; tunnel: subprocess.Popen[str] | None = None; tunnel_log = None
    remote_attempted = False
    adapter = None; snapshot = None; restore_equal = False; cleanup_ok = True; primary: BaseException | None = None; ready: dict[str, Any] | None = None
    events: list[dict[str, Any]] = []
    try:
        gates = exact_gates(args, run_root)
        if not port_free(local_port): raise ValueError("local tunnel port is busy")
        stage = "create_0600_passwordless_defaults"
        secret_root = Path(tempfile.mkdtemp(prefix=f".{args.run_id}.credentials.", dir="/tmp")); os.chmod(secret_root, 0o700)
        defaults = secret_root / "mysql.defaults.cnf"
        with defaults.open("x", encoding="utf-8") as handle:
            handle.write("[client]\nuser=root\nprotocol=tcp\nssl-mode=DISABLED\n"); handle.flush(); os.fsync(handle.fileno())
        os.chmod(defaults, 0o600); fsync_dir(secret_root)
        stage = "remote_start_and_double_lock"
        remote_attempted = True
        remote, ready = start_remote(args.run_id, remote_port); events.append({"event": "remote_ready", "container_id": ready["container_id"]})
        stage = "ssh_tunnel"
        tunnel_log = (run_root / "tunnel.stderr").open("w", encoding="utf-8")
        tunnel = subprocess.Popen(["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=10", "-L", f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}", HOST], stdout=subprocess.DEVNULL, stderr=tunnel_log, text=True)
        time.sleep(2)
        if tunnel.poll() is not None: raise RuntimeError("SSH tunnel failed")
        with socket.create_connection(("127.0.0.1", local_port), timeout=10) as probe:
            probe.settimeout(10); greeting = probe.recv(4)
        if len(greeting) != 4:
            raise RuntimeError("SSH tunnel did not deliver a MySQL greeting")
        tunnel_log.flush()
        stage = "materialize_0600_passwordless_defaults_and_runtime"
        assert defaults is not None
        runtime_path = run_root / "runtime_config.json"; runtime = materialize_runtime(runtime_path, defaults, run_root / "collector_serialization.lock", local_port)
        from mysql_cost_adapter import MySQLCLIClient, MySQLCostAdapter
        client = MySQLCLIClient(runtime["mysql_client"])
        stage = "tunneled_mysql_readiness"
        connection_error: BaseException | None = None
        for _ in range(30):
            try:
                client.execute("SELECT 1"); connection_error = None; break
            except BaseException as error:
                connection_error = error; time.sleep(1)
        if connection_error is not None:
            raise RuntimeError(f"tunneled MySQL readiness failed: {connection_error}")
        stage = "exact_cost_snapshot"
        adapter = MySQLCostAdapter(client, engine_name="default", device_type=0); snapshot = adapter.snapshot()
        write_json(run_root / "cost_snapshot_before.json", [asdict(item) for item in snapshot]); events.append({"event": "exact_cost_snapshot", "rows": len(snapshot)})
        if args.inject_failure_after_snapshot:
            stage = "post_snapshot_failure_injection"
            adapter.apply({"io_block_read_cost": 1.0})
            raise RuntimeError("INJECTED_FAILURE_AFTER_EXACT_COST_SNAPSHOT_AND_COST_APPLY")
        stage = "collect_17_endpoints"
        endpoint_dir = run_root / "endpoint_evidence"
        collect = subprocess.run([str(MODEL_PYTHON), str(COLLECTOR), "--request", str(REQUEST), "--runtime-config", str(runtime_path), "--output-dir", str(endpoint_dir), "--progress-jsonl", str(run_root / "endpoint_progress.jsonl"), "--json"], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (run_root / "collector.stdout").write_text(collect.stdout, encoding="utf-8"); (run_root / "collector.stderr").write_text(collect.stderr, encoding="utf-8")
        if collect.returncode: raise RuntimeError("17 endpoint collector failed")
        stage = "freeze_t"
        t_path = run_root / "T.mysql_ap_estimator_v1.json"
        freeze = subprocess.run(["python3", str(FREEZER), "--request", str(endpoint_dir / "freeze_request.json"), "--output", str(t_path), "--json"], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (run_root / "freeze.stdout").write_text(freeze.stdout, encoding="utf-8"); (run_root / "freeze.stderr").write_text(freeze.stderr, encoding="utf-8")
        if freeze.returncode: raise RuntimeError("T freeze failed")
        stage = "independent_verify"
        verification = verify_result(endpoint_dir, t_path); write_json(run_root / "independent_verification.json", verification)
        events.append({"event": "collection_freeze_verify_complete", **verification})
    except BaseException as error:
        primary = error; stage_at_failure = stage
    finally:
        if adapter is not None and snapshot is not None:
            try:
                stage = "finally_restore_exact_cost"
                adapter.restore(snapshot); restore_equal = adapter.snapshot() == snapshot
                events.append({"event": "exact_cost_restore", "equal": restore_equal})
                if not restore_equal: raise RuntimeError("cost restore comparison failed")
            except BaseException as error:
                if primary is None: primary = error
                else: primary = RuntimeError(f"{primary}; restore_failed={type(error).__name__}")
        if tunnel is not None:
            stage = "finally_remove_tunnel"
            try:
                tunnel.terminate()
                try: tunnel.wait(timeout=10)
                except subprocess.TimeoutExpired: tunnel.kill(); tunnel.wait()
            except BaseException as error:
                cleanup_ok = False
                if primary is None: primary = error
            try:
                if tunnel_log is not None: tunnel_log.close()
            except Exception: pass
            events.append({"event": "tunnel_removed"})
            if tunnel.poll() is None: cleanup_ok = False
        if remote is not None:
            stage = "finally_remote_cleanup"
            try:
                rc, stderr = stop_remote(remote); (run_root / "remote.stderr").write_text(stderr, encoding="utf-8")
            except BaseException as error:
                rc, stderr = 125, str(error)
                cleanup_ok = False
                if primary is None: primary = error
            if rc:
                cleanup_ok = False
                if primary is None: primary = RuntimeError(f"remote cleanup rc={rc}")
            events.append({"event": "remote_session_closed", "rc": rc})
        if remote_attempted:
            stage = "finally_emergency_postcondition"
            try:
                emergency_cleanup(args.run_id, int(ready["remote_pid"]) if ready is not None else None)
            except BaseException as error:
                cleanup_ok = False
                if primary is None: primary = error
                else: primary = RuntimeError(f"{primary}; emergency_cleanup_failed={type(error).__name__}")
        if secret_root is not None:
            shutil.rmtree(secret_root, ignore_errors=True)
            if secret_root.exists(): cleanup_ok = False
        events.append({"event": "defaults_removed", "equal": secret_root is None or not secret_root.exists()})
        if remote_attempted:
            try:
                copied = subprocess.run(["rsync", "-a", "--", f"{HOST}:{PROJECT}/endpoint_sessions/{args.run_id}/", str(run_root / "remote") + "/"], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
                events.append({"event": "remote_evidence_copy", "returncode": copied.returncode})
            except (subprocess.TimeoutExpired, OSError):
                events.append({"event": "remote_evidence_copy", "returncode": 124})
    write_json(run_root / "events.json", {"events": events})
    error_message = None if primary is None else re.sub(r"/tmp/\.[^ /]+\.credentials\.[^ /]+", "<redacted_external_defaults_dir>", str(primary))[:2000]
    injected_clean = args.inject_failure_after_snapshot and primary is not None and stage_at_failure == "post_snapshot_failure_injection" and restore_equal and cleanup_ok
    forbidden_outputs_absent = not (run_root / "endpoint_evidence").exists() and not (run_root / "T.mysql_ap_estimator_v1.json").exists()
    if args.inject_failure_after_snapshot and not forbidden_outputs_absent:
        injected_clean = False
    result = {"schema_version": "host01_ap_v5_endpoint_collection_run_v2", "run_id": args.run_id, "status": "EXPECTED_FAILURE_CLEANUP_PASS" if injected_clean else "PASS" if primary is None and cleanup_ok else "FAIL_CLOSED", "gates": gates, "remote_ready": ready, "failure_injected": args.inject_failure_after_snapshot, "failure_stage": stage_at_failure if primary else None, "failure_type": type(primary).__name__ if primary else None, "error_message": error_message, "cost_restore_equal": restore_equal, "cleanup_ok": cleanup_ok, "defaults_removed": secret_root is None or not secret_root.exists(), "tunnel_removed": tunnel is None or tunnel.poll() is not None, "failure_outputs_absent": forbidden_outputs_absent, "work_copy_retained": True, "template_not_used_as_datadir": True, "qualification": {"environment_and_experience_table_collection_only": True, "postgresql_counterpart": False, "formal_static_isolation_gate_passed": False, "r0": False, "sgd": False, "ready": False, "launch_authorized": False}}
    result_sha = write_json(run_root / "summary.json", result)
    manifest_sha = seal_run(run_root, published)
    result["published_run_dir"] = str(published)
    result["manifest_sha256"] = manifest_sha
    if args.inject_failure_after_snapshot:
        if result["status"] != "EXPECTED_FAILURE_CLEANUP_PASS": raise RuntimeError(f"failure cleanup did not pass: {primary}")
    elif result["status"] != "PASS":
        if primary is not None: raise primary
        raise RuntimeError("cleanup postconditions failed")
    result["summary_sha256"] = result_sha
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--remote-port", type=int, default=23306)
    parser.add_argument("--local-port", type=int, default=13316)
    parser.add_argument("--confirm-host", required=True)
    parser.add_argument("--confirm-datadir", required=True)
    parser.add_argument("--confirm-request-sha256", required=True)
    parser.add_argument("--confirm-runtime-sha256", required=True)
    parser.add_argument("--main-controller-release-sha256", required=True)
    parser.add_argument("--inject-failure-after-snapshot", action="store_true")
    args = parser.parse_args()
    def terminate(signum: int, frame: Any) -> None:
        raise RuntimeError(f"TERMINATION_SIGNAL_{signum}")
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGHUP, terminate)
    if not RUN_RE.fullmatch(args.run_id): raise SystemExit("invalid run id")
    result = run(args); print(json.dumps(result, sort_keys=True)); return 0


if __name__ == "__main__":
    try: raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"status": "FAIL_CLOSED", "error": str(error), "error_type": type(error).__name__}, sort_keys=True))
        raise SystemExit(2)
