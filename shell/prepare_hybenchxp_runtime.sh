#!/usr/bin/env bash
set -euo pipefail

SRC_DIR="${BINARY_PLAN_TUNING_HYBENCHXP_SOURCE_DIR:-/home/anonymous/HyBench-2024-master}"
LAUNCHER_SRC="${BINARY_PLAN_TUNING_HYBENCHXP_LAUNCHER:-${SRC_DIR}/hybench}"
DST_DIR="${BINARY_PLAN_TUNING_HYBENCHXP_RUNTIME_DIR:-/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/hybench_runtime/HyBench-2024-master}"

case "${DST_DIR}" in
  /disk1/*binary_plan_tuning_hybenchxp*|/disk2/*binary_plan_tuning_hybenchxp*) ;;
  *)
    echo "Refusing to prepare HyBench runtime outside binary_plan_tuning_hybenchxp data roots: ${DST_DIR}" >&2
    exit 2
    ;;
esac

if [[ ! -d "${SRC_DIR}" ]]; then
  echo "HyBench source directory not found: ${SRC_DIR}" >&2
  exit 2
fi
if [[ ! -f "${LAUNCHER_SRC}" ]]; then
  LAUNCHER_SRC="${SRC_DIR}/hybench"
fi
if [[ ! -x "${LAUNCHER_SRC}" ]]; then
  echo "HyBench launcher not executable: ${LAUNCHER_SRC}" >&2
  exit 2
fi
if [[ ! -d "${SRC_DIR}/lib" || ! -d "${SRC_DIR}/conf" ]]; then
  echo "HyBench source must contain lib/ and conf/: ${SRC_DIR}" >&2
  exit 2
fi

mkdir -p "$(dirname "${DST_DIR}")"
rm -rf "${DST_DIR}.tmp"
mkdir -p "${DST_DIR}.tmp"

cp -a "${LAUNCHER_SRC}" "${DST_DIR}.tmp/hybench"
cp -a "${LAUNCHER_SRC}" "${DST_DIR}.tmp/hybench_unblocked"
cp -a "${SRC_DIR}/lib" "${DST_DIR}.tmp/lib"
cp -a "${SRC_DIR}/conf" "${DST_DIR}.tmp/conf"
for data_name in Data_10x Data_100x; do
  if [[ -d "${SRC_DIR}/${data_name}" ]]; then
    ln -s "${SRC_DIR}/${data_name}" "${DST_DIR}.tmp/${data_name}"
  fi
done
chmod +x "${DST_DIR}.tmp/hybench"
chmod +x "${DST_DIR}.tmp/hybench_unblocked"
rm -f "${DST_DIR}.tmp/hybench-error.log"

rm -rf "${DST_DIR}"
mv "${DST_DIR}.tmp" "${DST_DIR}"

echo "hybench_runtime=${DST_DIR}"
echo "hybench_launcher=${LAUNCHER_SRC}"
