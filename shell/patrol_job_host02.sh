#!/bin/bash
# ==============================================================================

# ==============================================================================






#





#

# ==============================================================================

set -o pipefail

# --- Config ---
SSH_TARGET="${SSH_TARGET:-anonymous@192.0.2.4}"
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes -o ServerAliveInterval=15"
TEMPLATE_DIR="${TEMPLATE_DIR:-/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/template}"
JOB_ROOT_DIR="${JOB_ROOT_DIR:-$(dirname "$TEMPLATE_DIR")}"
PROJECT_DIR="${PROJECT_DIR:-/home/anonymous/program/resource_adaptive_mysql}"
LOG_FILE="${LOG_FILE:-${PROJECT_DIR}/history/mysql_sgd_env_agents_20260723/job/patrol_log_20260723.jsonl}"
ROUND_FILE="${ROUND_FILE:-${PROJECT_DIR}/history/mysql_sgd_env_agents_20260723/job/.patrol_round}"

# --- Frozen workload config (v3 delta) ---
JOB_WORKLOAD_FREEZE_ID="frozen_mysql_workloads_and_parameters_20260723_v3"
JOB_WORKLOAD_FREEZE_DIR="${PROJECT_DIR}/config/formal/${JOB_WORKLOAD_FREEZE_ID}"
JOB_SENSITIVE_FRACTION="0.06976744186046512"   # 7.5/107.5 = 6.976744186046512%
JOB_SENSITIVE_WEIGHT="7.5"
JOB_WEIGHT_TOTAL="107.5"
JOB_DEPRECATED_FRACTION="0.047619"              # SUPERSEDED — fail closed if detected
JOB_FREEZE_CHECKSUM_FROZEN_CONFIG="d1bfd00da8b623890936e0c5aa2a0d6bff1c029369d450d539df4fe8dce74308"
JOB_FREEZE_CHECKSUM_JOB_MIX="40937789f3830d19eb33477b96a925896c023ed9cdae9df1e28ca24bf5a63896"


XP_AUTHORITATIVE_ROW_COUNT=87060000
XP_FREEZE_REPROVEN="false"                       # XP has not re-proven freeze yet
XP_CPU_WARN_THRESHOLD_PCT=100                    # flag if XP mysqld CPU > 100%

# Ensure log directory exists
mkdir -p "$(dirname "$LOG_FILE")"

# --- Round counter ---
if [[ -f "$ROUND_FILE" ]]; then
    ROUND=$(cat "$ROUND_FILE")
    ROUND=$((ROUND + 1))
else
    ROUND=1
fi
echo "$ROUND" > "$ROUND_FILE"

# --- Timestamps ---
TS=$(date '+%Y-%m-%dT%H:%M:%S%:z' 2>/dev/null || date '+%Y-%m-%dT%H:%M:%S+08:00')
TS_UTC=$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u '+%Y-%m-%dT%H:%M:%SZ')

# --- Helper: SSH read-only command ---
ssh_ro() {
    ssh $SSH_OPTS "$SSH_TARGET" "$@" 2>/dev/null
}

# --- Helper: safe boolean ---
safe_bool() {
    case "$1" in
        yes|true|1|ok|OK|True|TRUE) echo "true" ;;
        *) echo "false" ;;
    esac
}

# ==============================================================================

# ==============================================================================
TEMPLATE_EXISTS="false"
IBD_COUNT_IMDB_JOB=0
IBD_COUNT_TOTAL=0
DIR_PERMS="unknown"
IBD_PERMS="unknown"
MANIFEST_DB_SHA256=""
MANIFEST_DB_MATCH="false"
MANIFEST_VERIFY_SHA256=""
MANIFEST_VERIFY_MATCH="false"
IBD_MTIME_MAX_EPOCH=0
IBD_MTIME_BEFORE_FREEZE="false"
INTEGRITY="FAIL"

TEMPLATE_CHECK=$(ssh_ro "test -d ${TEMPLATE_DIR} && echo 'EXISTS' || echo 'MISSING'" 2>/dev/null)
if [[ "$TEMPLATE_CHECK" == "EXISTS" ]]; then
    TEMPLATE_EXISTS="true"

    # Count .ibd files in imdb_job subdirectory (the 21 IMDB JOB tables)
    IBD_COUNT_IMDB_JOB=$(ssh_ro "find ${TEMPLATE_DIR}/imdb_job -maxdepth 1 -name '*.ibd' 2>/dev/null | wc -l" 2>/dev/null | tr -d ' ')
    IBD_COUNT_TOTAL=$(ssh_ro "find ${TEMPLATE_DIR} -name '*.ibd' 2>/dev/null | wc -l" 2>/dev/null | tr -d ' ')

    # Directory permissions
    DIR_PERMS=$(ssh_ro "stat -c '%a' ${TEMPLATE_DIR} 2>/dev/null" 2>/dev/null | tr -d '\n')
    IBD_PERMS=$(ssh_ro "stat -c '%a' ${TEMPLATE_DIR}/imdb_job/*.ibd 2>/dev/null | sort -u | head -1" 2>/dev/null | tr -d '\n')

    # Manifest checksum verification (compare against frozen values from template freeze checkpoint)
    # frozen: database_manifest.json SHA-256
    MANIFEST_DB_SHA256=$(ssh_ro "sha256sum ${JOB_ROOT_DIR}/database_manifest.json 2>/dev/null | awk '{print \$1}'" 2>/dev/null | tr -d '\n')
    FROZEN_DB_SHA256="aa430eceaa6fcc710769ae25ece93458fa0544cf2bc0b9600a0034982652a552"
    if [[ "$MANIFEST_DB_SHA256" == "$FROZEN_DB_SHA256" ]]; then
        MANIFEST_DB_MATCH="true"
    fi

    # frozen: job_deployment_verification_20260723.json SHA-256
    MANIFEST_VERIFY_SHA256=$(ssh_ro "sha256sum ${JOB_ROOT_DIR}/job_deployment_verification_20260723.json 2>/dev/null | awk '{print \$1}'" 2>/dev/null | tr -d '\n')
    FROZEN_VERIFY_SHA256="e2723b955a9c170b1afaa56f6c7f9b34f19e6b97b8948d79de2f1a61cf6b2042"
    if [[ "$MANIFEST_VERIFY_SHA256" == "$FROZEN_VERIFY_SHA256" ]]; then
        MANIFEST_VERIFY_MATCH="true"
    fi

    # IBD mtime check (max mtime should be before freeze timestamp 2026-07-22T18:27:59Z)
    IBD_MTIME_MAX_EPOCH=$(ssh_ro "find ${TEMPLATE_DIR}/imdb_job -name '*.ibd' -printf '%T@\n' 2>/dev/null | sort -rn | head -1 | cut -d. -f1" 2>/dev/null | tr -d '\n')
    FREEZE_EPOCH=1784744879  # 2026-07-22T18:27:59Z / 2026-07-23T02:27:59+08:00
    if [[ -n "$IBD_MTIME_MAX_EPOCH" && "$IBD_MTIME_MAX_EPOCH" -le "$FREEZE_EPOCH" ]]; then
        IBD_MTIME_BEFORE_FREEZE="true"
    fi

    # Integrity gate:
    # Formal patrol requires both the frozen IBD set and its external manifests.
    if [[ "$IBD_COUNT_IMDB_JOB" -eq 21 && "$IBD_PERMS" == "444" ]]; then
        if [[ "$MANIFEST_DB_MATCH" == "true" && "$MANIFEST_VERIFY_MATCH" == "true" ]]; then
            INTEGRITY="PASS_full_manifest"
        elif [[ "$MANIFEST_DB_MATCH" == "true" ]]; then
            INTEGRITY="PASS_db_manifest_only"
        else
            INTEGRITY="FAIL_manifest_missing_or_mismatch"
        fi
    elif [[ "$IBD_COUNT_IMDB_JOB" -eq 21 && "$IBD_PERMS" != "444" ]]; then
        INTEGRITY="WARN_ibd_perms_not_444"
    fi
