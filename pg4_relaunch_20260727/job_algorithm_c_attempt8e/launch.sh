#!/usr/bin/env bash
set -euo pipefail

PACKAGE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PREPARED_DIR=""
MODE=check

usage() {
  printf 'Usage: %s --prepared-dir DIR [--check|--execute]\n' "$0" >&2
}

while (($#)); do
  case "$1" in
    --prepared-dir)
      [[ $# -ge 2 ]] || { usage; exit 64; }
      PREPARED_DIR="$2"
      shift 2
      ;;
    --check)
      MODE=check
      shift
      ;;
    --execute)
      MODE=execute
      shift
      ;;
    *)
      usage
      exit 64
      ;;
  esac
done

[[ -n "${PREPARED_DIR}" ]] || { usage; exit 64; }
PREPARED_DIR="$(cd -- "${PREPARED_DIR}" && pwd)"
python3 "${PACKAGE_DIR}/prepare.py" verify --prepared-dir "${PREPARED_DIR}"

MANIFEST="${PREPARED_DIR}/launch_manifest.json"
MANIFEST_SHA="$(sha256sum "${MANIFEST}" | awk '{print $1}')"
if [[ "${MODE}" == check ]]; then
  printf 'CHECK_ONLY: admission passed; no process started; manifest_sha256=%s\n' "${MANIFEST_SHA}"
  exit 0
fi

EXPECTED_HOST="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["remote_host_shortname"])' "${MANIFEST}")"
ACTUAL_HOST="$(hostname -s)"
[[ "${ACTUAL_HOST}" == "${EXPECTED_HOST}" ]] || {
  printf 'FAIL_CLOSED: host mismatch: actual=%s expected=%s\n' "${ACTUAL_HOST}" "${EXPECTED_HOST}" >&2
  exit 42
}
[[ "${JOB_ALGORITHM_C_AUTHORIZATION:-}" == "${MANIFEST_SHA}" ]] || {
  printf 'FAIL_CLOSED: set JOB_ALGORITHM_C_AUTHORIZATION to the verified manifest SHA\n' >&2
  exit 42
}

WORKSPACE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["workspace"])' "${MANIFEST}")"
PYTHON="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["python"])' "${MANIFEST}")"
CONFIG="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["live_config_path"])' "${MANIFEST}")"
RUNNER="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["runner"])' "${MANIFEST}")"
STATE_DIR="${PREPARED_DIR}/run_state"
mkdir -p "${STATE_DIR}"
exec 9>"${STATE_DIR}/launch.lock"
flock -n 9 || {
  printf 'FAIL_CLOSED: this prepared attempt already owns the launch lock\n' >&2
  exit 42
}
if [[ -f "${STATE_DIR}/complete.json" ]]; then
  printf 'IDEMPOTENT: attempt already completed; refusing duplicate launch\n'
  exit 0
fi
if [[ -f "${STATE_DIR}/started.json" ]]; then
  printf 'FAIL_CLOSED: prior start has no completion seal; audit before any resume\n' >&2
  exit 42
fi

printf '{"manifest_sha256":"%s","pid":%d,"status":"started"}\n' \
  "${MANIFEST_SHA}" "$$" >"${STATE_DIR}/started.json"
set +e
(
  cd -- "${WORKSPACE}"
  OPTIMIZER_CONFIG="${CONFIG}" "${PYTHON}" -u "${RUNNER}"
)
STATUS=$?
set -e
if [[ ${STATUS} -eq 0 ]]; then
  printf '{"manifest_sha256":"%s","status":"complete"}\n' \
    "${MANIFEST_SHA}" >"${STATE_DIR}/complete.json"
else
  printf '{"exit_code":%d,"manifest_sha256":"%s","status":"failed"}\n' \
    "${STATUS}" "${MANIFEST_SHA}" >"${STATE_DIR}/failed.json"
fi
exit "${STATUS}"
