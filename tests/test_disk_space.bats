#!/usr/bin/env bats
#
# tests/test_disk_space.bats
#
# Unit tests for checkDiskSpace() and helpers in functions.sh.
# Mocks df / whiptail / runScript so Charon and VC-only nodes never
# get a consensus-resync prompt.
#
# Run: bats tests/test_disk_space.bats
#

setup() {
  cd "$BATS_TEST_DIRNAME/.."

  export COMMAND_LOG
  COMMAND_LOG=$(mktemp)
  export TEST_DIR
  TEST_DIR=$(mktemp -d)
  export ALERT_FILE="$TEST_DIR/mount_alerts.txt"
  export CONSENSUS_SERVICE_FILE="$TEST_DIR/consensus.service"
  export VALIDATOR_SERVICE_FILE="$TEST_DIR/validator.service"
  export CHARON_SERVICE_FILE="$TEST_DIR/charon.service"
  export DF_USE_PERCENT=50
  export WHIPTAIL_EXIT_CODE=0

  # shellcheck disable=SC1091
  source ./functions.sh

  # Override env defaults after source so tests stay isolated.
  export ALERT_FILE="$TEST_DIR/mount_alerts.txt"
  export THRESHOLD=10
  MOUNT_POINTS=("/")

  df() {
    echo "Filesystem      Size  Used Avail Use% Mounted on"
    echo "/dev/sda1       100G   90G  10G  ${DF_USE_PERCENT:-50}% /"
    return 0
  }
  export -f df

  whiptail() {
    echo "whiptail $*" >> "$COMMAND_LOG"
    if [[ "$*" == *"--msgbox"* ]]; then
      return 0
    fi
    return "${WHIPTAIL_EXIT_CODE:-0}"
  }
  export -f whiptail

  runScript() {
    echo "runScript $*" >> "$COMMAND_LOG"
  }
  export -f runScript

  ohai() {
    echo "ohai $*" >> "$COMMAND_LOG"
  }
  export -f ohai

  > "$COMMAND_LOG"
}

teardown() {
  rm -f "$COMMAND_LOG"
  rm -rf "$TEST_DIR"
}

write_validator_service() {
  cat > "$VALIDATOR_SERVICE_FILE" <<EOF
[Service]
ExecStart=/usr/local/bin/lighthouse validator_client --beacon-nodes=http://127.0.0.1:3600
EOF
}

write_charon_service() {
  cat > "$CHARON_SERVICE_FILE" <<EOF
[Service]
ExecStart=/usr/local/bin/charon run --beacon-node-endpoints=http://127.0.0.1:5052
EOF
}

# ── helpers ────────────────────────────────────────────────────────────────────

@test "hasConsensusService is true when consensus.service exists" {
  touch "$CONSENSUS_SERVICE_FILE"
  run hasConsensusService
  [ "$status" -eq 0 ]
}

@test "hasConsensusService is false when consensus.service is missing" {
  run hasConsensusService
  [ "$status" -eq 1 ]
}

@test "lowDiskSpaceTipsNoConsensusResync mentions Charon when enabled" {
  write_charon_service
  run lowDiskSpaceTipsNoConsensusResync
  [ "$status" -eq 0 ]
  [[ "$output" == *"NCDU"* ]]
  [[ "$output" == *"Upgrade Storage"* ]]
  [[ "$output" == *"Obol Charon"* ]]
  [[ "$output" != *"resync consensus"* ]]
}

@test "lowDiskSpaceTipsNoConsensusResync mentions VC-only when validator.service exists without Charon" {
  write_validator_service
  run lowDiskSpaceTipsNoConsensusResync
  [ "$status" -eq 0 ]
  [[ "$output" == *"NCDU"* ]]
  [[ "$output" == *"validator-client-only"* ]]
  [[ "$output" != *"Obol Charon"* ]]
}

# ── checkDiskSpace ─────────────────────────────────────────────────────────────

@test "checkDiskSpace offers consensus resync when consensus.service exists" {
  touch "$CONSENSUS_SERVICE_FILE"
  DF_USE_PERCENT=95
  WHIPTAIL_EXIT_CODE=0
  run checkDiskSpace
  [ "$status" -eq 0 ]
  grep -q "Recommend to resync consensus client" "$COMMAND_LOG"
  grep -q "runScript resync_consensus.sh" "$COMMAND_LOG"
  grep -q "Consensus resync complete" "$COMMAND_LOG"
  grep -q "Tips: Disk Space" "$COMMAND_LOG"
}

@test "checkDiskSpace skips consensus resync when user declines" {
  touch "$CONSENSUS_SERVICE_FILE"
  DF_USE_PERCENT=95
  WHIPTAIL_EXIT_CODE=1
  run checkDiskSpace
  [ "$status" -eq 0 ]
  grep -q "Recommend to resync consensus client" "$COMMAND_LOG"
  if grep -q "runScript resync_consensus.sh" "$COMMAND_LOG"; then
    echo "unexpected resync: $(cat "$COMMAND_LOG")"
    return 1
  fi
}

@test "checkDiskSpace does not offer consensus resync on Charon node without CL" {
  write_charon_service
  write_validator_service
  DF_USE_PERCENT=95
  run checkDiskSpace
  [ "$status" -eq 0 ]
  if grep -q "Recommend to resync consensus client" "$COMMAND_LOG"; then
    echo "unexpected CL resync prompt: $(cat "$COMMAND_LOG")"
    return 1
  fi
  if grep -q "runScript resync_consensus.sh" "$COMMAND_LOG"; then
    echo "unexpected resync: $(cat "$COMMAND_LOG")"
    return 1
  fi
  grep -q -- "--msgbox" "$COMMAND_LOG"
  grep -q "Obol Charon" "$COMMAND_LOG"
  grep -q "NCDU" "$COMMAND_LOG"
}

@test "checkDiskSpace does not offer consensus resync on VC-only node" {
  write_validator_service
  DF_USE_PERCENT=95
  run checkDiskSpace
  [ "$status" -eq 0 ]
  if grep -q "Recommend to resync consensus client" "$COMMAND_LOG"; then
    echo "unexpected CL resync prompt: $(cat "$COMMAND_LOG")"
    return 1
  fi
  if grep -q "runScript resync_consensus.sh" "$COMMAND_LOG"; then
    echo "unexpected resync: $(cat "$COMMAND_LOG")"
    return 1
  fi
  grep -q "validator-client-only" "$COMMAND_LOG"
  grep -q "Upgrade Storage" "$COMMAND_LOG"
}

@test "checkDiskSpace still offers consensus resync when Charon and consensus.service both exist" {
  touch "$CONSENSUS_SERVICE_FILE"
  write_charon_service
  DF_USE_PERCENT=95
  WHIPTAIL_EXIT_CODE=0
  run checkDiskSpace
  [ "$status" -eq 0 ]
  grep -q "Recommend to resync consensus client" "$COMMAND_LOG"
  grep -q "runScript resync_consensus.sh" "$COMMAND_LOG"
}

@test "checkDiskSpace skips dialog when free space is sufficient" {
  touch "$CONSENSUS_SERVICE_FILE"
  DF_USE_PERCENT=50
  run checkDiskSpace
  [ "$status" -eq 0 ]
  if grep -q "whiptail" "$COMMAND_LOG"; then
    echo "unexpected dialog: $(cat "$COMMAND_LOG")"
    return 1
  fi
  if grep -q "runScript resync_consensus.sh" "$COMMAND_LOG"; then
    echo "unexpected resync: $(cat "$COMMAND_LOG")"
    return 1
  fi
  grep -q "Free space check results" "$COMMAND_LOG"
}
