#!/usr/bin/env bash
# Fixed node04 destination transaction for the already-created txn154500
# gzip segmented JOB relocation.  This script never removes or overwrites
# an archive, stage, final tree, or evidence file.

set -Eeuo pipefail
umask 077
export LC_ALL=C
export PYTHONDONTWRITEBYTECODE=1

readonly TXN='job-v17-relocate-20260803t154500z-a1b2c3d4'
readonly V18_ID='44c6bda398c8eee5ba5fdf4bef95947cb497b27a3a161448fc084e344baa40ea'
readonly V18_MANIFEST_SHA='6d81666dd710a96af1fa8a9fb81493a348a5975ccf6e3ae980c9f6d33a597b8d'
readonly RUNTIME_ID='3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89'

readonly JOB_ROOT='/mnt/nvme2n1/anonymous/resource_adaptive_mysql/job'
readonly DST_TXN="${JOB_ROOT}/.relocation_v17/${TXN}"
readonly TEMPLATE_ARCHIVE="${DST_TXN}/template"
readonly RUNTIME_ARCHIVE="${DST_TXN}/runtime"
readonly TEMPLATE_FINAL="${JOB_ROOT}/template"
readonly RUNTIME_PARENT="${JOB_ROOT}/runtime_algorithm_c_successor_20260729/runtime"
readonly RUNTIME_FINAL="${RUNTIME_PARENT}/python311-${RUNTIME_ID}"
readonly TEMPLATE_STAGE="${JOB_ROOT}/.template.stage-${TXN}"
readonly RUNTIME_STAGE="${RUNTIME_PARENT}/.python311-${RUNTIME_ID}.stage-${TXN}"
readonly BUNDLE="${JOB_ROOT}/runtime_algorithm_c_successor_20260729/bundles/${V18_ID}"
readonly BOOTSTRAP_PY="/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729/runtime/python311-${RUNTIME_ID}/bin/python3.11"
readonly INVENTORY="${BUNDLE}/qualification/mysql_job_v17_portable_tree_inventory_v1.py"
readonly PUBLISHER="${BUNDLE}/qualification/mysql_job_v17_atomic_tree_publish_v1.py"
readonly GZIP_BIN='/usr/bin/gzip'

ACTION=''

usage() {
  printf '%s\n' \
    "Usage:" \
    "  $0 --check" \
    "  $0 --execute ${TXN}" \
    "" \
    "--check is read-only. --execute creates the two immutable stages, verifies" \
    "them, and publishes runtime first and template second with RENAME_NOREPLACE." \
    "All transaction paths and bundle identities are hard-coded; no cleanup exists."
}

die() {
  printf 'NOT_READY: %s\n' "$*" >&2
  exit 1
}

trap 'printf "NOT_READY: command failed at line %s; no cleanup or overwrite attempted\n" "$LINENO" >&2' ERR

while (($#)); do
  case "$1" in
    --check)
      [[ -z "$ACTION" ]] || die 'choose exactly one action'
      ACTION='check'
      shift
      ;;
    --execute)
      [[ -z "$ACTION" ]] || die 'choose exactly one action'
      [[ ${2-} == "$TXN" ]] || die "--execute requires the exact transaction id: ${TXN}"
      ACTION='execute'
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unsupported argument: $1"
      ;;
  esac
done

[[ -n "$ACTION" ]] || die 'one of --check or --execute is required'

lexists() {
  [[ -e "$1" || -L "$1" ]]
}

require_plain_file() {
  [[ -f "$1" && ! -L "$1" ]] || die "required regular file is absent or symlinked: $1"
}

require_absent() {
  lexists "$1" && die "fresh no-overwrite path already exists: $1"
  return 0
}

require_evidence_pair() {
  local directory=$1
  local name=$2
  require_plain_file "${directory}/${name}"
  require_plain_file "${directory}/${name}.sha256"
  (
    cd "$directory"
    sha256sum --strict -c "${name}.sha256"
  )
}

