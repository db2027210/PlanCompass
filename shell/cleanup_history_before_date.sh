#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HISTORY_DIR="${ROOT_DIR}/history"

usage() {
  cat <<'EOF'
Usage:
  bash shell/cleanup_history_before_date.sh CUTOFF [--apply]

Examples:
  bash shell/cleanup_history_before_date.sh 2026-05-01
  bash shell/cleanup_history_before_date.sh 20260501 --apply
  bash shell/cleanup_history_before_date.sh 2026-05-07_13:00:00 --apply

Supported cutoff formats:
  2026-05-01
  2026-05-01_13:00:00
  2026-05-01_130000
  20260501
  20260501130000

By default this script is dry-run only. Add --apply to delete matched files.
It deletes history items whose timestamp in the filename is strictly earlier than CUTOFF.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -lt 1 || $# -gt 2 ]]; then
  usage >&2
  exit 1
fi

CUTOFF_INPUT="$1"
APPLY=false
if [[ $# -eq 2 ]]; then
  if [[ "$2" != "--apply" ]]; then
    echo "unknown option: $2" >&2
    usage >&2
    exit 1
  fi
  APPLY=true
fi

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

  if [[ "${base}" =~ ([0-9]{14}) ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
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

CUTOFF_TS="$(normalize_cutoff "${CUTOFF_INPUT}")"

mapfile -t matches < <(
  find "${HISTORY_DIR}" -mindepth 1 -depth | while IFS= read -r path; do
    if should_delete_before_cutoff "${path}"; then
      printf '%s\n' "${path}"
    fi
  done | sort
)

if [[ ${#matches[@]} -eq 0 ]]; then
  echo "No matching history items strictly earlier than ${CUTOFF_TS}."
  exit 0
fi

if [[ "${APPLY}" == "true" ]]; then
  echo "Deleting ${#matches[@]} history items strictly earlier than ${CUTOFF_TS}:"
else
  echo "Dry-run: ${#matches[@]} history items would be deleted strictly earlier than ${CUTOFF_TS}:"
fi

for path in "${matches[@]}"; do
  echo "  ${path}"
done

if [[ "${APPLY}" != "true" ]]; then
  echo
  echo "No files were deleted. Re-run with --apply to delete."
  exit 0
fi

for path in "${matches[@]}"; do
  if [[ -d "${path}" ]]; then
    rm -rf -- "${path}"
  else
    rm -f -- "${path}"
  fi
done

echo "Deleted ${#matches[@]} history items."