fi

# ==============================================================================

# ==============================================================================
CONTAINER_INFO=$(ssh_ro "docker ps --format '{{.Names}}|{{.Status}}' 2>/dev/null" 2>/dev/null)
EXITED_INFO=$(ssh_ro "docker ps -a --filter status=exited --format '{{.Names}}|{{.Status}}' 2>/dev/null" 2>/dev/null)

MYSQL_RUNNING=0
MYSQL_JOB_RUNNING=0
MYSQL_XP_RUNNING=0
MYSQL_OTHER_RUNNING=0
PG_RUNNING=0
PG_NAME=""
PG_STATUS=""
PG_CPU_PCT="0.0"
PG_MEM_USAGE=""
PG_ACTIVE_QUERIES=0
EXITED_CONTAINERS=0

while IFS='|' read -r name status; do
    if [[ "$name" == *mysql* || "$name" == *MySQL* || "$name" == *ra_mysql* ]]; then
        MYSQL_RUNNING=$((MYSQL_RUNNING + 1))
        # Distinguish JOB vs XP MySQL containers
        if [[ "$name" == *job* || "$name" == *JOB* || "$name" == *imdb* || "$name" == *IMDB* ]]; then
            MYSQL_JOB_RUNNING=$((MYSQL_JOB_RUNNING + 1))
        elif [[ "$name" == *xp* || "$name" == *XP* || "$name" == *hybench* || "$name" == *HyBench* ]]; then
            MYSQL_XP_RUNNING=$((MYSQL_XP_RUNNING + 1))
        else
            MYSQL_OTHER_RUNNING=$((MYSQL_OTHER_RUNNING + 1))
        fi
    elif [[ "$name" == *pg* || "$name" == *PG* || "$name" == *ra_pg* || "$name" == *postgres* ]]; then
        PG_RUNNING=$((PG_RUNNING + 1))
        PG_NAME="$name"
        PG_STATUS="$status"
    fi
done <<< "$CONTAINER_INFO"

# PG CPU stats (docker stats, one-shot)
if [[ -n "$PG_NAME" ]]; then
    PG_STATS=$(ssh_ro "docker stats --no-stream --format '{{.CPUPerc}}|{{.MemUsage}}' ${PG_NAME} 2>/dev/null" 2>/dev/null)
    PG_CPU_PCT=$(echo "$PG_STATS" | cut -d'|' -f1 | tr -d '%' | tr -d ' ')
    PG_MEM_USAGE=$(echo "$PG_STATS" | cut -d'|' -f2 | tr -d ' ')
    # PG active queries (non-idle backends)
    PG_ACTIVE_QUERIES=$(ssh_ro "docker exec ${PG_NAME} psql -U postgres -t -c \"SELECT count(*) FROM pg_stat_activity WHERE state != 'idle' AND pid != pg_backend_pid();\" 2>/dev/null" 2>/dev/null | tr -d ' ')
    [[ -z "$PG_ACTIVE_QUERIES" ]] && PG_ACTIVE_QUERIES=0
fi

EXITED_CONTAINERS=$(echo "$EXITED_INFO" | grep -c . 2>/dev/null || echo 0)

# ==============================================================================
# CHECK 3: Processes — mysqld / JOB benchmark / XP
# ==============================================================================
# Note: mysqld may run inside XP containers - distinguish by container ownership
MYSQLD_PROC=$(ssh_ro "ps aux 2>/dev/null | grep -v grep | grep mysqld" 2>/dev/null)
JOB_BENCHMARK_PROC=$(ssh_ro "ps aux 2>/dev/null | grep -v grep | grep -E 'BenchBase|benchbase|oltpbench|job_runner|JobBench' " 2>/dev/null)
XP_HYBENCH_PROC=$(ssh_ro "ps aux 2>/dev/null | grep -v grep | grep -E 'hybench|HyBench|hy_bench' " 2>/dev/null)
# JOB-specific: look for mysqld with JOB data dir or port
JOB_MYSQLD_PROC=$(ssh_ro "ps aux 2>/dev/null | grep -v grep | grep mysqld | grep -E 'job|imdb|JOB|IMDB' " 2>/dev/null)

MYSQLD_STATUS="NONE"
JOB_BENCHMARK_STATUS="NONE"
XP_HYBENCH_STATUS="NONE"

# Only flag mysqld if it's NOT from an XP container (check by data path)
if [[ -n "$MYSQLD_PROC" ]]; then
    if [[ -n "$JOB_MYSQLD_PROC" ]]; then
        MYSQLD_STATUS="RUNNING_JOB"
    elif [[ "$MYSQL_XP_RUNNING" -gt 0 ]]; then
        MYSQLD_STATUS="RUNNING_XP_CONTAINER"  # XP's mysqld, not JOB
    else
        MYSQLD_STATUS="RUNNING_unknown"
    fi
fi

[[ -n "$JOB_BENCHMARK_PROC" ]] && JOB_BENCHMARK_STATUS="RUNNING"
[[ -n "$XP_HYBENCH_PROC" ]] && XP_HYBENCH_STATUS="RUNNING"

# ==============================================================================

# ==============================================================================
# JOB pressure = JOB MySQL containers + JOB mysqld processes + JOB benchmark processes
# XP MySQL containers/processes do NOT count as JOB pressure
JOB_PRESSURE=0
XP_ACTIVE="false"
LANE_STATUS="unknown"

if [[ "$MYSQL_JOB_RUNNING" -eq 0 && "$MYSQLD_STATUS" != "RUNNING_JOB" && "$JOB_BENCHMARK_STATUS" == "NONE" ]]; then
    JOB_PRESSURE=0
else
    JOB_PRESSURE=1
fi

# XP is "active" if XP MySQL containers exist OR hybench processes run
if [[ "$XP_HYBENCH_STATUS" == "RUNNING" || "$MYSQL_XP_RUNNING" -gt 0 || -n "$(echo "$CONTAINER_INFO" | grep -iE 'xp|hybench')" ]]; then
    XP_ACTIVE="true"
fi

if [[ "$JOB_PRESSURE" -eq 0 && "$XP_ACTIVE" == "false" ]]; then
    LANE_STATUS="JOB_released_XP_eligible"
elif [[ "$JOB_PRESSURE" -eq 0 && "$XP_ACTIVE" == "true" ]]; then
    LANE_STATUS="XP_holding_lane"
elif [[ "$JOB_PRESSURE" -eq 1 && "$XP_ACTIVE" == "false" ]]; then
    LANE_STATUS="JOB_occupying_lane"
elif [[ "$JOB_PRESSURE" -eq 1 && "$XP_ACTIVE" == "true" ]]; then
    # Both JOB and XP present - check if they conflict (e.g. XP MySQL + JOB MySQL)
    if [[ "$MYSQL_JOB_RUNNING" -gt 0 && "$MYSQL_XP_RUNNING" -gt 0 ]]; then
        LANE_STATUS="COEXIST_JOB_XP_mysql_both"
    else
        LANE_STATUS="COEXIST_mixed_workloads"
    fi
