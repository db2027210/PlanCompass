#!/usr/bin/env bash
# Prepared offline. Run only on node04 after the relocation publication passes.
set -euo pipefail

JOB_ID=44c6bda398c8eee5ba5fdf4bef95947cb497b27a3a161448fc084e344baa40ea
JOB_MANIFEST_SHA=6d81666dd710a96af1fa8a9fb81493a348a5975ccf6e3ae980c9f6d33a597b8d
UNIFIED_ID=028da5fabe8464197f1c79e85c300807817980fc750383ea1fbdcf32f3a47f87
UNIFIED_MANIFEST_SHA=35976e9bf8485f9a629d1602a1c947f9cfb60155e7b734011a92e9449a33d010
TPCH_ID=87ed507ed689c103781db5f96332ae7875e4773d04d69b98a7e63e3d4097b257
TPCC_ID=7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77

RUNTIME_ROOT=/mnt/nvme2n1/anonymous/resource_adaptive_mysql/job/runtime_algorithm_c_successor_20260729
: "${PY:=$RUNTIME_ROOT/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89/bin/python3.11}"
: "${JOB_BUNDLE:=$RUNTIME_ROOT/bundles/$JOB_ID}"
: "${UNIFIED_BUNDLE:=/mnt/nvme2n1/anonymous/resource_adaptive_mysql/job/control_bundles/$UNIFIED_ID}"
: "${TPCH_BUNDLE:=/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729/bundles/$TPCH_ID}"
: "${TPCC_BUNDLE:=/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/bundles/$TPCC_ID}"
ENTRY=$UNIFIED_BUNDLE/qualification/mysql_unified_job_tpch_tpcc_collector_entry_v1.py
PAIRED=$UNIFIED_BUNDLE/controller/mysql_unified_qualification_paired_authority_v2.py
SOLO_MATERIALIZER=$UNIFIED_BUNDLE/controller/mysql_unified_qualification_solo_authority_v1.py
AGGREGATE=$UNIFIED_BUNDLE/qualification/mysql_unified_three_pair_gate_aggregate_v2.py
PROCESS_INVENTORY=$UNIFIED_BUNDLE/qualification/mysql_unified_fresh_process_inventory_v1.py

die() { printf 'NOT_READY: %s\n' "$*" >&2; exit 2; }
need() { test -n "${!1:-}" || die "environment variable $1 is required"; }
absent() { test ! -e "$1" && test ! -L "$1" || die "refusing existing output: $1"; }

json_get() {
  "$PY" -B -c 'import json,sys
d=json.load(open(sys.argv[1],encoding="utf-8"))
for part in sys.argv[2].split("."):
 d=d[int(part)] if isinstance(d,list) else d[part]
if isinstance(d,bool): print("true" if d else "false")
elif d is None: print("")
elif isinstance(d,(dict,list)): print(json.dumps(d,separators=(",",":"),sort_keys=True))
else: print(d)' "$1" "$2"
}

sidecar() {
  local path=$1 digest
  digest=$(sha256sum "$path")
  digest=${digest%% *}
  (umask 077; printf '%s  %s\n' "$digest" "${path##*/}" > "$path.sha256")
  chmod 0400 "$path" "$path.sha256"
}

guard() {
  test "$(hostname)" = node04 || die "host is not node04"
  test "$(id -u)" -eq 0 || die "host root is required by the sealed inventory"
  test -x "$PY" || die "sealed Python is absent"
  test "${JOB_BUNDLE##*/}" = "$JOB_ID" || die "JOB bundle is not corrected v18"
  test "${UNIFIED_BUNDLE##*/}" = "$UNIFIED_ID" || die "collector is not corrected unified v2"
  case "${JOB_BUNDLE##*/}:${UNIFIED_BUNDLE##*/}" in
    *1f5d221658221f1a8b39862543d35684acc5f1c1cb0ce10273bd4d6ac1aeac05*|\
    *6cd345185daf38c1b7e58d1abbfa6dd1a6261f9bd6cb53fbc974289ecb455972*|\
    *347c8a19bea3917147989ea2f545d98a71ce8b3c910d09ebb9a6bcc9eddf21f9*|\
    *dd34adb64d4394fd3f696259a77c898ef122ec113da59faf0278b7ada4059980*|\
    *f939ac68906723ebf18f4ef7dc4771b0ec533de6c37286f350cb2e892044e486*|\
    *2af6a73fb96d39d70e648d9fc984bfe5f0b0378bb41b5fb5d5a981579bc87dbc*|\
    *906d7592f19d7a75f0beebd2d25cb4d4ca391a4d11ad283da90c421cc9ed0057*|\
    *5379fe2ac520988d0d264fa7f13b47ec1d90df803102f8329e302b2d3ca87751*)
      die "invalidated v17/v1/intermediate identity selected" ;;
  esac
  "$PY" -B -c 'import hashlib,json,pathlib,sys
