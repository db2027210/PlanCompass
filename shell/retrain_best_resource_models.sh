#!/usr/bin/env bash
set -euo pipefail

# Retrain the resource prediction experiments needed by resource_adaptive.
# Results are written under features_cache_M1/outputs/resource_adaptive_retrain_<timestamp>.

PYTHON_BIN="${PYTHON_BIN:-/home/anonymous/miniconda3/envs/resource_adaptive/bin/python}"
FC_DIR="${FC_DIR:-/home/anonymous/program/features_cache_M1}"
SEED="${SEED:-42}"
RUN_CPU="${RUN_CPU:-1}"
RUN_IO="${RUN_IO:-1}"
RUN_CANDIDATES="${RUN_CANDIDATES:-1}"

timestamp="$(date +%Y%m%d_%H%M%S)"
OUT_ROOT="${OUT_ROOT:-$FC_DIR/outputs/resource_adaptive_retrain_$timestamp}"
LOG_DIR="$OUT_ROOT/logs"
MANIFEST="$OUT_ROOT/manifest.txt"
INPUT_DIR="$OUT_ROOT/inputs"
CPU_HC_DATA_DIR="$INPUT_DIR/cpu_hc"
H_IO_DATA_DIR="$INPUT_DIR/h_io"

mkdir -p "$LOG_DIR"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python not executable: $PYTHON_BIN" >&2
  exit 1
fi

if [[ ! -d "$FC_DIR" ]]; then
  echo "features_cache_M1 not found: $FC_DIR" >&2
  exit 1
fi

cd "$FC_DIR"

if [[ ! -f data/h11_serial_features.parquet || ! -f data/c11_5s_features.parquet || ! -f data/ch21_5s_weighted_runtime_features.parquet ]]; then
  echo "Expected PostgreSQL feature parquets are missing under $FC_DIR/data" >&2
  exit 1
fi

prepare_inputs() {
  mkdir -p "$CPU_HC_DATA_DIR" "$H_IO_DATA_DIR"

  for f in data/h[0-9][0-9]_serial_features.parquet data/c[0-9][0-9]_5s_features.parquet; do
    [[ -e "$f" ]] || continue
    ln -sfn "$FC_DIR/$f" "$CPU_HC_DATA_DIR/$(basename "$f")"
  done

  for f in data/h[0-9][0-9]_serial_features.parquet data/h[0-9][0-9]_io_serial_features.parquet; do
    [[ -e "$f" ]] || continue
    ln -sfn "$FC_DIR/$f" "$H_IO_DATA_DIR/$(basename "$f")"
  done
}

prepare_inputs

{
  echo "resource_adaptive retrain manifest"
  echo "timestamp=$timestamp"
  echo "python=$PYTHON_BIN"
  echo "features_cache_dir=$FC_DIR"
  echo "out_root=$OUT_ROOT"
  echo "cpu_hc_data_dir=$CPU_HC_DATA_DIR"
  echo "h_io_data_dir=$H_IO_DATA_DIR"
  echo "seed=$SEED"
  echo
  echo "Notes:"
  echo "- Existing training scripts save metrics/results, not serialized model artifacts."
  echo "- Use the generated results to pick final model configs, then export artifacts in the next step."
  echo
} > "$MANIFEST"

run_step() {
  local name="$1"
  shift
  local log_file="$LOG_DIR/${name}.log"

  {
    echo
    echo "[$name]"
    printf 'command:'
    printf ' %q' "$@"
    echo
  } >> "$MANIFEST"

  echo "==> $name"
  echo "    log: $log_file"
  "$@" 2>&1 | tee "$log_file"
}

if [[ "$RUN_CPU" == "1" ]]; then
  run_step cpu_hc_formula \
    "$PYTHON_BIN" train_cpu_formula.py \
      --data-dir "$CPU_HC_DATA_DIR" \
      --out-dir "$OUT_ROOT/cpu_hc_formula" \
      --seed "$SEED"

  run_step cpu_ch_best_weighted_runtime \
    "$PYTHON_BIN" train_ch_models.py \
      --data-dir data \
      --pattern 'ch*_5s_weighted_runtime_features.parquet' \
      --out-dir "$OUT_ROOT/cpu_ch_weighted_runtime_sqrt_a1" \
      --seed "$SEED" \
      --model-set main \
      --sample-weight-mode sqrt_target \
      --weight-alpha 1 \
      --max-sample-weight 4

  run_step cpu_ch_robust_no_lag_target \
    "$PYTHON_BIN" train_ch_models.py \
      --data-dir data \
      --pattern 'ch*_5s_weighted_runtime_features.parquet' \
      --out-dir "$OUT_ROOT/cpu_ch_robust_no_lag_target" \
      --seed "$SEED" \
      --preset robust_random
