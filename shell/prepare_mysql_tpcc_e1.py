#!/usr/bin/env python3
"""Fail-closed host04 MySQL TPC-C E1 template-generation controller.

The default mode performs one read-only remote admission audit.  ``--execute``
requires literal host/project/image confirmations.  E1 only builds and seals a
10-warehouse template; it never launches R0 or the optimizer and never sends a
signal, Docker command, SQL statement, or filesystem mutation to PostgreSQL.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import textwrap
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = (
    ROOT / "config/formal/mysql_tpcc_e1_assets_20260723_v1/asset_manifest.json"
)
HOST_PATTERN = re.compile(r"[A-Za-z0-9_.-]+@[A-Za-z0-9_.:-]+\Z")


class TPCCE1Error(RuntimeError):
    """E1 cannot safely advance."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class SubprocessRunner:
    def run(self, args: Sequence[str], *, input_bytes: bytes | None = None) -> CommandResult:
        completed = subprocess.run(
            list(args), input=input_bytes, capture_output=True, check=False
        )
        return CommandResult(
            completed.returncode,
            completed.stdout.decode("utf-8", errors="replace"),
            completed.stderr.decode("utf-8", errors="replace"),
        )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _asset(path: Path, expected: str) -> bytes:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise TPCCE1Error(f"asset is not a safe regular file: {path}")
    data = path.read_bytes()
    if _sha256(data) != expected:
        raise TPCCE1Error(f"asset SHA-256 mismatch: {path}")
    return data


def _load_manifest(path: Path) -> tuple[Mapping[str, Any], bytes]:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise TPCCE1Error(f"manifest is not a safe regular file: {path}")
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TPCCE1Error("invalid asset manifest") from exc
    required = {
        "asset_id": "mysql_tpcc_e1_assets_20260723_v1",
        "status": "E1_ENVIRONMENT_PREPARATION_ONLY",
        "formal_r0_authorized": False,
        "host": "anonymous@192.0.2.7",
        "hostname": "node04",
    }
    if any(value.get(key) != expected for key, expected in required.items()):
        raise TPCCE1Error("asset manifest identity/status mismatch")
    return value, raw


def _ssh(host: str, command: str) -> tuple[str, ...]:
    return (
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        "--", host, command,
    )


