#!/usr/bin/env bash
set -uo pipefail

HOST="${PGHOST:-192.0.2.8}"
PORT="${PGPORT:-25432}"
USER="${PGUSER:-postgres}"
DBNAME="${PGDATABASE:-tpcds}"
PASSWORD="${PGPASSWORD:-}"
SQL_DIR=""
CLIENTS=8
DURATION=600
STATEMENT_TIMEOUT_MS=0
OUTPUT_DIR="result/tpcds_replay_$(date +%Y%m%d_%H%M%S)"
DISCARD_RESULTS=1

usage() {
  cat <<'USAGE'
Usage:
  shell/run_tpcds_replay_pg.sh --sql-dir DIR [options]

Options:
  --host HOST                  PostgreSQL host. Default: PGHOST or 192.0.2.8
  --port PORT                  PostgreSQL port. Default: PGPORT or 25432
  --user USER                  PostgreSQL user. Default: PGUSER or postgres
  --dbname DB                  PostgreSQL database. Default: PGDATABASE or tpcds
  --password PASSWORD          PostgreSQL password. Default: PGPASSWORD
  --sql-dir DIR                Directory containing .sql files. Required.
  --clients N                  Concurrent replay clients. Default: 8
  --duration SEC               Replay duration in seconds. Default: 600
  --statement-timeout-ms MS    SET statement_timeout for each session. 0 disables. Default: 0
  --output-dir DIR             Output directory for logs.
  --keep-results               Keep query result rows in worker logs. Default discards rows.
  --discard-results            Discard query result rows and keep errors. Default.
  -h, --help                   Show this help.

Example:
  shell/run_tpcds_replay_pg.sh \
    --host 192.0.2.8 --port 25432 --user postgres --dbname tpcds \
    --sql-dir /path/to/tpcds/queries --clients 8 --duration 600
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --user) USER="$2"; shift 2 ;;
    --dbname) DBNAME="$2"; shift 2 ;;
    --password) PASSWORD="$2"; shift 2 ;;
    --sql-dir) SQL_DIR="$2"; shift 2 ;;
    --clients) CLIENTS="$2"; shift 2 ;;
    --duration) DURATION="$2"; shift 2 ;;
    --statement-timeout-ms) STATEMENT_TIMEOUT_MS="$2"; shift 2 ;;
    --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
    --keep-results) DISCARD_RESULTS=0; shift ;;
    --discard-results) DISCARD_RESULTS=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$SQL_DIR" ]]; then
  echo "ERROR: --sql-dir is required" >&2
  usage >&2
  exit 2
fi

if [[ ! -d "$SQL_DIR" ]]; then
  echo "ERROR: sql dir does not exist: $SQL_DIR" >&2
  exit 2
fi

mapfile -t SQL_FILES < <(find "$SQL_DIR" -type f -name '*.sql' | sort)
if [[ "${#SQL_FILES[@]}" -eq 0 ]]; then
  echo "ERROR: no .sql files found in: $SQL_DIR" >&2
  exit 2
fi

mkdir -p "$OUTPUT_DIR"
SUMMARY_FILE="$OUTPUT_DIR/summary.csv"
echo "worker,queries,errors,elapsed_sec" > "$SUMMARY_FILE"

export PGPASSWORD="$PASSWORD"
export PGAPPNAME="tpcds_replay"

echo "TPC-DS replay started"
echo "  host=$HOST port=$PORT user=$USER dbname=$DBNAME"
echo "  sql_dir=$SQL_DIR sql_files=${#SQL_FILES[@]}"
echo "  clients=$CLIENTS duration=${DURATION}s statement_timeout_ms=$STATEMENT_TIMEOUT_MS"
echo "  output_dir=$OUTPUT_DIR"
echo "  discard_results=$DISCARD_RESULTS"

END_TS=$(( $(date +%s) + DURATION ))

run_worker() {
  local worker_id="$1"
  local queries=0
  local errors=0
  local start_ts
  local now_ts
  local sql_file
  local sql_idx
  local log_file="$OUTPUT_DIR/worker_${worker_id}.log"
  local psql_prefix=()

  psql_prefix=(
    psql
    -h "$HOST"
    -p "$PORT"
    -U "$USER"
    -d "$DBNAME"
    -v ON_ERROR_STOP=1
    -q
    -X
  )
  if [[ "$DISCARD_RESULTS" -eq 1 ]]; then
    psql_prefix+=(-o /dev/null)
  fi

  start_ts=$(date +%s)
  while true; do
    now_ts=$(date +%s)
    if [[ "$now_ts" -ge "$END_TS" ]]; then
      break
    fi

    sql_idx=$(( RANDOM % ${#SQL_FILES[@]} ))
    sql_file="${SQL_FILES[$sql_idx]}"

    {
      echo "===== $(date '+%F %T') worker=$worker_id query=$sql_file ====="
      if [[ "$STATEMENT_TIMEOUT_MS" -gt 0 ]]; then
        "${psql_prefix[@]}" -c "SET statement_timeout = ${STATEMENT_TIMEOUT_MS};" -f "$sql_file"
      else
        "${psql_prefix[@]}" -f "$sql_file"
      fi
    } >> "$log_file" 2>&1

    if [[ "$?" -eq 0 ]]; then
      queries=$((queries + 1))
    else
      errors=$((errors + 1))
    fi
  done

  now_ts=$(date +%s)
  echo "${worker_id},${queries},${errors},$((now_ts - start_ts))" >> "$SUMMARY_FILE"
}

for worker_id in $(seq 1 "$CLIENTS"); do
  run_worker "$worker_id" &
done

wait

awk -F, '
  NR == 1 { next }
  {
    q += $2
    e += $3
    if ($4 > max_elapsed) max_elapsed = $4
  }
  END {
    if (max_elapsed <= 0) max_elapsed = 1
    printf("total_queries=%d\n", q)
    printf("total_errors=%d\n", e)
    printf("elapsed_sec=%d\n", max_elapsed)
    printf("qps=%.6f\n", q / max_elapsed)
  }
' "$SUMMARY_FILE" | tee "$OUTPUT_DIR/result.txt"

echo "TPC-DS replay finished: $OUTPUT_DIR"
