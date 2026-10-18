#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

HYBENCH_DIR="${HYBENCH_DIR:-/home/anonymous/HyBench-2024-master}"
VM_SSH="${VM_SSH:-anonymous@192.0.2.8}"
SSH_OPTS="${SSH_OPTS:--o BatchMode=yes -o ConnectTimeout=5}"
SSH_TIMEOUT_SEC="${SSH_TIMEOUT_SEC:-720}"
PG_CTL="${PG_CTL:-/usr/lib/postgresql/17/bin/pg_ctl}"
PG_DATA="${PG_DATA:-/home/anonymous/pg_master/pg_data}"
DROP_CACHE_CMD="${DROP_CACHE_CMD:-sudo -n /home/anonymous/bin/drop_vm_cache.sh}"
STMT_FILE="${STMT_FILE:-conf/stmt_postgres.toml}"
BASE_PROPS="${BASE_PROPS:-conf/pg.props}"

XTPCLIENT=32
XAPCLIENT=4
RUN_MINS=5
TIMEOUT_SEC=900
OUTPUT_DIR=""
SEEDS="4508333 34515094 275110313"
KEEP_TEMP_PROPS=0
RANDOMIZE_SEED=0
RANDOM_TRIALS=1
INTERRUPTED=0
CURRENT_RUN_PID=""

cleanup_local_children() {
    local current_pid=""
    current_pid="$$"

    if [[ -n "$CURRENT_RUN_PID" ]]; then
        kill -TERM "-$CURRENT_RUN_PID" 2>/dev/null || true
        sleep 1
        kill -KILL "-$CURRENT_RUN_PID" 2>/dev/null || true
    fi

    pkill -TERM -P "$current_pid" 2>/dev/null || true
    jobs -pr 2>/dev/null | xargs -r kill -TERM 2>/dev/null || true
    sleep 1
    pkill -KILL -P "$current_pid" 2>/dev/null || true
    jobs -pr 2>/dev/null | xargs -r kill -KILL 2>/dev/null || true

    pkill -f "bash hybench -t runxp -c" 2>/dev/null || true
    pkill -f "com.hybench.HyBench -t runxp -c" 2>/dev/null || true
    pkill -f "pg.props.xp_seed_" 2>/dev/null || true
}

handle_interrupt() {
    local sig="${1:-INT}"
    if [[ "$INTERRUPTED" == "1" ]]; then
        return
    fi
    INTERRUPTED=1
    echo "Interrupted by ${sig}, cleaning up child processes..." >&2
    trap - INT TERM
    cleanup_local_children
    exit 130
}

trap 'handle_interrupt INT' INT
trap 'handle_interrupt TERM' TERM

usage() {
    cat <<USAGE
Usage: $0 [options]

Test HyBench XP seeds and mark seeds that cause PostgreSQL OOM.

Options:
  --seeds "S1 S2 ..."       Seed list. Default: existing top10 seeds.
  --seed-file PATH          CSV/text file. If CSV has a 'seed' column, use it; otherwise read first field/line.
  --xtpclient N             XP AT/TP concurrency. Default: ${XTPCLIENT}
  --xapclient N             XP IQ/AP concurrency. Default: ${XAPCLIENT}
  --run-mins N              xpRunMins. Default: ${RUN_MINS}
  --timeout-sec N           Per-seed timeout. Default: ${TIMEOUT_SEC}
  --output-dir PATH         Output dir. Default: result/xp_seed_<xtp>_<xap>_oom_<timestamp>
  --random-seed             Run with generated random_seed values instead of the fixed seed list.
  --random-trials N         Number of generated random_seed runs for --random-seed. Default: ${RANDOM_TRIALS}
  --keep-temp-props         Keep generated HyBench props files.
  -h, --help                Show this help.

Environment:
  HYBENCH_DIR=${HYBENCH_DIR}
  VM_SSH=${VM_SSH}
  PG_CTL=${PG_CTL}
  PG_DATA=${PG_DATA}
  DROP_CACHE_CMD=${DROP_CACHE_CMD}
USAGE
}