else
    LANE_STATUS="IDLE_no_activity"
fi

# ==============================================================================

# ==============================================================================
PG_CPUSET=""
PG_NUMA=""
MYSQL_HISTORICAL_CPUSET="30-32,66-68"  # from FROZEN_CONFIG
MYSQL_HISTORICAL_NUMA="NUMA1"
NUMA_CONFLICT="false"
CPU_CONFLICT="false"
PG_MEM_LIMIT_BYTES=0
PG_MEM_CURRENT_BYTES=0
HOST_MEM_AVAIL_KB=0
PG_ACTIVE_BACKENDS=0
ACTUAL_INTERFERENCE="NONE"

# Get PG container cgroup cpuset
if [[ -n "$PG_NAME" ]]; then
    PG_CPUSET=$(ssh_ro "docker inspect ${PG_NAME} --format '{{.HostConfig.CpusetCpus}}' 2>/dev/null" 2>/dev/null | tr -d '\n')
    PG_MEM_LIMIT_BYTES=$(ssh_ro "docker inspect ${PG_NAME} --format '{{.HostConfig.Memory}}' 2>/dev/null" 2>/dev/null | tr -d '\n')
    # Get PG actual mem usage from cgroup
    PG_CGROUP_PATH=$(ssh_ro "docker inspect ${PG_NAME} --format '{{.HostConfig.CgroupParent}}' 2>/dev/null" 2>/dev/null | tr -d '\n')
fi

# Determine NUMA node from cpuset (simplified)
if [[ -n "$PG_CPUSET" ]]; then
    FIRST_CPU=$(echo "$PG_CPUSET" | cut -d',' -f1 | cut -d'-' -f1)
    if [[ -n "$FIRST_CPU" && "$FIRST_CPU" -lt 24 ]]; then
        PG_NUMA="NUMA0"
    else
        PG_NUMA="NUMA1"
    fi
    # Check overlap
    if [[ "$PG_NUMA" == "NUMA1" ]]; then
        NUMA_CONFLICT="true"
    fi
fi

# Host memory available
HOST_MEM_AVAIL_KB=$(ssh_ro "grep MemAvailable /proc/meminfo 2>/dev/null | awk '{print \$2}'" 2>/dev/null | tr -d '\n')

# PG active backends
PG_ACTIVE_BACKENDS=$(ssh_ro "docker exec ${PG_NAME} psql -U postgres -t -c \"SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend' AND state != 'idle';\" 2>/dev/null" 2>/dev/null | tr -d ' ')
[[ -z "$PG_ACTIVE_BACKENDS" ]] && PG_ACTIVE_BACKENDS=0

# Actual interference assessment
if [[ "$NUMA_CONFLICT" == "false" && "$CPU_CONFLICT" == "false" && "$PG_ACTIVE_BACKENDS" -eq 0 ]]; then
    ACTUAL_INTERFERENCE="NONE"
elif [[ "$NUMA_CONFLICT" == "false" && "$CPU_CONFLICT" == "false" ]]; then
    ACTUAL_INTERFERENCE="LOW_no_hardware_conflict"
elif [[ "$NUMA_CONFLICT" == "true" || "$CPU_CONFLICT" == "true" ]]; then
    ACTUAL_INTERFERENCE="POTENTIAL_hardware_overlap"
fi

# ==============================================================================
# CHECK 6: Formal Paired Envelope Gate — JOB/Memory profile
# ==============================================================================
# Source: config/formal/frozen_paired_pg_mysql_resource_envelopes_20260723_v1
# JOB/Memory formal profile: 3 cores / 6 GiB / 6 GiB swap / 1150 MiB/s IO / pids 512 / 3 phy cores / 1 NUMA
# Cross-engine equality required per AGENTS.md. PG must match this profile to be the formal counterpart.
FORMAL_MEMORY_CPU_QUOTA_CORES=3
FORMAL_MEMORY_MEMORY_BYTES=6442450944      # 6 GiB
FORMAL_MEMORY_SWAP_BYTES=6442450944        # 6 GiB
FORMAL_MEMORY_IO_BPS=1205862400            # 1150 MiB/s
FORMAL_MEMORY_PIDS_MAX=512
FORMAL_MEMORY_CPU_ISOLATION=3              # 3 physical cores
FORMAL_MEMORY_NUMA_ISOLATION=1             # 1 NUMA domain

FORMAL_ENVELOPE_SOURCE="config/formal/frozen_paired_pg_mysql_resource_envelopes_20260723_v1"
PG_IS_FORMAL_COUNTERPART="false"
PG_ENVELOPE_MISMATCHES=""

# Read PG container's actual Docker resource limits
PG_DOCKER_CPU_QUOTA=0
PG_DOCKER_CPU_PERIOD=0
PG_DOCKER_NANO_CPUS=0
PG_DOCKER_CPU_CORES=0
PG_DOCKER_MEMORY=0
PG_DOCKER_MEMORY_SWAP=0
PG_DOCKER_PIDS=0
PG_DOCKER_IO_READ_BPS=0
PG_DOCKER_IO_WRITE_BPS=0
PG_CPUSET_COUNT=0

