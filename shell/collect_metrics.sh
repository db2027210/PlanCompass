#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  collect_metrics.sh [options]

Options:
  --no-prompt                   Run non-interactively (default behavior).
  --prompt                      Enable interactive prompts.
  --container NAME              Container name (default: opengauss_node1)
  --save-path PATH              Output directory (default: .)
  --duration SEC                Monitor duration seconds (default: 600)
  --interval SEC                Sample interval seconds (default: 5)
  --db-host HOST                DB host (default: 127.0.0.1)
  --db-port PORT                DB port (default: 11001)
  --db-user USER                DB user (default: gaussdb)
  --db-name NAME                DB name (default: benchbase)
  --db-password PASS            DB password (default: CHANGE_ME)
  --run-workload                Run workload automatically (default: no)
  --workload-cmd CMD            Workload command string
  --help                        Show this help
EOF
}

NO_PROMPT=1
CONTAINER="opengauss_node1"
SAVE_PATH="/disk2/anonymous/result"
DURATION_SEC=600
INTERVAL=5
DB_HOST="127.0.0.1"
DB_PORT=11001
DB_USER="gaussdb"
DB_NAME="benchbase"
DB_PASSWORD="${DB_PASSWORD:-CHANGE_ME}"
RUN_WORKLOAD="N"
WORKLOAD_CMD="${WORKLOAD_CMD:-cd /home/anonymous/benchbase-postgres && java -jar benchbase.jar -b tpcc -c config/postgres/sample_tpcc_config.xml --create=false --load=false --execute=true}"

while [ $# -gt 0 ]; do
  case "$1" in
    --no-prompt) NO_PROMPT=1; shift ;;
    --prompt) NO_PROMPT=0; shift ;;
    --container) CONTAINER="$2"; shift 2 ;;
    --save-path) SAVE_PATH="$2"; shift 2 ;;
    --duration) DURATION_SEC="$2"; shift 2 ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    --db-host) DB_HOST="$2"; shift 2 ;;
    --db-port) DB_PORT="$2"; shift 2 ;;
    --db-user) DB_USER="$2"; shift 2 ;;
    --db-name) DB_NAME="$2"; shift 2 ;;
    --db-password) DB_PASSWORD="$2"; shift 2 ;;
    --run-workload) RUN_WORKLOAD="Y"; shift ;;
    --workload-cmd) WORKLOAD_CMD="$2"; RUN_WORKLOAD="Y"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1"; usage; exit 1 ;;
  esac
done

if [ "$NO_PROMPT" -eq 0 ]; then
  read -p "Enter container name to monitor [${CONTAINER}]: " _v; CONTAINER=${_v:-$CONTAINER}
  read -p "Enter directory path to save results [${SAVE_PATH}]: " _v; SAVE_PATH=${_v:-$SAVE_PATH}
  read -p "Enter monitor duration in seconds [${DURATION_SEC}]: " _v; DURATION_SEC=${_v:-$DURATION_SEC}
  read -p "Enter sample interval in seconds [${INTERVAL}]: " _v; INTERVAL=${_v:-$INTERVAL}

  read -p "Enter DB host [${DB_HOST}]: " _v; DB_HOST=${_v:-$DB_HOST}
  read -p "Enter DB port [${DB_PORT}]: " _v; DB_PORT=${_v:-$DB_PORT}
  read -p "Enter DB user [${DB_USER}]: " _v; DB_USER=${_v:-$DB_USER}
  read -p "Enter DB name [${DB_NAME}]: " _v; DB_NAME=${_v:-$DB_NAME}
  if [ -z "$DB_PASSWORD" ]; then
    read -s -p "Enter DB password [hidden]: " DB_PASSWORD
    echo ""
  fi

  read -p "Run workload command automatically? [y/N]: " _v
  RUN_WORKLOAD=${_v:-$RUN_WORKLOAD}
  if [[ "$RUN_WORKLOAD" =~ ^[Yy]$ ]] && [ -z "$WORKLOAD_CMD" ]; then
    read -p "Enter workload command: " WORKLOAD_CMD
  fi
fi

if [[ "$RUN_WORKLOAD" =~ ^[Yy]$ ]] && [ -z "$WORKLOAD_CMD" ]; then
  echo "ERROR: RUN_WORKLOAD is enabled but workload command is empty"
  exit 1
fi

