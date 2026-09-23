#!/usr/bin/env bats
#
# tests/test_ethpillar_cli.bats
#
# Tests for ethpillar non-interactive CLI (help, status, start/stop/restart, targets, logs).
#

setup() {
    cd "$BATS_TEST_DIRNAME/.."

    export ETHPILLAR_VENV="/tmp/ethpillar_bats_cli_venv"
    rm -rf "$ETHPILLAR_VENV"

    export MOCK_BIN_DIR
    MOCK_BIN_DIR=$(mktemp -d)
    export COMMAND_LOG
    COMMAND_LOG=$(mktemp)
    export UPDATE_LOG
    UPDATE_LOG=$(mktemp)
    export TEST_SYSTEMD_DIR
    TEST_SYSTEMD_DIR=$(mktemp -d)
    export SYSTEMCTL_STATE_DIR
    SYSTEMCTL_STATE_DIR=$(mktemp -d)
    export ETHPILLAR_UPDATE_SCRIPT_DIR
    ETHPILLAR_UPDATE_SCRIPT_DIR=$(mktemp -d)

    # Default LATEST tags match the stub binaries in install_mock_clients.
    # Force-reset each test so a "behind" case cannot leak into the next.
    export MOCK_EL_LATEST=v1.30.0
    export MOCK_CL_LATEST=v5.3.0
    export MOCK_MEV_LATEST=v1.8.0
    export MOCK_CHARON_LATEST=v1.11.0
    export MOCK_EP_REMOTE_VERSION
    MOCK_EP_REMOTE_VERSION=$(grep '^EP_VERSION=' ethpillar.sh | cut -d'"' -f2)

    export EXEC_SERVICE_FILE="$TEST_SYSTEMD_DIR/execution.service"
    export CONSENSUS_SERVICE_FILE="$TEST_SYSTEMD_DIR/consensus.service"
    export VALIDATOR_SERVICE_FILE="$TEST_SYSTEMD_DIR/validator.service"
    export MEVBOOST_SERVICE_FILE="$TEST_SYSTEMD_DIR/mevboost.service"
    export CHARON_SERVICE_FILE="$TEST_SYSTEMD_DIR/charon.service"
    export CSM_VALIDATOR_SERVICE_FILE="$TEST_SYSTEMD_DIR/csm_nimbusvalidator.service"

    create_mock() {
        local name="$1"
        local stdout="${2:-}"
        cat <<EOF > "$MOCK_BIN_DIR/$name"
#!/bin/bash
echo "$name \$*" >> "$COMMAND_LOG"
if [ "$name" == "python3" ] && [[ "\$*" == *"-m venv"* ]]; then
    venv_path="\${@: -1}"
    command -p mkdir -p "\$venv_path/bin"
    {
        echo '#!/bin/bash'
        echo "echo \"pip \\\$*\" >> \"$COMMAND_LOG\""
        echo 'exit 0'
    } > "\$venv_path/bin/pip"
    command -p chmod +x "\$venv_path/bin/pip"
    command -p cp "$MOCK_BIN_DIR/venv_python3.template" "\$venv_path/bin/python3"
    command -p chmod +x "\$venv_path/bin/python3"
fi
if [ -n "$stdout" ]; then echo "$stdout"; fi
exit 0
EOF
        chmod +x "$MOCK_BIN_DIR/$name"
    }

    cat > "$MOCK_BIN_DIR/venv_python3.template" <<EOF
#!/bin/bash
echo "python3 \$*" >> "$COMMAND_LOG"
if [[ "\$*" == *release_info* ]]; then
  client=""
  prev=""
  for arg in "\$@"; do
    if [[ "\$prev" == "release_info" ]]; then
      client="\$arg"
      break
    fi
    prev="\$arg"
  done
  case "\${client,,}" in
    nethermind|geth|besu|erigon|reth|ethrex)
      echo "{\"version\":\"\${MOCK_EL_LATEST:-v1.30.0}\",\"commit\":\"\"}"
      ;;
    lighthouse|lodestar|teku|nimbus|prysm|grandine)
      echo "{\"version\":\"\${MOCK_CL_LATEST:-v5.3.0}\",\"commit\":\"\"}"
      ;;
    mevboost)
      echo "{\"version\":\"\${MOCK_MEV_LATEST:-v1.8.0}\",\"commit\":\"\"}"
      ;;
    charon)
      echo "{\"version\":\"\${MOCK_CHARON_LATEST:-v1.11.0}\",\"commit\":\"\"}"
      ;;
    *)
      echo "{\"version\":\"\${MOCK_LATEST_TAG:-v0.0.1}\",\"commit\":\"\"}"
      ;;
  esac
