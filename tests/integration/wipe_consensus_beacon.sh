#!/bin/bash
# Integration-only helper for Upgrade-matrix consensus downgrades.
#
# When the seeded consensus binary is newer than official LATEST (an RC such
# as Lighthouse v8.3.0-rc.0 vs LATEST v8.2.3), the newer process has already
# written a beacon DB the older binary cannot open. Stop consensus and delete
# only the beacon DB, then return without starting the unit. ethpillar upgrade
# installs the older binary and is the next process to create the DB.
#
# Paths come from resync_consensus.sh (beacon only; validator keystores stay).
# Production node updates do not call this script.

set -euo pipefail

CLIENT_RAW="${1:-}"
if [[ -z "$CLIENT_RAW" ]]; then
    echo "usage: wipe_consensus_beacon.sh <consensus-client>" >&2
    exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# shellcheck disable=SC1091
source "${REPO_ROOT}/resync_consensus.sh"

CL="$(normalize_client "$CLIENT_RAW")"
if [[ -z "$CL" ]]; then
    echo "❌ Refusing beacon wipe for unsupported consensus client: ${CLIENT_RAW}" >&2
    exit 1
fi

if [[ -z "${NETWORK:-}" ]]; then
    NETWORK="$(detect_network_from_systemd || true)"
fi
NETWORK="$(normalize_network "${NETWORK:-}")"
if [[ -z "$NETWORK" ]]; then
    if [[ "$CL" == "Grandine" ]]; then
        echo "❌ Cannot wipe Grandine beacon DB without a network" >&2
        exit 1
    fi
    # Non-Grandine beacon paths do not include the network slug.
    NETWORK="Mainnet"
fi

CONSENSUS_SERVICE_FILE="${CONSENSUS_SERVICE_FILE:-/etc/systemd/system/consensus.service}"
if [[ ! -f "$CONSENSUS_SERVICE_FILE" ]]; then
    echo "❌ consensus.service is not installed; cannot wipe beacon DB" >&2
    exit 1
fi

DATADIR="$(beacon_datadir_for_client)"
echo "Consensus downgrade beacon wipe: stopping ${CL} (${DATADIR})"
sudo systemctl stop consensus
if sudo systemctl is-active --quiet consensus; then
    echo "❌ consensus still active after stop; refusing to wipe ${DATADIR}" >&2
    exit 1
fi

# Leave the unit stopped. The older binary must be the next process to open the DB.
wipe_beacon_datadir "$DATADIR"
echo "✅ Beacon DB removed (${DATADIR}). Consensus left stopped for the downgrade upgrade."
