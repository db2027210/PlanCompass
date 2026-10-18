#!/usr/bin/env bash
set -euo pipefail

P=/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch
RUN_ID=${1:?run id required}
RUN_ROOT="$P/evidence/$RUN_ID"
DB=ra_mysql_tpch04_runtime
CLIENT="ra_${RUN_ID}_client"
NET="ra_${RUN_ID}_net"
JAR="$P/code/benchbase-mysql/benchbase.jar"
AGENT="$P/code/formal_clients_20260721/tpch-seed-agent.jar"
JRE="$P/code/ra-jre23-benchbase-instrument"
JRE_ARCHIVE="$P/code/ra-jre23-benchbase-instrument.tar.gz"
IMAGE=sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172
LOCK="$P/work/tpch_envelope_probe.lock"

umask 077
test ! -e "$RUN_ROOT"
mkdir -p "$RUN_ROOT/artifacts" "$RUN_ROOT/readback" "$RUN_ROOT/results"
exec > >(tee -a "$RUN_ROOT/controller.log") 2>&1
exec 9>"$LOCK"
flock -n 9
cleanup() {
  rc=$?
  trap - EXIT HUP INT TERM
  docker rm -f "$CLIENT" >/dev/null 2>&1 || true
  if docker inspect "$DB" >/dev/null 2>&1; then
    docker exec "$DB" sh -c 'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -NBe "SET GLOBAL general_log=OFF"' >/dev/null 2>&1 || true
    docker stop --time 120 "$DB" >/dev/null 2>&1 || true
    docker network disconnect "$NET" "$DB" >/dev/null 2>&1 || true
  fi
  docker network rm "$NET" >/dev/null 2>&1 || true
  printf '{"cleanup_complete":true,"database_stopped":true,"client_absent":true,"network_absent":true,"rc":%s}\n' "$rc" >"$RUN_ROOT/readback/cleanup.json"
  (cd "$RUN_ROOT" && find . -type f ! -name checksums.sha256 -print0 | sort -z | xargs -0 sha256sum > checksums.sha256) || true
  exit "$rc"
}
trap cleanup EXIT HUP INT TERM

date -u +%FT%TZ >"$RUN_ROOT/readback/start_utc.txt"
test "$(hostname)" = node04
test -z "$(pgrep -f '/tpch_node04/.*/parameter_optimizer.py' || true)"
test -z "$(docker ps --format '{{.Names}}' | grep -E '^ra_mysql_' || true)"
test "$(docker inspect "$DB" --format '{{.State.Running}}|{{.State.ExitCode}}|{{.HostConfig.CpusetCpus}}|{{.HostConfig.CpusetMems}}')" = 'false|0|18-20,54-56|1'
test "$(sha256sum "$JAR" | cut -d' ' -f1)" = 0acf80b463321f52323dc75f10643f17994046d0d20fd3a5faa1f3c94d38a032
test "$(sha256sum "$AGENT" | cut -d' ' -f1)" = 5f48249ebc47a50b87d878985e5cb1fee9b72a46b6ce4f87571f858312687ec4
test "$(sha256sum "$JRE_ARCHIVE" | cut -d' ' -f1)" = 07657b9abedb3a7e5256dfddaa11f9b2be10f8bc3e8d08872832dd61004b396c
"$JRE/bin/java" --list-modules | grep -q '^java.instrument@23\.0\.2$'
test "$(grep '^Cpus_allowed_list:' /proc/2436/status | cut -f2)" = 16-17,52-53
test "$(grep '^Mems_allowed_list:' /proc/2436/status | cut -f2)" = 0
test "$(sudo -n netstat -tnp 2>/dev/null | awk '$6=="ESTABLISHED" && ($4 ~ /:3306$/ || $5 ~ /:3306$/){n++}END{print n+0}')" = 0
docker inspect pg5_tpch_s04_20260722 >"$RUN_ROOT/readback/pg_before.json"

# Reuse the frozen work-copy container credential without printing it in logs.
ROOT_PASSWORD_FILE="$RUN_ROOT/.root_password"
MYSQL_DEFAULTS="$RUN_ROOT/.mysql.cnf"
docker inspect "$DB" --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | sed -n 's/^MYSQL_ROOT_PASSWORD=//p' >"$ROOT_PASSWORD_FILE"
test -s "$ROOT_PASSWORD_FILE"
{
  printf '[client]\nuser=root\npassword='
  cat "$ROOT_PASSWORD_FILE"
  printf '\n'
} >"$MYSQL_DEFAULTS"
chmod 600 "$ROOT_PASSWORD_FILE" "$MYSQL_DEFAULTS"