read_seed_file() {
    local path="$1"
    python3 - "$path" <<'PY'
from __future__ import annotations
import csv
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8", errors="replace")
lines = [line.strip() for line in text.splitlines() if line.strip()]
if not lines:
    sys.exit(0)

if "," in lines[0]:
    rows = list(csv.DictReader(lines))
    if rows and "seed" in rows[0]:
        print(" ".join(str(row["seed"]).strip() for row in rows if str(row.get("seed", "")).strip()))
        sys.exit(0)

seeds = []
for line in lines:
    if line.lower().startswith("seed,"):
        continue
    first = line.replace(",", " ").split()[0]
    if first.isdigit():
        seeds.append(first)
print(" ".join(seeds))
PY
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --seeds)
            SEEDS="$2"
            shift 2
            ;;
        --seed-file)
            SEEDS="$(read_seed_file "$2")"
            shift 2
            ;;
        --xtpclient)
            XTPCLIENT="$2"
            shift 2
            ;;
        --xapclient)
            XAPCLIENT="$2"
            shift 2
            ;;
        --run-mins)
            RUN_MINS="$2"
            shift 2
            ;;
        --timeout-sec)
            TIMEOUT_SEC="$2"
            shift 2
            ;;
        --output-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --random-seed)
            RANDOMIZE_SEED=1
            SEEDS=""
            shift
            ;;
        --random-trials)
            RANDOM_TRIALS="$2"
            shift 2
            ;;
        --keep-temp-props)
            KEEP_TEMP_PROPS=1
            shift
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

if [[ "$RANDOMIZE_SEED" == "1" ]]; then
    SEEDS=""
    for ((i = 1; i <= RANDOM_TRIALS; i++)); do
        SEEDS="${SEEDS} random_${i}"
    done
fi

if [[ -z "${SEEDS// }" ]]; then
    echo "ERROR: no seeds configured" >&2
    exit 2
fi

if [[ -z "$OUTPUT_DIR" ]]; then
    OUTPUT_DIR="result/xp_seed_${XTPCLIENT}_${XAPCLIENT}_oom_$(date +%Y%m%d_%H%M%S)"
fi
mkdir -p "$OUTPUT_DIR"

SUMMARY_CSV="$OUTPUT_DIR/summary.csv"
echo "seed,rc,oom,timeout,recovery_or_eof,xp_qps,xp_tps,iq_queries,at_transactions,log_path" > "$SUMMARY_CSV"

remote_pg_fast_restart() {
    timeout "$SSH_TIMEOUT_SEC" ssh ${SSH_OPTS} "$VM_SSH" "bash -lc '
        log=/tmp/test_xp_seed_pgctl.log
        {
            sudo -n -u postgres ${PG_CTL} -D ${PG_DATA} -w -t 600 -m fast restart ||
            sudo -n -u postgres ${PG_CTL} -D ${PG_DATA} -w -t 600 start
        } </dev/null >\"\$log\" 2>&1
        rc=\$?
        cat \"\$log\"
        exit \$rc
    '"
}

remote_pg_immediate_restart() {
    timeout "$SSH_TIMEOUT_SEC" ssh ${SSH_OPTS} "$VM_SSH" "bash -lc '
        log=/tmp/test_xp_seed_pgctl.log
        {
            sudo -n -u postgres ${PG_CTL} -D ${PG_DATA} -w -t 120 -m immediate stop || true
            sleep 5
            sudo -n -u postgres ${PG_CTL} -D ${PG_DATA} -w -t 600 start
        } </dev/null >\"\$log\" 2>&1
        rc=\$?
        cat \"\$log\"
        exit \$rc
    '"
}

remote_prepare_before_seed() {
    remote_pg_fast_restart
    timeout 120 ssh ${SSH_OPTS} "$VM_SSH" "bash -lc 'sleep 5; ${DROP_CACHE_CMD} || true; sleep 10'"
}

remote_dmesg_line_count() {
    timeout 30 ssh ${SSH_OPTS} "$VM_SSH" "dmesg | wc -l" || echo "0"
}

remote_new_dmesg_has_oom() {
    local start_line="$1"
    timeout 30 ssh ${SSH_OPTS} "$VM_SSH" "dmesg | tail -n +$((start_line + 1)) | grep -Eiq 'oom-kill|Out of memory|Killed process .*postgres'; echo \$?" || echo "255"
}

parse_metric() {
    local pattern="$1"
    local log_path="$2"
    python3 - "$pattern" "$log_path" <<'PY'
from __future__ import annotations
import re
import sys

pattern = sys.argv[1]
path = sys.argv[2]
text = open(path, encoding="utf-8", errors="replace").read()
matches = re.findall(pattern, text)
print(matches[-1] if matches else "")
PY
}