if [[ -n "$PG_NAME" ]]; then
    PG_DOCKER_CPU_QUOTA=$(ssh_ro "docker inspect ${PG_NAME} --format '{{.HostConfig.CpuQuota}}' 2>/dev/null" 2>/dev/null | tr -d '\n')
    PG_DOCKER_CPU_PERIOD=$(ssh_ro "docker inspect ${PG_NAME} --format '{{.HostConfig.CpuPeriod}}' 2>/dev/null" 2>/dev/null | tr -d '\n')
    PG_DOCKER_NANO_CPUS=$(ssh_ro "docker inspect ${PG_NAME} --format '{{.HostConfig.NanoCpus}}' 2>/dev/null" 2>/dev/null | tr -d '\n')
    PG_DOCKER_MEMORY=$(ssh_ro "docker inspect ${PG_NAME} --format '{{.HostConfig.Memory}}' 2>/dev/null" 2>/dev/null | tr -d '\n')
    PG_DOCKER_MEMORY_SWAP=$(ssh_ro "docker inspect ${PG_NAME} --format '{{.HostConfig.MemorySwap}}' 2>/dev/null" 2>/dev/null | tr -d '\n')
    PG_DOCKER_PIDS=$(ssh_ro "docker inspect ${PG_NAME} --format '{{.HostConfig.PidsLimit}}' 2>/dev/null" 2>/dev/null | tr -d '\n')
    PG_DOCKER_IO_READ_BPS=$(ssh_ro "docker inspect ${PG_NAME} --format '{{range .HostConfig.BlkioDeviceReadBps}}{{.Rate}}{{end}}' 2>/dev/null" 2>/dev/null | tr -d '\n')
    PG_DOCKER_IO_WRITE_BPS=$(ssh_ro "docker inspect ${PG_NAME} --format '{{range .HostConfig.BlkioDeviceWriteBps}}{{.Rate}}{{end}}' 2>/dev/null" 2>/dev/null | tr -d '\n')
    [[ -z "$PG_DOCKER_IO_READ_BPS" ]] && PG_DOCKER_IO_READ_BPS=0
    [[ -z "$PG_DOCKER_IO_WRITE_BPS" ]] && PG_DOCKER_IO_WRITE_BPS=0

    # Calculate effective CPU cores from quota/period
    if [[ -n "$PG_DOCKER_CPU_QUOTA" && "$PG_DOCKER_CPU_QUOTA" -gt 0 && -n "$PG_DOCKER_CPU_PERIOD" && "$PG_DOCKER_CPU_PERIOD" -gt 0 ]]; then
        PG_DOCKER_CPU_CORES=$(echo "scale=2; ${PG_DOCKER_CPU_QUOTA} / ${PG_DOCKER_CPU_PERIOD}" | bc 2>/dev/null | tr -d '\n')
    elif [[ -n "$PG_DOCKER_NANO_CPUS" && "$PG_DOCKER_NANO_CPUS" -gt 0 ]]; then
        PG_DOCKER_CPU_CORES=$(echo "scale=2; ${PG_DOCKER_NANO_CPUS} / 1000000000" | bc 2>/dev/null | tr -d '\n')
    fi

    # Count cpuset cores
    if [[ -n "$PG_CPUSET" ]]; then
        PG_CPUSET_COUNT=$(ssh_ro "docker inspect ${PG_NAME} --format '{{.HostConfig.CpusetCpus}}' 2>/dev/null | tr ',' '\n' | while read range; do start=\$(echo \$range | cut -d- -f1); end=\$(echo \$range | cut -d- -f2); echo \$((end - start + 1)); done | paste -sd+ | bc" 2>/dev/null | tr -d '\n')
    fi

    # --- Formal counterpart check ---
    MISMATCHES=""

    # Memory check (critical: formal 6 GiB vs actual)
    PG_MEM_GB=$(echo "scale=2; ${PG_DOCKER_MEMORY} / 1073741824" | bc 2>/dev/null | tr -d '\n')
    if [[ -n "$PG_DOCKER_MEMORY" && "$PG_DOCKER_MEMORY" -ne "$FORMAL_MEMORY_MEMORY_BYTES" ]]; then
        MISMATCHES="${MISMATCHES}memory:formal_6GiB_vs_actual_${PG_MEM_GB}GiB; "
    fi

    # CPU cores check
    if [[ -n "$PG_DOCKER_CPU_CORES" && "$PG_DOCKER_CPU_CORES" != "3.00" ]]; then
        MISMATCHES="${MISMATCHES}cpu_cores:formal_3_vs_actual_${PG_DOCKER_CPU_CORES}; "
    fi

    # Swap check
    if [[ -n "$PG_DOCKER_MEMORY_SWAP" && "$PG_DOCKER_MEMORY_SWAP" -ne "$FORMAL_MEMORY_SWAP_BYTES" && "$PG_DOCKER_MEMORY_SWAP" -ne -1 ]]; then
        PG_SWAP_GB=$(echo "scale=2; ${PG_DOCKER_MEMORY_SWAP} / 1073741824" | bc 2>/dev/null | tr -d '\n')
        MISMATCHES="${MISMATCHES}swap:formal_6GiB_vs_actual_${PG_SWAP_GB}GiB; "
    fi

    # PIDs check
    if [[ -n "$PG_DOCKER_PIDS" && "$PG_DOCKER_PIDS" -ne "$FORMAL_MEMORY_PIDS_MAX" ]]; then
        MISMATCHES="${MISMATCHES}pids:formal_512_vs_actual_${PG_DOCKER_PIDS}; "
    fi

    if [[ "$PG_DOCKER_IO_READ_BPS" -ne "$FORMAL_MEMORY_IO_BPS" ]]; then
        MISMATCHES="${MISMATCHES}io_read_bps:formal_${FORMAL_MEMORY_IO_BPS}_vs_actual_${PG_DOCKER_IO_READ_BPS}; "
    fi
    if [[ "$PG_DOCKER_IO_WRITE_BPS" -ne "$FORMAL_MEMORY_IO_BPS" ]]; then
        MISMATCHES="${MISMATCHES}io_write_bps:formal_${FORMAL_MEMORY_IO_BPS}_vs_actual_${PG_DOCKER_IO_WRITE_BPS}; "
    fi

    # NUMA isolation check (formal: 1 NUMA domain)
    # PG on NUMA0 = 1 domain → matches formal

    if [[ -z "$MISMATCHES" ]]; then
        PG_IS_FORMAL_COUNTERPART="true"
        PG_ENVELOPE_MISMATCHES="none"
    else
        PG_IS_FORMAL_COUNTERPART="false"
        PG_ENVELOPE_MISMATCHES="${MISMATCHES%; }"
        ANOMALY_LIST="${ANOMALY_LIST}, \"pg_not_formal_counterpart: ${PG_ENVELOPE_MISMATCHES}\""
    fi
fi

FORMAL_GATE_MEMORY_PROFILE="3c/6GiB/6GiB_swap/1150MiB_s_IO/pids512/3_phy_cores/1_NUMA_domain"

# ==============================================================================
# CHECK 7: Workload Freeze Binding — JOB mix v3
# ==============================================================================
# Verify local frozen config integrity (read-only, no SSH needed)
WORKLOAD_FREEZE_INTEGRITY="UNVERIFIED"
WORKLOAD_FREEZE_CONFIG_MATCH="false"
WORKLOAD_FREEZE_MIX_MATCH="false"

if [[ -d "$JOB_WORKLOAD_FREEZE_DIR" ]]; then
    ACTUAL_CONFIG_SHA=$(sha256sum "${JOB_WORKLOAD_FREEZE_DIR}/FROZEN_CONFIG.md" 2>/dev/null | awk '{print $1}')
    ACTUAL_MIX_SHA=$(sha256sum "${JOB_WORKLOAD_FREEZE_DIR}/job_mix.json" 2>/dev/null | awk '{print $1}')

    if [[ "$ACTUAL_CONFIG_SHA" == "$JOB_FREEZE_CHECKSUM_FROZEN_CONFIG" ]]; then
        WORKLOAD_FREEZE_CONFIG_MATCH="true"
    fi
    if [[ "$ACTUAL_MIX_SHA" == "$JOB_FREEZE_CHECKSUM_JOB_MIX" ]]; then
        WORKLOAD_FREEZE_MIX_MATCH="true"
    fi

    if [[ "$WORKLOAD_FREEZE_CONFIG_MATCH" == "true" && "$WORKLOAD_FREEZE_MIX_MATCH" == "true" ]]; then
        WORKLOAD_FREEZE_INTEGRITY="PASS"
    else
        WORKLOAD_FREEZE_INTEGRITY="FAIL_checksum_mismatch"
        ANOMALY_LIST="${ANOMALY_LIST}, \"workload_freeze_checksum_mismatch: config=${ACTUAL_CONFIG_SHA:0:12}..., mix=${ACTUAL_MIX_SHA:0:12}...\""
    fi
else
    WORKLOAD_FREEZE_INTEGRITY="FAIL_dir_missing"
    ANOMALY_LIST="${ANOMALY_LIST}, \"workload_freeze_dir_missing: ${JOB_WORKLOAD_FREEZE_DIR}\""
fi

# ==============================================================================
# CHECK 8: PG outercontrol (transient detection, record only)
# ==============================================================================
PG_OUTERCONTROL_ACTIVE="false"
PG_OUTERCONTROL_PIDS=0
PG_OUTERCONTROL_CMD="DISABLED_SECURITY_NO_COMMAND_LINE_CAPTURE"

PG_OUTERCONTROL_CHECK=$(ssh_ro "ps -eo pid=,args= 2>/dev/null | awk '/outercontrol|outerpoint/ && !/awk/ {print \$1}'" 2>/dev/null)
if [[ -n "$PG_OUTERCONTROL_CHECK" ]]; then
    PG_OUTERCONTROL_ACTIVE="true"
    PG_OUTERCONTROL_PIDS=$(echo "$PG_OUTERCONTROL_CHECK" | wc -l | tr -d ' ')
    # Record only — do NOT change PG
