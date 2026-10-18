#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  run_series_cases.sh <exp_type> [options]

Arguments:
  exp_type            Experiment type passed to experiment_run.sh (e.g. low|shadow)

Options:
  --series S          Target series: L|M|H (default: M)
  --bench B           Benchmark set: all|tpcc|tpch|ch (default: tpcc)
  --containers LIST   Container indexes, comma-separated (e.g. 1,2,5,6)
                      Default by series: L=1,2,3,4,5,6,7,8; M=1,2,5,6; H=1,5
  --recreate-container
                      For each run: docker compose up nodeX(+exporter), run, then rm -sf
  --compose-dir DIR   Docker compose directory (default: /home/anonymous/dockercompose/og)
  --compose-file F    Compose file name (default: og_node_docker_compose.yml)
  --compose-env-file F
                      Compose env file (default: .env.<series>)
  --skip-exporter     Recreate mode only: do not start/remove nodeX_exporter
  --exec-only         Use experiment_run_exec_only.sh (skip data load in each run)
  --continue-on-fail  Continue running next case on failure
  --dry-run           Print planned runs only
  -h, --help          Show help

Examples:
  ./run_series_cases.sh low --series M --bench tpcc
  ./run_series_cases.sh low --series M --bench tpcc --containers 1,2 --recreate-container
EOF
}

if [ "$#" -lt 1 ]; then
  usage
  exit 1
fi

EXP_TYPE="$1"
shift 1

SERIES="M"
BENCH="tpcc"
CONTAINERS=""
RECREATE_CONTAINER=0
COMPOSE_DIR="/home/anonymous/dockercompose/og"
COMPOSE_FILE="og_node_docker_compose.yml"
COMPOSE_ENV_FILE=""
SKIP_EXPORTER=0
EXEC_ONLY=0
CONTINUE_ON_FAIL=0
DRY_RUN=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --series) SERIES="$2"; shift 2 ;;
    --bench) BENCH="$2"; shift 2 ;;
    --containers) CONTAINERS="$2"; shift 2 ;;
    --recreate-container) RECREATE_CONTAINER=1; shift ;;
    --compose-dir) COMPOSE_DIR="$2"; shift 2 ;;
    --compose-file) COMPOSE_FILE="$2"; shift 2 ;;
    --compose-env-file) COMPOSE_ENV_FILE="$2"; shift 2 ;;
    --skip-exporter) SKIP_EXPORTER=1; shift ;;
    --exec-only) EXEC_ONLY=1; shift ;;
    --continue-on-fail) CONTINUE_ON_FAIL=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

case "$SERIES" in
  L|M|H) ;;
  *)
    echo "ERROR: --series must be L, M, or H"
    exit 1
    ;;
esac

case "$BENCH" in
  all|tpcc|tpch|ch) ;;
  *)
    echo "ERROR: --bench must be all|tpcc|tpch|ch"
    exit 1
    ;;
esac

DEFAULT_CONTAINERS=""
case "$SERIES" in
  L) DEFAULT_CONTAINERS="1,2,3,4,5,6,7,8" ;;
  M) DEFAULT_CONTAINERS="1,2,5,6" ;;
  H) DEFAULT_CONTAINERS="1,5" ;;
esac

if [ -z "$CONTAINERS" ]; then
  CONTAINERS="$DEFAULT_CONTAINERS"
fi

if [ -z "$COMPOSE_ENV_FILE" ]; then
  COMPOSE_ENV_FILE=".env.${SERIES}"
fi

declare -a CONTAINER_LIST=()
IFS=',' read -r -a _raw_containers <<< "$CONTAINERS"
for c in "${_raw_containers[@]}"; do
  c="$(echo "$c" | tr -d '[:space:]')"
  if ! [[ "$c" =~ ^[1-8]$ ]]; then
    echo "ERROR: invalid container index: $c (must be 1..8)"
    exit 1
  fi
  case "$SERIES" in
    L) ;; # all valid
    M)
      case "$c" in 1|2|5|6) ;; *) echo "ERROR: container $c not active in series M"; exit 1 ;; esac
      ;;
    H)
      case "$c" in 1|5) ;; *) echo "ERROR: container $c not active in series H"; exit 1 ;; esac
      ;;
  esac
  CONTAINER_LIST+=("$c")
done

declare -a CASES=()
if [ "$SERIES" = "M" ]; then
  TPCC_CASES=(c11 c12 c13 c14 c15 c16 c17 c18 c19 c20)
  TPCH_CASES=(h11 h12 h13 h14 h15 h16 h17 h18 h19 h20)
  CH_CASES=(ch21 ch22 ch23 ch24 ch25 ch26 ch27 ch28 ch29 ch30 ch31 ch32 ch33 ch34 ch35 ch36 ch37 ch38 ch39 ch40)
elif [ "$SERIES" = "L" ]; then
  TPCC_CASES=(c1 c2 c3 c4 c5 c6 c7 c8 c9 c10)
  TPCH_CASES=(h1 h2 h3 h4 h5 h6 h7 h8 h9 h10)
  CH_CASES=(ch1 ch2 ch3 ch4 ch5 ch6 ch7 ch8 ch9 ch10 ch11 ch12 ch13 ch14 ch15 ch16 ch17 ch18 ch19 ch20)
else
  TPCC_CASES=(c21 c22 c23 c24 c25 c26 c27 c28 c29 c30)
  TPCH_CASES=(h21 h22 h23 h24 h25 h26 h27 h28 h29 h30)
  CH_CASES=(ch41 ch42 ch43 ch44 ch45 ch46 ch47 ch48 ch49 ch50 ch51 ch52 ch53 ch54 ch55 ch56 ch57 ch58 ch59 ch60)
