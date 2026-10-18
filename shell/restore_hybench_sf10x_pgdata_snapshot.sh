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
LOG_DIR="${LOG_DIR:-/tmp/resource_adaptive_hybench_sf10x_pgdata_restore/logs}"

mkdir -p "${LOG_DIR}"
export PGPASSWORD="${DB_PASSWORD}"

restore_vm_resources() {
  vm_resource_apply_target_from_config || true
}

trap restore_vm_resources EXIT

ssh_opts=(-o ConnectTimeout=10 -p "${SSH_PORT}")
if [[ -n "${SSH_IDENTITY_FILE}" ]]; then
  ssh_opts+=(-i "${SSH_IDENTITY_FILE}")
fi

remote_cmd=$(cat <<EOF
set -euo pipefail
pg_ctl='${PG_CTL}'
pg_data='${PG_DATA_DIR}'
snapshot='${SNAPSHOT_DIR}'
log_file='/tmp/resource_adaptive_pgdata_restore.log'

if [[ ! -d "\${snapshot}" ]]; then
  echo "[pgdata-restore] snapshot not found: \${snapshot}" >&2
  echo "[pgdata-restore] create it once with shell/create_hybench_sf10x_pgdata_snapshot.sh after a clean rebuild" >&2
  exit 1
fi

echo "[pgdata-restore] stop PostgreSQL" >&2
sudo -n -u postgres "\${pg_ctl}" -D "\${pg_data}" status >/dev/null 2>&1 && \
  sudo -n -u postgres "\${pg_ctl}" -D "\${pg_data}" -w -t 600 -m fast stop || true

echo "[pgdata-restore] replace pg_data from snapshot" >&2
rm -rf "\${pg_data}.restore_tmp" || {
  echo "[pgdata-restore] no permission to remove \${pg_data}.restore_tmp; grant dr write permission or sudo NOPASSWD for file ops" >&2
  exit 1
}
cp -a "\${snapshot}" "\${pg_data}.restore_tmp" || {
  echo "[pgdata-restore] no permission to copy \${snapshot}; pg_data restore requires filesystem read/write permission" >&2
  exit 1
}
rm -rf "\${pg_data}" || {
  echo "[pgdata-restore] no permission to replace \${pg_data}; pg_data restore requires filesystem write permission" >&2
  exit 1
}
mv "\${pg_data}.restore_tmp" "\${pg_data}"

echo "[pgdata-restore] pg_data replacement complete" >&2
EOF
)

echo "[pgdata-restore] restore ${PG_DATA_DIR} from ${SNAPSHOT_DIR}" >&2
VM_RESOURCE_START_POSTGRES=0 VM_RESOURCE_WAIT_READY=0 vm_resource_apply_max
_vm_resource_wait_for_ssh "${SSH_TARGET}" "${SSH_PORT}" "${SSH_IDENTITY_FILE}"
ssh "${ssh_opts[@]}" "${SSH_TARGET}" "bash -lc $(printf '%q' "${remote_cmd}")"
echo "[pgdata-restore] pg_data restored; VM resource restore will start PostgreSQL" >&2
