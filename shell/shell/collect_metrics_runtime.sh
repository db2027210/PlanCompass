#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  collect_metrics_runtime.sh \
    --container <name> \
    --output <jsonl_path> \
    --duration <seconds> \
    --interval <seconds> \
    --db-host <host> \
    --db-port <port> \
    --db-user <user> \
    --db-name <name> \
    --db-password <password> \
    [--db-engine <pg|mysql>] \
    [--disable-plan-tree] \
    [--plan-query-timeout-sec <seconds>]
USAGE
}

CONTAINER=""
OUT=""
DURATION_SEC=""
INTERVAL=""
DB_HOST=""
DB_PORT=""
DB_USER=""
DB_NAME=""
DB_PASSWORD=""
DB_ENGINE="pg"
ENABLE_PLAN_TREE=1
PLAN_QUERY_TIMEOUT_SEC=3

while [[ $# -gt 0 ]]; do
  case "$1" in
    --container) CONTAINER="$2"; shift 2 ;;
    --output) OUT="$2"; shift 2 ;;
    --duration) DURATION_SEC="$2"; shift 2 ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    --db-host) DB_HOST="$2"; shift 2 ;;
    --db-port) DB_PORT="$2"; shift 2 ;;
    --db-user) DB_USER="$2"; shift 2 ;;
    --db-name) DB_NAME="$2"; shift 2 ;;
    --db-password) DB_PASSWORD="$2"; shift 2 ;;
    --db-engine) DB_ENGINE="$2"; shift 2 ;;
    --disable-plan-tree) ENABLE_PLAN_TREE=0; shift ;;
    --plan-query-timeout-sec) PLAN_QUERY_TIMEOUT_SEC="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1"; usage; exit 1 ;;
  esac
done

if [ -z "$CONTAINER" ] || [ -z "$OUT" ] || [ -z "$DURATION_SEC" ] || [ -z "$INTERVAL" ] || \
   [ -z "$DB_HOST" ] || [ -z "$DB_PORT" ] || [ -z "$DB_USER" ] || [ -z "$DB_NAME" ]; then
  usage
  exit 1
fi

case "$DB_ENGINE" in
  pg|mysql) ;;
  *)
    echo "ERROR: --db-engine must be 'pg' or 'mysql'"
    exit 1
    ;;
esac

if ! [[ "$PLAN_QUERY_TIMEOUT_SEC" =~ ^[0-9]+$ ]] || [ "$PLAN_QUERY_TIMEOUT_SEC" -lt 1 ]; then
  echo "ERROR: --plan-query-timeout-sec must be a positive integer"
  exit 1
fi

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "ERROR: container '$CONTAINER' not found"
  exit 1
fi

mkdir -p "$(dirname "$OUT")"
: > "$OUT"

CID=$(docker inspect --format '{{.Id}}' "$CONTAINER")

get_cgroup_io_path() {
  local cid="$1"
  local p1="/sys/fs/cgroup/docker/${cid}/io.stat"
  local p2="/sys/fs/cgroup/system.slice/docker-${cid}.scope/io.stat"
  local p3="/sys/fs/cgroup/docker-limited/${cid}/io.stat"
  if [ -f "$p1" ]; then echo "$p1"; return 0; fi
  if [ -f "$p2" ]; then echo "$p2"; return 0; fi
  if [ -f "$p3" ]; then echo "$p3"; return 0; fi
  echo ""
}

IO_STAT_PATH=$(get_cgroup_io_path "$CID")