fi
exit 0
EOF

    for cmd in apt-get git python3 usermod mkdir stty pip docker ccze; do
        create_mock "$cmd"
    done
    create_mock "whiptail"

    # git show origin/main:ethpillar.sh → EP_VERSION for self-upgrade skip.
    cat <<EOF > "$MOCK_BIN_DIR/git"
#!/bin/bash
echo "git \$*" >> "$COMMAND_LOG"
if [[ "\$*" == *show* && "\$*" == *ethpillar.sh* ]]; then
  echo "EP_VERSION=\"\${MOCK_EP_REMOTE_VERSION:-}\""
fi
exit 0
EOF
    chmod +x "$MOCK_BIN_DIR/git"

    local _upd
    for _upd in execution consensus validator mevboost charon; do
        cat > "$ETHPILLAR_UPDATE_SCRIPT_DIR/update_${_upd}.sh" <<EOF
#!/bin/bash
echo "update_${_upd}.sh \$*" >> "$UPDATE_LOG"
exit 0
EOF
        chmod +x "$ETHPILLAR_UPDATE_SCRIPT_DIR/update_${_upd}.sh"
    done

    cat <<EOF > "$MOCK_BIN_DIR/sudo"
#!/bin/bash
export PATH="$MOCK_BIN_DIR:\$PATH"
"\$@"
EOF
    chmod +x "$MOCK_BIN_DIR/sudo"

    cat <<EOF > "$MOCK_BIN_DIR/curl"
#!/bin/bash
echo "curl \$*" >> "$COMMAND_LOG"
echo '{}'
exit 0
EOF
    chmod +x "$MOCK_BIN_DIR/curl"

    # journalctl must never follow (-f) in CI. Logs argv so logs-dispatch tests
    # can assert the rolling consolidated unit list.
    cat <<EOF > "$MOCK_BIN_DIR/journalctl"
#!/bin/bash
echo "journalctl \$*" >> "$COMMAND_LOG"
exit 0
EOF
    chmod +x "$MOCK_BIN_DIR/journalctl"

    # Mock systemctl: tracks ActiveState per unit via files in SYSTEMCTL_STATE_DIR
    cat <<EOF > "$MOCK_BIN_DIR/systemctl"
#!/bin/bash
echo "systemctl \$*" >> "$COMMAND_LOG"
cmd="\$1"
unit="\$2"
state_file="$SYSTEMCTL_STATE_DIR/\$unit"
case "\$cmd" in
  is-active)
    if [[ -f "\$state_file" ]]; then
      cat "\$state_file"
    else
      echo "inactive"
    fi
    # systemctl is-active exits 0 only when active
    [[ "\$(cat "\$state_file" 2>/dev/null)" == "active" ]]
    exit \$?
    ;;
  start)
    echo "active" > "\$state_file"
    exit 0
    ;;
  stop)
    echo "inactive" > "\$state_file"
    exit 0
    ;;
  restart)
    echo "active" > "\$state_file"
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
EOF
    chmod +x "$MOCK_BIN_DIR/systemctl"

    export PATH="$MOCK_BIN_DIR:$PATH"
}

teardown() {
    rm -rf "$MOCK_BIN_DIR" "$TEST_SYSTEMD_DIR" "$SYSTEMCTL_STATE_DIR" \
        "${ETHPILLAR_UPDATE_SCRIPT_DIR:-}" \
        "${ETHPILLAR_VENV:-/tmp/ethpillar_bats_cli_venv}"
    rm -f "$COMMAND_LOG" "$UPDATE_LOG"
}

write_service() {
    local path="$1"
    local description="${2:-Test Client}"
    local exec_start="${3:-/bin/true}"
    cat <<EOF > "$path"
[Unit]
Description=$description

[Service]
ExecStart=$exec_start
EOF
}

write_bin() {
    local path="$1"
    local stdout="$2"
    cat <<EOF > "$path"
#!/bin/bash
echo "$(basename "$path") \$*" >> "$COMMAND_LOG"
echo "$stdout"
exit 0
EOF
    chmod +x "$path"
}

