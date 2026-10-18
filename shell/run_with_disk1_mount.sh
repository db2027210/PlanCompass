#!/usr/bin/env bash
set -euo pipefail

# The Codex execution environment may put sudo in a private mount namespace.
# Mount and exec the long-lived command in the same process tree so /disk1
# remains visible for the complete scheduler lifetime.
RECOVERY_MOUNT=/mnt/resource_adaptive_disk1_recovery

if ! mountpoint -q /disk1; then
    if mountpoint -q "${RECOVERY_MOUNT}"; then
        mount --bind "${RECOVERY_MOUNT}" /disk1
    else
        mount /disk1
    fi
fi

if [[ ! -d /disk1/resource_adaptive_experiment_matrix_20260718 ]]; then
    echo "expected experiment root is missing after mounting /disk1" >&2
    exit 1
fi

exec "$@"