START_TIME_STR=$(date '+%Y-%m-%d_%H-%M-%S')
OUT="${SAVE_PATH}/metrics-${CONTAINER}-${START_TIME_STR}.csv"
QUERY_OUT="${SAVE_PATH}/query_exec_time-${CONTAINER}-${START_TIME_STR}.csv"
POST_OUT="${SAVE_PATH}/post_workload_summary-${CONTAINER}-${START_TIME_STR}.csv"
DATA_DIST_OUT="${SAVE_PATH}/data_distribution-${CONTAINER}-${START_TIME_STR}.csv"
mkdir -p "$SAVE_PATH"

echo "timestamp,cpu_used,concurrency,read_bytes_per_sec,write_bytes_per_sec,read_iops,write_iops,p99_read,p99_write,total_read_bytes,total_write_bytes,total_read_ios,total_write_ios" > "$OUT"
echo "sample_ts,finish_epoch,finish_time,exec_ms,query_id,query_text" > "$QUERY_OUT"
echo "sample_ts,concurrency,concurrency_avg,concurrency_peak,cache_hit_rate,working_set_bytes,data_distribution,shared_buffers_bytes,usable_os_page_cache_for_pg_bytes,cache_capacity_effective" > "$POST_OUT"
echo "sample_ts,schema_name,table_name,size_bytes,size_pct" > "$DATA_DIST_OUT"

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "ERROR: container '$CONTAINER' not found"
  exit 1
fi

CID=$(docker inspect --format '{{.Id}}' "$CONTAINER")

get_cgroup_io_path() {
  local cid="$1"
  local p1="/sys/fs/cgroup/docker/${cid}/io.stat"
  local p2="/sys/fs/cgroup/system.slice/docker-${cid}.scope/io.stat"
  local p3="/sys/fs/cgroup/docker-limited/${cid}/io.stat"
  if [ -f "$p1" ]; then
    echo "$p1"
    return 0
  fi
  if [ -f "$p2" ]; then
    echo "$p2"
    return 0
  fi
  if [ -f "$p3" ]; then
    echo "$p3"
    return 0
  fi
  echo ""
}

IO_STAT_PATH=$(get_cgroup_io_path "$CID")
if [ -z "$IO_STAT_PATH" ]; then
  echo "WARN: io.stat not found for container '$CONTAINER', fallback to cgroup v1 blkio paths"
fi