write_charon_bin() {
    local path="$1"
    local ver="${2:-v1.11.0}"
    cat <<EOF > "$path"
#!/bin/bash
echo "charon \$*" >> "$COMMAND_LOG"
if [[ "\$1" == "version" ]]; then
  echo "$ver"
fi
exit 0
EOF
    chmod +x "$path"
}

# Install stub clients whose versions match the default MOCK_*_LATEST tags.
install_mock_clients() {
    write_bin "$MOCK_BIN_DIR/nethermind" "Nethermind 1.30.0"
    write_bin "$MOCK_BIN_DIR/lighthouse" "Lighthouse v5.3.0"
    write_bin "$MOCK_BIN_DIR/mev-boost" "mev-boost version v1.8.0"
    write_charon_bin "$MOCK_BIN_DIR/charon" "v1.11.0"

    write_service "$EXEC_SERVICE_FILE" "Nethermind Execution Client" "$MOCK_BIN_DIR/nethermind"
    write_service "$CONSENSUS_SERVICE_FILE" "Lighthouse Consensus Client" "$MOCK_BIN_DIR/lighthouse bn"
    write_service "$VALIDATOR_SERVICE_FILE" "Lighthouse Validator Client" "$MOCK_BIN_DIR/lighthouse vc"
    write_service "$MEVBOOST_SERVICE_FILE" "MEV-Boost" "$MOCK_BIN_DIR/mev-boost"
    write_service "$CHARON_SERVICE_FILE" "Charon" "$MOCK_BIN_DIR/charon run"
}

set_unit_state() {
    local unit="$1"
    local state="$2"
    echo "$state" > "$SYSTEMCTL_STATE_DIR/$unit"
}

@test "--help: lists commands and install-aware targets" {
    write_service "$EXEC_SERVICE_FILE" "Nethermind Execution Client"
    write_service "$CONSENSUS_SERVICE_FILE" "Lighthouse Consensus Client"

    run ./ethpillar.sh --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"status"* ]]
    [[ "$output" == *"check-updates"* ]]
    [[ "$output" == *"upgrade"* ]]
    [[ "$output" == *"logs"* ]]
    [[ "$output" == *"execution"* ]]
    [[ "$output" == *"consensus"* ]]
    [[ "$output" == *"ethpillar"* ]]
    [[ "$output" == *"Not installed:"* ]]
    [[ "$output" == *"validator"* ]]
    [[ "$output" == *"mevboost"* ]]
    ! grep -q whiptail "$COMMAND_LOG"
}

@test "help: same as --help" {
    run ./ethpillar.sh help
    [ "$status" -eq 0 ]
    [[ "$output" == *"Usage: ethpillar"* ]]
}

@test "unknown command: exits 1 with hint" {
    run ./ethpillar.sh not-a-real-command
    [ "$status" -eq 1 ]
    [[ "$output" == *"Unknown command"* ]]
    [[ "$output" == *"--help"* ]]
}

@test "logs: help lists the command as TUI Rolling Consolidated Logs" {
    run ./ethpillar.sh help
    [ "$status" -eq 0 ]
    [[ "$output" == *"logs"* ]]
    [[ "$output" == *"View rolling consolidated logs"* ]]
    [[ "$output" == *"TUI Rolling Consolidated Logs"* ]]
}

@test "logs: dispatches to view_journal_logs, not view_logs.sh" {
    run ./ethpillar.sh logs
    [ "$status" -eq 0 ]
    grep -q "journalctl -u validator -u consensus -u execution -u mevboost -u charon -u csm_nimbusvalidator --no-hostname -f" "$COMMAND_LOG"
    ! grep -q "view_logs.sh" "$COMMAND_LOG"
    ! grep -q "tmux" "$COMMAND_LOG"
}

@test "show_rolling_consolidated_logs: Aztec remote-rpc then the same journal units as TUI" {
    awk '
        /^show_rolling_consolidated_logs\(\)/ {flag=1}
        flag {print}
        flag && /^}/ {exit}
    ' functions.sh > "$MOCK_BIN_DIR/show_rolling_fn.txt"
    grep -q '/opt/ethpillar/aztec' "$MOCK_BIN_DIR/show_rolling_fn.txt"
    grep -q 'docker compose logs -f --tail=233' "$MOCK_BIN_DIR/show_rolling_fn.txt"
    grep -q 'view_journal_logs -u validator -u consensus -u execution -u mevboost -u charon -u csm_nimbusvalidator --no-hostname -f' "$MOCK_BIN_DIR/show_rolling_fn.txt"
    grep -q 'show_rolling_consolidated_logs' ethpillar.sh
    grep -q 'show_rolling_consolidated_logs' cli.sh
}

