#!/usr/bin/env bash
set -euo pipefail

result_root=/disk1/resource_adaptive_mysql_grid_20260718/results/mysql_grid_20260718
pilot_root=${result_root}/_pilots

install -d "${pilot_root}"

archive_dir() {
  local name=$1
  local suffix=$2
  local source=${result_root}/${name}
  local target=${pilot_root}/${name}-${suffix}
  test -d "${source}"
  test ! -e "${target}"
  mv "${source}" "${target}"
}

archive_dir B2-r2 incomplete-before-disk1
archive_dir A12-r1 failed-before-disk1
archive_dir B1-r3 failed-before-disk1
archive_dir A10-r1-attempt-2 incomplete-before-disk1
archive_dir B3-r2 failed-before-disk1
archive_dir A7-r1-w3 incomplete-before-disk1
archive_dir A8-r1 failed-before-disk1
archive_dir A11-r1 incomplete-before-disk1
