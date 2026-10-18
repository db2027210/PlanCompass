#!/usr/bin/env bash
set -euo pipefail

RUN_ROOT=${1:?run root required}
IMAGE=sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172
DB_NAME=ra_ap_prod_explain_4508333_db
NETWORK=ra_ap_prod_explain_4508333_net
mkdir -p "$RUN_ROOT/artifacts/raw_explain" "$RUN_ROOT/readback"
exec > >(tee -a "$RUN_ROOT/explain_controller.log") 2>&1

cleanup() {
  docker rm -f "$DB_NAME" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker inspect "$DB_NAME" >/dev/null 2>&1 && { echo "reserved explain DB name exists"; exit 20; } || true
docker network inspect "$NETWORK" >/dev/null 2>&1 && { echo "reserved explain network exists"; exit 21; } || true

date -u +%FT%TZ > "$RUN_ROOT/readback/explain_start_utc.txt"
pgrep -af postgres > "$RUN_ROOT/readback/explain_postgres_before.txt" || true
pgrep -af mysqld > "$RUN_ROOT/readback/explain_mysqld_before.txt" || true
docker network create "$NETWORK" > "$RUN_ROOT/readback/explain_network_create.txt"
docker run -d --name "$DB_NAME" --network "$NETWORK" \
  --cpus=3 --cpuset-cpus=24-26,60-62 --cpuset-mems=1 \
  --memory=6g --memory-swap=6g --pids-limit=512 \
  --device-read-bps=/dev/nvme0n1:1205862400 --device-write-bps=/dev/nvme0n1:1205862400 \
  -v "$RUN_ROOT/datadir:/var/lib/mysql:rw" \
  -v /tmp/mysql_template.cnf:/etc/mysql/conf.d/template.cnf:ro \
  -e MYSQL_ALLOW_EMPTY_PASSWORD=yes "$IMAGE" \
  mysqld --read-only=ON --super-read-only=ON > "$RUN_ROOT/readback/explain_db_container_id.txt"
for ignored in $(seq 1 90); do
  docker exec "$DB_NAME" mysqladmin ping --silent >/dev/null 2>&1 && break
  sleep 2
done
docker exec "$DB_NAME" mysqladmin ping --silent
docker inspect "$DB_NAME" > "$RUN_ROOT/readback/explain_db_inspect_running.json"
docker exec "$DB_NAME" mysql -NBe "SELECT @@read_only,@@super_read_only" \
  > "$RUN_ROOT/readback/explain_mysql_gate.tsv"

count=0
for sql_path in "$RUN_ROOT"/artifacts/explain_sql/*.sql; do
  base=$(basename "$sql_path" .sql)
  { printf 'EXPLAIN FORMAT=JSON '; cat "$sql_path"; } | \
    timeout 120 docker exec -i "$DB_NAME" mysql --batch --raw --skip-column-names hybench_ap \
    > "$RUN_ROOT/artifacts/raw_explain/$base.json"
  test -s "$RUN_ROOT/artifacts/raw_explain/$base.json"
  count=$((count + 1))
done
test "$count" -eq 56
printf '%s\n' "$count" > "$RUN_ROOT/readback/raw_explain_count.txt"
docker logs "$DB_NAME" > "$RUN_ROOT/artifacts/explain_db.log" 2>&1 || true
docker inspect "$DB_NAME" > "$RUN_ROOT/readback/explain_db_inspect_final.json"
pgrep -af postgres > "$RUN_ROOT/readback/explain_postgres_after.txt" || true
pgrep -af mysqld > "$RUN_ROOT/readback/explain_mysqld_after.txt" || true
date -u +%FT%TZ > "$RUN_ROOT/readback/explain_end_utc.txt"
cleanup
trap - EXIT
sha256sum "$RUN_ROOT"/artifacts/raw_explain/*.json > "$RUN_ROOT/raw_explain.sha256"
echo RAW_EXPLAIN_COMPLETE