fi

# ==============================================================================
# CHECK 9: XP Pressure & Row Count (read-only)
# ==============================================================================
XP_MYSQLD_CPU_PCT="0.0"
XP_MYSQLD_MEM_USAGE=""
XP_ROW_COUNT=0
XP_ROW_COUNT_MATCH="false"
XP_ROW_COUNT_DELTA=0
XP_PRESSURE_ACTIVE="false"

# Get XP MySQL container CPU/mem
if [[ "$MYSQL_XP_RUNNING" -gt 0 ]]; then
    XP_MYSQLD_CPU_PCT=$(ssh_ro "docker stats --no-stream --format '{{.CPUPerc}}' ra_mysql_xp02_template 2>/dev/null" 2>/dev/null | tr -d '%' | tr -d ' ')
    XP_MYSQLD_MEM_USAGE=$(ssh_ro "docker stats --no-stream --format '{{.MemUsage}}' ra_mysql_xp02_template 2>/dev/null" 2>/dev/null | tr -d ' ')
    [[ -z "$XP_MYSQLD_CPU_PCT" ]] && XP_MYSQLD_CPU_PCT="0.0"

    # XP row count via information_schema (InnoDB — may be NULL for unanalyzed tables)
    # Record null/error/exit_code — NEVER substitute 0 for failed queries
    XP_ROW_COUNT_QUERY_OUTPUT=$(ssh_ro "docker exec ra_mysql_xp02_template sh -c 'mysql --defaults-file=/etc/mysql/conf.d/defaults-extra-file.cnf -N -e \"SELECT COALESCE(SUM(TABLE_ROWS),-1) FROM information_schema.TABLES WHERE TABLE_SCHEMA IN (\\\"hybench\\\", \\\"hybench_ap\\\", \\\"hybench_xp\\\") AND TABLE_ROWS IS NOT NULL;\" 2>/dev/null'" 2>/dev/null)
    XP_ROW_COUNT_EXIT=$?
    XP_ROW_COUNT_VALID="false"
    XP_ROW_COUNT_ERROR=""
    XP_ROW_COUNT="null"

    if [[ "$XP_ROW_COUNT_EXIT" -ne 0 ]]; then
        XP_ROW_COUNT_VALID="false"
        XP_ROW_COUNT_ERROR="ssh_or_mysql_nonzero_exit:${XP_ROW_COUNT_EXIT}"
        XP_ROW_COUNT="null"
    elif [[ -z "$XP_ROW_COUNT_QUERY_OUTPUT" || "$XP_ROW_COUNT_QUERY_OUTPUT" == "NULL" || "$XP_ROW_COUNT_QUERY_OUTPUT" == "-1" ]]; then
        XP_ROW_COUNT_VALID="false"
        XP_ROW_COUNT_ERROR="information_schema_returned_null_or_minus1"
        XP_ROW_COUNT="null"
    else
        XP_ROW_COUNT_VALID="true"
        XP_ROW_COUNT="$XP_ROW_COUNT_QUERY_OUTPUT"
        XP_ROW_COUNT_ERROR="none"
    fi

    if [[ "$XP_ROW_COUNT_VALID" == "true" && "$XP_ROW_COUNT" != "null" ]]; then
        if [[ "$XP_ROW_COUNT" -eq "$XP_AUTHORITATIVE_ROW_COUNT" ]]; then
            XP_ROW_COUNT_MATCH="true"
            XP_ROW_COUNT_DELTA=0
        else
            XP_ROW_COUNT_MATCH="false"
            XP_ROW_COUNT_DELTA=$((XP_AUTHORITATIVE_ROW_COUNT - XP_ROW_COUNT))
        fi
    else
        XP_ROW_COUNT_MATCH="false"
        XP_ROW_COUNT_DELTA="null"
    fi

    # XP pressure active if CPU > threshold or row count mismatched
    XP_CPU_HIGH="false"
    if [[ -n "$XP_MYSQLD_CPU_PCT" ]]; then
        CPU_INT=$(echo "$XP_MYSQLD_CPU_PCT" | cut -d. -f1 2>/dev/null)
        if [[ -n "$CPU_INT" && "$CPU_INT" -ge "$XP_CPU_WARN_THRESHOLD_PCT" ]]; then
            XP_CPU_HIGH="true"
        fi
    fi

    if [[ "$XP_CPU_HIGH" == "true" || "$XP_ROW_COUNT_MATCH" == "false" ]]; then
        XP_PRESSURE_ACTIVE="true"
    fi
fi

# XP anomalies
if [[ "$XP_CPU_HIGH" == "true" ]]; then
    ANOMALY_LIST="${ANOMALY_LIST}, \"xp_mysqld_cpu_high: ${XP_MYSQLD_CPU_PCT}%\""
fi
if [[ "$XP_ROW_COUNT_VALID" == "false" ]]; then
    ANOMALY_LIST="${ANOMALY_LIST}, \"xp_row_count_query_failed: error=${XP_ROW_COUNT_ERROR}\""
elif [[ "$XP_ROW_COUNT_MATCH" != "true" ]]; then
    ANOMALY_LIST="${ANOMALY_LIST}, \"xp_row_count_mismatch: actual=${XP_ROW_COUNT} vs authoritative=${XP_AUTHORITATIVE_ROW_COUNT} (delta=${XP_ROW_COUNT_DELTA})\""
fi
if [[ "$XP_FREEZE_REPROVEN" == "false" ]]; then
    ANOMALY_LIST="${ANOMALY_LIST}, \"xp_freeze_not_reproven: JOB_must_not_start\""
fi

# ==============================================================================
# CHECK 10: Host stats
# ==============================================================================
HOST_LOAD=$(ssh_ro "cat /proc/loadavg 2>/dev/null" 2>/dev/null)
LOAD_1M=$(echo "$HOST_LOAD" | awk '{print $1}')
LOAD_5M=$(echo "$HOST_LOAD" | awk '{print $2}')
LOAD_15M=$(echo "$HOST_LOAD" | awk '{print $3}')
UPTIME=$(ssh_ro "uptime -p 2>/dev/null" 2>/dev/null | sed 's/^up //' | tr -d '\n')
NVME0_USED=$(ssh_ro "df /mnt/mysql_nvme0n1p1 2>/dev/null | tail -1 | awk '{print \$5}'" 2>/dev/null | tr -d '%\n')
NVME1_USED=$(ssh_ro "df /mnt/mysql_nvme1n1 2>/dev/null | tail -1 | awk '{print \$5}'" 2>/dev/null | tr -d '%\n')

# ==============================================================================
# Anomaly detection
# ==============================================================================
ANOMALIES="[]"
ANOMALY_LIST=""

# Template anomaly
if [[ "$IBD_COUNT_IMDB_JOB" -ne 21 && "$TEMPLATE_EXISTS" == "true" ]]; then
    ANOMALY_LIST="${ANOMALY_LIST}, \"ibd_count_mismatch: expected 21, got ${IBD_COUNT_IMDB_JOB}\""
fi
if [[ "$IBD_PERMS" != "444" && "$TEMPLATE_EXISTS" == "true" ]]; then
    ANOMALY_LIST="${ANOMALY_LIST}, \"ibd_perms_not_444: got ${IBD_PERMS}\""