@test "logs: unexpected leftover arguments error cleanly" {
    run ./ethpillar.sh logs leftover
    [ "$status" -eq 1 ]
    [[ "$output" == *"Unexpected argument"* ]]
    [[ "$output" == *"ethpillar logs"* ]]
    ! grep -q "journalctl -u validator" "$COMMAND_LOG"
    ! grep -q "view_logs.sh" "$COMMAND_LOG"
}

@test "status: no clients installed exits 0" {
    run ./ethpillar.sh status
    [ "$status" -eq 0 ]
    [[ "$output" == *"No clients installed"* ]]
}

@test "status: reports active and exits 0 when all active" {
    write_service "$EXEC_SERVICE_FILE"
    write_service "$CONSENSUS_SERVICE_FILE"
    set_unit_state execution active
    set_unit_state consensus active

    run ./ethpillar.sh status
    [ "$status" -eq 0 ]
    [[ "$output" == *"execution"* ]]
    [[ "$output" == *"active"* ]]
    [[ "$output" == *"consensus"* ]]
}

@test "status: exits 1 when a unit is inactive" {
    write_service "$EXEC_SERVICE_FILE"
    set_unit_state execution inactive

    run ./ethpillar.sh status
    [ "$status" -eq 1 ]
    [[ "$output" == *"inactive"* ]]
}

@test "status --json: emits JSON map" {
    write_service "$EXEC_SERVICE_FILE"
    set_unit_state execution active

    run ./ethpillar.sh status --json
    [ "$status" -eq 0 ]
    [[ "$output" == *'"execution":"active"'* ]]
}

@test "start/stop/restart: invoke systemctl for installed targets only" {
    write_service "$EXEC_SERVICE_FILE"
    write_service "$MEVBOOST_SERVICE_FILE"
    set_unit_state execution active
    set_unit_state mevboost active

    run ./ethpillar.sh stop all
    [ "$status" -eq 0 ]
    grep -q "systemctl stop execution" "$COMMAND_LOG"
    grep -q "systemctl stop mevboost" "$COMMAND_LOG"
    ! grep -q "systemctl stop consensus" "$COMMAND_LOG"

    : > "$COMMAND_LOG"
    run ./ethpillar.sh start execution
    [ "$status" -eq 0 ]
    grep -q "systemctl start execution" "$COMMAND_LOG"
    ! grep -q "systemctl start mevboost" "$COMMAND_LOG"

    : > "$COMMAND_LOG"
    run ./ethpillar.sh restart mevboost
    [ "$status" -eq 0 ]
    grep -q "systemctl restart mevboost" "$COMMAND_LOG"
}

@test "start all: charon before validator; stop all: validator before charon" {
    write_service "$VALIDATOR_SERVICE_FILE"
    write_service "$CHARON_SERVICE_FILE"
    set_unit_state validator active
    set_unit_state charon active

    run ./ethpillar.sh start all
    [ "$status" -eq 0 ]
    start_charon_line=$(grep -n "systemctl start charon" "$COMMAND_LOG" | head -1 | cut -d: -f1)
    start_validator_line=$(grep -n "systemctl start validator" "$COMMAND_LOG" | head -1 | cut -d: -f1)
    [ -n "$start_charon_line" ]
    [ -n "$start_validator_line" ]
    [ "$start_charon_line" -lt "$start_validator_line" ]

    : > "$COMMAND_LOG"
    run ./ethpillar.sh stop all
    [ "$status" -eq 0 ]
    stop_validator_line=$(grep -n "systemctl stop validator" "$COMMAND_LOG" | head -1 | cut -d: -f1)
    stop_charon_line=$(grep -n "systemctl stop charon" "$COMMAND_LOG" | head -1 | cut -d: -f1)
    [ -n "$stop_validator_line" ]
    [ -n "$stop_charon_line" ]
    [ "$stop_validator_line" -lt "$stop_charon_line" ]
}

@test "start: rejects unknown or not-installed target" {
    write_service "$EXEC_SERVICE_FILE"

    run ./ethpillar.sh start charon
    [ "$status" -eq 1 ]
    [[ "$output" == *"not installed"* ]]

    run ./ethpillar.sh start bob
    [ "$status" -eq 1 ]
    [[ "$output" == *"Unknown target"* ]]
}

