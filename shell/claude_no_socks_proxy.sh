#!/usr/bin/env bash
set -euo pipefail

unset HTTP_PROXY HTTPS_PROXY ALL_PROXY
unset http_proxy https_proxy all_proxy

exec /home/anonymous/.nvm/versions/node/v24.10.0/bin/claude "$@"