fi

if [ "$BENCH" = "all" ] || [ "$BENCH" = "tpcc" ]; then
  CASES+=("${TPCC_CASES[@]}")
fi
if [ "$BENCH" = "all" ] || [ "$BENCH" = "tpch" ]; then
  CASES+=("${TPCH_CASES[@]}")
fi
if [ "$BENCH" = "all" ] || [ "$BENCH" = "ch" ]; then
  CASES+=("${CH_CASES[@]}")
fi

if [ "${#CASES[@]}" -eq 0 ]; then
  echo "No cases selected."
  exit 1
fi

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
RUN_SCRIPT="$SCRIPT_DIR/experiment_run.sh"
if [ "$EXEC_ONLY" -eq 1 ]; then
  RUN_SCRIPT="$SCRIPT_DIR/experiment_run_exec_only.sh"
fi
if [ ! -x "$RUN_SCRIPT" ]; then
  echo "ERROR: run script not executable: $RUN_SCRIPT"
  exit 1
fi

if [ "$RECREATE_CONTAINER" -eq 1 ]; then
  if [ ! -f "$COMPOSE_DIR/$COMPOSE_FILE" ]; then
    echo "ERROR: compose file not found: $COMPOSE_DIR/$COMPOSE_FILE"
    exit 1
  fi
  if [ ! -f "$COMPOSE_DIR/$COMPOSE_ENV_FILE" ]; then
    echo "ERROR: compose env file not found: $COMPOSE_DIR/$COMPOSE_ENV_FILE"
    exit 1
  fi
fi

echo "Series: $SERIES"
echo "Bench: $BENCH"
echo "Containers: ${CONTAINER_LIST[*]}"
echo "Recreate container per run: $RECREATE_CONTAINER"
echo "Total runs: $((${#CASES[@]} * ${#CONTAINER_LIST[@]}))"
printf 'Case list: %s\n' "${CASES[*]}"

if [ "$DRY_RUN" -eq 1 ]; then
  exit 0
fi

compose_cmd=(docker compose -f "$COMPOSE_FILE" --env-file "$COMPOSE_ENV_FILE")

if docker compose version >/dev/null 2>&1; then
  compose_cmd=(docker compose -f "$COMPOSE_FILE" --env-file "$COMPOSE_ENV_FILE")
elif command -v docker-compose >/dev/null 2>&1; then
  compose_cmd=(docker-compose -f "$COMPOSE_FILE" --env-file "$COMPOSE_ENV_FILE")
else
  echo "ERROR: neither 'docker compose' nor 'docker-compose' is available."
  exit 1
fi

if [ "$EXP_TYPE" = "shadow" ] && [ "$RECREATE_CONTAINER" -eq 0 ]; then
  echo "Pre-start services for shadow mode (without recreate-container)..."
  pushd "$COMPOSE_DIR" >/dev/null
  for container_index in "${CONTAINER_LIST[@]}"; do
    node_service="node${container_index}"
    exporter_service="node${container_index}_exporter"
    if [ "$SKIP_EXPORTER" -eq 1 ]; then
      "${compose_cmd[@]}" up -d "$node_service"
    else
      "${compose_cmd[@]}" up -d "$node_service" "$exporter_service"
    fi
  done
  popd >/dev/null
fi

run_one_case() {
  local case_id="$1"
  local container_index="$2"
  local node_service="node${container_index}"
  local exporter_service="node${container_index}_exporter"

  echo "=================================================="
  echo "Running case: $case_id on container: $container_index"
  echo "=================================================="

  if [ "$RECREATE_CONTAINER" -eq 1 ]; then
    pushd "$COMPOSE_DIR" >/dev/null
    if [ "$SKIP_EXPORTER" -eq 1 ]; then
      "${compose_cmd[@]}" up -d "$node_service"
    else
      "${compose_cmd[@]}" up -d "$node_service" "$exporter_service"
    fi
    popd >/dev/null
  fi

  if ! "$RUN_SCRIPT" "$EXP_TYPE" "$case_id" "$container_index"; then
    if [ "$RECREATE_CONTAINER" -eq 1 ]; then
      pushd "$COMPOSE_DIR" >/dev/null
      if [ "$SKIP_EXPORTER" -eq 1 ]; then
        "${compose_cmd[@]}" rm -sf "$node_service" || true
      else
        "${compose_cmd[@]}" rm -sf "$node_service" "$exporter_service" || true
      fi
      popd >/dev/null
    fi
    return 1
  fi

  if [ "$RECREATE_CONTAINER" -eq 1 ]; then
    pushd "$COMPOSE_DIR" >/dev/null
    if [ "$SKIP_EXPORTER" -eq 1 ]; then
      "${compose_cmd[@]}" rm -sf "$node_service" || true
    else
      "${compose_cmd[@]}" rm -sf "$node_service" "$exporter_service" || true
    fi
    popd >/dev/null
  fi

  return 0
}

for container_index in "${CONTAINER_LIST[@]}"; do
  for case_id in "${CASES[@]}"; do
    if ! run_one_case "$case_id" "$container_index"; then
      echo "Case failed: $case_id on container $container_index"
      if [ "$CONTINUE_ON_FAIL" -eq 1 ]; then
        continue
      fi
      exit 1
    fi
  done
done

echo "All selected runs completed."
