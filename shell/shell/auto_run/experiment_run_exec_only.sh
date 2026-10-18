#!/bin/bash

# ============================================================


# ============================================================


EXP_TYPE="low"
CASE_ID="tpcc_test"
CONTAINER_NAME="opengauss_node1"

set -o pipefail

if [ "$#" -ne 3 ]; then
    echo "Usage: $0 <EXPERIMENT_TYPE> <CASE_ID> <CONTAINER_INDEX>"
    echo "Example: $0 shadow c11 1"
    exit 1
fi

EXP_TYPE="$1"
CASE_ID="$2"
CONTAINER_NAME="opengauss_node$3"


WORK_HOME="/home/anonymous/program/resource_adaptive/shell/shell"
BENCHBASE_ROOT="/home/anonymous/benchbase-postgres"
PYTHON_ENV="python"
RESULT_ROOT="${RESULT_ROOT:-/disk3/anonymous/experiment_result1_disk1}" #
DB_ENGINE="${DB_ENGINE:-pg}"  # pg | mysql
CONFIG_GEN_SCRIPT="$WORK_HOME/auto_run/gen_config.py"

# Check Python dependencies
if ! $PYTHON_ENV -c "import prometheus_api_client; import pandas" 2>/dev/null; then
    echo "❌ Error: Required Python packages (prometheus-api-client, pandas) not found in $PYTHON_ENV."
    echo "   Please run: $PYTHON_ENV -m pip install prometheus-api-client pandas"
    exit 1
fi


DB_NAME="benchbase"
if [[ "$DB_ENGINE" == "mysql" ]]; then
    USER="${DB_USER:-root}"
    PASSWORD="${DB_PASSWORD:-root}"
else
    USER="${DB_USER:-gaussdb}"
    PASSWORD="${DB_PASSWORD:-CHANGE_ME}"
fi

run_analyze_stats() {
    local benchmark="$1"
    local sql_file=""
    local base_dir="$WORK_HOME/auto_run"

    case "$benchmark" in
        tpcc) sql_file="$base_dir/tpcc_analyze.sql" ;;
        tpch) sql_file="$base_dir/tpch_analyze.sql" ;;
        chbenchmark) sql_file="$base_dir/chbenchmark_analyze.sql" ;;
        *)
            echo "-> Skip ANALYZE: unknown benchmark '$benchmark'"
            return 0
            ;;
    esac

    if [[ "$DB_ENGINE" == "mysql" ]]; then
        sql_file="${sql_file%.sql}_mysql.sql"
    fi

    if [ ! -f "$sql_file" ]; then
        echo "-> Skip ANALYZE: sql file not found: $sql_file" | tee -a "$SHELL_ERR_LOG"
        return 0
    fi

    echo "-> Running ANALYZE script: $sql_file"
    if [[ "$DB_ENGINE" == "mysql" ]]; then
        MYSQL_PWD="$PASSWORD" mysql -h "$HOST" -P "$PORT" -u "$USER" "$DB_NAME" < "$sql_file" 2>> "$SHELL_ERR_LOG" || \
            echo "WARN: MySQL ANALYZE failed, continue." | tee -a "$SHELL_ERR_LOG"
    else
        PGPASSWORD="$PASSWORD" psql -h "$HOST" -p "$PORT" -U "$USER" -d "$DB_NAME" -f "$sql_file" 2>> "$SHELL_ERR_LOG" || \
            echo "WARN: PG ANALYZE failed, continue." | tee -a "$SHELL_ERR_LOG"
    fi
}


PORT=$((11000 + $3))
HOST="localhost"
if [[ "$DB_ENGINE" == "mysql" ]]; then
    DB_URL="jdbc:mysql://$HOST:$PORT/$DB_NAME?useSSL=false&rewriteBatchedStatements=true&allowPublicKeyRetrieval=true"
else
    DB_URL="jdbc:postgresql://$HOST:$PORT/$DB_NAME?sslmode=disable&ApplicationName=chbenchmark&reWriteBatchedInserts=true"
fi

ensure_db_exists() {
    if [[ "$DB_ENGINE" == "mysql" ]]; then
        local exists
        exists=$(MYSQL_PWD="$PASSWORD" mysql -N -s -h "$HOST" -P "$PORT" -u "$USER" \
            -e "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME='${DB_NAME}' LIMIT 1;" 2>/dev/null || true)
        [[ "$exists" == "$DB_NAME" ]]
        return $?
    fi

    local exists
    exists=$(PGPASSWORD="$PASSWORD" psql -h "$HOST" -p "$PORT" -U "$USER" -d postgres -At \
        -c "SELECT datname FROM pg_database WHERE datname='${DB_NAME}' LIMIT 1;" 2>/dev/null || true)
    [[ "$exists" == "$DB_NAME" ]]
    return $?
}


