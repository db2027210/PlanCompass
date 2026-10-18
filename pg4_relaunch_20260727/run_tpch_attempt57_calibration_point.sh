#!/usr/bin/env bash
set -euo pipefail

manifest=
calibration_id=
output_dir=
params_json=
while (($#)); do
    case "$1" in
        --manifest) manifest="${2:?}"; shift 2 ;;
        --calibration-id) calibration_id="${2:?}"; shift 2 ;;
        --output-dir) output_dir="${2:?}"; shift 2 ;;
        --params-json) params_json="${2:?}"; shift 2 ;;
        *) printf 'unknown argument: %s\n' "$1" >&2; exit 64 ;;
    esac
done
test -n "$manifest"
test -n "$calibration_id"
test -n "$output_dir"
test -n "$params_json"

attempt=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt57_sf10_strict_r0_seeded_cpu7p7_w36_20260727
runtime="$attempt/followup/runtime"
python_bin=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
contract="$runtime/tpch_attempt57_followup.py"
workload_runner="$runtime/run_tpch_attempt57_weighted_workload.py"
sql_dir="$runtime/queries"
container=pg5_tpch_ap_slot_node01_20260726
database=fullgrid_tpch_sf10_migrated_20260727
container_port=64304
host_port=63301
cpu_limit_cores=7.7
memory_limit_bytes=2820000000
io_read_cap_bytes_s=16777216
io_write_cap_bytes_s=20971520
io_read_cap_mib_s=16
io_write_cap_mib_s=20
io_total_cap_mib_s=36
warmup_io_read_cap_bytes_s="$io_read_cap_bytes_s"
warmup_io_write_cap_bytes_s="$io_write_cap_bytes_s"
io_device=259:2

case "$manifest:$output_dir:$runtime" in
    *node04*|*attempt48*)
        printf 'forbidden TPC-H legacy lineage in attempt57 point\n' >&2
        exit 65
        ;;
