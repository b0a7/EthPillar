#!/bin/bash
# EthPillar Update / CLI Integration Test
# Runs inside the Docker container after the node is deployed.
# Exercises the non-interactive ethpillar CLI (upgrade, status, lifecycle).
#
# Upgrade-case deploy may have installed a seed (RC newer than LATEST, else a
# sane previous stable) via latest_override.py. That remap must already be
# cleared by run_inside_docker.py so this script's `ethpillar upgrade` path
# always resolves official LATEST (same as production). A safety clear is
# applied below. The persistent seeds manifest says which clients were seeded.
#
# Two-phase asserts per seeded target:
#   1. First upgrade must actually upgrade (no skip; PID changes; lands on LATEST)
#   2. Second upgrade must skip (skip message + check-updates exit 0; PID unchanged)
# Unseeded clients soft-skip the real-upgrade assert and only prove skip-when-latest.
#
# Install detection matches CLI: unit file on disk, not `systemctl list-unit-files`
# (the latter needs dbus and failed as non-root epstaker unless a Java client
# pulled dbus in as a side effect).

set -e
set -o pipefail

cd /ethpillar
SEEDS_FILE="${ETHPILLAR_INTEGRATION_UPGRADE_SEEDS:-/tmp/ethpillar-integration-upgrade-seeds.json}"
python3 /ethpillar/tests/integration/latest_override.py clear
source "${ETHPILLAR_ENV_FILE:-/ethpillar/env}"
: "${EL_IP_ADDRESS:=127.0.0.1}"
: "${EL_RPC_PORT:=8545}"
export EL_RPC_ENDPOINT="http://${EL_IP_ADDRESS}:${EL_RPC_PORT}"

ETHPILLAR=(bash /ethpillar/ethpillar.sh)
UPGRADE_LOG_DIR=$(mktemp -d)
trap 'rm -rf "$UPGRADE_LOG_DIR"' EXIT

unit_installed() {
    [[ -f "/etc/systemd/system/${1}.service" ]]
}

svc_pid() {
    local name="$1"
    if [[ "$(id -u)" -ne 0 ]] && command -v sudo >/dev/null 2>&1; then
        sudo systemctl show -p MainPID --value "$name" 2>/dev/null || echo "0"
    else
        systemctl show -p MainPID --value "$name" 2>/dev/null || echo "0"
    fi
}

service_client() {
    local svc="$1"
    local name
    [[ -f "/etc/systemd/system/${svc}.service" ]] || return 1
    name=$(grep Description= "/etc/systemd/system/${svc}.service" | awk -F= '{print $2}' | awk '{print $1}')
    [[ "$name" == "Erigon-Caplin" ]] && name=Erigon
    echo "${name,,}"
}

client_was_seeded() {
    local client="${1,,}"
    [[ -n "$client" && -f "$SEEDS_FILE" ]] || return 1
    jq -e --arg k "$client" '.clients[$k] != null and .clients[$k] != ""' "$SEEDS_FILE" >/dev/null 2>&1
}

seed_skip_reason() {
    local client="${1,,}"
    if [[ -f "$SEEDS_FILE" ]]; then
        jq -r --arg k "$client" '.skipped[$k] // empty' "$SEEDS_FILE" 2>/dev/null || true
    fi
}

upgrade_skipped() {
    local log="$1"
    grep -q "already up to date" "$log" && grep -q "skipping" "$log"
}

function check_binary() {
    local path="$1"
    if [[ ! -f "$path" && ! -d "$path" ]]; then
        echo "❌ Binary not found at expected path: $path"
        return 1
    fi
    echo "✅ Binary verified: $path"
}

function check_service_health() {
    local service="$1"
    echo "  [Integration] Delegating health check for $service to run_inside_docker.py..."
    bash /ethpillar/tests/integration/run_test.sh verify-service-health --service "$service"
}

function assert_status_active() {
    echo "Checking ethpillar status (expect active)..."
    "${ETHPILLAR[@]}" status
    echo "✅ ethpillar status: all installed clients active"
}

verify_binaries_and_health() {
    local svc
    for svc in "$@"; do
        unit_installed "$svc" || continue
        exec_path=$(grep -E "^ExecStart=" "/etc/systemd/system/${svc}.service" | head -n1 | sed 's/^ExecStart=//' | awk '{print $1}')
        check_binary "$exec_path"
        check_service_health "$svc"
    done
}

run_upgrade() {
    local target="$1"
    local log="$2"
    echo "Running: ethpillar upgrade ${target}"
    "${ETHPILLAR[@]}" upgrade "$target" | tee "$log"
}

assert_real_upgrade() {
    local target="$1"
    local log="$2"
    shift 2
    local svc old_pid new_pid
    if upgrade_skipped "$log"; then
        echo "❌ First ${target} upgrade skipped (expected a real upgrade from seed)"
        echo "---- upgrade output ----"
        cat "$log"
        exit 1
    fi
    for svc in "$@"; do
        unit_installed "$svc" || continue
        old_pid="${OLD_PIDS[$svc]:-0}"
        new_pid=$(svc_pid "$svc")
        if [[ "$old_pid" != "0" && "$old_pid" == "$new_pid" ]]; then
            echo "❌ ${svc} PID did not change (${old_pid}). Service was not restarted!"
            exit 1
        fi
        echo "✅ ${svc} restarted (${old_pid} → ${new_pid})"
    done
}

