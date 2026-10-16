#!/usr/bin/env bats
#
# tests/test_node_checker_vc_version.bats
#
# Node-checker versions use the same binary helpers as the update menus.
# Mixed CL/VC and integrated Grandine. No network and no running client.
#
# Run: bats tests/test_node_checker_vc_version.bats
#

setup() {
	cd "$BATS_TEST_DIRNAME/.."

	export TEST_DIR
	TEST_DIR=$(mktemp -d)
	export BIN_DIR="$TEST_DIR/bin"
	mkdir -p "$BIN_DIR"
	export EXEC_SERVICE_FILE="$TEST_DIR/execution.service"
	export CONSENSUS_SERVICE_FILE="$TEST_DIR/consensus.service"
	export VALIDATOR_SERVICE_FILE="$TEST_DIR/validator.service"
	export CHARON_SERVICE_FILE="$TEST_DIR/charon.service"
	export BIN_LOG="$TEST_DIR/bin.log"
	export CURL_LOG="$TEST_DIR/curl.log"
	export ETHPILLAR_ENV_FILE="${PWD}/env"
	: > "$BIN_LOG"
	: > "$CURL_LOG"

	# shellcheck disable=SC1091
	source ./plugins/node-checker/run.sh

	# ((total_checks++)) is false when the counter is 0, which aborts under
	# bats' set -e. Start at 1 so the production increment still runs the check.
	total_checks=1
	failed_checks=0
	warning_checks=0

	# Latest tag comes from deploy.common in production. Tests supply MOCK_LATEST.
	fetch_latest_release() {
		TAG="${MOCK_LATEST:-}"
		TAG_COMMIT=""
		[[ -n "$TAG" ]]
	}

	curl() {
		echo "$*" >> "$CURL_LOG"
		return 1
	}
}

teardown() {
	rm -rf "$TEST_DIR"
}

write_stub() {
	local path="$1"
	local output="$2"
	cat > "$path" <<EOF
#!/bin/bash
echo "$path" >> "$BIN_LOG"
echo "$output"
EOF
	chmod +x "$path"
}

write_consensus() {
	local name="$1"
	local bin="$2"
	local extra="${3:-}"
	cat > "$CONSENSUS_SERVICE_FILE" <<EOF
[Unit]
Description=${name} Beacon Node Consensus Client service for MAINNET
[Service]
ExecStart=${bin} ${extra}
EOF
}

write_execution() {
	local name="$1"
	local bin="$2"
	cat > "$EXEC_SERVICE_FILE" <<EOF
[Unit]
Description=${name} Execution Layer Client service for MAINNET
[Service]
ExecStart=${bin}
EOF
}

write_validator() {
	local name="$1"
	local bin="$2"
	local args="$3"
	cat > "$VALIDATOR_SERVICE_FILE" <<EOF
[Unit]
Description=${name} Validator Client service for MAINNET
[Service]
ExecStart=${bin} ${args}
EOF
}

@test "check_validator_version reads lighthouse vc when consensus is grandine" {
	local grandine="$BIN_DIR/grandine"
	local lighthouse="$BIN_DIR/lighthouse"
	write_stub "$grandine" "grandine 1.2.3"
	write_stub "$lighthouse" "Lighthouse v8.1.3-def5678"
	write_consensus Grandine "$grandine"
	write_validator Lighthouse "$lighthouse" "vc --network=sepolia"
	export MOCK_LATEST=v8.1.3

	check_validator_version >"$TEST_DIR/out" 2>&1

	grep -q "\[PASS\]" "$TEST_DIR/out"
	grep -q "Validator client (Lighthouse) version: v8.1.3" "$TEST_DIR/out"
	if grep -q "v1.2.3" "$TEST_DIR/out"; then
		echo "validator row reported the Grandine beacon version" >&2
		cat "$TEST_DIR/out" >&2
		return 1
	fi
	grep -q "$lighthouse" "$BIN_LOG"
	if grep -q "$grandine" "$BIN_LOG"; then
		echo "grandine binary was executed for the validator row" >&2
		cat "$BIN_LOG" >&2
		return 1
	fi
	if [[ -s "$CURL_LOG" ]]; then
		echo "validator row queried the network" >&2
		cat "$CURL_LOG" >&2
		return 1
	fi
	[ "$failed_checks" -eq 0 ]
	[ "$warning_checks" -eq 0 ]
}

