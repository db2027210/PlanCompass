#!/usr/bin/env bash
set -euo pipefail

mount_wrapper=/home/anonymous/program/resource_adaptive/shell/run_with_disk1_mount.sh
task_runner=/home/anonymous/program/resource_adaptive_mysql/scripts/run_mysql_grid_disk1_task.sh
output_root=/disk1/resource_adaptive_mysql_grid_20260718/results/mysql_grid_20260718

check_resources() {
  local cpu_count load_one available_kb
  cpu_count=$(nproc)
  load_one=$(awk '{print $1}' /proc/loadavg)
  awk -v load_value="${load_one}" -v cpus="${cpu_count}" \
    'BEGIN { exit !(load_value <= cpus) }'
  available_kb=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
  test "${available_kb}" -ge 67108864
}

check_resources

running_count=$(docker ps \
  --filter label=resource-adaptive-mysql.owner=mysql-grid-20260718-disk1 \
  --format '{{.Names}}' | wc -l)
test "${running_count}" -eq 4

for session in ra_mysql_A7r1_d1_v2 ra_mysql_B1r3_d1_v2 ra_mysql_A8r1_d1_v2 ra_mysql_A11r1_d1_v2; do
  ! tmux has-session -t "${session}" 2>/dev/null
done

for run_key in A7-r1 B1-r3 A8-r1 A11-r1; do
  test ! -e "${output_root}/${run_key}"
done

docker update --memory 6912m ra-mysql-d1-hybench-w3 >/dev/null
docker update --cpus 3.6 ra-mysql-d1-hybench-w5 >/dev/null

test "$(docker inspect --format '{{.HostConfig.Memory}}' ra-mysql-d1-hybench-w3)" -eq 7247757312
test "$(docker inspect --format '{{.HostConfig.NanoCpus}}' ra-mysql-d1-hybench-w5)" -eq 3600000000

check_resources

tmux new-session -d -s ra_mysql_A7r1_d1_v2 \
  "sudo -n ${mount_wrapper} sudo -u anonymous ${task_runner} A7-r1 A7 1 hybench_ap memory ra-mysql-d1-hybench-w3 13403 hybench_sf10x 4 6.75 10"

tmux new-session -d -s ra_mysql_B1r3_d1_v2 \
  "sudo -n ${mount_wrapper} sudo -u anonymous ${task_runner} B1-r3 B1 3 hybench_tp cpu ra-mysql-d1-hybench-w5 13405 hybench_sf10x 3.6 8 10"

tmux new-session -d -s ra_mysql_A8r1_d1_v2 \
  "sudo -n ${mount_wrapper} sudo -u anonymous ${task_runner} A8-r1 A8 1 job memory ra-mysql-d1-job-w2 13412 imdb_job 4 4.1015625 1"

tmux new-session -d -s ra_mysql_A11r1_d1_v2 \
  "sudo -n ${mount_wrapper} sudo -u anonymous ${task_runner} A11-r1 A11 1 tpcc cpu ra-mysql-d1-tpcc-cpu 13308 tpcc 0.28 16 143"
