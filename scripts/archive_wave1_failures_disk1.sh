#!/usr/bin/env bash
set -euo pipefail

result_root=/disk1/resource_adaptive_mysql_grid_20260718/results/mysql_grid_20260718
pilot_root=${result_root}/_pilots

archive_dir() {
  local name=$1
  local suffix=$2
  local source=${result_root}/${name}
  local target=${pilot_root}/${name}-${suffix}
  test -d "${source}"
  test ! -e "${target}"
  mv "${source}" "${target}"
}

archive_dir A7-r1 disk1-grid-v1-failed
archive_dir A8-r1 disk1-grid-v1-failed
archive_dir B1-r3 disk1-grid-v1-failed
