#!/usr/bin/env bash
set -euo pipefail

round=/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/gradient_debug_round11_tpcc_calibrated_deadband_v1_20260728
python_bin=/home/anonymous/miniconda3/envs/py39/bin/python
project=/home/anonymous/program/resource_adaptive
runner="$round/run_tpcc_round11_calibrated_top5_strict_serial.py"
policy="$round/code/replay_correctness_policy.py"
reclassifier="$round/reclassify_tpcc_round11_rank1.py"
selector="$round/tpcc_remaining_budget_selector.py"
ledger="$round/strict_top5_real_replay_round11_ledger_v1.json"
events="$round/top5_real_replay_round11_calibrated_v1/events.jsonl"
selection="$round/tpcc_round11_calibrated_top5.json"

expected_source_runner=0d44b3bd0d4b113ad1f08c4d3d2a461235d86b71da9dce11d3b75d5636f5ce31
expected_source_policy=e6bb7aefc67eb01e3d9138c7bf36cf93367d87779d58013cad41b2302f9c05f7
expected_source_reclassifier=855399f4258ab3838a385e2be4ca13a8fffc6cee91b3747b2896bb290187dde2
expected_source_selector=8c963c9f730e1038b61146e2e928b946a6b213e7ee95b9aa6b9c4ebdf0608376

expected_current_runner=23ce6940a760573db3d04e4de4d5e0d5e9ff115efaea30439a07d6e22a394eaf
expected_current_policy=59376c628925c021cb807ef96c5aec38808db6dcee9112addbadf02c7d630b14
expected_ledger=a608b9958ca5c855e2391ece01b711f2194ed0aa51b81c41a590dc02801123dc
expected_events=8f48c764c12c5d7f7232086efef96e0c09aa6ede0e5de792934ba38f61f0b596
expected_selection=08bd6ec14011d69c4517e8491b8528c7877329b7861416bd514d2378deddc307

sha() {
    sha256sum "$1" | awk '{print $1}'
}

fail() {
    printf 'FAIL_CLOSED: %s\n' "$*" >&2
    exit 90
}

[[ "$(sha /tmp/run_tpcc_round11_calibrated_top5_strict_serial.py)" == "$expected_source_runner" ]] || fail source_runner_sha
[[ "$(sha /tmp/tpcc_round11_current_correctness_policy.py)" == "$expected_source_policy" ]] || fail source_policy_sha
[[ "$(sha /tmp/reclassify_tpcc_round11_rank1.py)" == "$expected_source_reclassifier" ]] || fail source_reclassifier_sha
[[ "$(sha /tmp/tpcc_remaining_budget_selector.py)" == "$expected_source_selector" ]] || fail source_selector_sha

[[ "$(sha "$runner")" == "$expected_current_runner" ]] || fail current_runner_sha
[[ "$(sha "$policy")" == "$expected_current_policy" ]] || fail current_policy_sha
[[ ! -e "$reclassifier" ]] || fail unexpected_existing_reclassifier
[[ ! -e "$selector" ]] || fail unexpected_existing_selector
[[ "$(sha "$ledger")" == "$expected_ledger" ]] || fail ledger_sha
[[ "$(sha "$events")" == "$expected_events" ]] || fail events_sha
[[ "$(sha "$selection")" == "$expected_selection" ]] || fail selection_sha

if pgrep -f "$runner" >/dev/null; then
    fail runner_active
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
cp -p "$runner" "$runner.pre_zero_call_fix.$timestamp.$expected_current_runner.bak"
cp -p "$policy" "$policy.pre_zero_call_fix.$timestamp.$expected_current_policy.bak"

install -m 0755 /tmp/run_tpcc_round11_calibrated_top5_strict_serial.py "$runner"
install -m 0644 /tmp/tpcc_round11_current_correctness_policy.py "$policy"
install -m 0755 /tmp/reclassify_tpcc_round11_rank1.py "$reclassifier"
install -m 0644 /tmp/tpcc_remaining_budget_selector.py "$selector"

[[ "$(sha "$runner")" == "$expected_source_runner" ]] || fail deployed_runner_sha
[[ "$(sha "$policy")" == "$expected_source_policy" ]] || fail deployed_policy_sha
[[ "$(sha "$reclassifier")" == "$expected_source_reclassifier" ]] || fail deployed_reclassifier_sha
[[ "$(sha "$selector")" == "$expected_source_selector" ]] || fail deployed_selector_sha

PYTHONPATH="$round/code:$round:$project" \
    "$python_bin" -m py_compile "$runner" "$policy" "$reclassifier" "$selector"

cd "$round"
PYTHONPATH="$round/code:$round:$project" \
    "$python_bin" "$reclassifier"
