#!/usr/bin/env bash
set -euo pipefail
RUN_ROOT=${1:?run root required}
DATADIR=/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/.envelope_work/job_envelope_probe_20260722T223739Z/datadir
IMAGE=sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172
NAME=ra_mysql_job02_strict_explain_20260723
mkdir -p "$RUN_ROOT/artifacts/raw_explain" "$RUN_ROOT/readback"
exec > >(tee -a "$RUN_ROOT/controller.log") 2>&1
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker inspect "$NAME" >/dev/null 2>&1 && { echo reserved_name_exists; exit 20; } || true
test -d "$DATADIR"
stat -c '%A %a %U %G %n' "$DATADIR" > "$RUN_ROOT/readback/datadir_permissions.txt"
date -u +%FT%TZ > "$RUN_ROOT/readback/start_utc.txt"
docker inspect ra_pg5_job_node02_v1_a2 > "$RUN_ROOT/readback/pg_before.json"
pgrep -af postgres > "$RUN_ROOT/readback/postgres_before.txt" || true
docker run -d --name "$NAME" --network none --cgroupns host \
  --cpus=3 --cpuset-cpus=18-20,54-56 --cpuset-mems=1 \
  --memory=6442450944 --memory-swap=6442450944 --pids-limit=512 \
  --device-read-bps=/dev/nvme1n1:1205862400 --device-write-bps=/dev/nvme1n1:1205862400 \
  -v "$DATADIR:/var/lib/mysql:rw" "$IMAGE" \
  --skip-grant-tables --skip-networking --innodb-flush-method=O_DIRECT --skip-log-bin \
  --max-connections=32 > "$RUN_ROOT/readback/container_id.txt"
for ignored in $(seq 1 90); do
  docker exec "$NAME" mysqladmin ping --silent >/dev/null 2>&1 && break
  sleep 2
done
docker exec "$NAME" mysqladmin ping --silent
docker inspect "$NAME" > "$RUN_ROOT/readback/mysql_inspect_running.json"
docker exec "$NAME" mysql -NBe "SELECT @@skip_networking,@@innodb_flush_method,@@max_connections" \
  > "$RUN_ROOT/readback/mysql_readback.tsv"
count=0
for sql_path in "$RUN_ROOT"/artifacts/sql/*.sql; do
  base=$(basename "$sql_path" .sql)
  { printf 'EXPLAIN FORMAT=JSON '; cat "$sql_path"; } | \
    timeout 120 docker exec -i "$NAME" mysql --batch --raw --skip-column-names imdb_job \
    > "$RUN_ROOT/artifacts/raw_explain/$base.json"
  test -s "$RUN_ROOT/artifacts/raw_explain/$base.json"
  count=$((count + 1))
done
test "$count" -eq 9
printf '%s\n' "$count" > "$RUN_ROOT/readback/raw_explain_count.txt"
set +e
docker exec "$NAME" mysqladmin shutdown
shutdown_rc=$?
set -e
if test "$shutdown_rc" -ne 0 && test "$shutdown_rc" -ne 137; then
  echo "unexpected mysqladmin shutdown rc=$shutdown_rc"
  exit "$shutdown_rc"
fi
printf '%s\n' "$shutdown_rc" > "$RUN_ROOT/readback/mysqladmin_shutdown_rc.txt"
for ignored in $(seq 1 30); do
  test "$(docker inspect -f '{{.State.Running}}' "$NAME")" = false && break
  sleep 1
done
docker inspect "$NAME" > "$RUN_ROOT/readback/mysql_inspect_stopped.json"
docker logs "$NAME" > "$RUN_ROOT/artifacts/mysql.log" 2>&1 || true
docker rm "$NAME" > "$RUN_ROOT/readback/container_remove.txt"
trap - EXIT
docker inspect ra_pg5_job_node02_v1_a2 > "$RUN_ROOT/readback/pg_after.json"
pgrep -af postgres > "$RUN_ROOT/readback/postgres_after.txt" || true
date -u +%FT%TZ > "$RUN_ROOT/readback/end_utc.txt"
sha256sum "$RUN_ROOT"/artifacts/raw_explain/*.json > "$RUN_ROOT/raw_explain.sha256"
echo JOB_RAW_EXPLAIN_COMPLETE
