#!/bin/bash
# Verify functions.sh can parse versions from installed client binaries.
# Uses systemd service files (same paths as update_execution.sh / update_consensus.sh).
set -euo pipefail

cd /ethpillar
# shellcheck source=../../functions.sh
source ./functions.sh

fail=0

check_el_version() {
    [[ -f /etc/systemd/system/execution.service ]] || return 0
    local el
    el=$(grep Description= /etc/systemd/system/execution.service | awk -F= '{print $2}' | awk '{print $1}')
    # Erigon+Caplin integrated unit uses the erigon binary.
    [[ "$el" == "Erigon-Caplin" ]] && el=Erigon
    getExecutionCurrentVersion "$el"
    if [[ -z "$VERSION" || "$VERSION" == Unable* ]]; then
        echo "❌ EL version parse failed for ${el}: ${VERSION:-empty}"
        fail=1
        return 0
    fi
    echo "✅ EL ${el} version: ${VERSION}"
}

check_cl_version() {
    [[ -f /etc/systemd/system/consensus.service ]] || return 0
    local cl
    cl=$(grep Description= /etc/systemd/system/consensus.service | awk -F= '{print $2}' | awk '{print $1}')
    if [[ "$cl" == "Caplin" ]]; then
        echo "ℹ️  Skipping Caplin version (integrated in Erigon)"
        return 0
    fi
    getClVcCurrentVersion "$cl"
    if [[ -z "$VERSION" || "$VERSION" == "NotInstalled" ]]; then
        echo "❌ CL version parse failed for ${cl}: ${VERSION:-empty}"
        fail=1
        return 0
    fi
    echo "✅ CL ${cl} version: ${VERSION}"
}

check_vc_version() {
    [[ -f /etc/systemd/system/validator.service ]] || return 0
    local vc
    vc=$(grep Description= /etc/systemd/system/validator.service | awk -F= '{print $2}' | awk '{print $1}')
    getClVcCurrentVersion "$vc"
    if [[ -z "$VERSION" || "$VERSION" == "NotInstalled" ]]; then
        echo "❌ VC version parse failed for ${vc}: ${VERSION:-empty}"
        fail=1
        return 0
    fi
    echo "✅ VC ${vc} version: ${VERSION}"
}

echo "🔢 Verifying installed client version parsing..."
check_el_version
check_cl_version
check_vc_version

if [[ "$fail" -ne 0 ]]; then
    exit 1
fi