run_one_seed() {
    local seed="$1"
    local actual_seed="$seed"
    if [[ "$RANDOMIZE_SEED" == "1" ]]; then
        actual_seed="$(date +%s%N)"
        actual_seed="${actual_seed: -10}"
    fi
    local log_path="$OUTPUT_DIR/seed_${actual_seed}.log"
    local tmp_props="conf/pg.props.xp_seed_${actual_seed}_${XTPCLIENT}_${XAPCLIENT}.tmp"

    echo "===== seed=${actual_seed} xtpclient=${XTPCLIENT} xapclient=${XAPCLIENT} =====" | tee -a "$OUTPUT_DIR/run.log"
    remote_prepare_before_seed
    local dmesg_start
    dmesg_start="$(remote_dmesg_line_count)"

    echo "[seed=${actual_seed}] running HyBench; log=${log_path}" | tee -a "$OUTPUT_DIR/run.log"
    echo "[seed=${actual_seed}] progress: tail -f ${log_path}" | tee -a "$OUTPUT_DIR/run.log"

    set +e
    setsid bash -lc "
        cd '$HYBENCH_DIR' &&
        cp '$BASE_PROPS' '$tmp_props' &&
        sed -i 's/^random_seed=.*/random_seed=${actual_seed}/' '$tmp_props' &&
        sed -i 's/^xtpclient=.*/xtpclient=${XTPCLIENT}/' '$tmp_props' &&
        sed -i 's/^xapclient=.*/xapclient=${XAPCLIENT}/' '$tmp_props' &&
        sed -i 's/^xpRunMins=.*/xpRunMins=${RUN_MINS}/' '$tmp_props' &&
        exec timeout '$TIMEOUT_SEC' bash -lc 'cd \"$HYBENCH_DIR\" && exec bash hybench -t runxp -c \"$tmp_props\" -f \"$STMT_FILE\"'
    " > >(tee "$log_path") 2>&1 &
    CURRENT_RUN_PID="$!"
    wait "$CURRENT_RUN_PID"
    local rc="$?"
    CURRENT_RUN_PID=""
    set -e

    if [[ "$KEEP_TEMP_PROPS" != "1" ]]; then
        rm -f "$HYBENCH_DIR/$tmp_props" 2>/dev/null || true
    fi

    local timeout_flag=0
    if [[ "$rc" == "124" ]]; then
        timeout_flag=1
    fi

    local oom_grep_rc
    oom_grep_rc="$(remote_new_dmesg_has_oom "$dmesg_start")"
    local oom_flag=0
    if [[ "$oom_grep_rc" == "0" ]]; then
        oom_flag=1
    elif [[ "$oom_grep_rc" == "255" ]]; then
        oom_flag=1
        echo "[seed ${actual_seed}] unable to check dmesg after workload; marking unhealthy" | tee -a "$OUTPUT_DIR/run.log"
    fi

    local recovery_or_eof=0
    if grep -Eiq 'database system is in recovery mode|server closed the connection unexpectedly|EOFException|This connection has been closed|Out of memory|oom-kill' "$log_path"; then
        recovery_or_eof=1
    fi

    local xp_qps xp_tps iq_queries at_transactions
    xp_qps="$(parse_metric 'XP-QPS is ([0-9.]+|Infinity|NaN)' "$log_path")"
    xp_tps="$(parse_metric 'XP-TPS is ([0-9.]+|Infinity|NaN)' "$log_path")"
    iq_queries="$(parse_metric 'Total amount of XP-IQ Queries is ([0-9]+)' "$log_path")"
    at_transactions="$(parse_metric 'Total amount of XP-AT Transaction is ([0-9]+)' "$log_path")"

    echo "${actual_seed},${rc},${oom_flag},${timeout_flag},${recovery_or_eof},${xp_qps},${xp_tps},${iq_queries},${at_transactions},${log_path}" >> "$SUMMARY_CSV"

    if [[ "$oom_flag" == "1" || "$timeout_flag" == "1" || "$rc" != "0" || "$recovery_or_eof" == "1" ]]; then
        echo "[seed ${actual_seed}] unhealthy rc=${rc} oom=${oom_flag} timeout=${timeout_flag} recovery_or_eof=${recovery_or_eof}; restarting PostgreSQL" | tee -a "$OUTPUT_DIR/run.log"
        if ! remote_pg_immediate_restart; then
            echo "[seed ${actual_seed}] FATAL: PostgreSQL restart failed or VM SSH is not reachable; stop testing" | tee -a "$OUTPUT_DIR/run.log" >&2
            return 1
        fi
    else
        echo "[seed ${actual_seed}] healthy qps=${xp_qps} tps=${xp_tps}" | tee -a "$OUTPUT_DIR/run.log"
    fi
}

echo "Output dir: $OUTPUT_DIR"
echo "Seeds: $SEEDS"
echo "Concurrency: xtpclient=${XTPCLIENT}, xapclient=${XAPCLIENT}, run_mins=${RUN_MINS}"
echo "Timeout: ${TIMEOUT_SEC}s"

for seed in $SEEDS; do
    run_one_seed "$seed"
done

echo
echo "Summary: $SUMMARY_CSV"
echo "Healthy seeds:"
awk -F, 'NR > 1 && $2 == 0 && $3 == 0 && $4 == 0 && $5 == 0 {print "  seed=" $1 " xp_qps=" $6 " xp_tps=" $7}' "$SUMMARY_CSV"
