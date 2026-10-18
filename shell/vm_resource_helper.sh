#!/usr/bin/env bash

_vm_resource_require_config() {
  local config_path="${OPTIMIZER_CONFIG_PATH:-}"
  if [[ -z "${config_path}" || ! -f "${config_path}" ]]; then
    echo "[vm-resource] skip: OPTIMIZER_CONFIG_PATH is not set or missing" >&2
    return 1
  fi
  if ! command -v jq >/dev/null 2>&1; then
    echo "[vm-resource] skip: jq is required" >&2
    return 1
  fi
  return 0
}

_vm_resource_read_json() {
  local expr="$1"
  jq -r "${expr}" "${OPTIMIZER_CONFIG_PATH}"
}

_vm_resource_ceil_int() {
  local value="$1"
  awk -v v="${value}" 'BEGIN { if (v == "" || v == "null") { print 0; exit } print int(v == int(v) ? v : v + 1) }'
}

_vm_resource_memory_arg() {
  local value_gib="$1"
  awk -v v="${value_gib}" 'BEGIN {
    if (v == "" || v == "null" || v + 0 <= 0) { print "1G"; exit }
    mib = int((v + 0) * 1024 + 0.5)
    if (mib % 1024 == 0) { print int(mib / 1024) "G"; exit }
    print mib "M"
  }'
}

_vm_resource_host_cpu() {
  nproc
}

_vm_resource_host_mem_gb() {
  awk '/MemTotal:/ { print int($2 / 1024 / 1024) }' /proc/meminfo
}

_vm_resource_host_available_mem_gb() {
  awk '/MemAvailable:/ { print int($2 / 1024 / 1024) }' /proc/meminfo
}

_vm_resource_wait_for_postgres() {
  local timeout_sec="${VM_RESOURCE_READY_TIMEOUT_SEC:-900}"
  local interval_sec="${VM_RESOURCE_READY_INTERVAL_SEC:-5}"
  local deadline=$((SECONDS + timeout_sec))
  local next_log=$((SECONDS + 30))
  local pg_host="${DB_HOST:-192.0.2.8}"
  local pg_port="${DB_PORT:-25432}"
  local pg_user="${DB_USER:-postgres}"
  local pg_password="${DB_PASSWORD:-CHANGE_ME}"

  if [[ "${VM_RESOURCE_WAIT_READY:-1}" != "1" ]]; then
    return 0
  fi

  echo "[vm-resource] waiting for PostgreSQL ${pg_host}:${pg_port}" >&2
  while (( SECONDS < deadline )); do
    if PGPASSWORD="${pg_password}" PGCONNECT_TIMEOUT=5 \
      psql "postgresql://${pg_user}:${pg_password}@${pg_host}:${pg_port}/postgres" \
        -X -q -v ON_ERROR_STOP=1 -c "SELECT 1;" >/dev/null 2>&1; then
      echo "[vm-resource] PostgreSQL is reachable" >&2
      return 0
    fi
    if (( SECONDS >= next_log )); then
      echo "[vm-resource] still waiting for PostgreSQL ${pg_host}:${pg_port}" >&2
      next_log=$((SECONDS + 30))
    fi
    sleep "${interval_sec}"
  done

  echo "[vm-resource] PostgreSQL did not become reachable within ${timeout_sec}s" >&2
  return 1
}