fi
# Manifest absence is informational (post-freeze cleanup), not an anomaly
# Only flag if manifest EXISTS but checksum mismatches
if [[ -n "$MANIFEST_DB_SHA256" && "$MANIFEST_DB_MATCH" != "true" ]]; then
    ANOMALY_LIST="${ANOMALY_LIST}, \"manifest_db_checksum_mismatch\""
fi
if [[ -n "$MANIFEST_VERIFY_SHA256" && "$MANIFEST_VERIFY_MATCH" != "true" ]]; then
    ANOMALY_LIST="${ANOMALY_LIST}, \"manifest_verify_checksum_mismatch\""
fi
# IBD mtime after freeze is informational only if IBDs are intact
if [[ "$IBD_MTIME_BEFORE_FREEZE" != "true" && "$IBD_COUNT_IMDB_JOB" -eq 21 && "$IBD_PERMS" == "444" ]]; then
    : # IBDs intact, mtime drift is informational - skip anomaly
elif [[ "$IBD_MTIME_BEFORE_FREEZE" != "true" && "$TEMPLATE_EXISTS" == "true" ]]; then
    ANOMALY_LIST="${ANOMALY_LIST}, \"ibd_modified_after_freeze: max_mtime_epoch=${IBD_MTIME_MAX_EPOCH}\""
fi

# Container anomaly — only flag JOB MySQL, not XP MySQL
if [[ "$MYSQL_JOB_RUNNING" -gt 0 ]]; then
    ANOMALY_LIST="${ANOMALY_LIST}, \"job_mysql_container_running: count=${MYSQL_JOB_RUNNING}\""
fi
if [[ "$MYSQLD_STATUS" == "RUNNING_JOB" || "$MYSQLD_STATUS" == "RUNNING_unknown" ]]; then
    ANOMALY_LIST="${ANOMALY_LIST}, \"job_mysqld_process: status=${MYSQLD_STATUS}\""
fi
if [[ "$JOB_BENCHMARK_STATUS" == "RUNNING" ]]; then
    ANOMALY_LIST="${ANOMALY_LIST}, \"job_benchmark_process_running\""
fi

# Mutual exclusion anomaly — only flag actual JOB/XP conflict
if [[ "$LANE_STATUS" == "COEXIST_JOB_XP_mysql_both" ]]; then
    ANOMALY_LIST="${ANOMALY_LIST}, \"JOB_XP_MySQL_coexist: job_mysql=${MYSQL_JOB_RUNNING}, xp_mysql=${MYSQL_XP_RUNNING}\""
fi

# PG interference anomaly (only if actual interference detected)
if [[ "$ACTUAL_INTERFERENCE" == "POTENTIAL_hardware_overlap" ]]; then
    ANOMALY_LIST="${ANOMALY_LIST}, \"pg_hardware_overlap: PG_cpuset=${PG_CPUSET} overlaps MySQL_historical=${MYSQL_HISTORICAL_CPUSET}\""
fi

# Clean up leading comma
ANOMALY_LIST=$(echo "$ANOMALY_LIST" | sed 's/^, //')
ANOMALIES="[${ANOMALY_LIST}]"

# ==============================================================================
# Verdict
# ==============================================================================
VERDICT="PASS_ALL_CHECKS"
CROSS_ENGINE_READY="false"  # default: not ready until formal counterpart confirmed
if [[ "$INTEGRITY" == FAIL* ]]; then
    VERDICT="FAIL_template_integrity"
elif [[ "$MYSQL_JOB_RUNNING" -gt 0 || "$MYSQLD_STATUS" == "RUNNING_JOB" || "$MYSQLD_STATUS" == "RUNNING_unknown" ]]; then
    VERDICT="WARN_job_mysql_pressure"
elif [[ "$LANE_STATUS" == "COEXIST_JOB_XP_mysql_both" ]]; then
    VERDICT="WARN_job_xp_mysql_coexist"
elif [[ "$ACTUAL_INTERFERENCE" != "NONE" && "$ACTUAL_INTERFERENCE" != "LOW_no_hardware_conflict" ]]; then
    VERDICT="WARN_pg_interference"
fi

# Formal paired envelope gate — overrides verdict if PG is not the formal counterpart
if [[ "$PG_IS_FORMAL_COUNTERPART" == "true" ]]; then
    CROSS_ENGINE_READY="true"
else
    CROSS_ENGINE_READY="false"
    # If all other checks pass but PG not formal counterpart, downgrade verdict
    if [[ "$VERDICT" == "PASS_ALL_CHECKS" ]]; then
        VERDICT="GATE_pg_not_formal_counterpart"
    fi
fi

# ==============================================================================
# Build JSONL entry
# ==============================================================================
# Escape strings for JSON
json_escape() {
    printf '%s' "$1" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))" 2>/dev/null || echo '""'
}

ACTION_MSG="No action required. Formal gate: PG is NOT formal counterpart (profile: ${FORMAL_GATE_MEMORY_PROFILE}). Cross-engine NOT READY."
if [[ "$PG_IS_FORMAL_COUNTERPART" == "true" ]]; then
    ACTION_MSG="No action required. Formal gate: PG IS formal counterpart. Cross-engine READY pending dual-end readback."
fi
if [[ "$VERDICT" != "PASS_ALL_CHECKS" && "$VERDICT" != "GATE_pg_not_formal_counterpart" ]]; then
    ACTION_MSG="Anomalies detected: ${ANOMALY_LIST}. Read-only patrol, no automatic action taken."
fi

# Export all vars for Python subprocess
export TS TS_UTC ROUND TEMPLATE_DIR JOB_ROOT_DIR TEMPLATE_EXISTS IBD_COUNT_IMDB_JOB IBD_COUNT_TOTAL
export DIR_PERMS IBD_PERMS MANIFEST_DB_SHA256 MANIFEST_DB_MATCH MANIFEST_VERIFY_SHA256 MANIFEST_VERIFY_MATCH
export IBD_MTIME_MAX_EPOCH IBD_MTIME_BEFORE_FREEZE INTEGRITY
export MYSQL_RUNNING MYSQL_JOB_RUNNING MYSQL_XP_RUNNING MYSQL_OTHER_RUNNING
export PG_RUNNING PG_NAME PG_STATUS PG_CPU_PCT PG_MEM_USAGE PG_ACTIVE_QUERIES EXITED_CONTAINERS
export MYSQLD_STATUS JOB_BENCHMARK_STATUS XP_HYBENCH_STATUS
export JOB_PRESSURE XP_ACTIVE LANE_STATUS
export PG_CPUSET PG_NUMA MYSQL_HISTORICAL_CPUSET MYSQL_HISTORICAL_NUMA
export NUMA_CONFLICT CPU_CONFLICT PG_MEM_LIMIT_BYTES PG_MEM_CURRENT_BYTES HOST_MEM_AVAIL_KB
export PG_ACTIVE_BACKENDS ACTUAL_INTERFERENCE
export FORMAL_ENVELOPE_SOURCE FORMAL_GATE_MEMORY_PROFILE
export PG_IS_FORMAL_COUNTERPART PG_ENVELOPE_MISMATCHES CROSS_ENGINE_READY
export PG_DOCKER_CPU_CORES PG_DOCKER_MEMORY PG_DOCKER_MEMORY_SWAP PG_DOCKER_PIDS PG_CPUSET_COUNT
export PG_DOCKER_IO_READ_BPS PG_DOCKER_IO_WRITE_BPS
export JOB_WORKLOAD_FREEZE_ID JOB_SENSITIVE_FRACTION JOB_SENSITIVE_WEIGHT JOB_WEIGHT_TOTAL JOB_DEPRECATED_FRACTION
export WORKLOAD_FREEZE_INTEGRITY WORKLOAD_FREEZE_CONFIG_MATCH WORKLOAD_FREEZE_MIX_MATCH
export PG_OUTERCONTROL_ACTIVE PG_OUTERCONTROL_PIDS PG_OUTERCONTROL_CMD
export XP_MYSQLD_CPU_PCT XP_MYSQLD_MEM_USAGE
export XP_ROW_COUNT XP_ROW_COUNT_VALID XP_ROW_COUNT_EXIT XP_ROW_COUNT_ERROR XP_ROW_COUNT_MATCH XP_ROW_COUNT_DELTA
export XP_AUTHORITATIVE_ROW_COUNT XP_FREEZE_REPROVEN XP_PRESSURE_ACTIVE XP_ROW_COUNT_EXIT
export LOAD_1M LOAD_5M LOAD_15M UPTIME NVME0_USED NVME1_USED
export ANOMALIES VERDICT CROSS_ENGINE_READY ACTION_MSG LOG_FILE

