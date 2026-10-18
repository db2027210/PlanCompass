#!/usr/bin/env bash
set -euo pipefail

RUN_ROOT=${1:?run root required}
SOURCE_ROOT=/mnt/mysql_nvme0n1/anonymous/resource_adaptive_mysql/hybench_ap/candidate_binding_trace_20260723_0503
IMAGE=sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172
RUN_ID=ap_production_binding_4508333_20260723
DB_NAME=ra_ap_prod_bind_4508333_db
CLIENT_NAME=ra_ap_prod_bind_4508333_client
NETWORK=ra_ap_prod_bind_4508333_net

mkdir -p "$RUN_ROOT/artifacts" "$RUN_ROOT/readback" "$RUN_ROOT/datadir"
exec > >(tee -a "$RUN_ROOT/controller.log") 2>&1

cleanup() {
  docker rm -f "$CLIENT_NAME" >/dev/null 2>&1 || true
  docker rm -f "$DB_NAME" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker inspect "$CLIENT_NAME" >/dev/null 2>&1 && { echo "reserved client name exists"; exit 20; } || true
docker inspect "$DB_NAME" >/dev/null 2>&1 && { echo "reserved db name exists"; exit 21; } || true
docker network inspect "$NETWORK" >/dev/null 2>&1 && { echo "reserved network exists"; exit 22; } || true

date -u +%FT%TZ > "$RUN_ROOT/readback/start_utc.txt"
hostname > "$RUN_ROOT/readback/hostname.txt"
pgrep -af postgres > "$RUN_ROOT/readback/postgres_before.txt" || true
pgrep -af mysqld > "$RUN_ROOT/readback/mysqld_before.txt" || true
docker ps --no-trunc > "$RUN_ROOT/readback/docker_before.txt"
cat /proc/2809/cgroup > "$RUN_ROOT/readback/native_mysqld_cgroup.txt" 2>/dev/null || true
grep -E 'Cpus_allowed_list|Mems_allowed_list' /proc/2809/status > "$RUN_ROOT/readback/native_mysqld_affinity.txt" 2>/dev/null || true

sha256sum "$RUN_ROOT/production.jar" "$RUN_ROOT/mysql-connector-java-8.0.30.jar" \
  "$RUN_ROOT/production.props" "$RUN_ROOT/stmt_mysql.toml" > "$RUN_ROOT/readback/runtime_inputs.sha256"
test "$(sha256sum "$RUN_ROOT/production.jar" | awk '{print $1}')" = \
  6e615d1b704da0b095266fd5592fc72245a9c5034707a37ef91abfa65a7fc7f4
grep -qx 'random_seed=4508333' "$RUN_ROOT/production.props"
grep -qx 'apclient=4' "$RUN_ROOT/production.props"
grep -qx 'ap_query_sequence=1,2,3,4,5,6,7,8,9,10,11,12,13' "$RUN_ROOT/production.props"
grep -qx 'ap_query_sequence_limit=13' "$RUN_ROOT/production.props"

docker run --rm --entrypoint sh \
  -v "$SOURCE_ROOT/datadir:/src:ro" -v "$RUN_ROOT/datadir:/dst:rw" "$IMAGE" \
  -c 'cp -a /src/. /dst/'

docker network create "$NETWORK" > "$RUN_ROOT/readback/network_create.txt"
docker run -d --name "$DB_NAME" --network "$NETWORK" --network-alias production-db \
  --cpus=3 --cpuset-cpus=24-26,60-62 --cpuset-mems=1 \
  --memory=6g --memory-swap=6g --pids-limit=512 \
  --device-read-bps=/dev/nvme0n1:1205862400 --device-write-bps=/dev/nvme0n1:1205862400 \
  -p 127.0.0.1::3306 \
  -v "$RUN_ROOT/datadir:/var/lib/mysql:rw" \
  -v /tmp/mysql_template.cnf:/etc/mysql/conf.d/template.cnf:ro \
  -v /mnt/mysql_nvme0n1/anonymous/resource_adaptive_mysql/hybench_ap/data:/csv:ro \
  -e MYSQL_ALLOW_EMPTY_PASSWORD=yes "$IMAGE" \
  mysqld --read-only=ON --super-read-only=ON --general-log=ON --log-output=FILE \
  --general-log-file=/var/lib/mysql/production_general.log > "$RUN_ROOT/readback/db_container_id.txt"

for ignored in $(seq 1 90); do
  docker exec "$DB_NAME" mysqladmin ping --silent >/dev/null 2>&1 && break
  sleep 2
done
docker exec "$DB_NAME" mysqladmin ping --silent
docker inspect "$DB_NAME" > "$RUN_ROOT/readback/db_inspect_running.json"
docker exec "$DB_NAME" mysql -NBe \
  "SELECT @@read_only,@@super_read_only,@@general_log,@@log_output,@@general_log_file" \
  > "$RUN_ROOT/readback/mysql_gate.tsv"

docker create --name "$CLIENT_NAME" --network "$NETWORK" \
  --cpus=3 --cpuset-cpus=27-29,63-65 --cpuset-mems=1 \
  --memory=2g --memory-swap=2g --pids-limit=256 \
  -v /opt/java-21:/opt/java-21:ro \
  -v "$RUN_ROOT/production.jar:/work/production.jar:ro" \
  -v "$RUN_ROOT/mysql-connector-java-8.0.30.jar:/work/mysql-connector.jar:ro" \
  -v "$RUN_ROOT/production.props:/work/production.props:ro" \
  -v "$RUN_ROOT/stmt_mysql.toml:/work/stmt_mysql.toml:ro" \
  -v "$RUN_ROOT/artifacts:/out:rw" -w /out \
  --entrypoint /opt/java-21/bin/java "$IMAGE" \
  -cp /work/production.jar:/work/mysql-connector.jar com.hybench.HyBench \
  -t runAP -c /work/production.props -f /work/stmt_mysql.toml \
  > "$RUN_ROOT/readback/client_container_id.txt"
docker inspect "$CLIENT_NAME" > "$RUN_ROOT/readback/client_inspect_created.json"
set +e
timeout 240 docker start -a "$CLIENT_NAME" > "$RUN_ROOT/artifacts/client.log" 2>&1
client_rc=$?
set -e
printf '%s\n' "$client_rc" > "$RUN_ROOT/readback/client_exit_code.txt"
docker inspect "$CLIENT_NAME" > "$RUN_ROOT/readback/client_inspect_final.json"
test "$client_rc" -eq 0

docker exec "$DB_NAME" mysql -NBe "SET GLOBAL general_log=OFF"
docker cp "$DB_NAME:/var/lib/mysql/production_general.log" "$RUN_ROOT/artifacts/production_general.log"
docker logs "$DB_NAME" > "$RUN_ROOT/artifacts/db.log" 2>&1 || true
docker inspect "$DB_NAME" > "$RUN_ROOT/readback/db_inspect_final.json"
pgrep -af postgres > "$RUN_ROOT/readback/postgres_after.txt" || true
pgrep -af mysqld > "$RUN_ROOT/readback/mysqld_after.txt" || true
date -u +%FT%TZ > "$RUN_ROOT/readback/end_utc.txt"

cleanup
trap - EXIT
docker ps --no-trunc > "$RUN_ROOT/readback/docker_after.txt"
sha256sum "$RUN_ROOT/artifacts/production_general.log" "$RUN_ROOT/artifacts/client.log" \
  "$RUN_ROOT/readback/db_inspect_running.json" "$RUN_ROOT/readback/client_inspect_final.json" \
  > "$RUN_ROOT/evidence.sha256"
echo CAPTURE_COMPLETE