python3 - "$P/code/benchbase-mysql/config/mysql/sample_tpch_config.xml" "$RUN_ROOT/tpch_capture.xml" "$ROOT_PASSWORD_FILE" <<'PY'
import sys,xml.etree.ElementTree as E
src,dst,password_file=sys.argv[1:]; r=E.parse(src).getroot()
password=open(password_file).read().rstrip('\n')
def setv(tag,value):
 x=r.find(tag)
 if x is None:x=E.SubElement(r,tag)
 x.text=str(value)
setv('url','jdbc:mysql://production-db:3306/tpch_sf10?useUnicode=true&characterEncoding=utf-8&useSSL=false&allowPublicKeyRetrieval=true&useServerPrepStmts=true&cachePrepStmts=true&useCursorFetch=true&defaultFetchSize=1000')
setv('username','root');setv('password',password);setv('isolation','TRANSACTION_READ_COMMITTED');setv('scalefactor','10');setv('terminals','2');setv('randomSeed','4508333')
works=r.find('works'); first=works.findall('work')[0]
for x in works.findall('work')[1:]:works.remove(x)
for tag,val in [('serial','false'),('rate','unlimited'),('time','900'),('weights','4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,16,4,4,4,4')]:
 x=first.find(tag)
 if x is None:x=E.SubElement(first,tag)
 x.text=val
if hasattr(E, 'indent'):
 E.indent(r)
E.ElementTree(r).write(dst,encoding='utf-8',xml_declaration=True)
PY
sha256sum "$JAR" "$AGENT" "$JRE_ARCHIVE" "$RUN_ROOT/tpch_capture.xml" >"$RUN_ROOT/readback/runtime_inputs.sha256"

docker network create --internal "$NET" >"$RUN_ROOT/readback/network_create.txt"
docker network connect --alias production-db "$NET" "$DB"
docker start "$DB" >"$RUN_ROOT/readback/db_start.txt"
for _ in $(seq 1 120); do docker exec "$DB" sh -c 'exec mysqladmin -uroot -p"$MYSQL_ROOT_PASSWORD" ping --silent' >/dev/null 2>&1 && break; sleep 1; done
docker exec "$DB" sh -c 'exec mysqladmin -uroot -p"$MYSQL_ROOT_PASSWORD" ping --silent'
LOG="/var/lib/mysql/${RUN_ID}_general.log"
docker exec "$DB" sh -c 'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -NBe "$1"' sh "SET GLOBAL general_log=OFF; SET GLOBAL log_output='FILE'; SET GLOBAL general_log_file='$LOG'; SET GLOBAL general_log=ON"
docker exec "$DB" sh -c 'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -NBe "$1"' sh 'SELECT @@general_log,@@log_output,@@general_log_file,COUNT(*) FROM information_schema.tables WHERE table_schema="tpch_sf10"' >"$RUN_ROOT/readback/mysql_gate.tsv"
docker inspect "$DB" >"$RUN_ROOT/readback/db_running.json"

docker create --name "$CLIENT" --network "$NET" --cpus=4 --cpuset-cpus=25-28,61-64 --cpuset-mems=1 --memory=2g --memory-swap=2g --pids-limit=256 \
  -v "$JRE:/jre:ro" -v "$P/code/benchbase-mysql:/benchbase:ro" -v "$AGENT:/work/tpch-seed-agent.jar:ro" \
  -v "$RUN_ROOT/tpch_capture.xml:/work/tpch_capture.xml:ro" -v "$RUN_ROOT/results:/out:rw" -w /benchbase --entrypoint /jre/bin/java "$IMAGE" \
  -javaagent:/work/tpch-seed-agent.jar -jar /benchbase/benchbase.jar -b tpch -c /work/tpch_capture.xml --create=false --load=false --execute=true -d /out \
  >"$RUN_ROOT/readback/client_create.txt"
set +e
timeout 1100 docker start -a "$CLIENT" >"$RUN_ROOT/artifacts/client.log" 2>&1
client_rc=$?
set -e
echo "$client_rc" >"$RUN_ROOT/readback/client_exit_code.txt"
docker inspect "$CLIENT" >"$RUN_ROOT/readback/client_final.json"
docker exec "$DB" sh -c 'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -NBe "SET GLOBAL general_log=OFF"'
docker cp "$DB:$LOG" "$RUN_ROOT/artifacts/production_general.log"
docker inspect "$DB" >"$RUN_ROOT/readback/db_final.json"
docker inspect pg5_tpch_s04_20260722 >"$RUN_ROOT/readback/pg_after.json"
date -u +%FT%TZ >"$RUN_ROOT/readback/end_utc.txt"
test "$client_rc" = 0
echo CAPTURE_COMPLETE