def _remote_preflight_script(manifest: Mapping[str, Any]) -> bytes:
    database = manifest["database"]
    storage = manifest["storage"]
    assets = manifest["assets"]
    payload = {
        "hostname": manifest["hostname"],
        "image_id": database["image_id"],
        "project_root": manifest["project_root"],
        "template": storage["template"],
        "device": storage["device"],
        "major_minor": storage["major_minor"],
        "serial": storage["serial"],
        "filesystem": storage["filesystem"],
        "filesystem_uuid": storage["filesystem_uuid"],
        "mount": storage["mount"],
        "db_cpuset": database["cpuset_cpus"],
        "db_mems": database["cpuset_mems"],
        "remote_lib": assets["remote_library_tree"],
        "remote_jre": assets["remote_jre_tree"],
        "remote_config": assets["remote_config_tree"],
    }
    # The script emits exactly one JSON object and performs no write.
    script = r'''
import hashlib, json, os, pathlib, re, subprocess, sys
E = json.loads(__EXPECTED__)
def run(*args):
    p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,check=False)
    if p.returncode: raise RuntimeError("command failed: "+args[0]+": "+(p.stderr or p.stdout)[-500:])
    return p.stdout.strip()
def tree(root_s):
    root=pathlib.Path(root_s); h=hashlib.sha256(); count=0; size=0
    if not root.is_dir() or root.is_symlink(): raise RuntimeError("asset tree unavailable: "+root_s)
    for p in sorted((x for x in root.rglob("*") if x.is_file()),key=lambda x:x.relative_to(root).as_posix()):
        data=p.read_bytes(); rel=p.relative_to(root).as_posix().encode()
        h.update(rel+b"\0"+hashlib.sha256(data).hexdigest().encode()+b"\n")
        count+=1; size+=len(data)
    return h.hexdigest(),count,size
checks={}
checks["hostname"] = run("hostname") == E["hostname"]
image=json.loads(run("docker","image","inspect",E["image_id"]))[0]
checks["image"] = image["Id"] == E["image_id"]
line=run("lsblk","-dn","-o","MAJ:MIN,SERIAL,FSTYPE,UUID",E["device"]).split()
checks["device"] = len(line)>=4 and line[0]==E["major_minor"] and line[1]==E["serial"] and line[2]==E["filesystem"] and line[3]==E["filesystem_uuid"]
mount=run("findmnt","-rn","-o","SOURCE,MAJ:MIN,FSTYPE,TARGET","--target",E["mount"]).split()
checks["mount"] = len(mount)==4 and mount==[E["device"],E["major_minor"],E["filesystem"],E["mount"]]
root=pathlib.Path(E["project_root"]); checks["project_root"] = root.is_dir() and not root.is_symlink()
checks["template_absent"] = not pathlib.Path(E["template"]).exists()
checks["gaussdb_zero"] = subprocess.run(["pgrep","-x","gaussdb"],stdout=subprocess.PIPE,stderr=subprocess.PIPE).returncode==1
names=run("docker","ps","--format","{{.Names}}").splitlines()
checks["tpch_mysql_zero"] = not any(re.match(r"^ra_mysql_tpch04_",n) for n in names)
listeners=run("/usr/bin/netstat","-lnt")
checks["ports_free"] = not re.search(r":(?:13309|13310)\s",listeners)
pg=json.loads(run("docker","inspect","pg5_tpch_s04_20260722"))[0]
hc=pg["HostConfig"]
checks["pg_running"] = pg["State"]["Running"] is True
checks["pg_isolation"] = hc["CpusetCpus"]=="0-15,36-51" and hc["CpusetMems"]=="0"
checks["pg_disk"] = any(m.get("Source","").startswith("/mnt/nvme2n1/") for m in pg["Mounts"])
def cpus(spec):
    out=set()
    for item in spec.split(","):
        a,*b=item.split("-"); out.update(range(int(a),int(b[0])+1) if b else [int(a)])
    return out
checks["pg_cpu_disjoint"] = not (cpus(hc["CpusetCpus"]) & cpus(E["db_cpuset"]))
checks["pg_mems_disjoint"] = hc["CpusetMems"] != E["db_mems"]
for key in ("remote_lib","remote_jre","remote_config"):
    digest,count,size=tree(E[key]["path"])
    checks[key] = digest==E[key]["sha256_file_index_v1"] and count==E[key]["file_count"] and size==E[key]["bytes"]
checks["cgroup_v1_blkio"] = pathlib.Path("/sys/fs/cgroup/blkio").is_dir()
checks["sudo_noninteractive"] = subprocess.run(["sudo","-n","true"],stdout=subprocess.PIPE,stderr=subprocess.PIPE).returncode==0
failed=sorted(k for k,v in checks.items() if not v)
print(json.dumps({"status":"PASS" if not failed else "FAIL","checks":checks,"failed":failed},sort_keys=True))
sys.exit(0 if not failed else 1)
'''.replace("__EXPECTED__", repr(json.dumps(payload, separators=(",", ":"))))
    return textwrap.dedent(script).encode()