if [[ "$DB_ENGINE" == "mysql" ]]; then
    VOLUME_DEST="/var/lib/mysql"
else
    VOLUME_DEST="/var/lib/opengauss/data"
fi
VOLUME_PATH=$(docker inspect "$CONTAINER_NAME" --format='{{range .Mounts}}{{if eq .Destination "'"$VOLUME_DEST"'"}}{{.Source}}{{end}}{{end}}')
if [ -z "$VOLUME_PATH" ]; then
    echo "Error: Cannot find volume mounted to $VOLUME_DEST for $CONTAINER_NAME"
    exit 1
fi

echo "=== [Execution Only] Skip Data Load ==="
echo "=== : $CONTAINER_NAME | Port: $PORT ==="

if ! ensure_db_exists; then
    echo "❌ Error: database '$DB_NAME' does not exist on $HOST:$PORT, cannot run exec-only mode."
    echo "   Please run a full load pass first (experiment_run.sh) or create/load database manually."
    exit 1
fi


CONFIG_DIR="$WORK_HOME/experiment_docker_conf_and_data/conf"
mkdir -p "$CONFIG_DIR"
GENERATED_XML="$CONFIG_DIR/${EXP_TYPE}_${CASE_ID}.xml"

if [[ "$DB_ENGINE" == "mysql" ]]; then
    BASE_TEMPLATE_XML="$BENCHBASE_ROOT/config/mysql/sample_tpcc_config.xml"
else
    BASE_TEMPLATE_XML="$BENCHBASE_ROOT/config/postgres/sample_tpcc_config.xml"
fi
if [[ "$CASE_ID" == h* ]]; then
    if [[ "$DB_ENGINE" == "mysql" ]]; then
        BASE_TEMPLATE_XML="$BENCHBASE_ROOT/config/mysql/sample_tpch_config.xml"
    else
        BASE_TEMPLATE_XML="$BENCHBASE_ROOT/config/postgres/sample_tpch_config.xml"
    fi
elif [[ "$CASE_ID" == ch* ]]; then
    if [[ "$DB_ENGINE" == "mysql" ]]; then
        BASE_TEMPLATE_XML="$BENCHBASE_ROOT/config/mysql/sample_chbenchmark_config.xml"
    else
        BASE_TEMPLATE_XML="$BENCHBASE_ROOT/config/postgres/sample_chbenchmark_config.xml"
    fi
fi

EVAL_OUTPUT=$($PYTHON_ENV "$CONFIG_GEN_SCRIPT" \
    --case_id "$CASE_ID" \
    --exp_type "$EXP_TYPE" \
    --template "$BASE_TEMPLATE_XML" \
    --output "$GENERATED_XML" \
    --url "$DB_URL" \
    --db-engine "$DB_ENGINE")

if [ $? -ne 0 ]; then
    echo "Error generating config."
    exit 1
fi

eval "$EVAL_OUTPUT"
echo "-> Generated Config: Bench=$RUN_BENCHMARK, SF=$RUN_SF, Series=$RUN_SERIES"

# In exec-only shadow pass, we still need shadow PG config loaded
# (shared_preload_libraries/auto_explain) even though data load is skipped.
if [[ "$EXP_TYPE" == "shadow" && "$DB_ENGINE" == "pg" ]]; then
    AUTO_APPLY_SHADOW_CONF_IN_EXEC_ONLY="${AUTO_APPLY_SHADOW_CONF_IN_EXEC_ONLY:-1}"
    if [[ "$AUTO_APPLY_SHADOW_CONF_IN_EXEC_ONLY" == "1" ]]; then
        CAPACITY_NAME=""
        case "$RUN_SERIES" in
            "L") CAPACITY_NAME="low" ;;
            "M") CAPACITY_NAME="mid" ;;
            "H") CAPACITY_NAME="high" ;;
            *)
                echo "❌ Error: unknown RUN_SERIES '$RUN_SERIES' for shadow config injection."
                exit 1
                ;;
        esac

        SHADOW_CONF_SOURCE="$WORK_HOME/experiment_docker_conf_and_data/${EXP_TYPE}_${CAPACITY_NAME}_postgresql.conf"
        if [ ! -f "$SHADOW_CONF_SOURCE" ]; then
            echo "❌ Error: shadow config not found: $SHADOW_CONF_SOURCE"
            exit 1
        fi

        echo "-> Applying shadow config in exec-only mode: $SHADOW_CONF_SOURCE"
        docker cp "$SHADOW_CONF_SOURCE" "${CONTAINER_NAME}:/var/lib/opengauss/data/postgresql.conf"
        docker restart "$CONTAINER_NAME" >/dev/null

        echo "-> Waiting for DB to become ready after restart..."
        retry=0
        max_retry=30
        until pg_isready -h "$HOST" -p "$PORT" -U "$USER" >/dev/null 2>&1; do
            sleep 2
            retry=$((retry + 1))
            if [ "$retry" -ge "$max_retry" ]; then
                echo "❌ Error: DB not ready after restart when applying shadow config."
                exit 1
            fi
        done
        echo "-> DB is ready after shadow config apply."
    fi
