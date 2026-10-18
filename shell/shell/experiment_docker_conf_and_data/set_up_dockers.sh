#!/bin/bash

# ==================================

# ==================================


SIZE=""
DB_ENGINE="pg"
NODES="1-8"

usage() {
    echo ": $0 []..."
    echo ":"
    echo "  -s, --size        docker Configuration [: L|M|H] ()"
    echo "  --db-engine        [: pg|mysql] (: pg)"
    echo "  --nodes           / [: 1-4  1,2,5] (: 1-8)"
    echo " --help           "
    exit 1
}


while [[ $# -gt 0 ]]; do
    case "$1" in
        -s|--size) SIZE="$2"; shift 2 ;;
        --db-engine) DB_ENGINE="$2"; shift 2 ;;
        --nodes) NODES="$2"; shift 2 ;;
        --help) usage ;;
        *) echo "❌ : $1"; usage ;;
    esac
done


if [ -z "$SIZE" ]; then
    echo "❌ :  ----size"
    exit 1
fi

case "$SIZE" in
    L|M|H) ;; #
    *)
        echo "❌ : ----size : L|M|H"
        echo "   : $SIZE"
        exit 1
        ;;
esac

case "$DB_ENGINE" in
    pg|mysql) ;;
    *)
        echo "❌ : --db-engine  pg  mysql"
        exit 1
        ;;
esac

parse_nodes() {
    local spec="$1"
    local part
    local -a out=()

    IFS=',' read -r -a parts <<< "$spec"
    for part in "${parts[@]}"; do
        part="${part// /}"
        if [[ "$part" =~ ^([1-8])-([1-8])$ ]]; then
            local start="${BASH_REMATCH[1]}"
            local end="${BASH_REMATCH[2]}"
            if (( start > end )); then
                echo "❌ : --nodes : $part"
                exit 1
            fi
            for ((i=start; i<=end; i++)); do
                out+=("$i")
            done
        elif [[ "$part" =~ ^[1-8]$ ]]; then
            out+=("$part")
        else
            echo "❌ : --nodes : $part"
            echo "   : --nodes 1-4  --nodes 1,2,5"
            exit 1
        fi
    done


    SELECTED_NODES=($(printf "%s\n" "${out[@]}" | sort -n | uniq))
}

is_node_allowed_for_size() {
    local size="$1"
    local node="$2"
    case "$size" in
        L)
            return 0
            ;;
        M)
            [[ "$node" == "1" || "$node" == "2" || "$node" == "5" || "$node" == "6" ]]
            return $?
            ;;
        H)
            [[ "$node" == "1" || "$node" == "5" ]]
            return $?
            ;;
        *)
            return 1
            ;;
    esac
}

parse_nodes "$NODES"

for node in "${SELECTED_NODES[@]}"; do
    if ! is_node_allowed_for_size "$SIZE" "$node"; then
        echo "❌ : Size=$SIZE  node$node"
        exit 1
    fi
done


echo "docker Configuration: $SIZE, : $DB_ENGINE, : ${SELECTED_NODES[*]}"

DOCKER_COMPOSE_DIR="/home/anonymous/dockercompose/"
cd $DOCKER_COMPOSE_DIR

if [ "$DB_ENGINE" = "mysql" ]; then
    DOCKER_COMPOSE_DIR="/home/anonymous/dockercompose/mysql/"
    COMPOSE_FILE="mysql_node_docker_compose.yml"
else
    DOCKER_COMPOSE_DIR="/home/anonymous/dockercompose/og/"
    COMPOSE_FILE="og_node_docker_compose.yml"
fi
cd $DOCKER_COMPOSE_DIR

docker-compose -f "$COMPOSE_FILE" --env-file .env.$SIZE down --remove-orphans
SERVICES=()
for i in "${SELECTED_NODES[@]}"; do
    SERVICES+=("node$i" "node${i}_exporter")
done
docker-compose -f "$COMPOSE_FILE" --env-file .env.$SIZE --profile "$SIZE" up -d "${SERVICES[@]}"

for i in "${SELECTED_NODES[@]}"; do
    if (( i <= 4 )); then
        docker update --cpuset-mems="0" "opengauss_node$i"
    else
        docker update --cpuset-mems="1" "opengauss_node$i"
    fi
done

if [ "$DB_ENGINE" = "pg" ]; then
    for i in "${SELECTED_NODES[@]}"; do
        docker cp /home/anonymous/og_transfer/auto_explain.so "opengauss_node$i:/usr/local/opengauss/lib/postgresql/"
    done
else
    echo "MySQL mode: skip auto_explain plugin deployment."
fi

exit 0
