#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source_file="$root/hyxp_setlocal_driver/com/resourceadaptive/hyxp/SetLocalPostgresDriver.java"
output="${1:-$root/hyxp_setlocal_driver.jar}"
build_dir="$(mktemp -d)"
trap 'rm -rf "$build_dir"' EXIT

javac --release 11 -d "$build_dir/classes" "$source_file"
jar --create --file "$output.tmp" -C "$build_dir/classes" .
mv "$output.tmp" "$output"
sha256sum "$output"