json_escape() {
  local s="$1"
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\n'/\\n}
  s=${s//$'\r'/\\r}
  s=${s//$'\t'/\\t}
  printf '%s' "$s"
}

parse_human_bytes() {
  local s="$1"
  python3 -c "
import re, sys
s = sys.argv[1].strip().upper()
if s == '0B':
    print(0)
    sys.exit(0)
m = re.match(r'^([0-9\.]+)\s*([KMGTP]I?B)$', s)
if not m:
    print(0)
    sys.exit(0)
v = float(m.group(1))
u = m.group(2)
factors = {
    'B': 1, 'KB': 1000, 'MB': 10**6, 'GB': 10**9, 'TB': 10**12,
    'KIB': 1024, 'MIB': 1024**2, 'GIB': 1024**3, 'TIB': 1024**4
}
print(int(v * factors.get(u, 1)))" "$s" 2>/dev/null || echo 0
}

read_container_memory_bytes() {
  local mem_used_human
  mem_used_human=$(docker stats --no-stream --format '{{.MemUsage}}' "$CONTAINER" 2>/dev/null | awk -F'/' '{gsub(/^ +| +$/,"",$1); print $1}')
  if [ -z "${mem_used_human:-}" ]; then
    echo "0"
    return 0
  fi
  parse_human_bytes "$mem_used_human"
}

# Returns: rbytes wbytes rios wios
read_io_counters() {
  local io_path="$1"

  if [ -n "$io_path" ] && [ -f "$io_path" ]; then
    awk '
      {
        for (i=1; i<=NF; i++) {
          if ($i ~ /^rbytes=/) { split($i,a,"="); rb += a[2]; }
          else if ($i ~ /^wbytes=/) { split($i,a,"="); wb += a[2]; }
          else if ($i ~ /^rios=/) { split($i,a,"="); ro += a[2]; }
          else if ($i ~ /^wios=/) { split($i,a,"="); wo += a[2]; }
        }
      }
      END { printf "%d %d %d %d\n", rb+0, wb+0, ro+0, wo+0 }
    ' "$io_path"
    return 0
  fi

  local p_bytes_candidates=(
    "/sys/fs/cgroup/blkio/docker/${CID}/blkio.throttle.io_service_bytes"
    "/sys/fs/cgroup/blkio/docker-limited/${CID}/blkio.throttle.io_service_bytes"
    "/sys/fs/cgroup/blkio/system.slice/docker-${CID}.scope/blkio.throttle.io_service_bytes"
  )
  local p_ios_candidates=(
    "/sys/fs/cgroup/blkio/docker/${CID}/blkio.throttle.io_serviced"
    "/sys/fs/cgroup/blkio/docker-limited/${CID}/blkio.throttle.io_serviced"
    "/sys/fs/cgroup/blkio/system.slice/docker-${CID}.scope/blkio.throttle.io_serviced"
  )

  local p_bytes=""
  local p_ios=""
  local i=0
  for candidate in "${p_bytes_candidates[@]}"; do
    if [ -f "$candidate" ]; then
      p_bytes="$candidate"
      p_ios="${p_ios_candidates[$i]}"
      break
    fi
    i=$((i + 1))
  done

  if [ -n "$p_bytes" ]; then
    local rb=0
    local wb=0
    local ro=0
    local wo=0
    rb=$(awk '$2=="Read"{s+=$3} END{print s+0}' "$p_bytes")
    wb=$(awk '$2=="Write"{s+=$3} END{print s+0}' "$p_bytes")
    if [ -f "$p_ios" ]; then
      ro=$(awk '$2=="Read"{s+=$3} END{print s+0}' "$p_ios")
      wo=$(awk '$2=="Write"{s+=$3} END{print s+0}' "$p_ios")
    fi
    printf "%d %d %d %d\n" "$rb" "$wb" "$ro" "$wo"
    return 0
  fi

  # NEW FALLBACK: Use docker stats for I/O throughput.
  echo "WARN: cgroup IO stats not found, falling back to 'docker stats' (IOPS will be 0)." >&2
  local block_io
  block_io=$(docker stats --no-stream --format '{{.BlockIO}}' "$CONTAINER" 2>/dev/null)

  if [ -n "$block_io" ] && [[ "$block_io" != "0B / 0B" ]]; then
    local read_str write_str
    read_str=$(echo "$block_io" | awk -F' / ' '{print $1}')
    write_str=$(echo "$block_io" | awk -F' / ' '{print $2}')

    local rb wb
    rb=$(parse_human_bytes "$read_str")
    wb=$(parse_human_bytes "$write_str")
    
    # IOPS are not available from docker stats, so we report them as 0.
    printf "%d %d %d %d\n" "$rb" "$wb" "0" "0"
    return 0
  fi

  echo "0 0 0 0" # Final fallback if everything fails
}

fetch_plan_tree() {
  if [ "$ENABLE_PLAN_TREE" != "1" ]; then
    return 0
  fi

  if [ "$DB_ENGINE" = "pg" ]; then
    local plan_timeout_ms
    plan_timeout_ms=$((PLAN_QUERY_TIMEOUT_SEC * 1000))

    PGPASSWORD="$DB_PASSWORD" psql \
      -v ON_ERROR_STOP=0 \
      -q \
      -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
      -At -F $'\t' \
      -c "SET statement_timeout = ${plan_timeout_ms};
          SELECT coalesce(query_plan, '')
          FROM dbe_perf.statement_history
          WHERE finish_time > now() - interval '${INTERVAL} seconds'
            AND query NOT LIKE '%statement_history%'
          ORDER BY finish_time DESC
          LIMIT 1;" 2>/dev/null | tail -n 1 || true
    return 0
  fi

  # MySQL path:
  # 1) Preferred: EXPLAIN FORMAT=JSON FOR CONNECTION <id> for currently running query.
  # 2) Fallback: most recent SQL text from performance_schema history.
  local mysql_base mysql_pid explain_json recent_sql
  mysql_base=(mysql -N -s -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" "$DB_NAME")
  if [ -n "$DB_PASSWORD" ]; then
    mysql_base=(env MYSQL_PWD="$DB_PASSWORD" "${mysql_base[@]}")
  fi

  mysql_pid=$("${mysql_base[@]}" -e \
    "SELECT ID
     FROM information_schema.PROCESSLIST
     WHERE DB = '${DB_NAME}'
       AND COMMAND = 'Query'
       AND INFO IS NOT NULL
       AND ID <> CONNECTION_ID()
     ORDER BY TIME DESC
     LIMIT 1;" 2>/dev/null | head -n 1 || true)

  if [ -n "${mysql_pid:-}" ]; then
    explain_json=$("${mysql_base[@]}" -e "EXPLAIN FORMAT=JSON FOR CONNECTION ${mysql_pid};" 2>/dev/null || true)
    if [ -n "${explain_json:-}" ]; then
      printf '%s\n' "$explain_json"
      return 0
    fi
  fi

  recent_sql=$("${mysql_base[@]}" -e \
    "SELECT COALESCE(SQL_TEXT, '')
     FROM performance_schema.events_statements_history_long
     WHERE CURRENT_SCHEMA = '${DB_NAME}'
       AND SQL_TEXT IS NOT NULL
       AND SQL_TEXT <> ''
       AND EVENT_NAME LIKE 'statement/sql/%'
       AND SQL_TEXT NOT LIKE '%performance_schema%'
     ORDER BY EVENT_ID DESC
     LIMIT 1;" 2>/dev/null | head -n 1 || true)

  if [ -n "${recent_sql:-}" ]; then
    printf 'RECENT_SQL: %s\n' "$recent_sql"
  fi
}

calc_p99() {
  local f="$1"
  python3 - <<PY
import math
vals=[]
with open("$f","r",encoding="utf-8") as fp:
    for line in fp:
        line=line.strip()
        if not line:
            continue
        try:
            vals.append(float(line))
        except Exception:
            pass
if not vals:
    print(0)
else:
    vals.sort()
    k=max(0, math.ceil(0.99*len(vals))-1)
    print(vals[k])
PY
}

cpu_used() {
  docker stats --no-stream --format '{{.CPUPerc}}' "$CONTAINER" | tr -d '%' || echo "0"
}

tmp_dir=$(mktemp -d)
read_bw_file="$tmp_dir/read_bw.txt"
write_bw_file="$tmp_dir/write_bw.txt"
trap 'rm -rf "$tmp_dir"' EXIT

read prev_rbytes prev_wbytes prev_rios prev_wios < <(read_io_counters "$IO_STAT_PATH")
prev_ts=$(date +%s)

start_ts=$(date +%s)
end_ts=$((start_ts + DURATION_SEC))

while [ "$(date +%s)" -lt "$end_ts" ]; do
  ts=$(date +%s)
  read cur_rbytes cur_wbytes cur_rios cur_wios < <(read_io_counters "$IO_STAT_PATH")

  dt=$((ts - prev_ts))
  if [ "$dt" -le 0 ]; then dt=1; fi
  delta_r=$((cur_rbytes - prev_rbytes))
  delta_w=$((cur_wbytes - prev_wbytes))
  delta_ro=$((cur_rios - prev_rios))
  delta_wo=$((cur_wios - prev_wios))
  if [ "$delta_r" -lt 0 ]; then delta_r=0; fi
  if [ "$delta_w" -lt 0 ]; then delta_w=0; fi
  if [ "$delta_ro" -lt 0 ]; then delta_ro=0; fi
  if [ "$delta_wo" -lt 0 ]; then delta_wo=0; fi

  read_bw=$(python3 - <<PY
print(float($delta_r)/float($dt))
PY
)
  write_bw=$(python3 - <<PY
print(float($delta_w)/float($dt))
PY
)
  read_iops=$(python3 - <<PY
print(float($delta_ro)/float($dt))
PY
)
  write_iops=$(python3 - <<PY
print(float($delta_wo)/float($dt))
PY
)

  echo "$read_bw" >> "$read_bw_file"
  echo "$write_bw" >> "$write_bw_file"

  p99_read=$(calc_p99 "$read_bw_file")
  p99_write=$(calc_p99 "$write_bw_file")
  cpu_val=$(cpu_used)
  mem_bytes=$(read_container_memory_bytes)
  plan_tree_raw=$(fetch_plan_tree)
  plan_tree_escaped=$(json_escape "$plan_tree_raw")

  echo "{\"timestamp\":$ts,\"cpu_used\":${cpu_val:-0},\"read_bytes_per_sec\":${read_bw:-0},\"write_bytes_per_sec\":${write_bw:-0},\"read_iops\":${read_iops:-0},\"write_iops\":${write_iops:-0},\"p99_read\":$p99_read,\"p99_write\":$p99_write,\"total_read_bytes\":$cur_rbytes,\"total_write_bytes\":$cur_wbytes,\"memory_working_set_bytes\":${mem_bytes:-0},\"sql_plan_tree\":\"$plan_tree_escaped\"}" >> "$OUT"

  prev_rbytes=$cur_rbytes
  prev_wbytes=$cur_wbytes
  prev_rios=$cur_rios
  prev_wios=$cur_wios
  prev_ts=$ts
  sleep "$INTERVAL"
done