fi

if [[ "$RUN_IO" == "1" ]]; then
  run_step io_official_lru_best \
    "$PYTHON_BIN" train_lru_only.py \
      --scenario all \
      --seed "$SEED" \
      --out "$OUT_ROOT/io_official_lru_best.txt" \
      --h-read-model gbr

  if [[ "$RUN_CANDIDATES" == "1" ]]; then
    run_step io_h_serial_candidate \
      "$PYTHON_BIN" train_h_io_models.py \
        --data-dir "$H_IO_DATA_DIR" \
        --out-dir "$OUT_ROOT/io_h_serial_candidate" \
        --seed "$SEED" \
        --target all

    run_step io_c_runtime_candidate \
      "$PYTHON_BIN" train_c_io_models.py \
        --data-dir data \
        --out-dir "$OUT_ROOT/io_c_runtime_candidate" \
        --seed "$SEED" \
        --target all \
        --cpu-features \
        --two-stage-bins 0

    run_step io_ch_runtime_candidate \
      "$PYTHON_BIN" train_ch_io_models.py \
        --data-dir data \
        --out-dir "$OUT_ROOT/io_ch_runtime_candidate" \
        --seed "$SEED" \
        --target all \
        --cpu-features \
        --two-stage-bins 0
  fi
fi

run_step summarize_results \
  "$PYTHON_BIN" -c '
import json
import re
from pathlib import Path

root = Path("'"$OUT_ROOT"'")

def best_from_results_json(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"ERROR: {exc}"
    if "groups" in data:
        out = []
        for group, result in data["groups"].items():
            best = result.get("best_overall", {})
            metrics = best.get("test_metrics", {})
            model_name = best.get("model_name")
            r2 = metrics.get("r2")
            out.append(f"{group}: {model_name} r2={r2}")
        return "; ".join(out)
    if "config" in data and "results" in data:
        best = data["config"].get("best_model_by_test_r2")
        metrics = data["results"].get(best, {}).get("test_metrics", {})
        r2 = metrics.get("r2")
        return f"{best} r2={r2}"
    if "regression" in data:
        best = data["regression"].get("best")
        metrics = data["regression"].get("models", {}).get(best, {}).get("test", {})
        r2 = metrics.get("r2")
        text = f"{best} r2={r2}"
        if "two_stage" in data:
            two_stage_r2 = data["two_stage"].get("test", {}).get("r2")
            text += f"; two_stage r2={two_stage_r2}"
        return text
    if isinstance(data, dict):
        best_name = None
        best_r2 = None
        for name, result in data.items():
            r2 = result.get("test", {}).get("r2") if isinstance(result, dict) else None
            if isinstance(r2, (int, float)) and (best_r2 is None or r2 > best_r2):
                best_name = name
                best_r2 = r2
        if best_name:
            return f"{best_name} r2={best_r2}"
    return "unrecognized"

lines = ["Retrain result summary", f"root: {root}", ""]
for path in sorted(root.rglob("results.json")):
    lines.append(f"{path.relative_to(root)}: {best_from_results_json(path)}")

for path in sorted(root.glob("io_official_lru_best.txt")):
    lines.append("")
    lines.append(f"{path.name}:")
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if re.search(r"^(H|C|CH) (total|read|write):", line):
            lines.append(line)

summary = "\n".join(lines) + "\n"
(root / "retrain_summary.txt").write_text(summary, encoding="utf-8")
print(summary)
'

echo
echo "All retrain steps finished."
echo "Output: $OUT_ROOT"
echo "Manifest: $MANIFEST"
echo "Summary: $OUT_ROOT/retrain_summary.txt"