_vm_resource_start_postgres() {
  _vm_resource_require_config || return 0
  if [[ "${VM_RESOURCE_START_POSTGRES:-1}" != "1" ]]; then
    return 0
  fi

  local ssh_target ssh_port ssh_identity ssh_opts start_cmd
  ssh_target="${VM_RESOURCE_SSH_TARGET:-$(_vm_resource_read_json '.post_benchmark_cleanup.vm_restart_ssh_target // .metrics.vm_runtime.ssh_target // ""')}"
  ssh_port="${VM_RESOURCE_SSH_PORT:-$(_vm_resource_read_json '.post_benchmark_cleanup.vm_restart_ssh_port // .metrics.vm_runtime.ssh_port // 22')}"
  ssh_identity="${VM_RESOURCE_SSH_IDENTITY_FILE:-$(_vm_resource_read_json '.post_benchmark_cleanup.vm_restart_ssh_identity_file // .metrics.vm_runtime.ssh_identity_file // ""')}"
  start_cmd="${VM_RESOURCE_POSTGRES_START_CMD:-$(_vm_resource_read_json '.post_benchmark_cleanup.vm_start_cmd // ""')}"
  if [[ -z "${start_cmd}" || "${start_cmd}" == "null" ]]; then
    start_cmd="sudo -n -u postgres /usr/lib/postgresql/17/bin/pg_ctl -D /home/anonymous/pg_master/pg_data status >/dev/null 2>&1 && sudo -n -u postgres /usr/lib/postgresql/17/bin/pg_ctl -D /home/anonymous/pg_master/pg_data -w -t 600 -m fast restart </dev/null >/tmp/resource_adaptive_pg_start.log 2>&1 || sudo -n -u postgres /usr/lib/postgresql/17/bin/pg_ctl -D /home/anonymous/pg_master/pg_data -w -t 600 start </dev/null >/tmp/resource_adaptive_pg_start.log 2>&1; rc=\$?; cat /tmp/resource_adaptive_pg_start.log; exit \$rc"
  fi

  if [[ -z "${ssh_target}" || "${ssh_target}" == "null" ]]; then
    echo "[vm-resource] skip PostgreSQL start: ssh target or command is missing" >&2
    return 0
  fi

  ssh_opts=(-o ConnectTimeout=10 -p "${ssh_port}")
  if [[ -n "${ssh_identity}" && "${ssh_identity}" != "null" ]]; then
    ssh_opts+=(-i "${ssh_identity}")
  fi

  echo "[vm-resource] starting PostgreSQL via ${ssh_target}" >&2
  _vm_resource_wait_for_ssh "${ssh_target}" "${ssh_port}" "${ssh_identity}" || return 1
  if ! ssh "${ssh_opts[@]}" "${ssh_target}" "bash -lc $(printf '%q' "${start_cmd}")" >&2; then
    echo "[vm-resource] PostgreSQL start command failed on ${ssh_target}" >&2
    return 1
  fi
}

_vm_resource_wait_for_ssh() {
  local ssh_target="$1"
  local ssh_port="$2"
  local ssh_identity="$3"
  local timeout_sec="${VM_RESOURCE_SSH_READY_TIMEOUT_SEC:-300}"
  local interval_sec="${VM_RESOURCE_SSH_READY_INTERVAL_SEC:-5}"
  local deadline=$((SECONDS + timeout_sec))
  local next_log=$((SECONDS + 30))
  local ssh_opts

  ssh_opts=(-o BatchMode=yes -o ConnectTimeout=5 -p "${ssh_port}")
  if [[ -n "${ssh_identity}" && "${ssh_identity}" != "null" ]]; then
    ssh_opts+=(-i "${ssh_identity}")
  fi

  echo "[vm-resource] waiting for SSH ${ssh_target}:${ssh_port}" >&2
  while (( SECONDS < deadline )); do
    if ssh "${ssh_opts[@]}" "${ssh_target}" "true" >/dev/null 2>&1; then
      echo "[vm-resource] SSH is reachable" >&2
      return 0
    fi
    if (( SECONDS >= next_log )); then
      echo "[vm-resource] still waiting for SSH ${ssh_target}:${ssh_port}" >&2
      next_log=$((SECONDS + 30))
    fi
    sleep "${interval_sec}"
  done

  echo "[vm-resource] SSH did not become reachable within ${timeout_sec}s" >&2
  return 1
}

