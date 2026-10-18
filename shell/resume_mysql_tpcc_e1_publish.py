#!/usr/bin/env python3
"""Resume only the sealed-publication phase of the retained TPC-C E1 retry6."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
HOST = "anonymous@192.0.2.7"
PROJECT = "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc"
IMAGE = "sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
DEFAULT_RUN_ID = "20260722T220924Z_1489393"
RUN_ID_RE = re.compile(r"[A-Za-z0-9_.-]+\Z")


def ssh_script(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "--", HOST,
         "bash", "-s", "--", *args],
        cwd=ROOT, input=script, text=True, capture_output=True, check=False,
    )


def dry_run_script() -> str:
    return r'''set -euo pipefail
project="$1"; run_id="$2"; image="$3"
work="$project/.work/$run_id"; publish="$work/publish"; evidence="$project/evidence/$run_id"; template="$project/template"
test ! -e "$template" && test ! -L "$template"
test -d "$publish/datadir" && test -f "$publish/template_manifest.json"
grep -q '"event":"clean_shutdown"' "$evidence/events.jsonl"
python3 - "$evidence/row_counts.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); c=d.get('counts',{})
exact={'warehouse':10,'district':100,'customer':300000,'history':300000,'oorder':300000,'new_order':90000,'item':100000,'stock':1000000}
assert d.get('status')=='PASS' and all(c.get(k)==v for k,v in exact.items()) and 1500000<=c.get('order_line',-1)<=4500000
PY
test "$(docker image inspect "$image" --format '{{.Id}}')" = "$image"
test "$(docker inspect pg5_tpch_s04_20260722 --format '{{.State.Running}}|{{.HostConfig.CpusetCpus}}|{{.HostConfig.CpusetMems}}')" = 'true|0-15,36-51|0'
docker inspect pg5_tpch_s04_20260722 --format '{{range .Mounts}}{{println .Source}}{{end}}' | grep -q '^/mnt/nvme2n1/'
! pgrep -x gaussdb >/dev/null
! docker ps --format '{{.Names}}' | grep -Eq '^ra_mysql_tpch04_'
! docker inspect ra_mysql_tpcc04_e1_template >/dev/null 2>&1
! docker inspect ra_mysql_tpcc04_e1_loader >/dev/null 2>&1
printf '{"status":"DRY_RUN_RESUME_PUBLISH_OK","mutations":false,"run_id":"%s","clean_shutdown_checkpoint":true,"row_counts_checkpoint":true,"template_absent":true,"pg_untouched":true}\n' "$run_id"
'''


def execute_script() -> str:
    return r'''set -Eeuo pipefail
umask 077
project="$1"; run_id="$2"; image="$3"
work="$project/.work/$run_id"; publish="$work/publish"; evidence="$project/evidence/$run_id"; template="$project/template"
lock="$project/locks/host04_mysql_lane.lock"; owner="$project/locks/host04_mysql_lane.owner.json"; success=0
cleanup() {
  rc=$?; trap - EXIT HUP INT TERM
  printf '{"event":"resume_publish_lock_released","at":"%s","rc":%s}\n' "$(date --iso-8601=seconds)" "$rc" >> "$evidence/resume_publish_events.jsonl" || true
  rm -f -- "$owner"
  flock -u 9 || true
  if test "$success" = 1; then
    (cd "$evidence" && find . -type f ! -name checksums.sha256 -print0 | sort -z | xargs -0 sha256sum > checksums.sha256) || rc=1
  fi
  exit "$rc"
}
trap cleanup EXIT HUP INT TERM
exec 9>"$lock"; flock -n 9
test ! -e "$owner" && test ! -L "$owner"
printf '{"hostname":"node04","pid":%s,"run_id":"%s","phase":"resume_publish"}\n' "$$" "$run_id" > "$owner.partial.$$"
chmod 0600 "$owner.partial.$$"; mv -T -- "$owner.partial.$$" "$owner"
printf '{"event":"resume_publish_lock_acquired","at":"%s"}\n' "$(date --iso-8601=seconds)" >> "$evidence/resume_publish_events.jsonl"

# Revalidate the retained checkpoint and host admission while under the lease.
test ! -e "$template" && test ! -L "$template"
test -d "$publish/datadir" && test ! -L "$publish/datadir"
test -f "$publish/template_manifest.json" && test ! -L "$publish/template_manifest.json"
grep -q '"event":"clean_shutdown"' "$evidence/events.jsonl"
python3 - "$evidence/row_counts.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); c=d.get('counts',{})
exact={'warehouse':10,'district':100,'customer':300000,'history':300000,'oorder':300000,'new_order':90000,'item':100000,'stock':1000000}
assert d.get('status')=='PASS' and all(c.get(k)==v for k,v in exact.items()) and 1500000<=c.get('order_line',-1)<=4500000
PY
! pgrep -x gaussdb >/dev/null
! docker ps --format '{{.Names}}' | grep -Eq '^ra_mysql_tpch04_'
! /usr/bin/netstat -lnt | grep -Eq ':(13309|13310)[[:space:]]'
! docker inspect ra_mysql_tpcc04_e1_template >/dev/null 2>&1
! docker inspect ra_mysql_tpcc04_e1_loader >/dev/null 2>&1
test "$(docker image inspect "$image" --format '{{.Id}}')" = "$image"
docker inspect pg5_tpch_s04_20260722 > "$evidence/pg_readback_after_resume_publish.json"
python3 - "$evidence/pg_readback.json" "$evidence/pg_readback_after_resume_publish.json" <<'PY'
import json,sys
a=json.load(open(sys.argv[1]))[0]; b=json.load(open(sys.argv[2]))[0]
assert a['Id']==b['Id'] and b['State']['Running'] is True
for key in ('CpusetCpus','CpusetMems','NanoCpus','Memory','MemorySwap','PidsLimit'):
 assert a['HostConfig'][key]==b['HostConfig'][key]
assert b['HostConfig']['CpusetCpus']=='0-15,36-51' and b['HostConfig']['CpusetMems']=='0'
assert any(m.get('Source','').startswith('/mnt/nvme2n1/') for m in b['Mounts'])
PY

# Preserve the invalid zero-tree manifest as retry6 failure evidence.
if test ! -e "$evidence/retry6_invalid_zero_tree_manifest.json"; then
  cp -- "$publish/template_manifest.json" "$evidence/retry6_invalid_zero_tree_manifest.json"
  cp -- "$publish/template_manifest.json.sha256" "$evidence/retry6_invalid_zero_tree_manifest.json.sha256.original"
fi
python3 - "$evidence/retry6_invalid_zero_tree_manifest.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); assert d['datadir_bytes']==0 and d['datadir_file_count']==0 and d['datadir_directory_count']==0
PY

# Remove only MySQL's known shutdown socket symlink from this retained copy.
socket="$publish/datadir/mysql.sock"
if sudo -n test -L "$socket"; then
  test "$(sudo -n readlink -- "$socket")" = /var/run/mysqld/mysqld.sock
  sudo -n rm -- "$socket"
else
  ! sudo -n test -e "$socket"
fi

# Recompute the authoritative tree as root.  A resumed checkpoint may already
# be sealed 0555/0444, so root must also create and atomically install both
# manifest files inside the publish directory.
sudo -n python3 - "$publish/datadir" "$evidence/row_counts.json" "$image" "$publish/template_manifest.json.corrected" <<'PY'
import hashlib,json,pathlib,stat,sys,time
root=pathlib.Path(sys.argv[1]); h=hashlib.sha256(); count=directories=size=0
for p in sorted(root.rglob('*'),key=lambda x:x.relative_to(root).as_posix()):
 rel_text=p.relative_to(root).as_posix(); rel=rel_text.encode(); mode=p.lstat().st_mode
 if stat.S_ISDIR(mode): h.update(b'D\0'+rel+b'\0'); directories+=1; continue
 if not stat.S_ISREG(mode): raise ValueError('non-regular datadir entry: '+rel_text)
 n=p.stat().st_size; size+=n; count+=1; h.update(b'F\0'+rel+b'\0'+str(n).encode()+b'\0')
 with p.open('rb') as f:
  for block in iter(lambda:f.read(8*1024*1024),b''): h.update(block)
counts=json.load(open(sys.argv[2]))['counts']
d={'schema_version':1,'template_id':'mysql_tpcc_scale10_20260723_v1','status':'COMPLETE','warehouses':10,'terminals':32,'counts':counts,'datadir_sha256_tree_v1':h.hexdigest(),'datadir_directory_count':directories,'datadir_file_count':count,'datadir_bytes':size,'clean_shutdown':True,'image_id':sys.argv[3],'benchbase_jar_sha256':'a1e587715621705c69488f955d1c14445e62dd8f5f34ab95b8682256f8cddff1','config_template_sha256':'bbb9319b74a85d6dbb719e02f4c734c19790b47fe3fafe5ae11da74194fedbdf','formal_r0_authorized':False,'created_epoch':time.time(),'resumed_from_run_id':'20260722T220924Z_1489393'}
assert count>0 and directories>0 and size>0
pathlib.Path(sys.argv[4]).write_text(json.dumps(d,sort_keys=True)+'\n')
PY
sudo -n mv -T -- "$publish/template_manifest.json.corrected" "$publish/template_manifest.json"
sudo -n python3 - "$publish/template_manifest.json" "$publish/template_manifest.json.sha256" <<'PY'
import hashlib,pathlib,sys
p=pathlib.Path(sys.argv[1]); pathlib.Path(sys.argv[2]).write_text(hashlib.sha256(p.read_bytes()).hexdigest()+'  template_manifest.json\n')
PY

docker run --rm --network none --cpuset-cpus 25-28,61-64 --cpuset-mems 1 --cpus 1 \
  --memory 268435456 --memory-swap 268435456 --pids-limit 64 \
  -v "$publish:/seal:rw" --entrypoint /bin/sh "$image" -c \
  'set -eu; test -z "$(find /seal -xdev ! -type f ! -type d -print -quit)"; find /seal -xdev -type f -exec chmod 0444 {} +; find /seal -xdev -type d -exec chmod 0555 {} +; test -z "$(find /seal -xdev -perm /0222 -print -quit)"'

# Rehash after sealing and require an exact match before publication.
sudo -n python3 - "$publish/datadir" > "$evidence/datadir_tree_after_seal.json" <<'PY'
import hashlib,json,pathlib,stat,sys
root=pathlib.Path(sys.argv[1]); h=hashlib.sha256(); count=directories=size=0
for p in sorted(root.rglob('*'),key=lambda x:x.relative_to(root).as_posix()):
 rel_text=p.relative_to(root).as_posix(); rel=rel_text.encode(); mode=p.lstat().st_mode
 if stat.S_ISDIR(mode): h.update(b'D\0'+rel+b'\0'); directories+=1; continue
 if not stat.S_ISREG(mode): raise ValueError('non-regular datadir entry: '+rel_text)
 n=p.stat().st_size; size+=n; count+=1; h.update(b'F\0'+rel+b'\0'+str(n).encode()+b'\0')
 with p.open('rb') as f:
  for block in iter(lambda:f.read(8*1024*1024),b''): h.update(block)
print(json.dumps({'datadir_sha256_tree_v1':h.hexdigest(),'datadir_directory_count':directories,'datadir_file_count':count,'datadir_bytes':size},sort_keys=True))
PY
python3 - "$publish/template_manifest.json" "$evidence/datadir_tree_after_seal.json" <<'PY'
import json,sys
m=json.load(open(sys.argv[1])); h=json.load(open(sys.argv[2])); assert all(m[k]==v for k,v in h.items())
PY
sudo -n mv -T -- "$publish" "$template"
printf '{"status":"PASS","phase":"E1_TEMPLATE_COMPLETE","template":"%s","manifest_sha256":"%s","formal_r0_authorized":false,"resumed_from_run_id":"%s"}\n' "$template" "$(sha256sum "$template/template_manifest.json" | awk '{print $1}')" "$run_id" > "$evidence/final.json"
printf '{"event":"resume_publish_complete","at":"%s"}\n' "$(date --iso-8601=seconds)" >> "$evidence/resume_publish_events.jsonl"
success=1
cat "$evidence/final.json"
'''


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--confirm-host")
    parser.add_argument("--confirm-project")
    args = parser.parse_args(argv)
    if RUN_ID_RE.fullmatch(args.run_id) is None:
        print("ERROR: unsafe run id", file=sys.stderr); return 2
    if args.execute and (args.confirm_host != HOST or args.confirm_project != PROJECT):
        print("ERROR: execute requires exact host/project confirmations", file=sys.stderr); return 2
    result = ssh_script(execute_script() if args.execute else dry_run_script(), PROJECT, args.run_id, IMAGE)
    if result.stdout: print(result.stdout, end="")
    if result.stderr: print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0: return result.returncode
    if args.execute:
        local = ROOT / "history/mysql_sgd_env_agents_20260723/tpcc" / f"tpcc_e1_resume_publish_{args.run_id}"
        if local.exists() or local.is_symlink():
            print("ERROR: local evidence destination exists", file=sys.stderr); return 3
        local.mkdir(parents=True, mode=0o700)
        copied = subprocess.run(
            ["rsync", "-a", "--", f"{HOST}:{PROJECT}/evidence/{args.run_id}/", str(local)+"/"],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        if copied.returncode:
            print("ERROR: remote publish passed but evidence copy failed", file=sys.stderr); return 4
        print(f"LOCAL_EVIDENCE={local.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
