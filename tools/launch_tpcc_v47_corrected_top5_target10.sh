#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 --preflight-only|--armed" >&2
    exit 64
fi

mode=$1
if [[ "$mode" != "--preflight-only" && "$mode" != "--armed" ]]; then
    echo "invalid mode: $mode" >&2
    exit 64
fi

project=/home/anonymous/program/resource_adaptive
runtime=$project/history/tpcc_v40_clean5_exact_recalibration_package_20260803/runtime
package=$project/history/tpcc_v47_trusted3_serial_compat_package_20260804
formal_state=$project/history/tpcc_final_algorithm_fresh0_v36_20260803/formal_state.json
baseline=/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/candidate_measurements/point_20260729_022349_1052396/summary.json
python=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python

verify_sha() {
    local file_name=$1
    local expected_sha=$2
    test -f "$file_name"
    test ! -L "$file_name"
    test "$(sha256sum "$file_name" | awk '{print $1}')" = "$expected_sha"
}

verify_sha "$package/top5.json" cd668d10ed9a91c5efc8bb1efd5c360fed54587565cfd617988a9219b7696cad
verify_sha "$package/manifest.json" a3e75e81d8202216b70e726bcc2cb4820e93abd3cc0fe59a4b857f8e76eda4f1
verify_sha "$project/tools/build_tpcc_v47_trusted3_serial_compat.py" 8499552451b40abb446f8caa27aef3c4ce4b82b63d3bf4f04e379ff779ab6aa8
verify_sha "$project/tools/run_tpcc_v47_plan_regime_trusted3_strict_serial.py" 93e29028fb9e9adb92d6d2c3afe291c2e64c91ecfc8722ff647b15921f64d2d2
verify_sha "$project/tools/run_tpcc_v47_autonomous_trusted3_loop.py" 047c240d1982b23a086eab141c0e132d32af337923283b80aaf742e572e16278
verify_sha "$project/tools/audit_tpcc_v44_postrun_gate.py" 8fb54c982b4207ea2764ce8d58df962ac4f333673f373e7584f2fb924cd2cf1d
verify_sha "$project/tools/build_tpcc_v44_plan_regime_score_separation.py" 8bb9a5132b4a91206731334677aa8140b5cbbfa4f8e4420bd9a03653db81900a
verify_sha "$project/tools/prepare_tpcc_v43_trusted3_sha_bound_handoff.py" 9b5a599e5e059e6356858edadcbe19163b99f17b68c43179e2ec7ea00947ded7
verify_sha "$project/tools/run_tpcc_formal_target10_trusted3_v43_sha_bound.py" f215d191f6c8d7f17b1b7df1dbe9e7fbfdc2aaaf25fd2a25965e243fadc97cb3
verify_sha "$project/tools/run_tpcc_v43_trusted3_formal_substrate.py" ec8eab74c31fe1dda34b9eb06ce91ee5dce846bac276becd52a63904bad2738c
verify_sha "$runtime/adaptive_replay_policy.py" c3c7a826ff1ab65c04c93510efd17b26907a79a15b3d13ff91e5ca106de94584
verify_sha "$runtime/gradient_replay_metrics.py" 499f71cf7bdb8f882ab7776d1635b7abe021f1eee1e18d90b4aff2b66859592c
verify_sha "$runtime/resource_utility.py" 24cb1ed83ef36a577461675c7180ec3fa7343a8bcd3462063c028668433b4e36
verify_sha "$formal_state" 62e3fd40c93c163727367afd20b151c291c3646d63fbe0c5c5e2d32c3160aeb0
verify_sha "$baseline" 9c0f0d083796bef1ec8d49c4aa71b1d8e904fca43dbfb49a1708c0c40ccd7356

test ! -e "$package/top5_real_replay_round17_v47_trusted3_diagnostic_02"
test ! -e "$package/strict_top5_real_replay_round17_v47_ledger_v1.json"

if ps -eo stat=,comm=,args= | awk '
    $1 !~ /^Z/ &&
    ($2 ~ /^python/ || $2 ~ /^java/) &&
    $0 ~ /(run_tpcc_v47|oltpbench|tpcc_v47_trusted3_serial_compat_package)/ {
        found=1
    }
    END { exit found ? 0 : 1 }
'; then
    echo "active TPC-C v47/OLTPBench process detected" >&2
    exit 73
fi

export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$runtime:$project/tools:$project"
export ADAPTIVE_REAL_REPLAY_ENABLED=0

if [[ "$mode" == "--preflight-only" ]]; then
    exec /home/anonymous/miniconda3/envs/py313/bin/python -u \
        "$project/tools/run_tpcc_v47_autonomous_trusted3_loop.py" \
        --selection "$package/top5.json" \
        --campaign-root "$package" \
        --expected-formal-state-sha256 62e3fd40c93c163727367afd20b151c291c3646d63fbe0c5c5e2d32c3160aeb0 \
        --verify-only
fi

exec "$python" -u "$project/tools/run_tpcc_v47_autonomous_trusted3_loop.py" \
    --selection "$package/top5.json" \
    --campaign-root "$package" \
    --expected-formal-state-sha256 62e3fd40c93c163727367afd20b151c291c3646d63fbe0c5c5e2d32c3160aeb0
