#!/usr/bin/env bash
set -euo pipefail

# This script is executed as root inside a private mount namespace.  The
# mounts are visible only to the four task-owned QEMU processes and therefore
# cannot replace or disturb host/container bind mounts under /disk1 or /disk2.
mount -o ro,noload /dev/nvme1n1 /disk1
mount /dev/nvme2n1 /disk2
mount --bind /disk2 /mnt/resource_adaptive_disk2_recovery

exec sudo -u anonymous -H \
  /home/anonymous/miniconda3/envs/resource_adaptive/bin/python \
  /home/anonymous/program/resource_adaptive_double_loop_sgd_isolated/shell/provision_double_loop_rootless_fallback_pool.py \
  --root /disk3/resource_adaptive_double_loop_sgd_rootless_pool_20260718 \
  --seed /disk1/resource_adaptive_experiment_matrix_20260718/rootless_pool_20260718/seeds/hybench10x.qcow2 \
  --ready-timeout 600
