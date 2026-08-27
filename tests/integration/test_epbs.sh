#!/bin/bash
# EthPillar ePBS migration integration test (Prysm + MEV-Boost).
# Runs inside the Docker container after a Prysm+MEV node is deployed.
#
# Starts the validator with an empty wallet (no keystores) so we can
# catch unknown flags / invalid proposer-settings without importing keys.

set -e

cd /ethpillar
source "${ETHPILLAR_ENV_FILE:-/ethpillar/env}"

py="${ETHPILLAR_PYTHON:-python3}"
SETTINGS="/var/lib/prysm_validator/proposer-settings.json"
VC_UNIT="/etc/systemd/system/validator.service"
BN_UNIT="/etc/systemd/system/consensus.service"
MEV_UNIT="/etc/systemd/system/mevboost.service"
SIDECAR="127.0.0.1:18550"

epbs_cli() {
    PYTHONPATH=/ethpillar "$py" -m manage.epbs "$@"
}

check_service_health() {
    local service="$1"
    shift
    echo "  [ePBS] Health check for $service..."
    bash /ethpillar/tests/integration/run_test.sh verify-service-health --service "$service" "$@"
}

assert_prysm_vc() {
    if [[ ! -f "$VC_UNIT" ]]; then
        echo "❌ validator.service not found"
        exit 1
    fi
    if ! grep -qi 'Description=.*Prysm' "$VC_UNIT"; then
        echo "❌ ePBS integration test requires a Prysm validator client"
        grep Description= "$VC_UNIT" || true
        exit 1
    fi
}

assert_mev_installed() {
    if [[ ! -f "$MEV_UNIT" ]]; then
        echo "❌ mevboost.service not found (install with --mev)"
        exit 1
    fi
}

assert_unit_has() {
    local unit="$1"
    local needle="$2"
    if ! grep -qF -- "$needle" "$unit"; then
        echo "❌ $unit is missing: $needle"
        cat "$unit"
        exit 1
    fi
}

assert_unit_lacks() {
    local unit="$1"
    local needle="$2"
    if grep -qF -- "$needle" "$unit"; then
        echo "❌ $unit still contains: $needle"
        cat "$unit"
        exit 1
    fi
}

assert_proposer_settings() {
    if [[ ! -f "$SETTINGS" ]]; then
        echo "❌ proposer-settings.json not written at $SETTINGS"
        exit 1
    fi
    PYTHONPATH=/ethpillar "$py" - <<'PY'
import json
from pathlib import Path
path = Path("/var/lib/prysm_validator/proposer-settings.json")
data = json.loads(path.read_text(encoding="utf-8"))
builder = (data.get("default_config") or {}).get("builder") or {}
relays = builder.get("relays") or []
assert data.get("version") == 2, data
assert builder.get("enabled") is True, builder
assert relays, "proposer-settings.json has no relays"
print(f"✅ proposer-settings.json: {len(relays)} relay(s), builder.enabled=true")
PY
}

assert_vc_process_has_epbs_flags() {
    local pid
    pid=$(sudo systemctl show -p MainPID --value validator 2>/dev/null || echo "0")
    if [[ -z "$pid" || "$pid" == "0" ]]; then
        echo "❌ validator MainPID is 0 — VC is not running"
        sudo journalctl -u validator --no-pager -n 40 || true
        exit 1
    fi
    local cmdline
    cmdline=$(tr '\0' ' ' < "/proc/${pid}/cmdline" || true)
    if [[ -z "$cmdline" ]]; then
        echo "❌ could not read /proc/${pid}/cmdline"
        exit 1
    fi
    if [[ "$cmdline" != *"--enable-builder"* ]]; then
        echo "❌ running VC is missing --enable-builder"
        echo "  cmdline: $cmdline"
        exit 1
    fi
    if [[ "$cmdline" != *"--proposer-settings-file"* ]]; then
        echo "❌ running VC is missing --proposer-settings-file"
        echo "  cmdline: $cmdline"
        exit 1
    fi
    echo "✅ running VC pid=${pid} has --enable-builder and --proposer-settings-file"
}

reload_and_restart() {
    local svc
    echo "  [ePBS] daemon-reload + restart: $*"
    sudo systemctl daemon-reload
    for svc in "$@"; do
        if ! sudo systemctl restart "$svc"; then
            echo "❌ systemctl restart $svc failed"
            sudo journalctl -u "$svc" --no-pager -n 40 || true
            exit 1
        fi
    done
}

echo "========================================="
echo " Starting ePBS Migration Integration Test"
echo "========================================="

assert_prysm_vc
assert_mev_installed
assert_unit_has "$BN_UNIT" "$SIDECAR"
assert_unit_has "$VC_UNIT" "--enable-builder"

if ! sudo systemctl is-active --quiet mevboost; then
    echo "❌ mevboost is not active before prepare"
    sudo systemctl status mevboost --no-pager -l || true
    exit 1
fi
echo "✅ Pre-migration: Prysm VC + active MEV-Boost + BN sidecar URL"

echo
echo "--- prepare (copy relays onto VC, keep MEV-Boost) ---"
epbs_cli status
epbs_cli prepare --apply --json
assert_unit_has "$VC_UNIT" "--enable-builder"
assert_unit_has "$VC_UNIT" "--proposer-settings-file"
assert_proposer_settings
assert_unit_has "$BN_UNIT" "$SIDECAR"
if ! sudo systemctl is-active --quiet mevboost; then
    echo "❌ mevboost stopped during prepare (must stay up until complete)"
    sudo systemctl status mevboost --no-pager -l || true
    exit 1
fi
echo "✅ prepare: VC ePBS flags written; BN sidecar and MEV-Boost still present"

reload_and_restart validator
check_service_health validator --force-validator
assert_vc_process_has_epbs_flags
echo "✅ validator started after prepare (empty wallet, ePBS flags accepted)"

echo
echo "--- complete (disable MEV-Boost, strip BN sidecar) ---"
epbs_cli complete --apply --json
assert_unit_lacks "$BN_UNIT" "$SIDECAR"
assert_unit_has "$VC_UNIT" "--enable-builder"
assert_unit_has "$VC_UNIT" "--proposer-settings-file"
assert_proposer_settings
if sudo systemctl is-active --quiet mevboost; then
    echo "❌ mevboost is still active after complete"
    sudo systemctl status mevboost --no-pager -l || true
    exit 1
fi
if sudo systemctl is-enabled --quiet mevboost; then
    echo "❌ mevboost is still enabled after complete"
    sudo systemctl status mevboost --no-pager -l || true
    exit 1
fi
echo "✅ complete: BN sidecar gone; MEV-Boost stopped/disabled; VC flags kept"

reload_and_restart consensus validator
# Pre-Gloas Sepolia may log builder-client errors once the sidecar is gone.
# Unknown flags / EXEC failures are still fatal.
check_service_health consensus \
    --ignore-journal-pattern "cannot connect to builder client"
check_service_health validator --force-validator \
    --ignore-journal-pattern "cannot connect to builder client"
assert_vc_process_has_epbs_flags
epbs_cli status

echo "========================================="
echo " ePBS migration: Prysm VC started with flags"
echo "========================================="
