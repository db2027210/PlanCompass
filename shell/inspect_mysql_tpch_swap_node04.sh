#!/bin/sh
set -eu

SOCKET=/run/ra-docker-mysql-tpch-v1.sock
NAME=ra_mysql_tpch_v2_tpch_v2_20260731t144525z_6c3809b818ae9bbba8e7088_db

docker -H "unix://$SOCKET" inspect -f 'id={{.Id}} pid={{.State.Pid}} status={{.State.Status}} mem={{.HostConfig.Memory}} memswap={{.HostConfig.MemorySwap}} swappiness={{.HostConfig.MemorySwappiness}} cpuset={{.HostConfig.CpusetCpus}} oom={{.State.OOMKilled}}' "$NAME"
pid=$(docker -H "unix://$SOCKET" inspect -f '{{.State.Pid}}' "$NAME")
cgroup=$(awk -F: '$2 == "memory" {print $3}' "/proc/$pid/cgroup")
printf 'cgroup=%s\n' "$cgroup"
grep -E '^(total_swap|swap|total_rss|total_cache) ' "/sys/fs/cgroup/memory$cgroup/memory.stat"
for file in memory.limit_in_bytes memory.memsw.limit_in_bytes memory.swappiness memory.usage_in_bytes memory.memsw.usage_in_bytes; do
  printf '%s=' "$file"
  cat "/sys/fs/cgroup/memory$cgroup/$file"
done
free -b
