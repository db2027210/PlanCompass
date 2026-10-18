#!/usr/bin/env bash
set -euo pipefail

source_host=anonymous@192.0.2.7
target_host=anonymous@192.0.2.3
image=mysql:8.0

copy_template() {
  local label=$1
  local source=$2
  local target=$3
  local source_manifest target_manifest
  source_manifest=$(mktemp)
  target_manifest=$(mktemp)
  trap 'rm -f "$source_manifest" "$target_manifest"' RETURN

  test -z "$(
    ssh -o BatchMode=yes "$target_host" \
      docker run --rm --network none --entrypoint find \
        -v "$target:/target:rw" "$image" /target -mindepth 1 -print -quit
  )"
  ssh -o BatchMode=yes "$source_host" \
    docker run --rm --network none --entrypoint tar \
      -v "$source:/source:ro" "$image" -C /source -cf - . \
    | ssh -o BatchMode=yes "$target_host" \
      docker run --rm -i --network none --entrypoint tar \
        -v "$target:/target:rw" "$image" -C /target -xf -

  ssh -o BatchMode=yes "$source_host" \
    docker run --rm --network none --entrypoint find \
      -v "$source:/tree:ro" "$image" /tree -type f -printf '%P\t%s\n' \
    | LC_ALL=C sort >"$source_manifest"
  ssh -o BatchMode=yes "$target_host" \
    docker run --rm --network none --entrypoint find \
      -v "$target:/tree:ro" "$image" /tree -type f -printf '%P\t%s\n' \
    | LC_ALL=C sort >"$target_manifest"
  cmp "$source_manifest" "$target_manifest"
  printf '%s copied and file-size manifest verified: %s\n' "$label" "$target"
}

copy_template \
  tpch \
  /mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/template \
  /mnt/nvme1n1/anonymous/resource_adaptive_mysql/tpch/template

copy_template \
  tpcc \
  /mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/template/datadir \
  /mnt/mysql_nvme0n1/anonymous/resource_adaptive_mysql/tpcc/template/datadir