assert_skip_upgrade() {
    local target="$1"
    local log="$2"
    shift 2
    local svc old_pid new_pid check_rc
    if ! upgrade_skipped "$log"; then
        echo "❌ Second ${target} upgrade did not skip (expected already up to date — skipping)"
        echo "---- upgrade output ----"
        cat "$log"
        exit 1
    fi
    echo "✅ ${target} upgrade skipped (already up to date)"
    for svc in "$@"; do
        unit_installed "$svc" || continue
        old_pid="${OLD_PIDS[$svc]:-0}"
        new_pid=$(svc_pid "$svc")
        if [[ "$old_pid" != "$new_pid" ]]; then
            echo "❌ ${svc} PID changed on skip-upgrade (${old_pid} → ${new_pid})"
            exit 1
        fi
        echo "✅ ${svc} PID unchanged after skip (${new_pid})"
    done
    set +e
    "${ETHPILLAR[@]}" check-updates "$target"
    check_rc=$?
    set -e
    if [[ "$check_rc" -ne 0 ]]; then
        echo "❌ ethpillar check-updates ${target} exited ${check_rc} (expected 0 after skip)"
        exit 1
    fi
    echo "✅ ethpillar check-updates ${target} exit 0 (already latest)"
}

capture_pids() {
    local svc
    declare -gA OLD_PIDS=()
    for svc in "$@"; do
        unit_installed "$svc" || continue
        OLD_PIDS[$svc]=$(svc_pid "$svc")
    done
}

exercise_upgrade_target() {
    local target="$1"
    local client="$2"
    shift 2
    local services=("$@")
    local log1="${UPGRADE_LOG_DIR}/${target}-1.log"
    local log2="${UPGRADE_LOG_DIR}/${target}-2.log"
    local reason

    echo "========================================="
    echo " Upgrade target: ${target} (${client:-unknown})"
    echo "========================================="

    if client_was_seeded "$client"; then
        echo "Seeded ${client}: expecting a real upgrade, then skip-when-latest"
        capture_pids "${services[@]}"
        run_upgrade "$target" "$log1"
        assert_real_upgrade "$target" "$log1" "${services[@]}"
        verify_binaries_and_health "${services[@]}"
        echo "Verifying ${target} landed on official LATEST..."
        python3 /ethpillar/tests/integration/latest_snapshot.py clear
        bash /ethpillar/tests/integration/check_client_versions.sh

        capture_pids "${services[@]}"
        run_upgrade "$target" "$log2"
        assert_skip_upgrade "$target" "$log2" "${services[@]}"
    else
        reason=$(seed_skip_reason "$client")
        echo "⚠️  ${client:-$target} has no upgrade seed${reason:+ (${reason})}. Soft-skipping real-upgrade assert; proving skip-when-already-latest only."
        capture_pids "${services[@]}"
        run_upgrade "$target" "$log1"
        assert_skip_upgrade "$target" "$log1" "${services[@]}"
        verify_binaries_and_health "${services[@]}"
    fi
}

echo "========================================="
echo " Starting EthPillar CLI Integration Test"
echo "========================================="

if [[ -f "$SEEDS_FILE" ]]; then
    echo "Upgrade seeds:"
    python3 /ethpillar/tests/integration/latest_override.py seeds
else
    echo "No upgrade-seeds manifest at ${SEEDS_FILE} (treat all clients as unseeded)"
fi

echo "Help output (install-aware)..."
set +o pipefail
"${ETHPILLAR[@]}" --help | head -40
set -o pipefail

# Execution client
if unit_installed execution; then
    exercise_upgrade_target execution "$(service_client execution)" execution
else
    echo "No execution client installed (no /etc/systemd/system/execution.service). Skipping."
fi

# Consensus / validator. Prefer consensus (BN updater also restarts a separate VC).
if unit_installed consensus; then
    cl_client=$(service_client consensus)
    if [[ "$cl_client" == "caplin" ]]; then
        echo "ℹ️  Caplin is integrated in Erigon; skipping consensus upgrade target."
    else
        if unit_installed validator; then
            exercise_upgrade_target consensus "$cl_client" consensus validator
        else
            exercise_upgrade_target consensus "$cl_client" consensus
        fi
    fi
elif unit_installed validator; then
    exercise_upgrade_target validator "$(service_client validator)" validator
else
    echo "No consensus/validator client installed. Skipping."
fi

# MEV-Boost
if unit_installed mevboost; then
    exercise_upgrade_target mevboost mevboost mevboost
else
    echo "No MEV-Boost installed. Skipping."
fi

# Status + lifecycle smoke (CLI start/stop/restart)
if unit_installed execution || unit_installed consensus; then
    echo "========================================="
    echo " CLI status / lifecycle smoke"
    echo "========================================="

    assert_status_active

    echo "Running check-updates (informational)..."
    set +e
    "${ETHPILLAR[@]}" check-updates
    check_rc=$?
    set -e
    if [[ "$check_rc" -eq 1 ]]; then
        echo "❌ ethpillar check-updates failed with error"
        exit 1
    fi
    echo "✅ ethpillar check-updates completed (exit $check_rc; 0=current 2=updates available)"

    echo "Stopping all clients via CLI..."
    "${ETHPILLAR[@]}" stop all
    if "${ETHPILLAR[@]}" status; then
        echo "❌ ethpillar status succeeded after stop (expected non-zero)"
        exit 1
    fi
    echo "✅ ethpillar status correctly reports inactive after stop"

    echo "Starting all clients via CLI..."
    "${ETHPILLAR[@]}" start all
    # Allow units a moment to enter active
    sleep 2
    assert_status_active

    # Re-verify health after lifecycle
    for svc in execution consensus validator mevboost charon; do
        if unit_installed "$svc"; then
            check_service_health "$svc"
        fi
    done

    echo "Restarting all clients via CLI..."
    "${ETHPILLAR[@]}" restart all
    sleep 2
    assert_status_active
fi

echo "========================================="
echo " All ethpillar CLI update/lifecycle tests passed!"
echo "========================================="
