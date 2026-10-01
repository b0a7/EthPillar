#!/bin/bash
# Integration-only helper for Upgrade-matrix execution downgrades.
#
# When the seeded execution binary is newer than official LATEST, stop
# execution and delete chaindata, then return without starting the unit.
# ethpillar upgrade installs the older binary and is the next process to
# create the DB.
#
# Paths match resync_execution.sh resyncClient (contents only, directory
# kept) and, for Reth, functions.sh getExecutionDatadir / getExecutionStaticFiles
# with the same /var/lib/reth default. JWT (/secrets) and validator keystores
# are outside these directories and are not removed.
# Production node updates do not call this script.

set -euo pipefail

CLIENT_RAW="${1:-}"
if [[ -z "$CLIENT_RAW" ]]; then
    echo "usage: wipe_execution_datadir.sh <execution-client>" >&2
    exit 2
fi

normalize_execution_client() {
    local raw
    raw="$(echo "${1:-}" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
    case "$raw" in
        nethermind) echo "Nethermind" ;;
        besu) echo "Besu" ;;
        geth) echo "Geth" ;;
        erigon) echo "Erigon" ;;
        ethrex) echo "Ethrex" ;;
        reth) echo "Reth" ;;
        *) echo "" ;;
    esac
}

# Same directories as resync_execution.sh (rm -rf <dir>/*). Reth resolves the
# live --datadir / --datadir.static-files the way that script does.
execution_datadir_for_client() {
    case "$EL" in
        Nethermind) echo "/var/lib/nethermind" ;;
        Besu) echo "/var/lib/besu" ;;
        Geth) echo "/var/lib/geth" ;;
        Erigon) echo "/var/lib/erigon" ;;
        Ethrex) echo "/var/lib/ethrex" ;;
        Reth)
            getExecutionDatadir
            echo "${DATADIR:-/var/lib/reth}"
            ;;
        *)
            echo ""
            return 1
            ;;
    esac
}

# Refuse anything that is not an EthPillar datadir under /var/lib/<client>.
wipe_el_contents() {
    local datadir="$1"
    if [[ -z "$datadir" || "$datadir" == "/" || "$datadir" == "/var/lib" || "$datadir" != /var/lib/* ]]; then
        echo "❌ Refusing to delete unsafe execution path: '${datadir:-}'" >&2
        exit 1
    fi
    if [[ ! -d "$datadir" ]]; then
        echo "Execution datadir absent (${datadir}); nothing to wipe"
        return 0
    fi
    # Contents only, including hidden entries. The directory and its owner stay.
    sudo find "$datadir" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
}

EL="$(normalize_execution_client "$CLIENT_RAW")"
if [[ -z "$EL" ]]; then
    echo "❌ Refusing execution wipe for unsupported client: ${CLIENT_RAW}" >&2
    exit 1
fi

EXECUTION_SERVICE_FILE="${EXECUTION_SERVICE_FILE:-/etc/systemd/system/execution.service}"
if [[ ! -f "$EXECUTION_SERVICE_FILE" ]]; then
    echo "❌ execution.service is not installed; cannot wipe execution chaindata" >&2
    exit 1
fi

if [[ "$EL" == "Reth" ]]; then
    REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
    cd "$REPO_ROOT"
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/functions.sh"
    export EXEC_SERVICE_FILE="$EXECUTION_SERVICE_FILE"
fi

DATADIR="$(execution_datadir_for_client)"
echo "Execution downgrade chaindata wipe: stopping ${EL} (${DATADIR})"
sudo systemctl stop execution
if sudo systemctl is-active --quiet execution; then
    echo "❌ execution still active after stop; refusing to wipe ${DATADIR}" >&2
    exit 1
fi

wipe_el_contents "$DATADIR"

if [[ "$EL" == "Reth" ]]; then
    getExecutionStaticFiles
    if [[ -n "${STATIC_FILES:-}" && "$STATIC_FILES" != "$DATADIR" ]]; then
        echo "Execution downgrade chaindata wipe: Reth static files (${STATIC_FILES})"
        wipe_el_contents "$STATIC_FILES"
    fi
fi

echo "✅ Execution chaindata removed (${DATADIR}). Execution left stopped for the downgrade upgrade."
