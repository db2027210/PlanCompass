#!/bin/bash
# collect_db_stats.sh


set -e

STAGE=""
DB_HOST="localhost"
DB_PORT="5432"
DB_USER="gaussdb"
DB_NAME="benchbase"
DB_PASSWORD=""
OUTPUT_DIR="."
DB_ENGINE="pg"


while [[ $# -gt 0 ]]; do
    case "$1" in
        --stage) STAGE="$2"; shift 2 ;;
        --db-host) DB_HOST="$2"; shift 2 ;;
        --db-port) DB_PORT="$2"; shift 2 ;;
        --db-user) DB_USER="$2"; shift 2 ;;
        --db-name) DB_NAME="$2"; shift 2 ;;
        --db-password) DB_PASSWORD="$2"; shift 2 ;;
        --db-engine) DB_ENGINE="$2"; shift 2 ;;
        --output) OUTPUT_DIR="$2"; shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

mkdir -p "$OUTPUT_DIR"

run_sql() {
    if [ "$DB_ENGINE" = "pg" ]; then
        PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -At -c "$1"
    else
        MYSQL_PWD="$DB_PASSWORD" mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -N -s "$DB_NAME" -e "$1"
    fi
}

case "$DB_ENGINE" in
    pg|mysql) ;;
    *)
        echo "Unknown --db-engine: $DB_ENGINE (expect pg|mysql)"
        exit 1
        ;;
esac

if [ "$STAGE" == "pre" ]; then
    echo "-> [Stats] Collecting Pre-Workload metrics..."



    DIST_FILE="$OUTPUT_DIR/pre_data_distribution.csv"
    echo "timestamp,schema_name,table_name,size_bytes" > "$DIST_FILE"

    if [ "$DB_ENGINE" = "pg" ]; then
        SQL_DIST="SELECT n.nspname, c.relname, pg_total_relation_size(c.oid) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind = 'r' AND n.nspname NOT IN ('pg_catalog', 'information_schema') ORDER BY 3 DESC;"
        PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -At -F "," -c "$SQL_DIST" | \
        while IFS=, read -r schema table size; do
            echo "$(date +%s),$schema,$table,$size" >> "$DIST_FILE"
        done
    else
        SQL_DIST="SELECT table_schema, table_name, (data_length + index_length) AS size_bytes FROM information_schema.tables WHERE table_schema = '$DB_NAME' ORDER BY size_bytes DESC;"
        MYSQL_PWD="$DB_PASSWORD" mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -N -s "$DB_NAME" -e "$SQL_DIST" | \
        while IFS=$'\t' read -r schema table size; do
            echo "$(date +%s),$schema,$table,$size" >> "$DIST_FILE"
        done
    fi

    # 2. DB Buffer Pool (Bytes)
    if [ "$DB_ENGINE" = "pg" ]; then
        SQL_SB="SELECT CASE lower(coalesce(unit, '')) WHEN '8kb' THEN setting::bigint * 8192 WHEN 'kb' THEN setting::bigint * 1024 WHEN 'mb' THEN setting::bigint * 1024 * 1024 WHEN 'gb' THEN setting::bigint * 1024 * 1024 * 1024 ELSE setting::bigint END FROM pg_settings WHERE name = 'shared_buffers';"
        SB_BYTES=$(run_sql "$SQL_SB")
        BUFFER_METRIC_NAME="shared_buffers"
    else
        SQL_SB="SELECT COALESCE(CAST(variable_value AS UNSIGNED), 0) FROM performance_schema.global_variables WHERE variable_name = 'innodb_buffer_pool_size';"
        SB_BYTES=$(run_sql "$SQL_SB")
        BUFFER_METRIC_NAME="innodb_buffer_pool_size"
    fi
    SB_BYTES=${SB_BYTES:-0}

    # 3. OS Memory (MemAvailable)

    MEM_AVAIL_BYTES=$(awk '/MemAvailable:/ {print $2 * 1024; exit}' /proc/meminfo)



    USABLE_OS_CACHE=$((MEM_AVAIL_BYTES - SB_BYTES))
    if [ "$USABLE_OS_CACHE" -lt 0 ]; then USABLE_OS_CACHE=0; fi

    CACHE_CAP_EFF=$((SB_BYTES + USABLE_OS_CACHE))


    MEM_FILE="$OUTPUT_DIR/pre_memory_stats.csv"
    echo "metric,value_bytes" > "$MEM_FILE"
    echo "$BUFFER_METRIC_NAME,$SB_BYTES" >> "$MEM_FILE"
    echo "usable_os_page_cache,$USABLE_OS_CACHE" >> "$MEM_FILE"
    echo "cache_capacity_effective,$CACHE_CAP_EFF" >> "$MEM_FILE"
    echo "Saved pre-stats to $OUTPUT_DIR"

elif [ "$STAGE" == "post" ]; then
    echo "-> [Stats] Collecting Post-Workload metrics..."

    # Cache Hit Rate
    if [ "$DB_ENGINE" = "pg" ]; then
        # Formula: sum(blks_hit) / (sum(blks_hit) + sum(blks_read))
        SQL_HIT="SELECT COALESCE(sum(blks_hit)::numeric / NULLIF(sum(blks_hit + blks_read), 0), 0) FROM pg_stat_database WHERE datname = '$DB_NAME';"
        HIT_RATE=$(run_sql "$SQL_HIT")
    else
        # Formula: 1 - Innodb_buffer_pool_reads / Innodb_buffer_pool_read_requests
        SQL_HIT="SELECT COALESCE(1 - (r.variable_value + 0.0) / NULLIF(rr.variable_value + 0.0, 0), 0) FROM performance_schema.global_status r JOIN performance_schema.global_status rr ON rr.variable_name = 'Innodb_buffer_pool_read_requests' WHERE r.variable_name = 'Innodb_buffer_pool_reads';"
        HIT_RATE=$(run_sql "$SQL_HIT")
    fi

    HIT_FILE="$OUTPUT_DIR/post_cache_hit_rate.csv"
    echo "metric,value" > "$HIT_FILE"
    echo "cache_hit_rate,$HIT_RATE" >> "$HIT_FILE"
    echo "Saved post-stats to $HIT_FILE"
fi
