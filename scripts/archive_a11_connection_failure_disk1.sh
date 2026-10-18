#!/usr/bin/env bash
set -euo pipefail

result_root=/disk1/resource_adaptive_mysql_grid_20260718/results/mysql_grid_20260718
source=${result_root}/A11-r1
target=${result_root}/_pilots/A11-r1-too-many-connections-disk1

test -d "${source}"
test ! -e "${target}"
mv "${source}" "${target}"