@test "upgrade: rejects not-installed client target" {
    run ./ethpillar.sh upgrade execution
    [ "$status" -eq 1 ]
    [[ "$output" == *"not installed"* ]]
}

@test "upgrade: skips charon when already on LATEST (no update script / no stop)" {
    install_mock_clients
    set_unit_state charon active

    run ./ethpillar.sh upgrade charon
    echo "$output"
    [ "$status" -eq 0 ]
    [[ "$output" == *"charon: already up to date (1.11.0) — skipping"* ]]
    [ ! -s "$UPDATE_LOG" ]
    ! grep -q "systemctl stop" "$COMMAND_LOG"
    ! grep -q "update_charon.sh" "$COMMAND_LOG"
}

@test "upgrade: calls update script when charon is behind" {
    install_mock_clients
    set_unit_state charon active
    export MOCK_CHARON_LATEST=v1.12.0

    run ./ethpillar.sh upgrade charon
    echo "$output"
    [ "$status" -eq 0 ]
    grep -q "update_charon.sh --auto" "$UPDATE_LOG"
    ! grep -q "skipping" <<< "$output"
}

@test "upgrade: skips every --auto client target when already on LATEST" {
    install_mock_clients
    local t
    for t in execution consensus validator mevboost charon; do
        : > "$UPDATE_LOG"
        : > "$COMMAND_LOG"
        run ./ethpillar.sh upgrade "$t"
        echo "$output"
        [ "$status" -eq 0 ]
        [[ "$output" == *"${t}: already up to date"* ]]
        [[ "$output" == *"skipping"* ]]
        [ ! -s "$UPDATE_LOG" ]
        ! grep -q "systemctl stop" "$COMMAND_LOG"
    done
}

@test "upgrade: calls update script for each --auto target when behind" {
    install_mock_clients
    export MOCK_EL_LATEST=v1.31.0
    export MOCK_CL_LATEST=v5.4.0
    export MOCK_MEV_LATEST=v1.9.0
    export MOCK_CHARON_LATEST=v1.12.0

    local t
    for t in execution consensus validator mevboost charon; do
        : > "$UPDATE_LOG"
        run ./ethpillar.sh upgrade "$t"
        echo "$output"
        [ "$status" -eq 0 ]
        grep -q "update_${t}.sh --auto" "$UPDATE_LOG"
        ! grep -q "${t}: already up to date" <<< "$output"
    done
}

@test "upgrade all: skips current clients and only updates the one behind" {
    install_mock_clients
    export MOCK_CHARON_LATEST=v1.12.0

    run ./ethpillar.sh upgrade all
    echo "$output"
    [ "$status" -eq 0 ]
    [[ "$output" == *"execution: already up to date"* ]]
    [[ "$output" == *"consensus: already up to date"* ]]
    [[ "$output" == *"validator: already up to date"* ]]
    [[ "$output" == *"mevboost: already up to date"* ]]
    [[ "$output" == *"ethpillar: already up to date"* ]]
    ! grep -q "charon: already up to date" <<< "$output"
    grep -q "update_charon.sh --auto" "$UPDATE_LOG"
    ! grep -q "update_execution.sh" "$UPDATE_LOG"
    ! grep -q "update_consensus.sh" "$UPDATE_LOG"
    ! grep -q "update_validator.sh" "$UPDATE_LOG"
    ! grep -q "update_mevboost.sh" "$UPDATE_LOG"
    ! grep -q "systemctl stop" "$COMMAND_LOG"
}

@test "upgrade ethpillar: skips when EP_VERSION matches remote" {
    run ./ethpillar.sh upgrade ethpillar
    echo "$output"
    [ "$status" -eq 0 ]
    [[ "$output" == *"ethpillar: already up to date"* ]]
    [[ "$output" == *"skipping"* ]]
    ! grep -q "git checkout" "$COMMAND_LOG"
    ! grep -q "git reset" "$COMMAND_LOG"
    ! grep -q "git clean" "$COMMAND_LOG"
}

@test "upgrade ethpillar: runs self-update when remote EP_VERSION differs" {
    export MOCK_EP_REMOTE_VERSION="9.9.9"

    run ./ethpillar.sh upgrade ethpillar
    echo "$output"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Updating EthPillar"* ]]
    ! grep -q "skipping" <<< "$output"
    grep -q "git checkout" "$COMMAND_LOG"
}
