#!/usr/bin/env bats

setup() {
  # shellcheck disable=SC1091
  source "$BATS_TEST_DIRNAME/../functions.sh"
  TEST_BIN_DIR=$(mktemp -d)
  export EXEC_SERVICE_FILE=$(mktemp)
  export CONSENSUS_SERVICE_FILE=$(mktemp)
  export VALIDATOR_SERVICE_FILE=$(mktemp)
}

teardown() {
  rm -rf "$TEST_BIN_DIR"
  rm -f "$EXEC_SERVICE_FILE" "$CONSENSUS_SERVICE_FILE" "$VALIDATOR_SERVICE_FILE"
}

write_stub_binary() {
  local path="$1"
  shift
  cat > "$path" <<EOF
#!/bin/bash
$*
EOF
  chmod +x "$path"
}

# ── parse_execution_client_version ───────────────────────────────────────────

@test "parse_execution_client_version ignores trailing toolchain versions" {
  run parse_execution_client_version Ethrex $'ethrex/v16.0.0/rust/1.91.0'
  [ "$status" -eq 0 ]
  [ "$output" = "16.0.0" ]
}

@test "parse_execution_client_version ignores leading JDK version for Besu" {
  run parse_execution_client_version Besu $'openjdk version "25.0.1"\nbesu 25.3.0'
  [ "$status" -eq 0 ]
  [ "$output" = "25.3.0" ]
}

@test "parse_execution_client_version parses geth version output" {
  run parse_execution_client_version Geth $'Geth\nVersion: 1.14.12-stable-abc123'
  [ "$status" -eq 0 ]
  [ "$output" = "1.14.12" ]
}

@test "parse_execution_client_version parses geth version on one line" {
  run parse_execution_client_version Geth 'Geth Version: 1.14.12-stable-abc123'
  [ "$status" -eq 0 ]
  [ "$output" = "1.14.12" ]
}

@test "parse_execution_client_version parses reth version output" {
  run parse_execution_client_version Reth 'reth-ethereum-client 1.9.0 (abcdef)'
  [ "$status" -eq 0 ]
  [ "$output" = "1.9.0" ]
}

@test "parse_execution_client_version parses nethermind version output" {
  run parse_execution_client_version Nethermind $'Version:     1.38.0+c07a4d65\nCommit:      c07a4d65'
  [ "$status" -eq 0 ]
  [ "$output" = "1.38.0" ]
}

@test "parse_execution_client_version parses nethermind version on one line" {
  run parse_execution_client_version Nethermind 'Nethermind v1.32.0+abc'
  [ "$status" -eq 0 ]
  [ "$output" = "1.32.0" ]
}

@test "parse_execution_client_version parses erigon version output" {
  run parse_execution_client_version Erigon 'erigon version 3.0.12-alpha1'
  [ "$status" -eq 0 ]
  [ "$output" = "3.0.12" ]
}

@test "parse_execution_client_version parses ethrex binary version output" {
  run parse_execution_client_version Ethrex 'ethrex 16.0.0'
  [ "$status" -eq 0 ]
  [ "$output" = "16.0.0" ]
}

@test "parse_execution_client_version returns empty for unknown client" {
  run parse_execution_client_version Unknown 'client 1.2.3'
  [ "$status" -eq 1 ]
  [ "$output" = "" ]
}

# ── get_execution_version_output ─────────────────────────────────────────────

@test "get_execution_version_output uses geth version subcommand" {
  local stub="$TEST_BIN_DIR/geth"
  write_stub_binary "$stub" '[[ "$1" == "version" ]] && printf "%s\n%s\n" "Geth" "Version: 1.14.0-stable"'
  run get_execution_version_output "$stub" Geth
  [ "$status" -eq 0 ]
  [[ "$output" == *"Version: 1.14.0-stable"* ]]
}

@test "getExecutionCurrentVersion reads geth from execution service stub" {
  local stub="$TEST_BIN_DIR/geth"
  write_stub_binary "$stub" '[[ "$1" == "version" ]] && printf "%s\n%s\n" "Geth" "Version: 1.17.3-stable"'
  cat <<EOF > "$EXEC_SERVICE_FILE"
ExecStart=$stub
EOF
  EL=Geth
  getExecutionCurrentVersion
  [ "$VERSION" = "1.17.3" ]
}

@test "get_execution_version_output uses --version for other clients" {
  local stub="$TEST_BIN_DIR/ethrex"
  write_stub_binary "$stub" '[[ "$1" == "--version" ]] && echo "ethrex 16.0.0"'
  run get_execution_version_output "$stub" Ethrex
  [ "$status" -eq 0 ]
  [ "$output" = "ethrex 16.0.0" ]
}