fi


CURRENT_RESULT_DIR="$RESULT_ROOT/$EXP_TYPE/$CASE_ID"
ERROR_DIR="$CURRENT_RESULT_DIR/errors"
mkdir -p "$CURRENT_RESULT_DIR/metrics" "$CURRENT_RESULT_DIR/plans" "$CURRENT_RESULT_DIR/sql_logs" "$ERROR_DIR"
if [ $? -ne 0 ]; then
    echo "❌ Error: Failed to create result directories under $CURRENT_RESULT_DIR"
    echo "   Please set a writable RESULT_ROOT, e.g. RESULT_ROOT=/disk1/dr_test/experiment_result"
    exit 1
fi
SHELL_ERR_LOG="$ERROR_DIR/shell_cmd_error.log"
JAVA_ERR_LOG="$ERROR_DIR/java_stacktrace.log"

# --- SKIP Step 6 (Data Load) ---
echo "=== [Skipping Step 1] Data Load ==="


echo "=== [Step 2]  ==="
start_time_fmt=$(date "+%Y-%m-%d %H:%M:%S")
BENCH_TIME_SEC=${BENCH_TIME_SEC:-1200}


AUTO_RUN_ANALYZE=${AUTO_RUN_ANALYZE:-1}
if [[ "$AUTO_RUN_ANALYZE" == "1" ]]; then
    run_analyze_stats "$RUN_BENCHMARK"
fi


DB_STATS_SCRIPT="$WORK_HOME/collect_db_stats.sh"
if [ -f "$DB_STATS_SCRIPT" ]; then
    bash "$DB_STATS_SCRIPT" \
        --stage pre \
        --db-host "$HOST" --db-port "$PORT" --db-user "$USER" --db-name "$DB_NAME" --db-password "$PASSWORD" \
        --db-engine "$DB_ENGINE" \
        --output "$CURRENT_RESULT_DIR/metrics" 2>> "$SHELL_ERR_LOG"
fi


AUTO_COLLECT_METRICS=${AUTO_COLLECT_METRICS:-1}
METRICS_COLLECTOR_PID=""
METRICS_OUT_JSON="$CURRENT_RESULT_DIR/metrics/runtime_metrics_${CONTAINER_NAME}.jsonl"
if [[ "$AUTO_COLLECT_METRICS" == "1" ]]; then

    COLLECT_SCRIPT="/home/anonymous/program/resource_adaptive/shell/shell/collect_metrics_runtime.sh"


    if [ -x "$COLLECT_SCRIPT" ]; then
        METRICS_DURATION=${METRICS_DURATION:-$BENCH_TIME_SEC}
        METRICS_INTERVAL=${METRICS_INTERVAL:-5}
        if [[ "$EXP_TYPE" == "shadow" ]]; then
            ENABLE_PLAN_TREE=${ENABLE_PLAN_TREE:-1}
        else
            ENABLE_PLAN_TREE=${ENABLE_PLAN_TREE:-0}
        fi
        DISABLE_PLAN_TREE_ARG=()
        if [[ "$ENABLE_PLAN_TREE" != "1" ]]; then
            DISABLE_PLAN_TREE_ARG=(--disable-plan-tree)
        fi
        METRICS_DB_PASSWORD=${METRICS_DB_PASSWORD:-$PASSWORD}

        echo "-> Starting metrics collector in background..."
        bash "$COLLECT_SCRIPT" \
            --container "$CONTAINER_NAME" \
            --output "$METRICS_OUT_JSON" \
            --duration "$METRICS_DURATION" \
            --interval "$METRICS_INTERVAL" \
            --db-host "$HOST" \
            --db-port "$PORT" \
            --db-user "$USER" \
            --db-name "$DB_NAME" \
            --db-password "$METRICS_DB_PASSWORD" \
            --db-engine "$DB_ENGINE" \
            "${DISABLE_PLAN_TREE_ARG[@]}" \
            >> "$CURRENT_RESULT_DIR/metrics/collector.log" 2>&1 &

        METRICS_COLLECTOR_PID=$!
        echo "-> Metrics collector PID: $METRICS_COLLECTOR_PID"
    else
        echo "WARN: collect_metrics_runtime.sh not found." | tee -a "$SHELL_ERR_LOG"
    fi