esac
case "$(realpath -m "$output_dir")" in
    "$attempt"/followup/direction_calibration_v5/points/*) ;;
    *) printf 'output is not attempt57 calibration-owned: %s\n' "$output_dir" >&2; exit 65 ;;
esac

"$python_bin" "$contract" verify --manifest "$manifest" --phase calibration >/dev/null
manifest_sha256="$(sha256sum "$manifest" | cut -d' ' -f1)"
mkdir -p "$(dirname "$output_dir")"
if [[ -e "$output_dir" ]]; then
    printf 'point output already exists: %s\n' "$output_dir" >&2
    exit 66
fi
mkdir -m 0700 "$output_dir"

params_tsv="$output_dir/requested_parameters.tsv"
"$python_bin" - "$manifest" "$calibration_id" "$params_json" "$params_tsv" <<'PY'
import json
import math
import pathlib
import sys

manifest_path, calibration_id, params_text, output_path = sys.argv[1:]
manifest = json.loads(pathlib.Path(manifest_path).read_text(encoding="utf-8"))
params = json.loads(params_text)
probes = {
    probe["calibration_id"]: probe
    for probe in manifest["calibration"]["probes"]
}
if calibration_id not in probes:
    raise SystemExit(f"calibration id is not in sealed manifest: {calibration_id}")
expected = probes[calibration_id]["params"]
if params != expected or set(params) != set(manifest["parameters"]["active"]):
    raise SystemExit("requested parameters do not match the sealed probe")
for name in manifest["parameters"]["active"]:
    value = float(params[name])
    if not math.isfinite(value) or value <= 0.0:
        raise SystemExit(f"invalid parameter value: {name}={value}")
pathlib.Path(output_path).write_text(
    "".join(f"{name}\t{float(params[name]):.17g}\n" for name in manifest["parameters"]["active"]),
    encoding="utf-8",
)
PY

exec 9>"$attempt/followup/tpch_attempt57_lane.lock"
if ! flock -n 9; then
    printf 'attempt57 lane is already leased\n' >&2
    exit 67
fi
exec 8>/tmp/pg_tpch_sf10_strict_r0_20260727.lock
if ! flock -n 8; then
    printf 'node01 TPC-H SF10 lane is already leased\n' >&2
    exit 67
fi

psql_container() {
    timeout --signal=TERM --kill-after=5s 30s \
        docker exec "$container" psql \
        -p "$container_port" -U postgres -d "$database" \
        -v ON_ERROR_STOP=1 "$@"
}

docker update \
    --cpus "$cpu_limit_cores" \
    --memory "$memory_limit_bytes" \
    --memory-swap "$memory_limit_bytes" \
    "$container" >/dev/null
resource_readback="$(
    docker inspect -f \
        '{{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}' \
        "$container"
)"
test "$resource_readback" = "7700000000 $memory_limit_bytes $memory_limit_bytes"
docker start "$container" >/dev/null 2>&1 || true
for _ in $(seq 1 120); do
    if docker exec "$container" pg_isready \
        -p "$container_port" -d "$database" -U postgres >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
docker exec "$container" pg_isready \
    -p "$container_port" -d "$database" -U postgres >/dev/null

container_id="$(docker inspect -f '{{.Id}}' "$container")"
blkio_root="/sys/fs/cgroup/blkio/docker/$container_id"
test -d "$blkio_root"

set_io_caps() {
    local read_cap="$1"
    local write_cap="$2"
    if [[ "$(cat "$blkio_root/blkio.throttle.read_bps_device")" == \
            "$io_device $read_cap" ]] \
        && [[ "$(cat "$blkio_root/blkio.throttle.write_bps_device")" == \
            "$io_device $write_cap" ]]; then
        return 0
    fi
    printf '%s %s\n' "$io_device" "$read_cap" \
        | sudo -n tee "$blkio_root/blkio.throttle.read_bps_device" >/dev/null
    printf '%s %s\n' "$io_device" "$write_cap" \
        | sudo -n tee "$blkio_root/blkio.throttle.write_bps_device" >/dev/null
    test "$(cat "$blkio_root/blkio.throttle.read_bps_device")" = \
        "$io_device $read_cap"
    test "$(cat "$blkio_root/blkio.throttle.write_bps_device")" = \
        "$io_device $write_cap"
}

verify_parameter_readback() {
    local expected_path="$1"
    local readback_path="$2"
    psql_container -AtF $'\t' -c "
SELECT name, setting
FROM pg_settings
WHERE name IN (
  'seq_page_cost',
  'random_page_cost',
  'cpu_tuple_cost',
  'cpu_index_tuple_cost',
  'cpu_operator_cost',
  'parallel_setup_cost',
  'parallel_tuple_cost'
)
ORDER BY name
" >"$readback_path"
    "$python_bin" - "$expected_path" "$readback_path" <<'PY'
import math
import pathlib
import sys

def load(path):
    return {
        name: float(value)
        for name, value in (
            line.split("\t", 1)
            for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }

requested = load(sys.argv[1])
actual = load(sys.argv[2])
if requested.keys() != actual.keys():
    raise SystemExit(f"parameter readback set mismatch: {actual.keys()}")
for name, expected in requested.items():
    if not math.isclose(actual[name], expected, rel_tol=1e-12, abs_tol=1e-12):
        raise SystemExit(f"parameter readback mismatch: {name}={actual[name]} expected={expected}")
PY
}

defaults_tsv="$output_dir/default_parameters.tsv"
cat >"$defaults_tsv" <<'EOF'
seq_page_cost	1
random_page_cost	4
cpu_tuple_cost	0.01
cpu_index_tuple_cost	0.005
cpu_operator_cost	0.0025
parallel_setup_cost	1000
parallel_tuple_cost	0.1
EOF

formal_cap_controller_pid=
cleanup() {
    local saved_rc=$?
    trap - EXIT
    if [[ -n "$formal_cap_controller_pid" ]]; then
        kill "$formal_cap_controller_pid" >/dev/null 2>&1 || true
        wait "$formal_cap_controller_pid" >/dev/null 2>&1 || true
    fi
    set_io_caps "$warmup_io_read_cap_bytes_s" "$warmup_io_write_cap_bytes_s" || true
    verify_parameter_readback \
        "$defaults_tsv" "$output_dir/exit_global_parameters.tsv" || true
    if [[ "$saved_rc" -ne 0 ]]; then
        exit "$saved_rc"
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

active_workers="$(psql_container -Atqc "
SELECT count(*)
FROM pg_stat_activity
WHERE datname = current_database()
  AND application_name LIKE 'resource-adaptive-tpch-worker-%'
")"
test "$active_workers" = "0"

global_before_path="$output_dir/global_parameters_before.tsv"
verify_parameter_readback "$defaults_tsv" "$global_before_path"

set_io_caps "$warmup_io_read_cap_bytes_s" "$warmup_io_write_cap_bytes_s"
(
    sleep 175
    set_io_caps "$io_read_cap_bytes_s" "$io_write_cap_bytes_s"
) &
formal_cap_controller_pid=$!

PYTHONPATH="$runtime:/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/lib/python3.11/site-packages" \
PYTHONNOUSERSITE=1 \
"$python_bin" -S -u "$workload_runner" \
    --port "$host_port" \
    --database "$database" \
    --container "$container" \
    --sql-dir "$sql_dir" \
    --output-dir "$output_dir/measurement" \
    --workers 36 \
    --seed 20260722 \
    --measurement-schedule balanced_seeded_cycle \
    --warmup-seconds 180 \
    --session-work-mem 1MB \
    --statement-timeout-ms 900000 \
    --minimum-seconds 180 \
    --maximum-seconds 600 \
    --cpu-limit-cores "$cpu_limit_cores" \
    --memory-limit-bytes "$memory_limit_bytes" \
    --io-total-limit-mib-s "$io_total_cap_mib_s" \
    --io-read-cap-mib-s "$io_read_cap_mib_s" \
    --io-write-cap-mib-s "$io_write_cap_mib_s" \
    --stability-target-resource memory \
    --candidate-params-json "$params_json"

if [[ -n "$formal_cap_controller_pid" ]]; then
    kill "$formal_cap_controller_pid" >/dev/null 2>&1 || true
    wait "$formal_cap_controller_pid" >/dev/null 2>&1 || true
    formal_cap_controller_pid=
fi
set_io_caps "$warmup_io_read_cap_bytes_s" "$warmup_io_write_cap_bytes_s"
global_after_path="$output_dir/global_parameters_after.tsv"
verify_parameter_readback \
    "$defaults_tsv" "$global_after_path"
cmp -s "$global_before_path" "$global_after_path"

"$python_bin" - \
    "$output_dir/measurement/summary.json" \
    "$params_tsv" \
    "$output_dir/point_result.json" \
    "$calibration_id" \
    "$manifest_sha256" <<'PY'
import json
import pathlib
import sys

summary_path, params_path, output_path, calibration_id, manifest_sha256 = sys.argv[1:]
summary = json.loads(pathlib.Path(summary_path).read_text(encoding="utf-8"))
params = {
    name: float(value)
    for name, value in (
        line.split("\t", 1)
        for line in pathlib.Path(params_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
}
assessment = summary.get("measurement_assessment") or {}
stable = assessment.get("stable") is True
cleanup = summary.get("worker_cleanup") or {}
application = summary.get("candidate_parameter_application") or {}
effective = summary.get("effective_parameters") or {}
usable = (
    summary.get("status") == "complete"
    and stable
    and int(summary.get("failed_queries") or 0) == 0
    and cleanup.get("database_backends_fully_released") is True
    and effective == params
    and application.get("mode") == "transaction_local_set_local"
    and application.get("alter_system_used") is False
    and application.get("global_settings_changed") is False
    and application.get("immediate_readback_required") is True
    and application.get("workers_expected") == 36
    and application.get("workers_verified") == 36
)
payload = {
    "schema_version": 1,
    "status": "complete" if usable else "failed",
    "calibration_id": calibration_id,
    "manifest_sha256": manifest_sha256,
    "effective_parameters": effective,
    "db_qps": summary.get("db_qps"),
    "resource_utilization": summary.get("resource_utilization"),
    "candidate_measurement_protocol": {
        "stable": stable,
        "correctness_passed": int(summary.get("failed_queries") or 0) == 0,
        "effective_measurement_seconds": summary.get("effective_measurement_seconds"),
        "stop_reason": summary.get("stop_reason"),
    },
    "cleanup": {
        "database_backends_fully_released": cleanup.get(
            "database_backends_fully_released"
        ) is True,
        "defaults_restored": True,
        "global_defaults_unchanged": True,
    },
    "candidate_parameter_application": application,
    "summary_path": str(pathlib.Path(summary_path).resolve()),
}
pathlib.Path(output_path).write_text(
    json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
if not usable:
    raise SystemExit("attempt57 calibration measurement is not usable")
PY