specs=[
 (pathlib.Path(sys.argv[1]),sys.argv[2],sys.argv[3],"mysql_job_node04_disjoint_successor_bundle_v18"),
 (pathlib.Path(sys.argv[4]),sys.argv[5],sys.argv[6],"mysql_unified_job_tpch_tpcc_collector_bundle_v2"),
 (pathlib.Path(sys.argv[7]),sys.argv[8],None,"mysql_tpch_v23_recalibrated_successor_v3"),
 (pathlib.Path(sys.argv[9]),sys.argv[10],None,"mysql_tpcc_prefault_successor_bundle_v16")]
for root,bid,msha,schema in specs:
 mpath=root/"bundle_manifest.json"; raw=mpath.read_bytes(); m=json.loads(raw)
 assert root.name==bid==m["bundle_id"] and m["schema_version"]==schema
 if msha is not None: assert hashlib.sha256(raw).hexdigest()==msha
 assert m["formal_scope"]=="mysql_only" and m["cross_engine_comparison_prohibited"] is True
 for rel,want in m["files"].items():
  p=root/rel; assert p.is_file() and not p.is_symlink()
  assert hashlib.sha256(p.read_bytes()).hexdigest()==want
r=json.loads((specs[0][0]/"authority/mysql_job_node04_live_disjoint_relocation_contract_v2.json").read_text())
assert r["job"]["database_cpuset"]=="25-27,61-63"
assert r["job"]["client_cpuset"]=="28-31,64-67"
assert r["node04_live_expected"]["tpch"]["database_cpuset"]=="18-20,54-56"
assert r["fresh_current_full_process_zero_conflict_verified"] is False
print("PASS_EXACT_CORRECTED_IDENTITIES")' \
    "$JOB_BUNDLE" "$JOB_ID" "$JOB_MANIFEST_SHA" \
    "$UNIFIED_BUNDLE" "$UNIFIED_ID" "$UNIFIED_MANIFEST_SHA" \
    "$TPCH_BUNDLE" "$TPCH_ID" "$TPCC_BUNDLE" "$TPCC_ID"
  test -x "$ENTRY" && test -x "$PAIRED" && test -x "$SOLO_MATERIALIZER" && test -x "$PROCESS_INVENTORY" && test -x "$AGGREGATE" \
    || die "unified producer closure is absent"
}

collect_one() {
  local cpus=$1 bundle=$2 db=$3 client=$4 defaults=$5 output=$6
  absent "$output"
  taskset -c "$cpus" "$PY" -B "$ENTRY" --collect-only \
    --bundle "$bundle" --container "$db" \
    --mysql-client-container "$client" --mysql-binary-in-client /usr/bin/mysql \
    --mysql-defaults-file "$defaults" --output "$output"
}

verify_readbacks() {
  "$PY" -B -c 'import hashlib,json,pathlib,sys,time
expected={
 "job_memory":(sys.argv[4],"25-27,61-63","28-31,64-67","259:3",65102),
 "tpch_memory":(sys.argv[5],"18-20,54-56","0-7,36-43","259:1",65103),
 "tpcc_cpu":(sys.argv[6],"21-24,57-60","8-15,44-51","259:2",65106)}
for p in map(pathlib.Path,sys.argv[1:4]):
 d=json.loads(p.read_text()); row=expected[d["scenario"]]
 assert d["status"]=="PASS_LIVE_READBACK_NOT_LAUNCH_AUTHORITY"
 assert d["hostname"]=="node04" and d["bundle_id"]==row[0]
 assert d["resource_envelope"]["cpuset_cpus"]==row[1]
 assert d["client_resource_envelope"]["cpuset_cpus"]==row[2]
 assert d["storage"]["major_minor"]==row[3] and d["mysql_port"]==row[4]
 assert time.time()-float(d["collected_epoch"]) <= 120
 s=p.with_name(p.name+".sha256")
 assert s.read_text()==hashlib.sha256(p.read_bytes()).hexdigest()+"  "+p.name+"\n"
print("PASS_THREE_FRESH_EXACT_READBACKS")' \
    "$JOB_READBACK" "$TPCH_READBACK" "$TPCC_READBACK" \
    "$JOB_ID" "$TPCH_ID" "$TPCC_ID"
}