fi


if [[ "$EXP_TYPE" == "shadow" && "$DB_ENGINE" == "pg" ]]; then
    PGPASSWORD=$PASSWORD psql -h $HOST -p $PORT -U $USER -d $DB_NAME -c "SELECT pg_rotate_logfile();" 2>> "$SHELL_ERR_LOG"
fi


if ! cd "$BENCHBASE_ROOT"; then
    echo "❌ Error: Failed to cd to BENCHBASE_ROOT: $BENCHBASE_ROOT" | tee -a "$SHELL_ERR_LOG"
    exit 1
fi

if [ ! -f "benchbase.jar" ]; then
    echo "❌ Error: benchbase.jar not found in $(pwd)" | tee -a "$SHELL_ERR_LOG"
    exit 1
fi

BENCHBASE_RUN_LOG="$CURRENT_RESULT_DIR/benchbase_execution.log"

THREAD_POOL_SIZE=0
if [[ "$RUN_SERIES" == "H" ]]; then THREAD_POOL_SIZE=140; else THREAD_POOL_SIZE=78; fi

sed -i "s|<time>[0-9]*</time>|<time>${BENCH_TIME_SEC}</time>|g" "$GENERATED_XML"
echo "-> Modified execution time in XML to ${BENCH_TIME_SEC} seconds."

echo "-> Running Benchbase (Log: $BENCHBASE_RUN_LOG)..."
JAVA_EXIT_CODE=0
java -Xmx128g -Djdk.virtualThreadScheduler.parallelism=$THREAD_POOL_SIZE \
     -jar benchbase.jar -b "$RUN_BENCHMARK" -c "$GENERATED_XML" --execute=true \
     2>> "$SHELL_ERR_LOG" | tee "$BENCHBASE_RUN_LOG" || JAVA_EXIT_CODE=$?

if [ "$JAVA_EXIT_CODE" -ne 0 ]; then
    echo "❌ Error: Benchbase execution failed with exit code $JAVA_EXIT_CODE" | tee -a "$SHELL_ERR_LOG"
    if [ -n "$METRICS_COLLECTOR_PID" ] && kill -0 "$METRICS_COLLECTOR_PID" 2>/dev/null; then
        echo "-> Stopping metrics collector due to Benchbase failure (PID: $METRICS_COLLECTOR_PID)..."
        kill "$METRICS_COLLECTOR_PID" 2>/dev/null || true
        wait "$METRICS_COLLECTOR_PID" 2>/dev/null || true
    fi
    exit "$JAVA_EXIT_CODE"
fi

if [ -n "$METRICS_COLLECTOR_PID" ] && kill -0 "$METRICS_COLLECTOR_PID" 2>/dev/null; then
    echo "-> Waiting metrics collector to finish (PID: $METRICS_COLLECTOR_PID)..."
    wait "$METRICS_COLLECTOR_PID" 2>/dev/null || true
fi


if [ -f "$DB_STATS_SCRIPT" ]; then
    bash "$DB_STATS_SCRIPT" \
        --stage post \
        --db-host "$HOST" --db-port "$PORT" --db-user "$USER" --db-name "$DB_NAME" --db-password "$PASSWORD" \
        --db-engine "$DB_ENGINE" \
        --output "$CURRENT_RESULT_DIR/metrics" 2>> "$SHELL_ERR_LOG"
fi


grep -iE "Exception|FATAL|Connection refused|Error:|Caused by|Validation failed" "$BENCHBASE_RUN_LOG" > "$JAVA_ERR_LOG"


echo "=== [Step 3]  ==="


GENERATED_CSV=$(grep "Log Flush Complete!" "$BENCHBASE_RUN_LOG" | awk -F "File: " '{print $2}' | tr -d '[:space:]')
if [ -n "$GENERATED_CSV" ] && [ -f "$GENERATED_CSV" ]; then
    cp "$GENERATED_CSV" "$CURRENT_RESULT_DIR/sql_logs/"
