#!/usr/bin/env bash
set -euo pipefail

data_root=/disk1/resource_adaptive_mysql_grid_20260718
owner_label=resource-adaptive-mysql.owner=mysql-grid-20260718-disk1

test -f "${data_root}/migration_complete"

docker run -d --pull never \
  --name ra-mysql-d1-hybench-w3 \
  --label "${owner_label}" \
  --label resource-adaptive-mysql.worker=hybench-w3 \
  --cpuset-cpus 264-267 --cpus 4 \
  --memory 7680m --memory-swap 7680m \
  --restart no \
  --log-opt max-size=10m --log-opt max-file=3 \
  -p 127.0.0.1:13403:3306 \
  --mount type=bind,src="${data_root}/workers/hybench-w3",dst=/var/lib/mysql \
  -e MYSQL_ROOT_PASSWORD=CHANGE_ME \
  mysql:8.0 \
  --local-infile=ON --innodb-buffer-pool-size=5G \
  --innodb-flush-log-at-trx-commit=2 --sync-binlog=0 \
  --max-connections=512

docker run -d --pull never \
  --name ra-mysql-d1-hybench-w5 \
  --label "${owner_label}" \
  --label resource-adaptive-mysql.worker=hybench-w5 \
  --cpuset-cpus 272-275 --cpus 3.7 \
  --memory 8192m --memory-swap 8192m \
  --restart no \
  --log-opt max-size=10m --log-opt max-file=3 \
  -p 127.0.0.1:13405:3306 \
  --mount type=bind,src="${data_root}/workers/hybench-w5",dst=/var/lib/mysql \
  -e MYSQL_ROOT_PASSWORD=CHANGE_ME \
  mysql:8.0 \
  --local-infile=ON --innodb-buffer-pool-size=6500M \
  --innodb-flush-log-at-trx-commit=2 --sync-binlog=0 \
  --max-connections=512

docker run -d --pull never \
  --name ra-mysql-d1-job-w2 \
  --label "${owner_label}" \
  --label resource-adaptive-mysql.worker=job-w2 \
  --cpuset-cpus 284-287 --cpus 4 \
  --memory 4200m --memory-swap 4200m \
  --restart no \
  --log-opt max-size=10m --log-opt max-file=3 \
  -p 127.0.0.1:13412:3306 \
  --mount type=bind,src="${data_root}/workers/job-w2",dst=/var/lib/mysql \
  -e MYSQL_ROOT_PASSWORD=CHANGE_ME \
  mysql:8.0 \
  --local-infile=ON --innodb-buffer-pool-size=3G \
  --innodb-flush-log-at-trx-commit=2 --sync-binlog=0 \
  --max-connections=512

docker run -d --pull never \
  --name ra-mysql-d1-tpcc-cpu \
  --label "${owner_label}" \
  --label resource-adaptive-mysql.worker=tpcc-cpu \
  --cpuset-cpus 68-71 --cpus 0.28 \
  --memory 16g --memory-swap 16g \
  --restart no \
  --log-opt max-size=10m --log-opt max-file=3 \
  -p 127.0.0.1:13308:3306 \
  --mount type=bind,src="${data_root}/isolated/tpcc-cpu",dst=/var/lib/mysql \
  -e MYSQL_ROOT_PASSWORD=CHANGE_ME \
  mysql:8.0 mysqld