readbacks() {
  guard
  for name in JOB_DB JOB_CLIENT JOB_DEFAULTS TPCH_DB TPCH_CLIENT TPCH_DEFAULTS TPCC_DB TPCC_CLIENT TPCC_DEFAULTS JOB_READBACK TPCH_READBACK TPCC_READBACK; do need "$name"; done
  collect_one '28-31,64-67' "$JOB_BUNDLE" "$JOB_DB" "$JOB_CLIENT" "$JOB_DEFAULTS" "$JOB_READBACK" & p1=$!
  collect_one '0-7,36-43' "$TPCH_BUNDLE" "$TPCH_DB" "$TPCH_CLIENT" "$TPCH_DEFAULTS" "$TPCH_READBACK" & p2=$!
  collect_one '8-15,44-51' "$TPCC_BUNDLE" "$TPCC_DB" "$TPCC_CLIENT" "$TPCC_DEFAULTS" "$TPCC_READBACK" & p3=$!
  rc=0; wait "$p1" || rc=1; wait "$p2" || rc=1; wait "$p3" || rc=1
  test "$rc" -eq 0 || die "one or more readbacks failed"
  verify_readbacks
}

process_preflight() {
  guard
  for name in JOB_READBACK TPCH_READBACK TPCC_READBACK PROCESS_ROOT; do need "$name"; done
  absent "$PROCESS_ROOT"; mkdir -m 0700 "$PROCESS_ROOT"
  "$PY" -B "$PROCESS_INVENTORY" --bundle "$JOB_BUNDLE" --readback "$JOB_READBACK" --output "$PROCESS_ROOT/job.json"
  "$PY" -B "$PROCESS_INVENTORY" --bundle "$TPCH_BUNDLE" --readback "$TPCH_READBACK" --output "$PROCESS_ROOT/tpch.json"
  "$PY" -B "$PROCESS_INVENTORY" --bundle "$TPCC_BUNDLE" --readback "$TPCC_READBACK" --output "$PROCESS_ROOT/tpcc.json"
  "$PY" -B -c 'import json,pathlib,sys,time
for p in map(pathlib.Path,sys.argv[1:]):
 d=json.loads(p.read_text()); assert d["status"]=="PASS_FRESH_FULL_PROCESS_ZERO_CONFLICT_NOT_LAUNCH_AUTHORITY"
 assert time.time()-d["collected_epoch"]<=120 and d["inventory"]["conflicts"]==[]
print("PASS_THREE_FRESH_FULL_PROCESS_ZERO_CONFLICT_PREFLIGHTS")' \
    "$PROCESS_ROOT/job.json" "$PROCESS_ROOT/tpch.json" "$PROCESS_ROOT/tpcc.json"
}

verify_qualification() {
  "$PY" -B -c 'import hashlib,json,pathlib,sys
p=pathlib.Path(sys.argv[1]); d=json.loads(p.read_text()); u=pathlib.Path(sys.argv[2])
assert d["status"]=="PASS_COMPLETE_8_STREAM_DEBUG_QUALIFICATION_NOT_GATE_AUTHORITY"
assert d["collector_sha256"]==hashlib.sha256((u/"bundle_manifest.json").read_bytes()).hexdigest()
assert d["formal_authorized"] is False and d["accepted_round_authorized"] is False
assert len(d["samples"])>=181
for sample in d["samples"]:
 r=sample["required_streams"]["process_cpu_affinity_and_device_inventory"]["rolling_full_host_inventory"]
 assert r["inventory_complete"] is True and r["conflicts"]==[]
 assert r["permission_failure_count"]==0 and r["race_count"]==0
 assert r["foreign_writer_count"]==0
 if d["scenario"]=="job_memory":
  assert r["target_infrastructure_daemon_count"]==1
  assert r["target_infrastructure_is_foreign_writer"] is False
 else:
  assert r["target_dedicated_docker_daemon_verified"] is True
  assert int(r["target_dedicated_docker_daemon_pid"])>1
print("PASS_FRESH_ROLLING_FULL_PROCESS_ZERO_CONFLICT",d["scenario"],d["mode"])' "$1" "$UNIFIED_BUNDLE"
}

