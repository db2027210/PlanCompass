#!/usr/bin/env bash
set -euo pipefail

source /home/anonymous/program/resource_adaptive/shell/vm_resource_helper.sh

DB_HOST="${DB_HOST:-192.0.2.8}"
DB_PORT="${DB_PORT:-25432}"
DB_USER="${DB_USER:-postgres}"
DB_PASSWORD="${DB_PASSWORD:-CHANGE_ME}"
SSH_TARGET="${SSH_TARGET:-anonymous@${DB_HOST}}"
SSH_PORT="${SSH_PORT:-22}"
SSH_IDENTITY_FILE="${SSH_IDENTITY_FILE:-}"
PG_CTL="${PG_CTL:-/usr/lib/postgresql/17/bin/pg_ctl}"
PG_DATA_DIR="${PG_DATA_DIR:-/home/anonymous/pg_master/pg_data}"
SNAPSHOT_DIR="${SNAPSHOT_DIR:-/home/anonymous/pg_master/pg_data_hybench_sf10x_baseline}"
OVERWRITE="${OVERWRITE:-0}"
USE_VM_RESOURCE_BOOST="${USE_VM_RESOURCE_BOOST:-1}"

ssh_opts=(-o ConnectTimeout=10 -p "${SSH_PORT}")
if [[ -n "${SSH_IDENTITY_FILE}" ]]; then
  ssh_opts+=(-i "${SSH_IDENTITY_FILE}")
fi

restore_vm_resources() {
  if [[ "${USE_VM_RESOURCE_BOOST}" = "1" ]]; then
    vm_resource_apply_target_from_config || true
  fi
}

trap restore_vm_resources EXIT

remote_cmd=$(cat <<EOF
set -euo pipefail
pg_ctl='${PG_CTL}'
pg_data='${PG_DATA_DIR}'
snapshot='${SNAPSHOT_DIR}'
overwrite='${OVERWRITE}'

if [[ ! -d "\${pg_data}" ]]; then
  echo "[pgdata-snapshot] pg_data not found: \${pg_data}" >&2
  exit 1
fi
if [[ -e "\${snapshot}" && "\${overwrite}" != "1" ]]; then
  echo "[pgdata-snapshot] snapshot already exists: \${snapshot}" >&2
  echo "[pgdata-snapshot] set OVERWRITE=1 to replace it" >&2
  exit 1
fi

echo "[pgdata-snapshot] stop PostgreSQL" >&2
sudo -n -u postgres "\${pg_ctl}" -D "\${pg_data}" status >/dev/null 2>&1 && \
  sudo -n -u postgres "\${pg_ctl}" -D "\${pg_data}" -w -t 600 -m fast stop || true

echo "[pgdata-snapshot] copy \${pg_data} -> \${snapshot}" >&2
rm -rf "\${snapshot}.tmp" || {
  echo "[pgdata-snapshot] no permission to remove \${snapshot}.tmp; grant dr write permission or sudo NOPASSWD for file ops" >&2
  exit 1
}
mkdir -p "\$(dirname "\${snapshot}")"
cp -a "\${pg_data}" "\${snapshot}.tmp" || {
  echo "[pgdata-snapshot] no permission to copy \${pg_data}; pg_data snapshot requires filesystem read/write permission" >&2
  exit 1
}
rm -rf "\${snapshot}"
mv "\${snapshot}.tmp" "\${snapshot}"

echo "[pgdata-snapshot] restart PostgreSQL" >&2
sudo -n -u postgres "\${pg_ctl}" -D "\${pg_data}" -w -t 600 start
echo "[pgdata-snapshot] ready: \${snapshot}" >&2
EOF
)

if [[ "${USE_VM_RESOURCE_BOOST}" = "1" ]]; then
  echo "[pgdata-snapshot] boost VM resources for snapshot copy" >&2
  VM_RESOURCE_START_POSTGRES=0 VM_RESOURCE_WAIT_READY=0 vm_resource_apply_max
  _vm_resource_wait_for_ssh "${SSH_TARGET}" "${SSH_PORT}" "${SSH_IDENTITY_FILE}"
fi

ssh "${ssh_opts[@]}" "${SSH_TARGET}" "bash -lc $(printf '%q' "${remote_cmd}")"
_vm_resource_wait_for_postgres
