#!/usr/bin/env bash
set -euo pipefail

BASE=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726
PYTHON=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
WHEELS=/tmp/tpch_py311_wheels
VENDOR_ROOT="$BASE/vendor"
TARGET="$VENDOR_ROOT/py311"
STAGED="$VENDOR_ROOT/.py311.staged.$$"
LAUNCHER="$BASE/code/launch_tpch_formal_after_workload_binding_fix.sh"
NEW_LAUNCHER=/tmp/launch_tpch_formal_after_workload_binding_fix.sh
SYSTEM_LIBSTDCPP=/usr/lib64/libstdc++.so.6

OLD_LAUNCHER_SHA=ad863468fcf12edf4a74d25084dc99f2b895176fe3f7c072ae61efb807648285
NEW_LAUNCHER_SHA=59f4ca0b7f5aae5831ae4c734337bba5ceb8d00875e467636f7db63cd3189f5f

fail() { printf 'ERROR %s\n' "$*" >&2; exit 1; }
sha() { sha256sum "$1" | awk '{print $1}'; }

[[ ! -e "$TARGET" && ! -e "$STAGED" ]] || fail "vendor target or staging path already exists"
[[ -r "$SYSTEM_LIBSTDCPP" ]] || fail "system libstdc++ compatibility library is missing"
[[ "$(sha "$LAUNCHER")" == "$OLD_LAUNCHER_SHA" ]] || fail "launcher SHA mismatch"
[[ "$(sha "$NEW_LAUNCHER")" == "$NEW_LAUNCHER_SHA" ]] || fail "new launcher SHA mismatch"

declare -A EXPECTED=(
    [joblib-1.5.2-py3-none-any.whl]=4e1f0bdbb987e6d843c70cf43714cb276623def372df3c22fe5266b2670bc241
    [pandas-2.2.3-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl]=c124333816c3a9b03fbeef3a9f230ba9a737e9e5bb4060aa2107a86cc0a497fc
    [pyarrow-17.0.0-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl]=0b72e87fe3e1db343995562f7fff8aee354b55ee83d13afba65400c178ab2597
    [python_dateutil-2.9.0.post0-py2.py3-none-any.whl]=a8b2bc7bffae282281c8140a97d3aa9c14da0b136dfe83f850eea9a5f7470427
    [pytz-2026.3.post1-py2.py3-none-any.whl]=dd95840dd199baea12d9cc096a1d452caa6596a1c1e4b5f3dbd1541855d5e815
    [scikit_learn-1.5.2-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl]=f8b0ccd4a902836493e026c03256e8b206656f91fbcc4fde28c57a5b752561f1
    [scipy-1.14.1-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl]=fef8c87f8abfb884dac04e97824b61299880c43f4ce675dd2cbeadd3c9b466d2
    [six-1.17.0-py2.py3-none-any.whl]=4721f391ed90541fddacab5acf947aa0d3dc7d27b2e1e8eda2be8970586c3274
    [threadpoolctl-3.6.0-py3-none-any.whl]=43a0b8fd5a2928500110039e43a5eed8480b918967083ea48dc3ab9f13c4a7fb
    [tzdata-2026.3-py2.py3-none-any.whl]=dc096730c87af6cab1b171c9d532be840741ff5d459015e7f6947bd7d7e54931
)

wheel_paths=()
for name in "${!EXPECTED[@]}"; do
    path="$WHEELS/$name"
    [[ -f "$path" && "$(sha "$path")" == "${EXPECTED[$name]}" ]] \
        || fail "wheel SHA mismatch: $name"
    wheel_paths+=("$path")
done

mkdir -p "$STAGED"
"$PYTHON" - "$STAGED" "${wheel_paths[@]}" <<'PY'
import pathlib
import sys
import zipfile

target = pathlib.Path(sys.argv[1])
for wheel in sys.argv[2:]:
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(target)
PY

LD_PRELOAD="$SYSTEM_LIBSTDCPP" \
PYTHONPATH="$STAGED:$BASE/code" PYTHONNOUSERSITE=1 "$PYTHON" - <<'PY'
import joblib
import numpy
import pandas
import pyarrow
import scipy
import sklearn
from resource_model_retrainer import _ml_imports

assert joblib.__version__ == "1.5.2"
assert numpy.__version__ == "1.26.4"
assert pandas.__version__ == "2.2.3"
assert pyarrow.__version__ == "17.0.0"
assert scipy.__version__ == "1.14.1"
assert sklearn.__version__ == "1.5.2"
assert len(_ml_imports()) == 10
print("TPCH_VENDOR_SMOKE_OK", flush=True)
PY

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$VENDOR_ROOT"
mv "$STAGED" "$TARGET"
LD_PRELOAD="$SYSTEM_LIBSTDCPP" \
PYTHONPATH="$TARGET:$BASE/code" PYTHONNOUSERSITE=1 "$PYTHON" -c \
    'import joblib,numpy,pandas,pyarrow,scipy,sklearn; print("TPCH_VENDOR_INSTALLED")'

cp -p "$LAUNCHER" "$LAUNCHER.pre_vendor_fix.$STAMP"
cp -p "$NEW_LAUNCHER" "$LAUNCHER.next"
mv "$LAUNCHER.next" "$LAUNCHER"
chmod +x "$LAUNCHER"

[[ "$(sha "$LAUNCHER")" == "$NEW_LAUNCHER_SHA" ]] || fail "installed launcher SHA mismatch"
printf 'TPCH_VENDOR_DEPLOYED backup_stamp=%s\n' "$STAMP"