materialize_solo() {
  guard
  for name in SOLO_BUNDLE SOLO_READBACK SOURCE_SOLO_COMMAND SOURCE_SOLO_AUTHORITY SOLO_ATTEMPT SOLO_MATERIAL_ROOT; do need "$name"; done
  absent "$SOLO_MATERIAL_ROOT"
  "$PY" -B "$SOLO_MATERIALIZER" \
    --scenario-bundle "$SOLO_BUNDLE" --collector-bundle "$UNIFIED_BUNDLE" \
    --collector "$ENTRY" --own-readback "$SOLO_READBACK" \
    --source-solo-command "$SOURCE_SOLO_COMMAND" \
    --source-solo-authority "$SOURCE_SOLO_AUTHORITY" \
    --qualification-attempt-id "$SOLO_ATTEMPT" \
    --output-directory "$SOLO_MATERIAL_ROOT" --ttl-seconds 900
}

solo() {
  guard
  for name in SOLO_CPUSET SOLO_BUNDLE SOLO_DB SOLO_CLIENT SOLO_DEFAULTS SOLO_TOKEN SOLO_AUTHORITY SOLO_COMMAND SOLO_OUTPUT; do need "$name"; done
  absent "$SOLO_OUTPUT"
  taskset -c "$SOLO_CPUSET" "$PY" -B "$ENTRY" --qualification-solo \
    --bundle "$SOLO_BUNDLE" --container "$SOLO_DB" \
    --mysql-client-container "$SOLO_CLIENT" --mysql-binary-in-client /usr/bin/mysql \
    --mysql-defaults-file "$SOLO_DEFAULTS" --output "$SOLO_OUTPUT" \
    --main-agent-token-file "$SOLO_TOKEN" \
    --main-agent-authority "$SOLO_AUTHORITY" --command-spec "$SOLO_COMMAND"
  verify_qualification "$SOLO_OUTPUT"
}

host_stream_for() {
  "$PY" -B -c 'import json,pathlib,sys
d=json.load(open(sys.argv[1])); root=pathlib.Path(d["measurement_output"]["host_tmpfs_directory"])
print(root/("qualification_paired_"+sys.argv[2].replace("_","-")+".host_metrics.jsonl"))' "$1" "$2"
}

materialize_pair() {
  guard
  for name in PAIR_ROOT A_BUNDLE B_BUNDLE A_READBACK B_READBACK A_SOLO_COMMAND A_SOLO_AUTHORITY B_SOLO_COMMAND B_SOLO_AUTHORITY A_ATTEMPT B_ATTEMPT A_SCENARIO B_SCENARIO; do need "$name"; done
  absent "$PAIR_ROOT"; mkdir -m 0700 "$PAIR_ROOT"
  a_stream=$(host_stream_for "$A_SOLO_COMMAND" "$B_SCENARIO")
  b_stream=$(host_stream_for "$B_SOLO_COMMAND" "$A_SCENARIO")
  test -d "${a_stream%/*}" && test -d "${b_stream%/*}" || die "metric stream parent absent"
  "$PY" -B "$PAIRED" --scenario-bundle "$A_BUNDLE" \
    --collector-bundle "$UNIFIED_BUNDLE" --collector "$ENTRY" \
    --own-readback "$A_READBACK" --peer-readback "$B_READBACK" \
    --solo-command "$A_SOLO_COMMAND" --solo-authority "$A_SOLO_AUTHORITY" \
    --qualification-attempt-id "$A_ATTEMPT" --peer-qualification-attempt-id "$B_ATTEMPT" \
    --peer-workload-metric-stream "$b_stream" --output-directory "$PAIR_ROOT/a-authority"
  "$PY" -B "$PAIRED" --scenario-bundle "$B_BUNDLE" \
    --collector-bundle "$UNIFIED_BUNDLE" --collector "$ENTRY" \
    --own-readback "$B_READBACK" --peer-readback "$A_READBACK" \
    --solo-command "$B_SOLO_COMMAND" --solo-authority "$B_SOLO_AUTHORITY" \
    --qualification-attempt-id "$B_ATTEMPT" --peer-qualification-attempt-id "$A_ATTEMPT" \
    --peer-workload-metric-stream "$a_stream" --output-directory "$PAIR_ROOT/b-authority"
}