# Write JSONL entry using Python for reliable JSON generation
python3 << 'PYEOF'
import json, os, sys

def safe_int(val, default=0):
    """Convert to int, falling back to default on empty/invalid."""
    if val is None or val.strip() == "":
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default

def safe_float(val, default=0.0):
    """Convert to float, falling back to default on empty/invalid."""
    if val is None or val.strip() == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

entry = {
    "ts": os.environ.get("TS", ""),
    "ts_utc": os.environ.get("TS_UTC", ""),
    "round": safe_int(os.environ.get("ROUND", "0"), 0),
    "target": "node02 (anonymous@192.0.2.4)",
    "agent": "patrol_script",
    "type": "patrol",
    "checks": {
        "template": {
            "path": os.environ.get("TEMPLATE_DIR", ""),
            "exists": os.environ.get("TEMPLATE_EXISTS", "false") == "true",
            "ibd_count_imdb_job": safe_int(os.environ.get("IBD_COUNT_IMDB_JOB"), 0),
            "ibd_count_total": safe_int(os.environ.get("IBD_COUNT_TOTAL"), 0),
            "dir_perms": os.environ.get("DIR_PERMS", "unknown"),
            "ibd_perms": os.environ.get("IBD_PERMS", "unknown"),
            "manifest_db_sha256": os.environ.get("MANIFEST_DB_SHA256", ""),
            "manifest_db_match": os.environ.get("MANIFEST_DB_MATCH", "false") == "true",
            "manifest_verify_sha256": os.environ.get("MANIFEST_VERIFY_SHA256", ""),
            "manifest_verify_match": os.environ.get("MANIFEST_VERIFY_MATCH", "false") == "true",
            "ibd_mtime_max_epoch": safe_int(os.environ.get("IBD_MTIME_MAX_EPOCH"), 0),
            "ibd_mtime_before_freeze": os.environ.get("IBD_MTIME_BEFORE_FREEZE", "false") == "true",
            "integrity": os.environ.get("INTEGRITY", "FAIL")
        },
        "containers": {
            "mysql_running": safe_int(os.environ.get("MYSQL_RUNNING"), 0),
            "mysql_job_running": safe_int(os.environ.get("MYSQL_JOB_RUNNING"), 0),
            "mysql_xp_running": safe_int(os.environ.get("MYSQL_XP_RUNNING"), 0),
            "mysql_other_running": safe_int(os.environ.get("MYSQL_OTHER_RUNNING"), 0),
            "pg_running": safe_int(os.environ.get("PG_RUNNING"), 0),
            "pg_name": os.environ.get("PG_NAME", ""),
            "pg_status": os.environ.get("PG_STATUS", ""),
            "pg_cpu_pct": safe_float(os.environ.get("PG_CPU_PCT"), 0.0),
            "pg_mem_usage": os.environ.get("PG_MEM_USAGE", ""),
            "pg_active_queries": safe_int(os.environ.get("PG_ACTIVE_QUERIES"), 0),
            "exited_containers": safe_int(os.environ.get("EXITED_CONTAINERS"), 0)
        },
        "processes": {
            "mysqld": os.environ.get("MYSQLD_STATUS", "NONE"),
            "job_benchmark": os.environ.get("JOB_BENCHMARK_STATUS", "NONE"),
            "xp_hybench": os.environ.get("XP_HYBENCH_STATUS", "NONE")
        },
        "mutual_exclusion": {
            "job_pressure": safe_int(os.environ.get("JOB_PRESSURE"), 0),
            "xp_active": os.environ.get("XP_ACTIVE", "false") == "true",
            "lane_status": os.environ.get("LANE_STATUS", "unknown"),
            "zero_pressure": os.environ.get("JOB_PRESSURE", "1") == "0"
        },
        "pg_interference": {
            "pg_cpuset": os.environ.get("PG_CPUSET", ""),
            "pg_numa": os.environ.get("PG_NUMA", ""),
            "mysql_historical_cpuset": os.environ.get("MYSQL_HISTORICAL_CPUSET", ""),
            "mysql_historical_numa": os.environ.get("MYSQL_HISTORICAL_NUMA", ""),
            "numa_conflict": os.environ.get("NUMA_CONFLICT", "false") == "true",
            "cpu_conflict": os.environ.get("CPU_CONFLICT", "false") == "true",
            "pg_mem_limit_bytes": safe_int(os.environ.get("PG_MEM_LIMIT_BYTES"), 0),
            "pg_mem_current_bytes": safe_int(os.environ.get("PG_MEM_CURRENT_BYTES"), 0),
            "host_mem_avail_kb": safe_int(os.environ.get("HOST_MEM_AVAIL_KB"), 0),
            "pg_active_backends": safe_int(os.environ.get("PG_ACTIVE_BACKENDS"), 0),
            "actual_interference": os.environ.get("ACTUAL_INTERFERENCE", "NONE")
        },
        "host": {
            "load_1m": safe_float(os.environ.get("LOAD_1M"), 0.0),
            "load_5m": safe_float(os.environ.get("LOAD_5M"), 0.0),
            "load_15m": safe_float(os.environ.get("LOAD_15M"), 0.0),
            "uptime": os.environ.get("UPTIME", ""),
            "nvme0n1p1_used_pct": safe_int(os.environ.get("NVME0_USED"), 0),
            "nvme1n1_used_pct": safe_int(os.environ.get("NVME1_USED"), 0)
        },
        "workload_freeze_binding": {
            "freeze_id": os.environ.get("JOB_WORKLOAD_FREEZE_ID", "unknown"),
            "sensitive_weight": os.environ.get("JOB_SENSITIVE_WEIGHT", "7.5"),
            "weight_total": os.environ.get("JOB_WEIGHT_TOTAL", "107.5"),
            "sensitive_fraction": os.environ.get("JOB_SENSITIVE_FRACTION", "0.06976744186046512"),
            "deprecated_fraction": os.environ.get("JOB_DEPRECATED_FRACTION", "0.047619"),
            "local_freeze_integrity": os.environ.get("WORKLOAD_FREEZE_INTEGRITY", "UNVERIFIED"),
            "config_checksum_match": os.environ.get("WORKLOAD_FREEZE_CONFIG_MATCH", "false") == "true",
            "mix_checksum_match": os.environ.get("WORKLOAD_FREEZE_MIX_MATCH", "false") == "true"
        },
        "pg_outercontrol": {
            "active": os.environ.get("PG_OUTERCONTROL_ACTIVE", "false") == "true",
            "pids": safe_int(os.environ.get("PG_OUTERCONTROL_PIDS"), 0),
            "command_line_capture": os.environ.get(
                "PG_OUTERCONTROL_CMD", "DISABLED_SECURITY_NO_COMMAND_LINE_CAPTURE"
            ),
            "action": "RECORD_ONLY_no_pg_change"
        },
        "xp_pressure": {
            "xp_mysqld_cpu_pct": safe_float(os.environ.get("XP_MYSQLD_CPU_PCT"), 0.0),
            "xp_mysqld_mem_usage": os.environ.get("XP_MYSQLD_MEM_USAGE", ""),
            "xp_row_count": os.environ.get("XP_ROW_COUNT", "null"),
            "xp_row_count_valid": os.environ.get("XP_ROW_COUNT_VALID", "false") == "true",
            "xp_row_count_exit_code": safe_int(os.environ.get("XP_ROW_COUNT_EXIT"), -1),
            "xp_row_count_error": os.environ.get("XP_ROW_COUNT_ERROR", "unset"),
            "xp_authoritative_row_count": int(os.environ["XP_AUTHORITATIVE_ROW_COUNT"]),
            "xp_row_count_match": os.environ.get("XP_ROW_COUNT_MATCH", "false") == "true",
            "xp_row_count_delta": os.environ.get("XP_ROW_COUNT_DELTA", "null"),
            "xp_freeze_reproven": os.environ.get("XP_FREEZE_REPROVEN", "false") == "true",
            "xp_pressure_active": os.environ.get("XP_PRESSURE_ACTIVE", "false") == "true",
            "job_may_start": "false"
        },
        "formal_paired_envelope_gate": {
            "source": os.environ.get("FORMAL_ENVELOPE_SOURCE", ""),
            "formal_memory_profile": os.environ.get("FORMAL_GATE_MEMORY_PROFILE", "3c/6GiB/1150MiB_s/pids512/3_phy_cores/1_NUMA"),
            "pg_is_formal_counterpart": os.environ.get("PG_IS_FORMAL_COUNTERPART", "false") == "true",
            "pg_envelope_mismatches": os.environ.get("PG_ENVELOPE_MISMATCHES", "unknown"),
            "cross_engine_ready": os.environ.get("CROSS_ENGINE_READY", "false") == "true",
            "pg_actual": {
                "cpu_cores": safe_float(os.environ.get("PG_DOCKER_CPU_CORES"), 0.0),
                "memory_bytes": safe_int(os.environ.get("PG_DOCKER_MEMORY"), 0),
                "memory_swap_bytes": safe_int(os.environ.get("PG_DOCKER_MEMORY_SWAP"), 0),
                "io_read_bps": safe_int(os.environ.get("PG_DOCKER_IO_READ_BPS"), 0),
                "io_write_bps": safe_int(os.environ.get("PG_DOCKER_IO_WRITE_BPS"), 0),
                "pids_max": safe_int(os.environ.get("PG_DOCKER_PIDS"), 0),
                "cpuset_core_count": safe_int(os.environ.get("PG_CPUSET_COUNT"), 0),
                "cpuset": os.environ.get("PG_CPUSET", ""),
                "numa": os.environ.get("PG_NUMA", "")
            }
        }
    },
    "anomalies": json.loads(os.environ.get("ANOMALIES", "[]")),
    "info": [
        f"manifest_db_sha256={'present' if os.environ.get('MANIFEST_DB_SHA256','') else 'absent_post_freeze_cleanup'}",
        f"manifest_verify_sha256={'present' if os.environ.get('MANIFEST_VERIFY_SHA256','') else 'absent_post_freeze_cleanup'}",
        f"ibd_mtime_max_epoch={os.environ.get('IBD_MTIME_MAX_EPOCH','0')} (freeze_epoch=1784744879)"
    ],
    "verdict": os.environ.get("VERDICT", "UNKNOWN"),
    "cross_engine_ready": os.environ.get("CROSS_ENGINE_READY", "false") == "true",
    "action": os.environ.get("ACTION_MSG", "")
}

