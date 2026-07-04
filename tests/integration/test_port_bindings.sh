#!/bin/bash
# Non-interactive RPC bind helper for integration tests.
# Drives the production exposeRpc helpers with deterministic yes/no answers.
set -euo pipefail

cd /ethpillar
source "${ETHPILLAR_ENV_FILE:-/ethpillar/env}"
source /ethpillar/functions.sh
export TERM="${TERM:-dumb}"

SERVICE="${1:?service name required (execution|consensus)}"
BIND_ADDR="${2:?bind address required (127.0.0.1|0.0.0.0)}"

getClient

case "${BIND_ADDR}" in
    0.0.0.0   ) responses='yy';;
    127.0.0.1 ) responses='yn';;
    * ) echo "Unsupported bind address: ${BIND_ADDR}" >&2; exit 1;;
esac

case "${SERVICE}" in
    execution ) exposeRpcEL <<< "${responses}";;
    consensus ) exposeRpcCL <<< "${responses}";;
    * ) echo "Unknown service: ${SERVICE}" >&2; exit 1;;
esac
