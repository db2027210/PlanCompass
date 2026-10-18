#!/usr/bin/env bash
set -euo pipefail

release="/disk1/resource_adaptive/pg_algorithm_c_native_hybench_20260809_v2"
props=""
previous=""
for argument in "$@"; do
  if [[ "${previous}" == "-c" ]]; then
    props="${argument}"
    break
  fi
  previous="${argument}"
done
if [[ -z "${props}" ]]; then
  echo "missing -c props argument" >&2
  exit 64
fi
if [[ ! -f "${props}" ]]; then
  echo "props file does not exist: ${props}" >&2
  exit 66
fi

props_dir="$(dirname "${props}")"
props_name="$(basename "${props}" .props)"
runtime="${props_dir}/client_runtime/${props_name}"
mkdir -p "${runtime}"

sf="$(awk -F= '$1 == "sf" {gsub(/[[:space:]]/, "", $2); print $2; exit}' "${props}")"
if [[ ! "${sf}" =~ ^[A-Za-z0-9_.-]+$ ]]; then
  echo "invalid or missing sf in props: ${props}" >&2
  exit 65
fi
data_name="Data_${sf}"
release_data="${release}/${data_name}"
runtime_data="${runtime}/${data_name}"
for required_file in Related_transfer_bids Related_checking_bids; do
  if [[ ! -r "${release_data}/${required_file}" ]]; then
    echo "required HyBench risk-control data is missing: ${release_data}/${required_file}" >&2
    exit 66
  fi
done
if [[ -e "${runtime_data}" && ! -L "${runtime_data}" ]]; then
  echo "refuse to replace non-symlink runtime data path: ${runtime_data}" >&2
  exit 73
fi
ln -sfnT "${release_data}" "${runtime_data}"

if [[ "${HYBENCH_NATIVE_PREFLIGHT_ONLY:-0}" == "1" ]]; then
  printf 'runtime=%s\ndata=%s\n' "${runtime}" "${runtime_data}"
  exit 0
fi

cd "${runtime}"
exec java -cp "${release}/lib/*:." com.hybench.HyBench "$@"