@test "check_validator_version reads prysm vc when consensus is nimbus" {
	local nimbus="$BIN_DIR/nimbus_beacon_node"
	local prysm="$BIN_DIR/prysm-validator"
	write_stub "$nimbus" "Nimbus beacon node v24.8.0-00aedddf"
	write_stub "$prysm" "Prysm/v7.1.2/8f0c1aa"
	write_consensus Nimbus "$nimbus"
	write_validator Prysm "$prysm" "--beacon-rpc-provider=127.0.0.1:4000"
	export MOCK_LATEST=v7.1.2

	check_validator_version >"$TEST_DIR/out" 2>&1

	grep -q "\[PASS\]" "$TEST_DIR/out"
	grep -q "Validator client (Prysm) version: v7.1.2" "$TEST_DIR/out"
	if grep -q "v24.8.0" "$TEST_DIR/out"; then
		echo "validator row reported the Nimbus beacon version" >&2
		cat "$TEST_DIR/out" >&2
		return 1
	fi
	grep -q "$prysm" "$BIN_LOG"
	if grep -q "$nimbus" "$BIN_LOG"; then
		echo "nimbus binary was executed for the validator row" >&2
		return 1
	fi
	if [[ -s "$CURL_LOG" ]]; then
		echo "validator row queried the network" >&2
		return 1
	fi
}

@test "check_validator_version reads integrated grandine from the consensus binary" {
	local grandine="$BIN_DIR/grandine"
	write_stub "$grandine" "grandine 2.1.0"
	write_consensus Grandine "$grandine" "--keystore-dir=/var/lib/grandine/validator_keys"
	rm -f "$VALIDATOR_SERVICE_FILE"
	export VALIDATOR_SERVICE_FILE="$TEST_DIR/missing-validator.service"
	export MOCK_LATEST=v2.1.0

	check_validator_version >"$TEST_DIR/out" 2>&1

	grep -q "\[PASS\]" "$TEST_DIR/out"
	grep -q "Validator client (Grandine) version: v2.1.0" "$TEST_DIR/out"
	if grep -q "v9.9.9" "$TEST_DIR/out"; then
		echo "integrated Grandine row used the REST payload" >&2
		cat "$TEST_DIR/out" >&2
		return 1
	fi
	grep -q "$grandine" "$BIN_LOG"
	if [[ -s "$CURL_LOG" ]]; then
		echo "integrated Grandine validator row queried the network" >&2
		return 1
	fi
}

@test "check_consensus_version reads the consensus binary not the validator binary" {
	local grandine="$BIN_DIR/grandine"
	local lighthouse="$BIN_DIR/lighthouse"
	write_stub "$grandine" "grandine 1.2.3"
	write_stub "$lighthouse" "Lighthouse v8.1.3-def5678"
	write_consensus Grandine "$grandine"
	write_validator Lighthouse "$lighthouse" "vc"
	export MOCK_LATEST=v1.2.3

	check_consensus_version >"$TEST_DIR/out" 2>&1

	grep -q "\[PASS\]" "$TEST_DIR/out"
	grep -q "Consensus client (Grandine) version: v1.2.3" "$TEST_DIR/out"
	grep -q "$grandine" "$BIN_LOG"
	if grep -q "$lighthouse" "$BIN_LOG"; then
		echo "consensus row executed the validator binary" >&2
		cat "$BIN_LOG" >&2
		return 1
	fi
	if [[ -s "$CURL_LOG" ]]; then
		echo "consensus row queried the network" >&2
		cat "$CURL_LOG" >&2
		return 1
	fi
}

@test "check_consensus_version accepts grandine binary output without a v prefix" {
	local grandine="$BIN_DIR/grandine"
	write_stub "$grandine" "grandine 2.0.4"
	write_consensus Grandine "$grandine"
	export MOCK_LATEST=v2.0.4

	check_consensus_version >"$TEST_DIR/out" 2>&1

	grep -q "\[PASS\]" "$TEST_DIR/out"
	grep -q "Consensus client (Grandine) version: v2.0.4 (latest)" "$TEST_DIR/out"
}

@test "check_execution_version keeps the Ethrex version and drops the rustc version" {
	local ethrex="$BIN_DIR/ethrex"
	write_stub "$ethrex" "ethrex/v29.0.1/x86_64-unknown-linux-gnu/rustc-v1.93.0"
	write_execution Ethrex "$ethrex"
	export MOCK_LATEST=v29.0.1

	check_execution_version >"$TEST_DIR/out" 2>&1

	grep -q "\[PASS\]" "$TEST_DIR/out"
	grep -q "Execution client (Ethrex) version: 29.0.1 (latest)" "$TEST_DIR/out"
	if grep -q "v1.93.0" "$TEST_DIR/out"; then
		echo "Ethrex row kept the rustc version" >&2
		cat "$TEST_DIR/out" >&2
		return 1
	fi
}