# CSV-safe string
csv_escape() {
  local s="$1"
  s=${s//$'\r'/ }
  s=${s//$'\n'/\\n}
  s=${s//\"/\"\"}
  printf '"%s"' "$s"
}

collect_query_time_from_logs() {
  local since_epoch="$1"
  local logs_tmp="${tmp_dir}/db_duration_logs.txt"

  if ! docker logs --timestamps --since "$since_epoch" "$CONTAINER" >"$logs_tmp" 2>/dev/null; then
    echo "WARN: failed to read docker logs for per-query timing."
    return 0
  fi

  local rows
  rows=$(python3 - "$logs_tmp" "$QUERY_OUT" <<'PY'
import csv
import datetime as dt
import hashlib
import re
import sys

log_file, out_file = sys.argv[1], sys.argv[2]
pat = re.compile(r"duration:\s*([0-9]+(?:\.[0-9]+)?)\s*ms.*statement:\s*(.*)$", re.IGNORECASE)
count = 0

with open(log_file, "r", encoding="utf-8", errors="ignore") as f, \
     open(out_file, "a", encoding="utf-8", newline="") as out:
    writer = csv.writer(out)
    for line in f:
        line = line.rstrip("\n")
        if "duration:" not in line.lower() or "statement:" not in line.lower():
            continue
        m = pat.search(line)
        if not m:
            continue

        exec_ms = float(m.group(1))
        query_text = m.group(2).strip()
        if not query_text:
            continue

        ts_token = line.split(" ", 1)[0]
        finish_epoch = ""
        try:
            finish_epoch = str(int(dt.datetime.fromisoformat(ts_token.replace("Z", "+00:00")).timestamp()))
        except Exception:
            pass

        query_id = hashlib.md5(query_text.encode("utf-8")).hexdigest()
        sample_ts = finish_epoch
        writer.writerow([sample_ts, finish_epoch, ts_token, f"{exec_ms:.3f}", query_id, query_text])
        count += 1

print(count)
PY
)
  echo "Query time rows parsed from logs: ${rows:-0}"
}

# Returns: rbytes wbytes rios wios
read_io_counters() {
  local io_path="$1"

  if [ -n "$io_path" ] && [ -f "$io_path" ]; then
    # cgroup v2 io.stat
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

  # cgroup v1 fallback
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

  echo "0 0 0 0"
}

# Running p99 from a list file of floats
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

CPU_USED() {
  docker stats --no-stream --format '{{.CPUPerc}}' "$CONTAINER" | tr -d '%' || echo "0"
}

get_current_concurrency() {
  PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -At -c \
    "SELECT count(*)
     FROM pg_stat_activity
     WHERE datname = '${DB_NAME}'
       AND state = 'active'
       AND pid <> pg_backend_pid();" 2>/dev/null || echo "0"
}

get_memory_cgroup_path() {
  local cid="$1"
  local p1="/sys/fs/cgroup/docker/${cid}"
  local p2="/sys/fs/cgroup/system.slice/docker-${cid}.scope"
  local p3="/sys/fs/cgroup/docker-limited/${cid}"
  if [ -d "$p1" ]; then echo "$p1"; return 0; fi
  if [ -d "$p2" ]; then echo "$p2"; return 0; fi
  if [ -d "$p3" ]; then echo "$p3"; return 0; fi
  echo ""
}

read_container_working_set_bytes() {
  local cid="$1"
  local cg
  cg=$(get_memory_cgroup_path "$cid")

  # cgroup v2: working_set ~= memory.current - inactive_file
  if [ -n "$cg" ] && [ -f "$cg/memory.current" ] && [ -f "$cg/memory.stat" ]; then
    local current inactive ws
    current=$(cat "$cg/memory.current" 2>/dev/null || echo "0")
    inactive=$(awk '$1=="inactive_file"{print $2; found=1} END{if(!found) print 0}' "$cg/memory.stat" 2>/dev/null || echo "0")
    ws=$((current - inactive))
    if [ "$ws" -lt 0 ]; then ws=0; fi
    echo "$ws"
    return 0
  fi

  # cgroup v1 fallback
  if [ -n "$cg" ] && [ -f "$cg/memory.usage_in_bytes" ] && [ -f "$cg/memory.stat" ]; then
    local usage inactive ws
    usage=$(cat "$cg/memory.usage_in_bytes" 2>/dev/null || echo "0")
    inactive=$(awk '$1=="total_inactive_file"{print $2; found=1} END{if(!found) print 0}' "$cg/memory.stat" 2>/dev/null || echo "0")
    ws=$((usage - inactive))
    if [ "$ws" -lt 0 ]; then ws=0; fi
    echo "$ws"
    return 0
  fi

  # Fallback: docker stats memory usage (first value before '/').
  local mem_used_human
  mem_used_human=$(docker stats --no-stream --format '{{.MemUsage}}' "$CONTAINER" 2>/dev/null | awk -F'/' '{gsub(/^ +| +$/,"",$1); print $1}')
  if [ -n "${mem_used_human:-}" ]; then
    python3 - "$mem_used_human" <<'PY'
import re
import sys

s = sys.argv[1].strip()
m = re.match(r'^([0-9]+(?:\.[0-9]+)?)\s*([KMGTP]?i?B)$', s, re.IGNORECASE)
if not m:
    print(0)
    raise SystemExit(0)

v = float(m.group(1))
u = m.group(2).upper()
factors = {
    "B": 1,
    "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4, "PB": 1000**5,
    "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "TIB": 1024**4, "PIB": 1024**5,
}
print(int(v * factors.get(u, 1)))
PY
    return 0
  fi

  echo "0"
}

collect_post_workload_metrics() {
  local ts
  ts=$(date +%s)

  local concurrency
  concurrency=$(get_current_concurrency)
  concurrency=${concurrency:-0}

  local concurrency_avg concurrency_peak
  concurrency_avg=$(python3 - "$concurrency_file" <<'PY'
import sys
vals = []
try:
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                vals.append(float(line))
            except Exception:
                pass
except Exception:
    pass
print(round(sum(vals) / len(vals), 4) if vals else 0)
PY
)
  concurrency_peak=$(python3 - "$concurrency_file" <<'PY'
import sys
vals = []
try:
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                vals.append(float(line))
            except Exception:
                pass
except Exception:
    pass
print(int(max(vals)) if vals else 0)
PY
)

  local cache_hit_rate
  cache_hit_rate=$(PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -At -c \
    "SELECT COALESCE(round(sum(blks_hit)::numeric / NULLIF(sum(blks_hit + blks_read), 0), 6), 0)
     FROM pg_stat_database
     WHERE datname = '${DB_NAME}';" 2>/dev/null || echo "0")
  cache_hit_rate=${cache_hit_rate:-0}

  local shared_buffers_bytes
  shared_buffers_bytes=$(PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -At -c \
    "SELECT CASE lower(coalesce(unit, ''))
              WHEN '8kb' THEN setting::bigint * 8192
              WHEN 'kb' THEN setting::bigint * 1024
              WHEN 'mb' THEN setting::bigint * 1024 * 1024
              WHEN 'gb' THEN setting::bigint * 1024 * 1024 * 1024
              WHEN 'tb' THEN setting::bigint * 1024 * 1024 * 1024 * 1024
              WHEN '' THEN setting::bigint
              ELSE setting::bigint
            END
     FROM pg_settings
     WHERE name = 'shared_buffers';" 2>/dev/null || echo "0")
  shared_buffers_bytes=${shared_buffers_bytes:-0}

  local working_set_bytes
  working_set_bytes=$(read_container_working_set_bytes "$CID")
  working_set_bytes=${working_set_bytes:-0}

  local mem_available_bytes usable_os_page_cache_for_pg_bytes cache_capacity_effective
  mem_available_bytes=$(awk '/MemAvailable:/ {print $2 * 1024; exit}' /proc/meminfo 2>/dev/null || echo "0")
  mem_available_bytes=${mem_available_bytes:-0}
  usable_os_page_cache_for_pg_bytes=$((mem_available_bytes - shared_buffers_bytes))
  if [ "$usable_os_page_cache_for_pg_bytes" -lt 0 ]; then usable_os_page_cache_for_pg_bytes=0; fi
  cache_capacity_effective=$((shared_buffers_bytes + usable_os_page_cache_for_pg_bytes))

  local data_distribution
  data_distribution=$(PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -At -c \
    "WITH tbl AS (
       SELECT n.nspname AS schema_name, c.relname, pg_total_relation_size(c.oid) AS sz
       FROM pg_class c
       JOIN pg_namespace n ON n.oid = c.relnamespace
       WHERE c.relkind = 'r'
         AND n.nspname NOT IN ('pg_catalog', 'information_schema')
     ),
     total AS (
       SELECT COALESCE(sum(sz), 0) AS total_sz FROM tbl
     ),
     topn AS (
       SELECT schema_name, relname, sz,
              CASE WHEN (SELECT total_sz FROM total) > 0
                   THEN round(100.0 * sz / (SELECT total_sz FROM total), 2)
                   ELSE 0 END AS pct
       FROM tbl
       ORDER BY sz DESC
       LIMIT 10
     )
     SELECT COALESCE(string_agg(schema_name || '.' || relname || ':' || pct || '%', '; ' ORDER BY sz DESC), '')
     FROM topn;" 2>/dev/null || echo "")
  data_distribution_escaped=$(csv_escape "$data_distribution")

  echo "$ts,$concurrency,$concurrency_avg,$concurrency_peak,$cache_hit_rate,$working_set_bytes,$data_distribution_escaped,$shared_buffers_bytes,$usable_os_page_cache_for_pg_bytes,$cache_capacity_effective" >> "$POST_OUT"

  PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -At -F $'\t' -c \
    "WITH tbl AS (
       SELECT n.nspname AS schema_name, c.relname AS table_name, pg_total_relation_size(c.oid) AS sz
       FROM pg_class c
       JOIN pg_namespace n ON n.oid = c.relnamespace
       WHERE c.relkind = 'r'
         AND n.nspname NOT IN ('pg_catalog', 'information_schema')
     ),
     total AS (
       SELECT COALESCE(sum(sz), 0) AS total_sz FROM tbl
     )
     SELECT schema_name, table_name, sz,
            CASE WHEN (SELECT total_sz FROM total) > 0
                 THEN round(100.0 * sz / (SELECT total_sz FROM total), 4)
                 ELSE 0 END AS size_pct
     FROM tbl
     ORDER BY sz DESC
     LIMIT 50;" 2>/dev/null | \
  while IFS=$'\t' read -r schema_name table_name size_bytes size_pct; do
    [ -z "${schema_name:-}" ] && continue
    echo "$ts,$schema_name,$table_name,$size_bytes,$size_pct" >> "$DATA_DIST_OUT"
  done
}

tmp_dir=$(mktemp -d)
read_bw_file="$tmp_dir/read_bw.txt"
write_bw_file="$tmp_dir/write_bw.txt"
concurrency_file="$tmp_dir/concurrency.txt"
BENCHMARK_PID=""

cleanup() {
  if [ -n "${BENCHMARK_PID:-}" ] && kill -0 "$BENCHMARK_PID" 2>/dev/null; then
    echo "Stopping workload process (PID: $BENCHMARK_PID)..."
    kill "$BENCHMARK_PID" 2>/dev/null || true
  fi
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

read prev_rbytes prev_wbytes prev_rios prev_wios < <(read_io_counters "$IO_STAT_PATH")
base_rbytes=$prev_rbytes
base_wbytes=$prev_wbytes
base_rios=$prev_rios
base_wios=$prev_wios
prev_ts=$(date +%s)

start_ts=$(date +%s)
end_ts=$((start_ts + DURATION_SEC))

echo "Metrics collection starting for $CONTAINER"
echo "Output: $OUT"

if [[ "$RUN_WORKLOAD" =~ ^[Yy]$ ]] && [ -n "$WORKLOAD_CMD" ]; then
  echo "Starting workload in background..."
  bash -lc "$WORKLOAD_CMD" &
  BENCHMARK_PID=$!
  echo "Workload PID: $BENCHMARK_PID"
  sleep 5
fi

while [ "$(date +%s)" -lt "$end_ts" ]; do
  ts=$(date +%s)
  read cur_rbytes cur_wbytes cur_rios cur_wios < <(read_io_counters "$IO_STAT_PATH")

  dt=$((ts - prev_ts))
  if [ "$dt" -le 0 ]; then
    dt=1
  fi

  delta_r=$((cur_rbytes - prev_rbytes))
  delta_w=$((cur_wbytes - prev_wbytes))
  delta_ro=$((cur_rios - prev_rios))
  delta_wo=$((cur_wios - prev_wios))

  # Avoid negative deltas due to counter reset/restart
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

  cpu_used=$(CPU_USED)
  concurrency=$(get_current_concurrency)
  concurrency=${concurrency:-0}
  echo "$concurrency" >> "$concurrency_file"

  total_read_bytes=$((cur_rbytes - base_rbytes))
  total_write_bytes=$((cur_wbytes - base_wbytes))
  total_read_ios=$((cur_rios - base_rios))
  total_write_ios=$((cur_wios - base_wios))

  if [ "$total_read_bytes" -lt 0 ]; then total_read_bytes=0; fi
  if [ "$total_write_bytes" -lt 0 ]; then total_write_bytes=0; fi
  if [ "$total_read_ios" -lt 0 ]; then total_read_ios=0; fi
  if [ "$total_write_ios" -lt 0 ]; then total_write_ios=0; fi

  echo "$ts,$cpu_used,$concurrency,$read_bw,$write_bw,$read_iops,$write_iops,$p99_read,$p99_write,$total_read_bytes,$total_write_bytes,$total_read_ios,$total_write_ios" >> "$OUT"

  prev_rbytes=$cur_rbytes
  prev_wbytes=$cur_wbytes
  prev_rios=$cur_rios
  prev_wios=$cur_wios
  prev_ts=$ts

  sleep "$INTERVAL"
done

if [[ "$RUN_WORKLOAD" =~ ^[Yy]$ ]] && [ -n "${BENCHMARK_PID:-}" ] && kill -0 "$BENCHMARK_PID" 2>/dev/null; then
  echo "Waiting workload process to finish before post-workload summary..."
  wait "$BENCHMARK_PID" 2>/dev/null || true
  BENCHMARK_PID=""
fi

collect_query_time_from_logs "$start_ts"
collect_post_workload_metrics

echo "Metrics collection finished."
echo "Output file: $OUT"
echo "Query execution file: $QUERY_OUT"
echo "Post-workload summary file: $POST_OUT"
echo "Data distribution file: $DATA_DIST_OUT"