def _remote_worker_script(manifest: Mapping[str, Any], stage: str, run_id: str) -> bytes:
    # This worker is intentionally a fixed policy program.  It receives no
    # secret bytes from the controller; secrets are generated on host04.
    values = {
        "PROJECT": manifest["project_root"],
        "TEMPLATE": manifest["storage"]["template"],
        "STAGE": stage,
        "RUN_ID": run_id,
        "IMAGE": manifest["database"]["image_id"],
        "JAR_SHA": manifest["assets"]["benchbase_jar"]["sha256"],
        "XML_SHA": manifest["assets"]["config_template"]["sha256"],
        "DB_NAME": manifest["database"]["container_name"],
        "LOADER_NAME": manifest["loader"]["container_name"],
        "ROOT_SECRET": manifest["credential_contract"]["root_password_file"],
        "REMOTE_LIB": manifest["assets"]["remote_library_tree"]["path"],
        "REMOTE_JRE": manifest["assets"]["remote_jre_tree"]["path"],
        "REMOTE_CONFIG": manifest["assets"]["remote_config_tree"]["path"],
        "LIB_SHA": manifest["assets"]["remote_library_tree"]["sha256_file_index_v1"],
        "JRE_SHA": manifest["assets"]["remote_jre_tree"]["sha256_file_index_v1"],
        "CONFIG_SHA": manifest["assets"]["remote_config_tree"]["sha256_file_index_v1"],
    }
    script = r'''#!/bin/bash
set -Eeuo pipefail
umask 077
project=__PROJECT__; template=__TEMPLATE__; stage=__STAGE__; run_id=__RUN_ID__
image=__IMAGE__; jar_sha=__JAR_SHA__; xml_sha=__XML_SHA__
db_name=__DB_NAME__; loader_name=__LOADER_NAME__; root_secret=__ROOT_SECRET__
remote_lib=__REMOTE_LIB__; remote_jre=__REMOTE_JRE__
remote_config=__REMOTE_CONFIG__; lib_sha=__LIB_SHA__; jre_sha=__JRE_SHA__; config_sha=__CONFIG_SHA__
locks="$project/locks"; evidence="$project/evidence/$run_id"; work="$project/.work/$run_id"
owner="$locks/host04_mysql_lane.owner.json"; lock="$locks/host04_mysql_lane.lock"
created_root_secret=0; success=0; clean_shutdown_checkpoint=0
safe_stage_cleanup() { test "$stage" = "$project/.incoming/$run_id" && rm -rf -- "$stage"; }
safe_work_cleanup() {
  if test "$work" = "$project/.work/$run_id" && test -d "$work"; then
    docker run --rm --network none --cpuset-cpus 25-28,61-64 --cpuset-mems 1 --cpus 1 --memory 268435456 --memory-swap 268435456 --pids-limit 64 -v "$work:/cleanup:rw" --entrypoint /bin/sh "$image" -c 'chmod -R u+rwX /cleanup 2>/dev/null || true; find /cleanup -mindepth 1 -delete' >/dev/null 2>&1 || true
    rmdir -- "$work" 2>/dev/null || true
  fi
}
early_cleanup() { rc=$?; trap - EXIT HUP INT TERM; safe_stage_cleanup; exit "$rc"; }
trap early_cleanup EXIT HUP INT TERM
mkdir -p -- "$locks"
chmod 0700 "$locks"
exec 9>"$lock"
if ! flock -n 9; then echo '{"status":"FAIL","reason":"lane_lease_busy"}'; exit 73; fi
if test -f "$owner"; then
  python3 - "$owner" <<'PY' || { echo '{"status":"FAIL","reason":"invalid_stale_owner"}'; exit 74; }
import json,os,socket,sys
d=json.load(open(sys.argv[1])); assert d.get("hostname")==socket.gethostname()
p=int(d["pid"]); expected=str(d["proc_starttime"]); assert p>1 and expected.isdigit()
try: actual=open(f"/proc/{p}/stat").read().split()[21]
except FileNotFoundError: actual=None
if actual==expected: raise SystemExit(1)
PY
fi
shell_starttime=$(awk '{print $22}' "/proc/$$/stat")
python3 - "$owner.partial.$$" "$run_id" "$$" "$shell_starttime" <<'PY'
import json,os,socket,sys,time
p=sys.argv[1]; d={"hostname":socket.gethostname(),"pid":int(sys.argv[3]),"proc_starttime":sys.argv[4],"run_id":sys.argv[2],"acquired_epoch":time.time()}
open(p,"x").write(json.dumps(d,sort_keys=True)+"\n"); os.chmod(p,0o600)
PY
mv -T -- "$owner.partial.$$" "$owner"
mkdir -p -- "$evidence" "$project/.work"
chmod 0700 "$evidence" "$project/.work"
cleanup() {
  rc=$?
  trap - EXIT HUP INT TERM
  if test "$success" != 1 && test -d "$evidence"; then
    docker inspect "$db_name" >"$evidence/failure_db_inspect.json" 2>/dev/null || true
    docker inspect "$loader_name" >"$evidence/failure_loader_inspect.json" 2>/dev/null || true
    for container in "$db_name" "$loader_name"; do
      docker logs "$container" 2>&1 | python3 -c 'import functools,pathlib,sys; data=sys.stdin.buffer.read(); secrets=[p.read_bytes().strip() for p in map(pathlib.Path,sys.argv[1:]) if p.is_file()]; sys.stdout.buffer.write(functools.reduce(lambda d,s:d.replace(s,b"[REDACTED_SECRET]"),secrets,data))' "$root_secret" "$work/loader.secret" >"$evidence/failure_${container}.log" || true
    done
    (cd "$evidence" && find . -type f ! -name failure_checksums.sha256 -print0 | sort -z | xargs -0 sha256sum >failure_checksums.sha256) || true
  fi
  docker rm -f "$loader_name" >/dev/null 2>&1 || true
  docker rm -f "$db_name" >/dev/null 2>&1 || true
  if test "$success" != 1 && test "$clean_shutdown_checkpoint" != 1; then
    safe_work_cleanup
    if test "$created_root_secret" = 1; then rm -f -- "$root_secret"; fi
  fi
  safe_stage_cleanup
  if test "$success" = 1; then safe_work_cleanup; fi
  rm -f -- "$owner"
  exit "$rc"
}
trap cleanup EXIT HUP INT TERM
log() { printf '{"ts":"%s","event":"%s"}\n' "$(date -Ins)" "$1" >>"$evidence/events.jsonl"; }

# Repeat every mutable admission fact while holding the exclusive lane lease.
test "$(hostname)" = node04
! pgrep -x gaussdb >/dev/null
! docker ps --format '{{.Names}}' | grep -Eq '^ra_mysql_tpch04_'
! /usr/bin/netstat -lnt | grep -Eq ':(13309|13310)[[:space:]]'
test "$(docker image inspect "$image" --format '{{.Id}}')" = "$image"
read -r mm serial fs uuid < <(lsblk -dn -o MAJ:MIN,SERIAL,FSTYPE,UUID /dev/nvme3n1)
test "$mm|$serial|$fs|$uuid" = '259:2|S5LFNA0RA00595|xfs|bf82de40-be84-4610-8404-5a7dc7078ed9'
test "$(findmnt -rn -o SOURCE,MAJ:MIN,FSTYPE,TARGET --target /mnt/mysql_tpcc_nvme3n1)" = '/dev/nvme3n1 259:2 xfs /mnt/mysql_tpcc_nvme3n1'
test ! -e "$template"; test -d "$stage"; test ! -L "$stage"
test "$(sha256sum "$stage/benchbase-formal.jar" | awk '{print $1}')" = "$jar_sha"
test "$(sha256sum "$stage/tpcc_scale10_template.xml" | awk '{print $1}')" = "$xml_sha"
python3 - "$remote_lib" "$lib_sha" 28 12924398 "$remote_jre" "$jre_sha" 100 58429996 "$remote_config" "$config_sha" 155 275537 <<'PY'
import hashlib,pathlib,sys
for i in (1,5,9):
 root=pathlib.Path(sys.argv[i]); expected=sys.argv[i+1]; expected_count=int(sys.argv[i+2]); expected_size=int(sys.argv[i+3])
 h=hashlib.sha256(); count=0; size=0
 for p in sorted((x for x in root.rglob("*") if x.is_file()),key=lambda x:x.relative_to(root).as_posix()):
  data=p.read_bytes(); rel=p.relative_to(root).as_posix().encode(); h.update(rel+b"\0"+hashlib.sha256(data).hexdigest().encode()+b"\n"); count+=1; size+=len(data)
 assert (h.hexdigest(),count,size)==(expected,expected_count,expected_size)
PY
docker inspect pg5_tpch_s04_20260722 >"$evidence/pg_readback.json"
test "$(docker inspect pg5_tpch_s04_20260722 --format '{{.State.Running}}|{{.HostConfig.CpusetCpus}}|{{.HostConfig.CpusetMems}}')" = 'true|0-15,36-51|0'
docker inspect pg5_tpch_s04_20260722 --format '{{range .Mounts}}{{println .Source}}{{end}}' | grep -E '^/mnt/nvme2n1/' >/dev/null
log admission_pass

mkdir -p -- "$work/datadir" "$work/control" "$(dirname "$root_secret")"
chmod 0700 "$work" "$work/datadir" "$work/control" "$(dirname "$root_secret")"
if ! test -e "$root_secret"; then
  openssl rand -hex 32 >"$root_secret.partial.$$"; chmod 0600 "$root_secret.partial.$$"
  mv -T -- "$root_secret.partial.$$" "$root_secret"; created_root_secret=1
fi
test -f "$root_secret"; test ! -L "$root_secret"; test "$(stat -Lc %a "$root_secret")" = 600
openssl rand -hex 32 >"$work/loader.secret"; chmod 0600 "$work/loader.secret"
python3 - "$stage/tpcc_scale10_template.xml" "$work/loader.secret" "$work/tpcc_runtime.xml" <<'PY'
import os,sys
src,secret,out=sys.argv[1:]; value=open(secret).read().strip(); assert value and value.isalnum()
s=open(src).read(); s=s.replace("${TPCC_LOADER_USER}","tpcc_loader").replace("${TPCC_LOADER_PASSWORD}",value)
assert "${" not in s; open(out,"x").write(s); os.chmod(out,0o600)
PY

docker run -d --name "$db_name" --cpuset-cpus 21-24,57-60 --cpuset-mems 1 --cpus 4 \
  --memory 8589934592 --memory-swap 8589934592 --pids-limit 512 -p 13310:3306 \
  -e MYSQL_ROOT_PASSWORD_FILE=/run/secrets/root_password \
  -v "$root_secret:/run/secrets/root_password:ro" -v "$work/datadir:/var/lib/mysql:rw" -v "$work/control:/run/e1-control:rw" \
  --entrypoint /bin/sh "$image" -c 'while test ! -e /run/e1-control/go; do sleep 1; done; exec docker-entrypoint.sh mysqld "$@"' e1gate \
  --innodb-flush-method=O_DIRECT --innodb-flush-log-at-trx-commit=1 \
  --sync-binlog=1 --innodb-doublewrite=ON --skip-log-bin --max-connections=256 \
  --innodb-adaptive-hash-index=OFF --transaction-isolation=REPEATABLE-READ >/dev/null
cid=$(docker inspect "$db_name" --format '{{.Id}}')
cg="/sys/fs/cgroup/blkio/docker/$cid"; test -d "$cg"
printf '259:2 1205862400\n' | sudo -n tee "$cg/blkio.throttle.read_bps_device" >/dev/null
printf '259:2 1205862400\n' | sudo -n tee "$cg/blkio.throttle.write_bps_device" >/dev/null
grep -Fx '259:2 1205862400' "$cg/blkio.throttle.read_bps_device" >"$evidence/io_readback.txt"
grep -Fx '259:2 1205862400' "$cg/blkio.throttle.write_bps_device" >"$evidence/io_writeback.txt"
touch "$work/control/go"
docker inspect "$db_name" >"$evidence/db_inspect.json"
test "$(docker inspect "$db_name" --format '{{.HostConfig.CpusetCpus}}|{{.HostConfig.CpusetMems}}|{{.HostConfig.NanoCpus}}|{{.HostConfig.Memory}}|{{.HostConfig.MemorySwap}}|{{.HostConfig.PidsLimit}}')" = '21-24,57-60|1|4000000000|8589934592|8589934592|512'
docker exec "$db_name" sh -c 'umask 077; { printf "[client]\nuser=root\npassword="CHANGE_ME"\n"; } >/tmp/root.cnf'
authenticated_ready=0
for _ in $(seq 1 120); do
  # mysqladmin ping can return success while authentication is denied during
  # the image entrypoint's temporary-server phase. Require a real
  # authenticated SQL round trip so loading cannot race password setup.
  if docker exec "$db_name" mysql --defaults-extra-file=/tmp/root.cnf -N -B -e 'SELECT 1' >/dev/null 2>&1; then
    authenticated_ready=1
    break
  fi
  sleep 1
done
test "$authenticated_ready" = 1
printf "SELECT @@version,@@innodb_flush_log_at_trx_commit,@@sync_binlog,@@innodb_doublewrite,@@innodb_flush_method;\n" \
  | docker exec -i "$db_name" mysql --defaults-extra-file=/tmp/root.cnf -N -B >"$evidence/mysql_fixed_readback.tsv"
python3 - "$evidence/mysql_fixed_readback.tsv" <<'PY'
import sys
v,flush,sync,doublewrite,method=open(sys.argv[1]).read().strip().split("\t")
assert v.startswith("8.0.45") and flush=="1" and sync=="1" and doublewrite=="ON" and method=="O_DIRECT"
PY
{
  printf "CREATE DATABASE tpcc CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; CREATE USER 'tpcc_loader'@'%%' IDENTIFIED BY '"
  tr -d '\n' <"$work/loader.secret"
  printf "'; GRANT ALL ON tpcc.* TO 'tpcc_loader'@'%%'; FLUSH PRIVILEGES;\n"
} | docker exec -i "$db_name" mysql --defaults-extra-file=/tmp/root.cnf >/dev/null
log database_ready

docker run --name "$loader_name" --network host --cpuset-cpus 25-28,61-64 --cpuset-mems 1 \
  --cpus 4 --memory 4294967296 --memory-swap 4294967296 --pids-limit 512 \
  -v "$remote_jre:/jre:ro" -v "$remote_lib:/benchbase/lib:ro" \
  -v "$remote_config:/benchbase/config:ro" -w /benchbase \
  -v "$stage/benchbase-formal.jar:/benchbase/benchbase.jar:ro" \
  -v "$work/tpcc_runtime.xml:/run/tpcc.xml:ro" --entrypoint /jre/bin/java "$image" \
  -Xmx3072m -cp '/benchbase/benchbase.jar:/benchbase/lib/*' com.oltpbenchmark.DBWorkload \
  -b tpcc -c /run/tpcc.xml \
  --create=true --load=true --execute=false -d /tmp/benchbase \
  2>&1 | python3 -c 'import sys; s=open(sys.argv[1],"rb").read().strip(); sys.stdout.buffer.write(sys.stdin.buffer.read().replace(s,b"[REVOKED_EPHEMERAL_SECRET]"))' "$work/loader.secret" >"$evidence/loader.combined.log"
docker inspect "$loader_name" >"$evidence/loader_inspect.json"
test "$(docker inspect "$loader_name" --format '{{.HostConfig.CpusetCpus}}|{{.HostConfig.CpusetMems}}|{{.HostConfig.NanoCpus}}|{{.HostConfig.Memory}}|{{.HostConfig.MemorySwap}}|{{.HostConfig.PidsLimit}}')" = '25-28,61-64|1|4000000000|4294967296|4294967296|512'

printf "SELECT 'warehouse',COUNT(*) FROM tpcc.warehouse UNION ALL SELECT 'district',COUNT(*) FROM tpcc.district UNION ALL SELECT 'customer',COUNT(*) FROM tpcc.customer UNION ALL SELECT 'history',COUNT(*) FROM tpcc.history UNION ALL SELECT 'oorder',COUNT(*) FROM tpcc.oorder UNION ALL SELECT 'new_order',COUNT(*) FROM tpcc.new_order UNION ALL SELECT 'item',COUNT(*) FROM tpcc.item UNION ALL SELECT 'stock',COUNT(*) FROM tpcc.stock UNION ALL SELECT 'order_line',COUNT(*) FROM tpcc.order_line;\n" \
  | docker exec -i "$db_name" mysql --defaults-extra-file=/tmp/root.cnf -N -B >"$evidence/row_counts.tsv"
python3 - "$evidence/row_counts.tsv" "$evidence/row_counts.json" <<'PY'
import json,sys
d={k:int(v) for k,v in (line.split("\t") for line in open(sys.argv[1]) if line.strip())}
exact={"warehouse":10,"district":100,"customer":300000,"history":300000,"oorder":300000,"new_order":90000,"item":100000,"stock":1000000}
assert all(d.get(k)==v for k,v in exact.items()); assert 1500000 <= d.get("order_line",-1) <= 4500000
open(sys.argv[2],"x").write(json.dumps({"status":"PASS","counts":d},sort_keys=True)+"\n")
PY
printf "DROP USER 'tpcc_loader'@'%%'; FLUSH PRIVILEGES;\n" | docker exec -i "$db_name" mysql --defaults-extra-file=/tmp/root.cnf >/dev/null
rm -f -- "$work/tpcc_runtime.xml"
docker exec "$db_name" rm -f -- /tmp/root.cnf
docker stop --time 120 "$db_name" >/dev/null
docker logs "$db_name" >"$evidence/mysql.log" 2>&1
grep -F 'Shutdown complete' "$evidence/mysql.log" >/dev/null
python3 - "$root_secret" "$work/loader.secret" "$evidence" <<'PY'
import pathlib,sys
secrets=[open(p,"rb").read().strip() for p in sys.argv[1:3]]
assert all(secrets)
for p in pathlib.Path(sys.argv[3]).rglob("*"):
 if p.is_file():
  data=p.read_bytes()
  if any(secret in data for secret in secrets): raise ValueError("secret leaked into evidence: "+str(p))
PY
rm -f -- "$work/loader.secret"
docker rm "$db_name" >/dev/null
docker rm "$loader_name" >/dev/null
log clean_shutdown
clean_shutdown_checkpoint=1

socket_path="$work/datadir/mysql.sock"
if sudo -n test -L "$socket_path"; then
  test "$(sudo -n readlink -- "$socket_path")" = /var/run/mysqld/mysqld.sock
  sudo -n rm -- "$socket_path"
else
  ! sudo -n test -e "$socket_path"
fi
sudo -n python3 - "$work/datadir" "$evidence/row_counts.json" "$image" "$jar_sha" "$xml_sha" >"$work/template_manifest.json" <<'PY'
import hashlib,json,os,pathlib,stat,sys,time
root=pathlib.Path(sys.argv[1]); h=hashlib.sha256(); count=0; directories=0; size=0
for p in sorted(root.rglob("*"),key=lambda x:x.relative_to(root).as_posix()):
 rel_text=p.relative_to(root).as_posix(); rel=rel_text.encode(); mode=p.lstat().st_mode
 if stat.S_ISDIR(mode): h.update(b"D\0"+rel+b"\0"); directories+=1; continue
 if not stat.S_ISREG(mode): raise ValueError("non-regular datadir entry: "+rel_text)
 n=p.stat().st_size; size+=n; count+=1; h.update(b"F\0"+rel+b"\0"+str(n).encode()+b"\0")
 with p.open("rb") as f:
  for block in iter(lambda:f.read(8*1024*1024),b""): h.update(block)
counts=json.load(open(sys.argv[2]))["counts"]
d={"schema_version":1,"template_id":"mysql_tpcc_scale10_20260723_v1","status":"COMPLETE","warehouses":10,"terminals":32,"counts":counts,"datadir_sha256_tree_v1":h.hexdigest(),"datadir_directory_count":directories,"datadir_file_count":count,"datadir_bytes":size,"clean_shutdown":True,"image_id":sys.argv[3],"benchbase_jar_sha256":sys.argv[4],"config_template_sha256":sys.argv[5],"formal_r0_authorized":False,"created_epoch":time.time()}
print(json.dumps(d,sort_keys=True))
PY
mkdir "$work/publish"
# The MySQL image intentionally owns the datadir after initialization.  The
# host controller owns the exact parent but cannot rename that child on this
# XFS/SELinux host.  Use the already-admitted non-interactive root channel for
# this one path-bounded rename, then continue all content handling read-only.
sudo -n mv -- "$work/datadir" "$work/publish/datadir"
mv "$work/template_manifest.json" "$work/publish/template_manifest.json"
(cd "$work/publish" && sha256sum template_manifest.json >template_manifest.json.sha256)
# The datadir is owned by the image's mysql user.  Do all recursive type,
# permission and seal operations in one tightly limited root helper instead
# of traversing it as the unprivileged host account.
docker run --rm --network none --cpuset-cpus 25-28,61-64 --cpuset-mems 1 --cpus 1 \
  --memory 268435456 --memory-swap 268435456 --pids-limit 64 \
  -v "$work/publish:/seal:rw" --entrypoint /bin/sh "$image" -c \
  'set -eu; test -z "$(find /seal -xdev ! -type f ! -type d -print -quit)"; find /seal -xdev -type f -exec chmod 0444 {} +; find /seal -xdev -type d -exec chmod 0555 {} +; test -z "$(find /seal -xdev -perm /0222 -print -quit)"'
sudo -n mv -T -- "$work/publish" "$template"
success=1
log template_published
python3 - "$template/template_manifest.json" "$evidence/final.json" <<'PY'
import hashlib,json,sys
m=json.load(open(sys.argv[1])); out={"status":"PASS","phase":"E1_TEMPLATE_COMPLETE","template":sys.argv[1].rsplit('/',1)[0],"manifest_sha256":hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest(),"formal_r0_authorized":False}
open(sys.argv[2],"x").write(json.dumps(out,sort_keys=True)+"\n")
PY
(cd "$evidence" && find . -type f ! -name checksums.sha256 -print0 | sort -z | xargs -0 sha256sum >checksums.sha256)
cat "$evidence/final.json"
'''
    for key, value in values.items():
        script = script.replace(f"__{key}__", shlex.quote(str(value)))
    return textwrap.dedent(script).encode()


