#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HISTORY_DIR="${ROOT_DIR}/history"

# Usage:
#   bash shell/cleanup_history_before_april.sh [CUTOFF]
#
# Supported cutoff formats:
#   2026-04-01
#   2026-04-01_00:52:15
#   2026-04-01_005215
#   20260401
#   20260401005215
#
# All history items strictly earlier than cutoff are deleted.
CUTOFF_INPUT="${1:-2026-04-01}"

if [[ ! -d "${HISTORY_DIR}" ]]; then
  echo "history directory not found: ${HISTORY_DIR}" >&2
  exit 1
fi

normalize_cutoff() {
  local raw="$1"
  local digits
  digits="$(printf '%s' "${raw}" | tr -cd '0-9')"

  case "${#digits}" in
    8)
      printf '%s000000\n' "${digits}"
      ;;
    14)
      printf '%s\n' "${digits}"
      ;;
    *)
      echo "invalid cutoff format: ${raw}" >&2
      return 1
      ;;
  esac
}

extract_timestamp() {
  local base="$1"

  if [[ "${base}" =~ ([0-9]{4})([0-9]{2})([0-9]{2})_([0-9]{6}) ]]; then
    printf '%s%s%s%s\n' \
      "${BASH_REMATCH[1]}" \
      "${BASH_REMATCH[2]}" \
      "${BASH_REMATCH[3]}" \
      "${BASH_REMATCH[4]}"
    return 0
  fi

  if [[ "${base}" =~ ([0-9]{4})-([0-9]{2})-([0-9]{2})_([0-9]{6}) ]]; then
    printf '%s%s%s%s\n' \
      "${BASH_REMATCH[1]}" \
      "${BASH_REMATCH[2]}" \
      "${BASH_REMATCH[3]}" \
      "${BASH_REMATCH[4]}"
    return 0
  fi

  if [[ "${base}" =~ ([0-9]{4})-([0-9]{2})-([0-9]{2}) ]]; then
    printf '%s%s%s000000\n' \
      "${BASH_REMATCH[1]}" \
      "${BASH_REMATCH[2]}" \
      "${BASH_REMATCH[3]}"
    return 0
  fi

  if [[ "${base}" =~ ([0-9]{8})_([0-9]{6}) ]]; then
    printf '%s%s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
    return 0
  fi

  if [[ "${base}" =~ ([0-9]{8}) ]]; then
    printf '%s000000\n' "${BASH_REMATCH[1]}"
    return 0
  fi

  return 1
}

should_delete_before_cutoff() {
  local path="$1"
  local base
  local ts

  base="$(basename "${path}")"
  if ! ts="$(extract_timestamp "${base}")"; then
    return 1
  fi

  [[ "${ts}" < "${CUTOFF_TS}" ]]
}

collect_matches() {
  local find_root="$1"
  shift

  if [[ ! -e "${find_root}" ]]; then
    return 0
  fi

  find "${find_root}" "$@" | while IFS= read -r path; do
    if should_delete_before_cutoff "${path}"; then
      printf '%s\n' "${path}"
    fi
  done | sort
}

CUTOFF_TS="$(normalize_cutoff "${CUTOFF_INPUT}")"

mapfile -t run_files < <(
  collect_matches "${HISTORY_DIR}" -maxdepth 1 -type f -name 'run_*.log'
)

mapfile -t optimization_history_files < <(
  collect_matches "${HISTORY_DIR}" -maxdepth 1 -type f -name 'optimization_history_*.jsonl'
)

mapfile -t plan_capture_files < <(
  collect_matches "${HISTORY_DIR}/plan_captures" -maxdepth 1 -type f
)

mapfile -t plan_detail_files < <(
  collect_matches "${HISTORY_DIR}/plan_details" -maxdepth 1 -type f
)

mapfile -t io_calibration_files < <(
  collect_matches "${HISTORY_DIR}/io_calibration" -maxdepth 1 -type f
)

mapfile -t auto_explain_paths < <(
  collect_matches "${HISTORY_DIR}/auto_explain" -mindepth 1 -maxdepth 1
)

if [[
  ${#run_files[@]} -eq 0 &&
  ${#optimization_history_files[@]} -eq 0 &&
  ${#plan_capture_files[@]} -eq 0 &&
  ${#plan_detail_files[@]} -eq 0 &&
  ${#io_calibration_files[@]} -eq 0 &&
  ${#auto_explain_paths[@]} -eq 0
]]; then
  echo "No matching files to delete before ${CUTOFF_TS}."
  exit 0
fi

echo "Deleting history items strictly earlier than: ${CUTOFF_TS}"
echo "Files to delete:"
for path in \
  "${run_files[@]}" \
  "${optimization_history_files[@]}" \
  "${plan_capture_files[@]}" \
  "${plan_detail_files[@]}" \
  "${io_calibration_files[@]}" \
  "${auto_explain_paths[@]}"; do
  echo "  ${path}"
done

for path in \
  "${run_files[@]}" \
  "${optimization_history_files[@]}" \
  "${plan_capture_files[@]}" \
  "${plan_detail_files[@]}" \
  "${io_calibration_files[@]}"; do
  rm -f -- "${path}"
done

for path in "${auto_explain_paths[@]}"; do
  rm -rf -- "${path}"
done

echo "Deleted ${#run_files[@]} run logs, ${#optimization_history_files[@]} optimization history files, ${#plan_capture_files[@]} plan captures, ${#plan_detail_files[@]} plan detail files, ${#io_calibration_files[@]} io calibration files, and ${#auto_explain_paths[@]} auto_explain directories."