check_bundle_closure() {
  [[ -d "$BUNDLE" && ! -L "$BUNDLE" ]] || die "v18 control bundle is not published: $BUNDLE"
  require_plain_file "${BUNDLE}/bundle_manifest.json"
  require_plain_file "${BUNDLE}/bundle_manifest.json.sha256"
  "$BOOTSTRAP_PY" -B - "$BUNDLE" "$V18_ID" "$V18_MANIFEST_SHA" <<'PY'
import hashlib
import json
import os
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
bundle_id = sys.argv[2]
expected_manifest_sha = sys.argv[3]

def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()

manifest_path = root / "bundle_manifest.json"
actual_manifest_sha = digest(manifest_path)
if actual_manifest_sha != expected_manifest_sha:
    raise SystemExit("NOT_READY: v18 raw manifest SHA-256 differs")
sidecar = root / "bundle_manifest.json.sha256"
if sidecar.read_text(encoding="ascii") != actual_manifest_sha + "  bundle_manifest.json\n":
    raise SystemExit("NOT_READY: v18 manifest sidecar differs")
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if (root.name != bundle_id
        or manifest.get("bundle_id") != bundle_id
        or manifest.get("schema_version") != "mysql_job_node04_disjoint_successor_bundle_v18"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("postgresql_mutation_prohibited") is not True
        or manifest.get("relocation_contract") != "authority/mysql_job_node04_live_disjoint_relocation_contract_v2.json"
        or manifest.get("relocation_transaction_contract") != "authority/mysql_job_v17_node02_to_node04_tree_relocation_transaction_v1.json"):
    raise SystemExit("NOT_READY: v18 scope or relocation binding differs")
files = manifest.get("files")
if not isinstance(files, dict) or not files:
    raise SystemExit("NOT_READY: v18 file closure is absent")
resolved_root = root.resolve(strict=True)
for relative, expected in sorted(files.items()):
    if (not isinstance(relative, str) or relative.startswith("/")
            or "\\" in relative or ".." in pathlib.PurePosixPath(relative).parts):
        raise SystemExit("NOT_READY: unsafe v18 closure member")
    path = root / relative
    if path.is_symlink() or not path.is_file():
        raise SystemExit("NOT_READY: absent/symlinked v18 closure member: " + relative)
    if resolved_root not in path.resolve(strict=True).parents:
        raise SystemExit("NOT_READY: escaped v18 closure member: " + relative)
    if digest(path) != expected:
        raise SystemExit("NOT_READY: v18 closure hash differs: " + relative)
print("PASS: v18 mysql_only bundle identity and complete file closure")
PY
}