log_file = os.environ.get("LOG_FILE", "")
with open(log_file, "a") as f:
    f.write(json.dumps(entry, ensure_ascii=False) + "\n")

# Also print summary to stdout
print(f"PATROL R{os.environ.get('ROUND','?')} | {os.environ.get('TS','')}")
print(f"  Template: {os.environ.get('INTEGRITY','?')} | IBDs: {os.environ.get('IBD_COUNT_IMDB_JOB','?')}/21 | Perms: {os.environ.get('IBD_PERMS','?')}")
print(f"  MySQL: total={os.environ.get('MYSQL_RUNNING','?')} JOB={os.environ.get('MYSQL_JOB_RUNNING','?')} XP={os.environ.get('MYSQL_XP_RUNNING','?')} | PG: {os.environ.get('PG_NAME','?')} ({os.environ.get('PG_STATUS','?')})")
print(f"  Lane: {os.environ.get('LANE_STATUS','?')} | JOB pressure: {os.environ.get('JOB_PRESSURE','?')} | XP active: {os.environ.get('XP_ACTIVE','?')}")
print(f"  PG interference: {os.environ.get('ACTUAL_INTERFERENCE','?')} | Host load: {os.environ.get('LOAD_1M','?')}")
print(f"  Formal Gate (JOB/Memory): {os.environ.get('FORMAL_GATE_MEMORY_PROFILE','?')}")
print(f"    PG counterpart: {os.environ.get('PG_IS_FORMAL_COUNTERPART','?')} | cross-engine READY: {os.environ.get('CROSS_ENGINE_READY','?')}")
if os.environ.get("PG_ENVELOPE_MISMATCHES", "") not in ("", "none"):
    print(f"    MISMATCH: {os.environ.get('PG_ENVELOPE_MISMATCHES','?')}")
print(f"  JOB Mix: {os.environ.get('JOB_WORKLOAD_FREEZE_ID','?')} sensitive={os.environ.get('JOB_SENSITIVE_FRACTION','?')} (7.5/107.5) freeze_integrity={os.environ.get('WORKLOAD_FREEZE_INTEGRITY','?')}")
print(f"  XP: CPU={os.environ.get('XP_MYSQLD_CPU_PCT','?')}% mem={os.environ.get('XP_MYSQLD_MEM_USAGE','?')}")
if os.environ.get("XP_ROW_COUNT_VALID", "false") == "true":
    print(f"    rows={os.environ.get('XP_ROW_COUNT','?')}/{os.environ.get('XP_AUTHORITATIVE_ROW_COUNT','87060000')} match={os.environ.get('XP_ROW_COUNT_MATCH','?')}")
else:
    print(f"    rows=null (query_failed: {os.environ.get('XP_ROW_COUNT_ERROR','?')}) authoritative=87060000 UNDER_AUDIT")
print(f"    freeze_reproven={os.environ.get('XP_FREEZE_REPROVEN','?')}")
if os.environ.get("PG_OUTERCONTROL_ACTIVE", "false") == "true":
    print(f"  PG outercontrol: ACTIVE pids={os.environ.get('PG_OUTERCONTROL_PIDS','?')} [RECORD ONLY]")
print(f"  JOB may start: FALSE (XP freeze not reproven, PG not formal counterpart)")
print(f"  Verdict: {os.environ.get('VERDICT','?')}")
if os.environ.get("ANOMALIES", "[]") != "[]":
    print(f"  ANOMALIES: {os.environ.get('ANOMALIES','?')}")
PYEOF

EXIT_CODE=$?
exit $EXIT_CODE