vm_resource_apply_max() {
  _vm_resource_require_config || return 0
  local vm_name disk_dev host_cpu host_mem_gb io_read_mb io_write_mb
  vm_name="${VM_RESOURCE_VM_NAME:-$(_vm_resource_read_json '.vm_resource.vm_name // "artifact-ubuntu20"')}"
  disk_dev="${VM_RESOURCE_DISK_DEV:-$(_vm_resource_read_json '.vm_resource.disk_dev // .metrics.vm_runtime.disk_devices[0] // "vda"')}"
  host_cpu="${VM_RESOURCE_MAX_CPU:-$(_vm_resource_read_json '.vm_resource.boost_cpu // 0')}"
  host_mem_gb="${VM_RESOURCE_MAX_MEMORY_GB:-$(_vm_resource_read_json '.vm_resource.boost_memory_gb // 0')}"
  io_read_mb="${VM_RESOURCE_MAX_READ_MB:-$(_vm_resource_read_json '.vm_resource.boost_read_mb // 0')}"
  io_write_mb="${VM_RESOURCE_MAX_WRITE_MB:-$(_vm_resource_read_json '.vm_resource.boost_write_mb // 0')}"
  if (( host_cpu <= 0 )); then
    host_cpu="$(_vm_resource_host_cpu)"
  fi
  if (( host_mem_gb <= 0 )); then
    host_mem_gb="$(_vm_resource_host_available_mem_gb)"
    if (( host_mem_gb > 0 )); then
      host_mem_gb=$(( host_mem_gb / 2 ))
    fi
  fi
  if (( host_cpu > 255 )); then
    host_cpu=255
  fi
  if (( host_mem_gb < 1 )); then
    host_mem_gb=1
  fi

  echo "[vm-resource] boost vm=${vm_name} cpu=${host_cpu} mem=${host_mem_gb}G io=${io_read_mb}/${io_write_mb} MB/s disk=${disk_dev}" >&2
  if ! bash /home/anonymous/program/resource_adaptive/shell/set_vm_resource.sh \
    "${vm_name}" "${host_cpu}" "${host_mem_gb}G" "${io_read_mb}" "${io_write_mb}" "${disk_dev}"; then
    echo "[vm-resource] failed to apply boosted VM resource profile for ${vm_name}" >&2
    return 1
  fi
  _vm_resource_start_postgres
  _vm_resource_wait_for_postgres
}

vm_resource_apply_target_from_config() {
  _vm_resource_require_config || return 0
  local vm_name disk_dev cfg_cpu cfg_mem cfg_read cfg_write cpu_int mem_arg read_int write_int
  vm_name="${VM_RESOURCE_VM_NAME:-$(_vm_resource_read_json '.vm_resource.vm_name // "artifact-ubuntu20"')}"
  disk_dev="${VM_RESOURCE_DISK_DEV:-$(_vm_resource_read_json '.vm_resource.disk_dev // .metrics.vm_runtime.disk_devices[0] // "vda"')}"
  cfg_cpu="$(_vm_resource_read_json '.vm_resource.cpu // .capability.cpu // 0')"
  cfg_mem="$(_vm_resource_read_json '.vm_resource.memory_gb // .capability.memory // 0')"
  cfg_read="$(_vm_resource_read_json '.vm_resource.read_mb // .capability.io // 0')"
  cfg_write="$(_vm_resource_read_json '.vm_resource.write_mb // .capability.io // 0')"
  cpu_int="$(_vm_resource_ceil_int "${cfg_cpu}")"
  mem_arg="$(_vm_resource_memory_arg "${cfg_mem}")"
  read_int="$(_vm_resource_ceil_int "${cfg_read}")"
  write_int="$(_vm_resource_ceil_int "${cfg_write}")"

  echo "[vm-resource] restore vm=${vm_name} cpu=${cpu_int} mem=${mem_arg} io=${read_int}/${write_int} MB/s disk=${disk_dev}" >&2
  if ! bash /home/anonymous/program/resource_adaptive/shell/set_vm_resource.sh \
    "${vm_name}" "${cpu_int}" "${mem_arg}" "${read_int}" "${write_int}" "${disk_dev}"; then
    echo "[vm-resource] failed to apply target VM resource profile for ${vm_name}" >&2
    return 1
  fi
  _vm_resource_start_postgres
  _vm_resource_wait_for_postgres
}
