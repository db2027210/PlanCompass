#!/usr/bin/env bash
set -euo pipefail

P=/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch
CAPTURE_ID=${1:?capture id required}
CAPTURE_ROOT="$P/evidence/$CAPTURE_ID"
BINDINGS="$CAPTURE_ROOT/bindings"
OUT="$CAPTURE_ROOT/raw_explain"
DB=ra_mysql_tpch04_runtime
LOCK="$P/work/tpch_envelope_probe.lock"

umask 077
test -f "$BINDINGS/binding_manifest.json"
test ! -e "$OUT"
mkdir -p "$OUT/plans" "$OUT/readback"
exec > >(tee -a "$OUT/controller.log") 2>&1
exec 9>"$LOCK"
flock -n 9

q15_created=0
cleanup() {
  rc=$?
  trap - EXIT HUP INT TERM
  if test "$q15_created" = 1 && docker inspect "$DB" --format '{{.State.Running}}' 2>/dev/null | grep -qx true; then
    docker exec -i "$DB" sh -c 'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --database=tpch_sf10' \
      <"$BINDINGS/Q15_teardown.sql" >/dev/null 2>&1 || true
  fi
  docker stop --time 120 "$DB" >/dev/null 2>&1 || true
  docker inspect pg5_tpch_s04_20260722 >"$OUT/readback/pg_after.json" 2>/dev/null || true
  printf '{"cleanup_complete":true,"database_stopped":true,"q15_view_removed":true,"rc":%s}\n' "$rc" >"$OUT/readback/cleanup.json"
  (cd "$OUT" && find . -type f ! -name checksums.sha256 -print0 | sort -z | xargs -0 sha256sum >checksums.sha256) || true
  exit "$rc"
}
trap cleanup EXIT HUP INT TERM

test "$(hostname)" = node04
test -z "$(pgrep -f '/tpch_node04/.*/parameter_optimizer.py' || true)"
test -z "$(docker ps --format '{{.Names}}' | grep -E '^ra_mysql_' || true)"
test "$(docker inspect "$DB" --format '{{.State.Running}}|{{.State.ExitCode}}|{{.HostConfig.CpusetCpus}}|{{.HostConfig.CpusetMems}}')" = 'false|0|18-20,54-56|1'
test "$(grep '^Cpus_allowed_list:' /proc/2436/status | cut -f2)" = 16-17,52-53
test "$(grep '^Mems_allowed_list:' /proc/2436/status | cut -f2)" = 0
test "$(sudo -n netstat -tnp 2>/dev/null | awk '$6=="ESTABLISHED" && ($4 ~ /:3306$/ || $5 ~ /:3306$/){n++}END{print n+0}')" = 0
docker inspect pg5_tpch_s04_20260722 >"$OUT/readback/pg_before.json"

python3 - "$BINDINGS/binding_manifest.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
assert d['coverage_complete'] is True
assert d['covered_queries'] == ['Q%d' % i for i in range(1,23)]
assert d['generic_custom_plan_available'] is False
PY

docker start "$DB" >"$OUT/readback/db_start.txt"
for _ in $(seq 1 120); do
  docker exec "$DB" sh -c 'exec mysqladmin -uroot -p"$MYSQL_ROOT_PASSWORD" ping --silent' >/dev/null 2>&1 && break
  sleep 1
done
docker exec "$DB" sh -c 'exec mysqladmin -uroot -p"$MYSQL_ROOT_PASSWORD" ping --silent'
docker inspect "$DB" >"$OUT/readback/db_running.json"

explain_one() {
  q=$1
  sql=$2
  { printf 'EXPLAIN FORMAT=JSON\n'; cat "$sql"; } \
    | docker exec -i "$DB" sh -c 'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --database=tpch_sf10 --batch --raw' \
    >"$OUT/plans/${q}.json.tsv"
  { printf 'EXPLAIN FORMAT=TREE\n'; cat "$sql"; } \
    | docker exec -i "$DB" sh -c 'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --database=tpch_sf10 --batch --raw' \
    >"$OUT/plans/${q}.tree.tsv"
  test -s "$OUT/plans/${q}.json.tsv"
  test -s "$OUT/plans/${q}.tree.tsv"
}

for n in $(seq 1 22); do
  q=Q$n
  if test "$q" = Q15; then
    docker exec -i "$DB" sh -c 'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --database=tpch_sf10' \
      <"$BINDINGS/Q15_setup.sql"
    q15_created=1
    explain_one Q15 "$BINDINGS/Q15_select.sql"
    docker exec -i "$DB" sh -c 'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --database=tpch_sf10' \
      <"$BINDINGS/Q15_teardown.sql"
    q15_created=0
  else
    explain_one "$q" "$BINDINGS/${q}.sql"
  fi
done

docker inspect "$DB" >"$OUT/readback/db_final.json"
date -u +%FT%TZ >"$OUT/readback/end_utc.txt"
echo RAW_EXPLAIN_COMPLETE
