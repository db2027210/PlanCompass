#!/usr/bin/env bash
# Offline fixture for the fixed node04 gzip destination transaction wrapper.

set -Eeuo pipefail
export LC_ALL=C

readonly PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)
readonly TARGET="${PROJECT_ROOT}/shell/mysql_job_v18_node04_gzip_destination_txn154500.sh"
readonly PUBLISHER="${PROJECT_ROOT}/artifacts/mysql_job_node04_disjoint_successor_v18/44c6bda398c8eee5ba5fdf4bef95947cb497b27a3a161448fc084e344baa40ea/qualification/mysql_job_v17_atomic_tree_publish_v1.py"
fixture_root=$(mktemp -d /tmp/mysql-job-v18-gzip-fixture.XXXXXX)
trap 'rm -rf -- "$fixture_root"' EXIT

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

bash -n "$TARGET"
rg -F "readonly GZIP_BIN='/usr/bin/gzip'" "$TARGET" >/dev/null
rg -F 'readonly BUNDLE="${JOB_ROOT}/runtime_algorithm_c_successor_20260729/bundles/${V18_ID}"' "$TARGET" >/dev/null
rg -F 'cat "${ordered_segments[@]}" | "$GZIP_BIN" -t' "$TARGET" >/dev/null
rg -F '| "$GZIP_BIN" -dc' "$TARGET" >/dev/null
rg -F 'publish_one runtime ' "$TARGET" >/dev/null
rg -F 'publish_one template ' "$TARGET" >/dev/null
rg -F 'require_absent "$TEMPLATE_STAGE"' "$TARGET" >/dev/null
rg -F 'require_absent "$RUNTIME_STAGE"' "$TARGET" >/dev/null
rg -F 'require_absent "$TEMPLATE_FINAL"' "$TARGET" >/dev/null
rg -F 'require_absent "$RUNTIME_FINAL"' "$TARGET" >/dev/null
rg -F 'renameat2(RENAME_NOREPLACE)' "$PUBLISHER" >/dev/null
rg -F 'rename_noreplace(stage, final)' "$PUBLISHER" >/dev/null
if rg -n -i '\bpigz\b' "$TARGET"; then
  fail 'target still contains a pigz dependency'
fi
if rg -n '(^|[;&|[:space:]])(rm|rmdir|unlink|shred|truncate|mv)([[:space:]]|$)' "$TARGET"; then
  fail 'target contains a deletion or replacement command'
fi
runtime_line=$(rg -n '^publish_one runtime ' "$TARGET" | cut -d: -f1)
template_line=$(rg -n '^publish_one template ' "$TARGET" | cut -d: -f1)
[[ "$runtime_line" -lt "$template_line" ]] || fail 'publication order is not runtime then template'

make_tree() {
  local root=$1
  local label=$2
  mkdir -p "$root/nested/empty"
  printf '%s\n' "$label" >"$root/nested/label.txt"
  # Long deterministic content ensures the stream can be divided into four chunks.
  awk 'BEGIN { for (i=0; i<20000; i++) printf "%08d fixture-gzip-ordered-segments\n", i }' >"$root/payload.txt"
  ln "$root/payload.txt" "$root/payload-hardlink.txt"
  ln -s nested/label.txt "$root/label-link"
}

seal_archive() {
  local source=$1
  local archive=$2
  local count=$3
  mkdir -p "$archive"
  tar --create --file=- --format=pax --numeric-owner --acls --xattrs \
    --xattrs-include='*' --sparse --directory="$source" . \
    | /usr/bin/gzip -c >"$archive/archive.tar.gz"
  if [[ "$count" -eq 1 ]]; then
    cp "$archive/archive.tar.gz" "$archive/archive.tar.gz.part-000000"
  else
    split -n "l/${count}" -d -a 6 "$archive/archive.tar.gz" "$archive/archive.tar.gz.part-"
  fi
  find "$archive" -maxdepth 1 -type f -name 'archive.tar.gz.part-*' \
    -printf '%f\n' | sort >"$archive/segments.list"
  [[ $(wc -l <"$archive/segments.list") -eq "$count" ]] || fail 'fixture split count differs'
  (
    cd "$archive"
    xargs sha256sum <segments.list >segments.sha256
    sha256sum segments.list segments.sha256 >control.sha256
  )
}

verify_archive() {
  local archive=$1
  local expected=$2
  local -a ordered_segments=()
  mapfile -t ordered_segments <"$archive/segments.list"
  [[ ${#ordered_segments[@]} -eq "$expected" ]] || fail 'ordered array count differs'
  (
    cd "$archive"
    sha256sum --strict -c control.sha256 >/dev/null
    sha256sum --strict -c segments.sha256 >/dev/null
    cat "${ordered_segments[@]}" | /usr/bin/gzip -t
  )
}

extract_archive() {
  local archive=$1
  local destination=$2
  local -a ordered_segments=()
  mapfile -t ordered_segments <"$archive/segments.list"
  mkdir -p "$destination"
  (
    cd "$archive"
    cat "${ordered_segments[@]}" \
      | /usr/bin/gzip -dc \
      | tar --extract --file=- --same-permissions --numeric-owner \
          --acls --xattrs --xattrs-include='*' --sparse \
          --delay-directory-restore --directory="$destination"
  )
}

make_tree "$fixture_root/template-source" template
make_tree "$fixture_root/runtime-source" runtime
seal_archive "$fixture_root/template-source" "$fixture_root/template-archive" 4
seal_archive "$fixture_root/runtime-source" "$fixture_root/runtime-archive" 1
verify_archive "$fixture_root/template-archive" 4
verify_archive "$fixture_root/runtime-archive" 1

# A reordered raw segment stream must not pass gzip integrity testing.
mapfile -t template_segments <"$fixture_root/template-archive/segments.list"
if (
  cd "$fixture_root/template-archive"
  cat "${template_segments[1]}" "${template_segments[0]}" \
      "${template_segments[2]}" "${template_segments[3]}" \
    | /usr/bin/gzip -t 2>/dev/null
); then
  fail 'reordered template segments unexpectedly passed gzip -t'
fi

extract_archive "$fixture_root/template-archive" "$fixture_root/template-stage" &
template_pid=$!
extract_archive "$fixture_root/runtime-archive" "$fixture_root/runtime-stage" &
runtime_pid=$!
rc=0
wait "$template_pid" || rc=1
wait "$runtime_pid" || rc=1
[[ $rc -eq 0 ]] || fail 'parallel fixture extraction failed'

diff -r "$fixture_root/template-source" "$fixture_root/template-stage"
diff -r "$fixture_root/runtime-source" "$fixture_root/runtime-stage"
printf 'PASS_OFFLINE_FIXTURE: syntax gzip_order sha_controls template_parts=4 runtime_parts=1 parallel_extract no_delete no_overwrite renameat2 publish_order\n'