check_archive() {
  local kind=$1
  local directory=$2
  local expected_list=$3
  local expected_count=$4
  local actual_list
  local name
  local -a ordered_segments=()

  [[ -d "$directory" && ! -L "$directory" ]] || die "${kind} archive directory is absent or symlinked"
  for name in control.sha256 segments.list segments.sha256 source-pre.json source-pre.json.sha256 source-post.json source-post.json.sha256 source-stability.json source-stability.json.sha256; do
    require_plain_file "${directory}/${name}"
  done
  actual_list=$(<"${directory}/segments.list")
  [[ "$actual_list" == "$expected_list" ]] || die "${kind} segment list is not the fixed contiguous gzip list"
  [[ $(wc -l <"${directory}/segments.list") -eq "$expected_count" ]] || die "${kind} segment count differs"
  mapfile -t ordered_segments <"${directory}/segments.list"
  [[ ${#ordered_segments[@]} -eq "$expected_count" ]] || die "${kind} ordered segment array count differs"
  while IFS= read -r name; do
    [[ "$name" =~ ^archive\.tar\.gz\.part-[0-9]{6}$ ]] || die "${kind} unsafe segment name"
    require_plain_file "${directory}/${name}"
    [[ -s "${directory}/${name}" ]] || die "${kind} has an empty segment: $name"
  done <"${directory}/segments.list"
  [[ $(find "$directory" -maxdepth 1 -name 'archive.tar.gz.part-*' -printf '%f\n' | wc -l) -eq "$expected_count" ]] || die "${kind} archive has missing or additional gzip parts"
  (
    cd "$directory"
    sha256sum --strict -c control.sha256
    sha256sum --strict -c segments.sha256
    # The array preserves the already-validated contiguous segments.list order.
    cat "${ordered_segments[@]}" | "$GZIP_BIN" -t
  )
  require_evidence_pair "$directory" source-pre.json
  require_evidence_pair "$directory" source-post.json
  require_evidence_pair "$directory" source-stability.json
}

check_source_authority() {
  "$BOOTSTRAP_PY" -B - "$TEMPLATE_ARCHIVE" "$RUNTIME_ARCHIVE" <<'PY'
import hashlib
import json
import pathlib
import sys

expected = {
    "template": "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/template",
    "runtime": "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/runtime_algorithm_c_successor_20260729/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89",
}

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

for kind, raw_directory in zip(("template", "runtime"), sys.argv[1:]):
    directory = pathlib.Path(raw_directory)
    pre_path = directory / "source-pre.json"
    post_path = directory / "source-post.json"
    stability_path = directory / "source-stability.json"
    pre = json.loads(pre_path.read_text(encoding="utf-8"))
    post = json.loads(post_path.read_text(encoding="utf-8"))
    stability = json.loads(stability_path.read_text(encoding="utf-8"))
    for document in (pre, post):
        if (document.get("schema_version") != "mysql_job_portable_tree_inventory_v1"
                or document.get("status") != "PASS_EXACT_READ_ONLY_INVENTORY_NOT_COPY_AUTHORITY"
                or document.get("hostname") != "node02"
                or document.get("root") != expected[kind]
                or document.get("remote_mutation_performed") is not False):
            raise SystemExit("NOT_READY: %s source inventory authority differs" % kind)
    if (stability.get("schema_version") != "mysql_job_portable_tree_copy_comparison_v1"
            or stability.get("status") != "PASS_SOURCE_DESTINATION_EXACT_TREE_IDENTITY"
            or stability.get("source_inventory_sha256") != sha(pre_path)
            or stability.get("destination_inventory_sha256") != sha(post_path)
            or stability.get("content_identity_sha256") != post.get("content_identity_sha256")):
        raise SystemExit("NOT_READY: %s source pre/post stability differs" % kind)
    if kind == "runtime" and (post.get("entry_count") != 40852
            or post.get("logical_unique_file_bytes") != 1077311137):
        raise SystemExit("NOT_READY: runtime authoritative count/bytes differ")
print("PASS: immutable node02 source pre/post authorities")
PY
}

check_fresh_outputs() {
  local path
  require_absent "$TEMPLATE_STAGE"
  require_absent "$RUNTIME_STAGE"
  require_absent "$TEMPLATE_FINAL"
  require_absent "$RUNTIME_FINAL"
  for path in \
    "$TEMPLATE_ARCHIVE/stage.json" "$TEMPLATE_ARCHIVE/stage.json.sha256" \
    "$TEMPLATE_ARCHIVE/source-stage.json" "$TEMPLATE_ARCHIVE/source-stage.json.sha256" \
    "$TEMPLATE_ARCHIVE/publication.json" "$TEMPLATE_ARCHIVE/publication.json.sha256" \
    "$TEMPLATE_ARCHIVE/final.json" "$TEMPLATE_ARCHIVE/final.json.sha256" \
    "$TEMPLATE_ARCHIVE/source-final.json" "$TEMPLATE_ARCHIVE/source-final.json.sha256" \
    "$RUNTIME_ARCHIVE/stage.json" "$RUNTIME_ARCHIVE/stage.json.sha256" \
    "$RUNTIME_ARCHIVE/source-stage.json" "$RUNTIME_ARCHIVE/source-stage.json.sha256" \
    "$RUNTIME_ARCHIVE/publication.json" "$RUNTIME_ARCHIVE/publication.json.sha256" \
    "$RUNTIME_ARCHIVE/final.json" "$RUNTIME_ARCHIVE/final.json.sha256" \
    "$RUNTIME_ARCHIVE/source-final.json" "$RUNTIME_ARCHIVE/source-final.json.sha256"; do
    require_absent "$path"
  done
}

validate_comparison() {
  local path=$1
  "$BOOTSTRAP_PY" -B - "$path" <<'PY'
import json
import pathlib
import sys
value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
if (value.get("schema_version") != "mysql_job_portable_tree_copy_comparison_v1"
        or value.get("status") != "PASS_SOURCE_DESTINATION_EXACT_TREE_IDENTITY"):
    raise SystemExit("NOT_READY: portable tree comparison did not pass")
PY
}

extract_archive() {
  local directory=$1
  local stage=$2
  local -a ordered_segments=()
  mapfile -t ordered_segments <"${directory}/segments.list"
  (
    cd "$directory"
    cat "${ordered_segments[@]}" \
      | "$GZIP_BIN" -dc \
      | tar --extract --file=- --same-owner --same-permissions --numeric-owner \
          --acls --xattrs --xattrs-include='*' --sparse --delay-directory-restore \
          --directory="$stage"
  )
}

inventory_stage() {
  local kind=$1
  local stage=$2
  local directory=$3
  "$BOOTSTRAP_PY" -B "$INVENTORY" \
    --root "$stage" --label "${kind}-stage-${TXN}" \
    --output "$directory/stage.json"
  "$BOOTSTRAP_PY" -B "$INVENTORY" \
    --compare-source "$directory/source-post.json" \
    --compare-destination "$directory/stage.json" \
    --output "$directory/source-stage.json"
  validate_comparison "$directory/source-stage.json"
}

publish_one() {
  local kind=$1
  local stage=$2
  local final=$3
  local directory=$4
  "$BOOTSTRAP_PY" -B "$PUBLISHER" \
    --kind "$kind" --transaction-id "$TXN" \
    --stage "$stage" --final "$final" \
    --source-pre-inventory "$directory/source-pre.json" \
    --source-inventory "$directory/source-post.json" \
    --stage-inventory "$directory/stage.json" \
    --source-stability-comparison "$directory/source-stability.json" \
    --comparison "$directory/source-stage.json" \
    --output "$directory/publication.json"
  "$BOOTSTRAP_PY" -B "$INVENTORY" \
    --root "$final" --label "${kind}-visible-final-${TXN}" \
    --output "$directory/final.json"
  "$BOOTSTRAP_PY" -B "$INVENTORY" \
    --compare-source "$directory/source-post.json" \
    --compare-destination "$directory/final.json" \
    --output "$directory/source-final.json"
  validate_comparison "$directory/source-final.json"
}

[[ $(hostname) == 'node04' ]] || die 'this destination transaction is node04-only'
for command in sha256sum tar find stat wc cat; do
  command -v "$command" >/dev/null || die "required command is absent: $command"
done
[[ -f "$GZIP_BIN" && ! -L "$GZIP_BIN" && -x "$GZIP_BIN" ]] || die 'fixed /usr/bin/gzip is absent, symlinked, or not executable'
[[ -x "$BOOTSTRAP_PY" && ! -L "$BOOTSTRAP_PY" ]] || die 'fixed bootstrap Python is absent, symlinked, or not executable'
[[ -d "$JOB_ROOT" && ! -L "$JOB_ROOT" ]] || die 'JOB destination root is absent or symlinked'
[[ -d "$RUNTIME_PARENT" && ! -L "$RUNTIME_PARENT" ]] || die 'runtime final parent is absent or symlinked'
[[ -d "$DST_TXN" && ! -L "$DST_TXN" ]] || die 'fixed destination transaction directory is absent or symlinked'
[[ $(stat -c '%d' "$JOB_ROOT") == $(stat -c '%d' "$RUNTIME_PARENT") ]] || die 'template/runtime final parents are not on one filesystem'

readonly TEMPLATE_LIST=$'archive.tar.gz.part-000000\narchive.tar.gz.part-000001\narchive.tar.gz.part-000002\narchive.tar.gz.part-000003'
readonly RUNTIME_LIST='archive.tar.gz.part-000000'

check_archive template "$TEMPLATE_ARCHIVE" "$TEMPLATE_LIST" 4
check_archive runtime "$RUNTIME_ARCHIVE" "$RUNTIME_LIST" 1
check_source_authority
check_bundle_closure
check_fresh_outputs

if [[ "$ACTION" == 'check' ]]; then
  printf 'PASS_READ_ONLY: txn=%s gzip_segments=template:4,runtime:1 finals=absent bundle=%s\n' "$TXN" "$V18_ID"
  exit 0
fi

# All immutable inputs and every no-overwrite target passed before first write.
mkdir -m 0700 "$TEMPLATE_STAGE" "$RUNTIME_STAGE"

extract_archive "$TEMPLATE_ARCHIVE" "$TEMPLATE_STAGE" &
template_extract_pid=$!
extract_archive "$RUNTIME_ARCHIVE" "$RUNTIME_STAGE" &
runtime_extract_pid=$!
extract_rc=0
wait "$template_extract_pid" || extract_rc=1
wait "$runtime_extract_pid" || extract_rc=1
[[ $extract_rc -eq 0 ]] || die 'parallel extraction failed; stages retained, finals untouched'

inventory_stage template "$TEMPLATE_STAGE" "$TEMPLATE_ARCHIVE" &
template_inventory_pid=$!
inventory_stage runtime "$RUNTIME_STAGE" "$RUNTIME_ARCHIVE" &
runtime_inventory_pid=$!
inventory_rc=0
wait "$template_inventory_pid" || inventory_rc=1
wait "$runtime_inventory_pid" || inventory_rc=1
[[ $inventory_rc -eq 0 ]] || die 'parallel stage inventory failed; stages retained, finals untouched'

# Deliberate order: runtime first, then its final readback; template second.
# A runtime-only publication never authorizes JOB and is not auto-reverted.
publish_one runtime "$RUNTIME_STAGE" "$RUNTIME_FINAL" "$RUNTIME_ARCHIVE"
publish_one template "$TEMPLATE_STAGE" "$TEMPLATE_FINAL" "$TEMPLATE_ARCHIVE"

printf 'PASS_PUBLISHED_NOT_LAUNCH_AUTHORITY: txn=%s order=runtime,template exact_final_readback=true\n' "$TXN"