def _save_evidence(directory: Path, payload: bytes) -> Mapping[str, str]:
    if not directory.is_absolute() or directory.exists() or directory.is_symlink():
        raise TPCCE1Error("evidence directory must be a new absolute path")
    directory.mkdir(parents=True, mode=0o700)
    target = directory / "tpcc_e1_result.json"
    digest = _sha256(payload)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        if os.write(fd, payload) != len(payload):
            raise TPCCE1Error("short evidence write")
        os.fsync(fd)
    finally:
        os.close(fd)
    sidecar = directory / "tpcc_e1_result.json.sha256"
    sidecar.write_text(f"{digest}  {target.name}\n", encoding="ascii")
    os.chmod(sidecar, 0o600)
    return {"json": str(target), "sha256": digest, "sidecar": str(sidecar)}


def prepare_e1(
    *, manifest_path: Path = DEFAULT_MANIFEST, execute: bool = False,
    confirm_host: str | None = None, confirm_project: str | None = None,
    confirm_image: str | None = None, evidence_dir: Path | None = None,
    runner: Any | None = None, run_id: str | None = None,
) -> Mapping[str, Any]:
    manifest_path = manifest_path.absolute()
    manifest, manifest_bytes = _load_manifest(manifest_path)
    host = manifest["host"]
    if HOST_PATTERN.fullmatch(host) is None:
        raise TPCCE1Error("invalid frozen host")
    jar_ref = manifest["assets"]["benchbase_jar"]
    xml_ref = manifest["assets"]["config_template"]
    jar_path = ROOT / jar_ref["local_path"]
    xml_path = ROOT / xml_ref["local_path"]
    _asset(jar_path, jar_ref["sha256"])
    xml_bytes = _asset(xml_path, xml_ref["sha256"])
    if b"${TPCC_LOADER_PASSWORD}" not in xml_bytes or b"<scalefactor>10</scalefactor>" not in xml_bytes:
        raise TPCCE1Error("secret-free scale-10 XML contract mismatch")
    command_runner = runner or SubprocessRunner()
    preflight = command_runner.run(
        _ssh(host, "python3 -"), input_bytes=_remote_preflight_script(manifest)
    )
    if preflight.returncode != 0:
        raise TPCCE1Error(f"remote read-only preflight failed: {(preflight.stderr or preflight.stdout)[-1000:]}")
    try:
        preflight_json = json.loads(preflight.stdout)
    except json.JSONDecodeError as exc:
        raise TPCCE1Error("remote preflight returned invalid JSON") from exc
    if preflight_json.get("status") != "PASS":
        raise TPCCE1Error("remote preflight did not pass")
    plan: dict[str, Any] = {
        "schema_version": 1,
        "mode": "execute" if execute else "dry_run",
        "asset_id": manifest["asset_id"],
        "asset_manifest_sha256": _sha256(manifest_bytes),
        "preflight": preflight_json,
        "mutations_performed": False,
        "formal_r0_authorized": False,
        "touches_postgresql": False,
    }
    if not execute:
        return plan
    if (
        confirm_host != host
        or confirm_project != manifest["project_root"]
        or confirm_image != manifest["database"]["image_id"]
    ):
        raise TPCCE1Error("execute requires exact host/project/image confirmations")
    if evidence_dir is None:
        raise TPCCE1Error("execute requires a local evidence directory")
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"_{os.getpid()}"
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", run_id):
        raise TPCCE1Error("unsafe run ID")
    stage = f"{manifest['project_root']}/.incoming/{run_id}"
    make_stage = "set -eu; umask 077; mkdir -p " + shlex.quote(str(Path(stage).parent)) + "; mkdir " + shlex.quote(stage)
    result = command_runner.run(_ssh(host, make_stage))
    if result.returncode:
        raise TPCCE1Error("cannot create unique remote asset stage")
    for source, name in ((jar_path, "benchbase-formal.jar"), (xml_path, "tpcc_scale10_template.xml")):
        result = command_runner.run(("scp", "-q", "--", str(source), f"{host}:{stage}/{name}"))
        if result.returncode:
            cleanup = "set -eu; stage=" + shlex.quote(stage) + "; case \"$stage\" in " + shlex.quote(manifest["project_root"] + "/.incoming/") + "*) rm -rf -- \"$stage\";; *) exit 90;; esac"
            command_runner.run(_ssh(host, cleanup))
            raise TPCCE1Error(f"asset upload failed: {name}")
    worker = command_runner.run(
        _ssh(host, "bash -s"), input_bytes=_remote_worker_script(manifest, stage, run_id)
    )
    if worker.returncode != 0:
        raise TPCCE1Error(f"remote E1 worker failed rc={worker.returncode}: {(worker.stderr or worker.stdout)[-1000:]}")
    try:
        result_json = json.loads(worker.stdout)
    except json.JSONDecodeError as exc:
        raise TPCCE1Error("remote E1 worker returned invalid JSON") from exc
    if result_json.get("status") != "PASS" or result_json.get("formal_r0_authorized") is not False:
        raise TPCCE1Error("remote E1 completion contract failed")
    plan.update(result_json)
    plan["mutations_performed"] = True
    plan["local_evidence"] = _save_evidence(evidence_dir.absolute(), worker.stdout.encode())
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-host")
    parser.add_argument("--confirm-project")
    parser.add_argument("--confirm-image")
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args()
    try:
        result = prepare_e1(
            manifest_path=args.manifest, execute=args.execute,
            confirm_host=args.confirm_host, confirm_project=args.confirm_project,
            confirm_image=args.confirm_image, evidence_dir=args.evidence_dir,
        )
    except TPCCE1Error as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
