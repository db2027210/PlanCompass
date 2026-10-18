#!/usr/bin/env bash
set -euo pipefail

# Move large frozen history files from /home.../resource_adaptive to /disk2
# while leaving symlinks behind, so existing reports and tools can still open
# the original paths. Default mode is dry-run; pass --execute to move.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHIVE_ROOT="${ARCHIVE_ROOT:-/disk2/resource_adaptive_archive/resource_adaptive_history_offload_$(date +%Y%m%d)}"
MIN_SIZE_MB="${MIN_SIZE_MB:-300}"
BEFORE_DATE="${BEFORE_DATE:-2026-07-01}"
EXECUTE=0
INCLUDE_JULY_FROZEN=0
LIMIT="${LIMIT:-0}"

usage() {
  cat <<USAGE
Usage: $0 [--execute] [--include-july-frozen] [--archive-root PATH] [--min-size-mb N] [--before YYYY-MM-DD] [--limit N]

Default is dry-run. Candidates are large history files under:
  history/pg_benchmark_bottleneck_matrix

Safety:
  - Does not delete files.
  - Moves each file to ARCHIVE_ROOT preserving relative path.
  - Replaces original with a symlink to the archived file.
  - Writes a manifest TSV under ARCHIVE_ROOT.
  - Excludes the current v6 Memory run by default.

Recommended first command:
  $0 --min-size-mb 300 --before 2026-07-01

Recommended execution to free >40G from old June logs:
  $0 --execute --min-size-mb 300 --before 2026-07-01
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --execute)
      EXECUTE=1
      shift
      ;;
    --include-july-frozen)
      INCLUDE_JULY_FROZEN=1
      shift
      ;;
    --archive-root)
      ARCHIVE_ROOT="$2"
      shift 2
      ;;
    --min-size-mb)
      MIN_SIZE_MB="$2"
      shift 2
      ;;
    --before)
      BEFORE_DATE="$2"
      shift 2
      ;;
    --limit)
      LIMIT="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

cd "${PROJECT_ROOT}"

if [[ ! -d /disk2 ]]; then
  echo "ERROR: /disk2 does not exist" >&2
  exit 1
fi

if [[ "${EXECUTE}" -eq 1 ]]; then
  mkdir -p "${ARCHIVE_ROOT}"
  if [[ ! -w "${ARCHIVE_ROOT}" ]]; then
    echo "ERROR: archive root is not writable: ${ARCHIVE_ROOT}" >&2
    exit 1
  fi
fi

manifest="${ARCHIVE_ROOT}/manifest.tsv"
tmp_list="$(mktemp)"
trap 'rm -f "${tmp_list}"' EXIT

find history/pg_benchmark_bottleneck_matrix \
  -type f \
  \( -name '*.log' -o -name '*optimizer_history*.jsonl' -o -name '*_db_monitor.csv' \) \
  -size +"${MIN_SIZE_MB}"M \
  ! -path '*pg_bonect_phase2_dbqps_amp_binary_top40_memory_strict90_vm4g_resource_sanity_v6_20260706_015306/*' \
  -printf '%T@	%s	%TY-%Tm-%Td	%p\n' \
  | awk -F '\t' -v before="${BEFORE_DATE}" -v include_july="${INCLUDE_JULY_FROZEN}" '
      {
        date=$3
        path=$4
        if (date < before || include_july == "1") {
          print $0
        }
      }
    ' \
  | sort -k2,2nr > "${tmp_list}"

if [[ "${LIMIT}" != "0" ]]; then
  head -n "${LIMIT}" "${tmp_list}" > "${tmp_list}.limited"
  mv "${tmp_list}.limited" "${tmp_list}"
fi

total_bytes="$(awk -F '\t' '{sum += $2} END {printf "%.0f", sum}' "${tmp_list}")"
count="$(wc -l < "${tmp_list}" | tr -d ' ')"
total_gb="$(awk -v b="${total_bytes}" 'BEGIN {printf "%.2f", b/1024/1024/1024}')"

echo "archive_root=${ARCHIVE_ROOT}"
echo "mode=$([[ "${EXECUTE}" -eq 1 ]] && echo execute || echo dry-run)"
echo "candidate_count=${count}"
echo "candidate_total_gb=${total_gb}"
echo
awk -F '\t' '{printf "%10.2f GiB  %s  %s\n", $2/1024/1024/1024, $3, $4}' "${tmp_list}" | head -80

if [[ "${EXECUTE}" -ne 1 ]]; then
  echo
  echo "DRY RUN ONLY. Re-run with --execute to move files and leave symlinks."
  exit 0
fi

{
  echo -e "timestamp	source_path	archive_path	bytes	mtime_date"
} > "${manifest}"

while IFS=$'\t' read -r _mtime bytes date path; do
  [[ -n "${path}" ]] || continue
  if [[ -L "${path}" ]]; then
    echo "skip symlink: ${path}"
    continue
  fi
  if [[ ! -f "${path}" ]]; then
    echo "skip missing: ${path}"
    continue
  fi
  dest="${ARCHIVE_ROOT}/${path}"
  mkdir -p "$(dirname "${dest}")"
  if [[ -e "${dest}" ]]; then
    echo "ERROR: destination already exists: ${dest}" >&2
    exit 1
  fi
  mv "${path}" "${dest}"
  ln -s "${dest}" "${path}"
  echo -e "$(date -Is)	${path}	${dest}	${bytes}	${date}" >> "${manifest}"
  echo "moved: ${path} -> ${dest}"
done < "${tmp_list}"

echo
echo "DONE. Manifest: ${manifest}"
df -h / /disk2 2>/dev/null || df -h /