launch_side() {
  local cpus=$1 bundle=$2 defaults=$3 launch=$4 prefix=$5
  local db client peer peer_readback output token authority command
  db=$(json_get "$launch" database_container); client=$(json_get "$launch" client_container)
  peer=$(json_get "$launch" peer_container); peer_readback=$(json_get "$launch" peer_readback)
  output=$(json_get "$launch" output); token=$(json_get "$launch" main_agent_token_file)
  authority=$(json_get "$launch" main_agent_authority); command=$(json_get "$launch" command_spec)
  absent "$output"
  taskset -c "$cpus" "$PY" -B "$ENTRY" --qualification-paired \
    --bundle "$bundle" --container "$db" --mysql-client-container "$client" \
    --mysql-binary-in-client /usr/bin/mysql --mysql-defaults-file "$defaults" \
    --output "$output" --main-agent-token-file "$token" \
    --main-agent-authority "$authority" --command-spec "$command" \
    --peer-readback "$peer_readback" --peer-container "$peer" \
    > "$PAIR_ROOT/$prefix.stdout.log" 2> "$PAIR_ROOT/$prefix.stderr.log"
}

launch_pair() {
  guard
  for name in PAIR_ROOT A_BUNDLE B_BUNDLE A_DEFAULTS B_DEFAULTS A_CPUSET B_CPUSET; do need "$name"; done
  test -f "$PAIR_ROOT/a-authority/launch.json" && test -f "$PAIR_ROOT/b-authority/launch.json" || die "paired authorities absent"
  launch_side "$A_CPUSET" "$A_BUNDLE" "$A_DEFAULTS" "$PAIR_ROOT/a-authority/launch.json" a & p1=$!
  launch_side "$B_CPUSET" "$B_BUNDLE" "$B_DEFAULTS" "$PAIR_ROOT/b-authority/launch.json" b & p2=$!
  rc=0; wait "$p1" || rc=1; wait "$p2" || rc=1; test "$rc" -eq 0 || die "paired launch failed"
  a_output=$(json_get "$PAIR_ROOT/a-authority/launch.json" output)
  b_output=$(json_get "$PAIR_ROOT/b-authority/launch.json" output)
  verify_qualification "$a_output"; verify_qualification "$b_output"
}

compare_gate() {
  guard
  for name in A_SOLO_EVIDENCE A_PAIRED_EVIDENCE B_SOLO_EVIDENCE B_PAIRED_EVIDENCE GATE_SCENARIO GATE_TOKEN GATE_OUTPUT; do need "$name"; done
  absent "$GATE_OUTPUT"
  "$PY" -B "$ENTRY" --compare-bidirectional \
    --a-solo-evidence "$A_SOLO_EVIDENCE" --a-paired-evidence "$A_PAIRED_EVIDENCE" \
    --b-solo-evidence "$B_SOLO_EVIDENCE" --b-paired-evidence "$B_PAIRED_EVIDENCE" \
    --gate-scenario "$GATE_SCENARIO" --host-id node04 \
    --attempt-token "$GATE_TOKEN" --output "$GATE_OUTPUT"
}

aggregate() {
  guard
  for name in JOB_TPCH_GATE JOB_TPCC_GATE TPCH_TPCC_GATE AGGREGATE_OUTPUT; do need "$name"; done
  absent "$AGGREGATE_OUTPUT"
  "$PY" -B "$AGGREGATE" \
    --contract "$JOB_BUNDLE/authority/mysql_algorithm_c_distributed_four_scene_successor_contract_v2_20260729.json" \
    --job-tpch-gate "$JOB_TPCH_GATE" --job-tpcc-gate "$JOB_TPCC_GATE" \
    --tpch-tpcc-gate "$TPCH_TPCC_GATE" --output "$AGGREGATE_OUTPUT"
}

case "${1:-}" in
  guard) guard ;;
  readbacks) readbacks ;;
  process-preflight) process_preflight ;;
  solo) solo ;;
  materialize-solo) materialize_solo ;;
  materialize-pair) materialize_pair ;;
  launch-pair) launch_pair ;;
  compare-gate) compare_gate ;;
  aggregate) aggregate ;;
  *) die "usage: $0 {guard|readbacks|process-preflight|materialize-solo|solo|materialize-pair|launch-pair|compare-gate|aggregate}" ;;
esac
