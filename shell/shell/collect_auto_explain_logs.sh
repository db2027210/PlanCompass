#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  collect_auto_explain_logs.sh \
    --container <name> \
    --since "YYYY-MM-DD HH:MM:SS" \
    --output-dir <path> \
    [--log-subdir <dir>] \
    [--pattern <glob>] \
    [--until "YYYY-MM-DD HH:MM:SS"]

Example:
  bash collect_auto_explain_logs.sh \
    --container opengauss_node1 \
    --since "2026-03-06 13:00:00" \
    --output-dir /disk2/dr_experiment_result/shadow/c11/plans
USAGE
}

CONTAINER=""
SINCE=""
UNTIL=""
OUTPUT_DIR=""
LOG_SUBDIR="pg_log"
PATTERN="*.log"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --container) CONTAINER="$2"; shift 2 ;;
    --since) SINCE="$2"; shift 2 ;;
    --until) UNTIL="$2"; shift 2 ;;
    --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
    --log-subdir) LOG_SUBDIR="$2"; shift 2 ;;
    --pattern) PATTERN="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$CONTAINER" || -z "$SINCE" || -z "$OUTPUT_DIR" ]]; then
  usage
  exit 1
fi

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "ERROR: container not found: $CONTAINER"
  exit 1
fi

resolve_log_src_dir() {
  local c="$1"
  local direct_log
  local og_data
  local pg_data
  local generic_data
  local mysql
  direct_log=$(docker inspect "$c" --format='{{range .Mounts}}{{if eq .Destination "/var/log/postgresql"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || true)
  og_data=$(docker inspect "$c" --format='{{range .Mounts}}{{if eq .Destination "/var/lib/opengauss/data"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || true)
  pg_data=$(docker inspect "$c" --format='{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || true)
  generic_data=$(docker inspect "$c" --format='{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || true)
  mysql=$(docker inspect "$c" --format='{{range .Mounts}}{{if eq .Destination "/var/lib/mysql"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || true)

  if [[ -n "$direct_log" ]]; then
    printf '%s' "$direct_log"
    return 0
  fi
  if [[ -n "$og_data" ]]; then
    printf '%s/%s' "$og_data" "$LOG_SUBDIR"
    return 0
  fi
  if [[ -n "$pg_data" ]]; then
    printf '%s/%s' "$pg_data" "$LOG_SUBDIR"
    return 0
  fi
  if [[ -n "$generic_data" ]]; then
    printf '%s/%s' "$generic_data" "$LOG_SUBDIR"
    return 0
  fi
  if [[ -n "$mysql" ]]; then
    printf '%s/%s' "$mysql" "$LOG_SUBDIR"
    return 0
  fi
  return 1
}

LOG_SRC_DIR="$(resolve_log_src_dir "$CONTAINER" || true)"
if [[ -z "$LOG_SRC_DIR" ]]; then
  echo "ERROR: could not resolve DB log directory for container: $CONTAINER"
  exit 1
fi
if ! sudo test -d "$LOG_SRC_DIR"; then
  echo "ERROR: log directory not found: $LOG_SRC_DIR"
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

echo "[collect_auto_explain_logs] container=$CONTAINER"
echo "[collect_auto_explain_logs] log_src=$LOG_SRC_DIR"
echo "[collect_auto_explain_logs] since=$SINCE"
if [[ -n "$UNTIL" ]]; then
  echo "[collect_auto_explain_logs] until=$UNTIL"
fi

tmp_list=$(mktemp)
trap 'rm -f "$tmp_list"' EXIT

if [[ -n "$UNTIL" ]]; then
  sudo find "$LOG_SRC_DIR" -type f -name "$PATTERN" -newermt "$SINCE" ! -newermt "$UNTIL" | sort > "$tmp_list"
else
  sudo find "$LOG_SRC_DIR" -type f -name "$PATTERN" -newermt "$SINCE" | sort > "$tmp_list"
fi

count=$(wc -l < "$tmp_list" | tr -d '[:space:]')
if [[ "$count" == "0" ]]; then
  echo "WARN: no log files matched time window."
  exit 0
fi

echo "[collect_auto_explain_logs] matched_files=$count"

while IFS= read -r src; do
  base=$(basename "$src")
  dst="$OUTPUT_DIR/$base"
  sudo cp "$src" "$dst"
  sudo chmod a+r "$dst"
  echo "  copied: $base"
done < "$tmp_list"

echo "[collect_auto_explain_logs] done -> $OUTPUT_DIR"