else
    # Fallback: Copy any CSV/JSON files modified in the last 5 minutes in BENCHBASE_ROOT
    echo "⚠️ Warning: Could not detect generated CSV from logs. Attempting fallback copy..."
    find . -maxdepth 1 -name "tpcc_*.csv" -mmin -5 -exec cp {} "$CURRENT_RESULT_DIR/sql_logs/" \;
    find . -maxdepth 1 -name "tpcc_*.json" -mmin -5 -exec cp {} "$CURRENT_RESULT_DIR/sql_logs/" \;
fi


NODE_SUFFIX=$(echo "$CONTAINER_NAME" | grep -o "node[0-9]*")
if [ -z "$NODE_SUFFIX" ]; then NODE_SUFFIX="node1"; fi

echo "-> Running monitor collection script..."
if ! $PYTHON_ENV "/home/anonymous/program/resource_adaptive/shell/shell/experiment_monitor_collect.py" \
    --time "$start_time_fmt" \
    --node "$NODE_SUFFIX" \
    --profile "$RUN_SERIES" \
    --db-engine "$DB_ENGINE" \
    --db-name "$DB_NAME" \
    --container-metrics-source docker-runtime \
    --runtime-jsonl "$METRICS_OUT_JSON" \
    --output "$CURRENT_RESULT_DIR/metrics" >> "$CURRENT_RESULT_DIR/metrics/monitor.log" 2>> "$SHELL_ERR_LOG"; then
    echo "❌ Error: Monitor collection failed. Check $SHELL_ERR_LOG for details."
    echo "--- Last 10 lines of Error Log ---"
    tail -n 10 "$SHELL_ERR_LOG"
    echo "----------------------------------"
else
    echo "-> Monitor collection finished."
    # List generated files
    ls -lh "$CURRENT_RESULT_DIR/metrics/"
fi


if [[ "$EXP_TYPE" == "shadow" && "$DB_ENGINE" == "pg" ]]; then
    PLAN_RAW_LOG_DIR="$CURRENT_RESULT_DIR/plans/raw_logs"
    PLAN_EVENTS_JSONL="$CURRENT_RESULT_DIR/plans/plan_events.jsonl"
    PLAN_SUMMARY_CSV="$CURRENT_RESULT_DIR/plans/plan_summary.csv"
    PLAN_HASH_SUMMARY_CSV="$CURRENT_RESULT_DIR/plans/plan_hash_summary.csv"

    COLLECT_PLAN_LOG_SCRIPT="$WORK_HOME/collect_auto_explain_logs.sh"
    PARSE_PLAN_LOG_SCRIPT="$WORK_HOME/parse_auto_explain_logs.py"

    if [[ -x "$COLLECT_PLAN_LOG_SCRIPT" && -f "$PARSE_PLAN_LOG_SCRIPT" ]]; then
        echo "-> Collect auto_explain logs since workload start: $start_time_fmt"
        bash "$COLLECT_PLAN_LOG_SCRIPT" \
            --container "$CONTAINER_NAME" \
            --since "$start_time_fmt" \
            --output-dir "$PLAN_RAW_LOG_DIR" \
            >> "$CURRENT_RESULT_DIR/metrics/collector.log" 2>> "$SHELL_ERR_LOG" || \
            echo "WARN: auto_explain log collection failed." | tee -a "$SHELL_ERR_LOG"

        if [ -d "$PLAN_RAW_LOG_DIR" ]; then
            echo "-> Parse auto_explain logs to plan events..."
            $PYTHON_ENV "$PARSE_PLAN_LOG_SCRIPT" \
                --log-dir "$PLAN_RAW_LOG_DIR" \
                --output-jsonl "$PLAN_EVENTS_JSONL" \
                --output-summary-csv "$PLAN_SUMMARY_CSV" \
                --output-plan-hash-csv "$PLAN_HASH_SUMMARY_CSV" \
                >> "$CURRENT_RESULT_DIR/metrics/collector.log" 2>> "$SHELL_ERR_LOG" || \
                echo "WARN: auto_explain log parsing failed." | tee -a "$SHELL_ERR_LOG"
        fi
    else
        echo "WARN: plan log scripts not found, skip auto_explain parsing." | tee -a "$SHELL_ERR_LOG"
    fi
fi

echo "✅ "
echo "📊 : $CURRENT_RESULT_DIR/metrics"
exit 0