# ── getExecutionCurrentVersion ───────────────────────────────────────────────

@test "getExecutionCurrentVersion reads ethrex from execution service stub" {
  local stub="$TEST_BIN_DIR/ethrex"
  write_stub_binary "$stub" '[[ "$1" == "--version" ]] && echo "ethrex 16.0.0 (rustc 1.91.0)"'
  cat <<EOF > "$EXEC_SERVICE_FILE"
ExecStart=$stub
EOF
  EL=Ethrex
  getExecutionCurrentVersion
  [ "$VERSION" = "16.0.0" ]
}

@test "getExecutionCurrentVersion reads besu from execution service stub" {
  local stub="$TEST_BIN_DIR/besu"
  write_stub_binary "$stub" '[[ "$1" == "--version" ]] && printf "%s\n%s\n" "openjdk version \"25.0.1\"" "besu 25.3.0"'
  cat <<EOF > "$EXEC_SERVICE_FILE"
ExecStart=$stub
EOF
  EL=Besu
  getExecutionCurrentVersion
  [ "$VERSION" = "25.3.0" ]
}

# ── getClVcCurrentVersion ────────────────────────────────────────────────────

@test "getClVcCurrentVersion reads lighthouse from consensus service stub" {
  local stub="$TEST_BIN_DIR/lighthouse"
  write_stub_binary "$stub" 'echo "Lighthouse v5.2.1-abc"'
  cat <<EOF > "$CONSENSUS_SERVICE_FILE"
ExecStart=$stub
EOF
  getClVcCurrentVersion Lighthouse cl
  [ "$VERSION" = "v5.2.1" ]
}

@test "getClVcCurrentVersion reads lighthouse vc from validator when consensus is grandine" {
  local grandine="$TEST_BIN_DIR/grandine"
  local lighthouse="$TEST_BIN_DIR/lighthouse"
  write_stub_binary "$grandine" 'echo "grandine 2.0.4"'
  write_stub_binary "$lighthouse" 'echo "Lighthouse v8.1.3-abc"'
  cat <<EOF > "$CONSENSUS_SERVICE_FILE"
ExecStart=$grandine
EOF
  cat <<EOF > "$VALIDATOR_SERVICE_FILE"
ExecStart=$lighthouse vc --network=sepolia
EOF
  getClVcCurrentVersion Lighthouse vc
  [ "$VERSION" = "v8.1.3" ]
}

@test "getClVcCurrentVersion cl role ignores validator service for lighthouse" {
  local cl_stub="$TEST_BIN_DIR/lighthouse-bn"
  local vc_stub="$TEST_BIN_DIR/lighthouse-vc"
  write_stub_binary "$cl_stub" 'echo "Lighthouse v5.0.0-bn"'
  write_stub_binary "$vc_stub" 'echo "Lighthouse v9.9.9-vc"'
  cat <<EOF > "$CONSENSUS_SERVICE_FILE"
ExecStart=$cl_stub
EOF
  cat <<EOF > "$VALIDATOR_SERVICE_FILE"
ExecStart=$vc_stub vc --network=mainnet
EOF
  getClVcCurrentVersion Lighthouse cl
  [ "$VERSION" = "v5.0.0" ]
}

@test "getClVcCurrentVersion reads vc-only nimbus from validator service stub" {
  local stub="$TEST_BIN_DIR/nimbus_validator_client"
  write_stub_binary "$stub" 'echo "Nimbus v24.11.0"'
  cat <<EOF > "$VALIDATOR_SERVICE_FILE"
ExecStart=$stub
EOF
  getClVcCurrentVersion Nimbus vc
  [ "$VERSION" = "v24.11.0" ]
}

@test "getClVcCurrentVersion accepts explicit client override" {
  local stub="$TEST_BIN_DIR/prysm-validator"
  write_stub_binary "$stub" 'echo "Prysm v5.0.0"'
  cat <<EOF > "$VALIDATOR_SERVICE_FILE"
ExecStart=$stub
EOF
  CLIENT=Lighthouse
  getClVcCurrentVersion Prysm vc
  [ "$VERSION" = "v5.0.0" ]
}

@test "getClVcCurrentVersion normalizes grandine version prefix" {
  local stub="$TEST_BIN_DIR/grandine"
  write_stub_binary "$stub" 'echo "grandine 2.0.4"'
  cat <<EOF > "$CONSENSUS_SERVICE_FILE"
ExecStart=$stub
EOF
  getClVcCurrentVersion Grandine cl
  [ "$VERSION" = "v2.0.4" ]
}
