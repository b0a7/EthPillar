#!/usr/bin/env bats
# Shell-level tests for Keymanager ensure helpers (inactive VC → offer start).

setup() {
  # shellcheck disable=SC1091
  source "$BATS_TEST_DIRNAME/../functions.sh"

  export COMMAND_LOG
  COMMAND_LOG=$(mktemp)
  export CONSENSUS_SERVICE_FILE
  CONSENSUS_SERVICE_FILE=$(mktemp)
  export VALIDATOR_SERVICE_FILE
  VALIDATOR_SERVICE_FILE=$(mktemp)
  export BASE_DIR="$BATS_TEST_DIRNAME/.."

  export SYSTEMCTL_ACTIVE_STATE="inactive"
  systemctl() {
    echo "systemctl $*" >> "$COMMAND_LOG"
    if [[ "$1" == "is-active" ]]; then
      if [[ "$SYSTEMCTL_ACTIVE_STATE" == "active" ]]; then
        return 0
      fi
      return 3
    fi
    return 0
  }
  export -f systemctl

  sudo() {
    echo "sudo $*" >> "$COMMAND_LOG"
    if [[ "$1" == "systemctl" ]]; then
      shift
      systemctl "$@"
      return $?
    fi
    return 0
  }
  export -f sudo

  # Source helpers only (no deposit-cli download / menus).
  # shellcheck disable=SC1091
  source "$BATS_TEST_DIRNAME/../manage_validator_keys.sh" helpers-only

  > "$COMMAND_LOG"
  KM_VALIDATOR_UNIT=""
  KM_VALIDATOR_MODE=""
}

teardown() {
  rm -f "$COMMAND_LOG" "$CONSENSUS_SERVICE_FILE" "$VALIDATOR_SERVICE_FILE"
}

write_prysm_validator_service() {
  cat > "$VALIDATOR_SERVICE_FILE" <<EOF
[Unit]
Description=Prysm Validator Client service for MAINNET

[Service]
ExecStart=/usr/local/bin/prysm-validator --rpc --rpc-port=7500
EOF
}

write_grandine_integrated_consensus() {
  cat > "$CONSENSUS_SERVICE_FILE" <<EOF
[Service]
ExecStart=/usr/local/bin/grandine --keystore-dir=/var/lib/grandine/validator_keys
EOF
}

@test "keymanagerResolveValidatorUnit → validator for separate VC" {
  write_prysm_validator_service
  # Do not use bats run — function sets globals in current shell
  keymanagerResolveValidatorUnit
  [ "$KM_VALIDATOR_UNIT" = "validator" ]
  [ "$KM_VALIDATOR_MODE" = "separate" ]
}

@test "keymanagerResolveValidatorUnit → consensus for integrated grandine" {
  rm -f "$VALIDATOR_SERVICE_FILE"
  export VALIDATOR_SERVICE_FILE="/nonexistent/validator.service"
  write_grandine_integrated_consensus
  keymanagerResolveValidatorUnit
  [ "$KM_VALIDATOR_UNIT" = "consensus" ]
  [ "$KM_VALIDATOR_MODE" = "integrated_grandine" ]
}

@test "keymanagerResolveValidatorUnit fails when no validator unit" {
  rm -f "$VALIDATOR_SERVICE_FILE" "$CONSENSUS_SERVICE_FILE"
  export VALIDATOR_SERVICE_FILE="/nonexistent/validator.service"
  export CONSENSUS_SERVICE_FILE="/nonexistent/consensus.service"
  run keymanagerResolveValidatorUnit
  [ "$status" -ne 0 ]
}

@test "keymanagerValidatorIsActive respects systemctl is-active" {
  SYSTEMCTL_ACTIVE_STATE="inactive"
  run keymanagerValidatorIsActive validator
  [ "$status" -ne 0 ]

  SYSTEMCTL_ACTIVE_STATE="active"
  run keymanagerValidatorIsActive validator
  [ "$status" -eq 0 ]
}

@test "keymanagerOfferStartValidator returns 1 when unit already active" {
  write_prysm_validator_service
  SYSTEMCTL_ACTIVE_STATE="active"
  run keymanagerOfferStartValidator "http://127.0.0.1:7500"
  [ "$status" -ne 0 ]
  ! grep -q "systemctl start" "$COMMAND_LOG"
}
