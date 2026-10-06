#!/usr/bin/env bats
#
# tests/test_expose_rpc_cl.bats
#
# exposeRpcCL / revoke: config mutation and the consensus restart, with stubs.
# No Ethereum client, no systemd, no multi-second sleep.
#
# Run: bats tests/test_expose_rpc_cl.bats
#

setup() {
	cd "$BATS_TEST_DIRNAME/.."
	# shellcheck disable=SC1091
	source ./functions.sh

	export TEST_DIR
	TEST_DIR=$(mktemp -d)
	export CONSENSUS_SERVICE_FILE="$TEST_DIR/consensus.service"
	export COMMAND_LOG="$TEST_DIR/sudo.log"
	: > "$COMMAND_LOG"

	# exposeRpcCL prompts with read -rsn1 and then restarts via sudo.
	getNetworkConfig() {
		ip_current="203.0.113.10"
		interface_current="eth0"
		network_current="203.0.113.0/24"
		export ip_current interface_current network_current
	}
	clear() { :; }
	sleep() { echo "sleep $*" >> "$COMMAND_LOG"; }
	sudo() {
		case "$1" in
			systemctl|service)
				echo "sudo $*" >> "$COMMAND_LOG"
				return 0
				;;
			*)
				command "$@"
				;;
		esac
	}
}

teardown() {
	rm -rf "$TEST_DIR"
}

write_unit() {
	local client="$1"
	local flag="$2"
	local value="$3"
	local port_flag="${4:---http-port}"
	cat > "$CONSENSUS_SERVICE_FILE" <<EOF
[Unit]
Description=${client} Beacon Node Consensus Client service for MAINNET
[Service]
ExecStart=/usr/local/bin/${client,,} --network=mainnet ${flag}=${value} ${port_flag}=5052
EOF
}

write_multiline_teku() {
	local value="$1"
	cat > "$CONSENSUS_SERVICE_FILE" <<EOF
[Unit]
Description=Teku Beacon Node Consensus Client service for MAINNET
[Service]
ExecStart=/usr/local/bin/teku \\
  --network=mainnet \\
  --rest-api-interface=${value} \\
  --rest-api-port=5052
EOF
}

answer_expose() {
	local keys="$1"
	: > "$COMMAND_LOG"
	printf '%s' "$keys" | exposeRpcCL >"$TEST_DIR/out" 2>&1
}

@test "exposeRpcCL rewrites each supported CL bind flag to 0.0.0.0 and restarts consensus" {
	local client flag port_flag
	while IFS='|' read -r client flag port_flag; do
		write_unit "$client" "$flag" "127.0.0.1" "$port_flag"
		CL="$client"
		answer_expose yy
		if ! grep -q -- "${flag}=0.0.0.0" "$CONSENSUS_SERVICE_FILE"; then
			echo "missing ${flag}=0.0.0.0 for ${client}" >&2
			cat "$CONSENSUS_SERVICE_FILE" >&2
			return 1
		fi
		if grep -q -- "${flag}=127.0.0.1" "$CONSENSUS_SERVICE_FILE"; then
			echo "localhost bind still present for ${client}" >&2
			cat "$CONSENSUS_SERVICE_FILE" >&2
			return 1
		fi
		grep -q "Exposing ${client} RPC Access with flag: ${flag}" "$TEST_DIR/out"
		grep -q "sudo systemctl daemon-reload" "$COMMAND_LOG"
		grep -q "sudo service consensus restart" "$COMMAND_LOG"
		grep -q "sleep 5" "$COMMAND_LOG"
		# Port flag is not the bind flag and must survive the rewrite.
		grep -q -- "${port_flag}=5052" "$CONSENSUS_SERVICE_FILE"
	done <<'EOF'
Lighthouse|--http-address|--http-port
Nimbus|--rest-address|--rest-port
Teku|--rest-api-interface|--rest-api-port
Lodestar|--rest.address|--rest.port
Prysm|--http-host|--http-port
Grandine|--http-address|--http-port
EOF
}

@test "exposeRpcCL revoke sets the CL bind back to 127.0.0.1 and restarts consensus" {
	write_unit Lighthouse --http-address 0.0.0.0
	CL=Lighthouse
	answer_expose yn
	grep -q -- "--http-address=127.0.0.1" "$CONSENSUS_SERVICE_FILE"
	if grep -q -- "--http-address=0.0.0.0" "$CONSENSUS_SERVICE_FILE"; then
		echo "exposed bind still present" >&2
		cat "$CONSENSUS_SERVICE_FILE" >&2
		return 1
	fi
	grep -q "Closing Lighthouse RPC Access with flag: --http-address" "$TEST_DIR/out"
	grep -q "sudo systemctl daemon-reload" "$COMMAND_LOG"
	grep -q "sudo service consensus restart" "$COMMAND_LOG"
}

@test "exposeRpcCL revoke rewrites a multiline Teku unit to 127.0.0.1" {
	write_multiline_teku "0.0.0.0"
	CL=Teku
	answer_expose yn
	grep -q -- "--rest-api-interface=127.0.0.1" "$CONSENSUS_SERVICE_FILE"
	if grep -q -- "--rest-api-interface=0.0.0.0" "$CONSENSUS_SERVICE_FILE"; then
		echo "exposed Teku bind still present" >&2
		cat "$CONSENSUS_SERVICE_FILE" >&2
		return 1
	fi
	grep -q -- "--rest-api-port=5052" "$CONSENSUS_SERVICE_FILE"
	grep -q "sudo service consensus restart" "$COMMAND_LOG"
	grep -q "Closing Teku RPC Access" "$TEST_DIR/out"
}

@test "exposeRpcCL declines without changing the unit or restarting" {
	write_unit Nimbus --rest-address 127.0.0.1 --rest-port
	cp "$CONSENSUS_SERVICE_FILE" "$TEST_DIR/before"
	CL=Nimbus
	answer_expose n
	cmp -s "$TEST_DIR/before" "$CONSENSUS_SERVICE_FILE"
	if grep -q "systemctl" "$COMMAND_LOG"; then
		echo "restart ran after decline" >&2
		cat "$COMMAND_LOG" >&2
		return 1
	fi
	if grep -q "sleep" "$COMMAND_LOG"; then
		echo "sleep ran after decline" >&2
		return 1
	fi
}

@test "exposeRpcCL does not restart when the bind is already exposed" {
	write_unit Prysm --http-host 0.0.0.0
	cp "$CONSENSUS_SERVICE_FILE" "$TEST_DIR/before"
	CL=Prysm
	answer_expose yy
	cmp -s "$TEST_DIR/before" "$CONSENSUS_SERVICE_FILE"
	grep -q "Already configured with --http-host=0.0.0.0" "$TEST_DIR/out"
	if grep -q "systemctl\\|service consensus" "$COMMAND_LOG"; then
		echo "restart ran for an already-exposed unit" >&2
		cat "$COMMAND_LOG" >&2
		return 1
	fi
}

@test "exposeRpcCL does not restart when revoke target is already localhost" {
	write_unit Grandine --http-address 127.0.0.1
	cp "$CONSENSUS_SERVICE_FILE" "$TEST_DIR/before"
	CL=Grandine
	answer_expose yn
	cmp -s "$TEST_DIR/before" "$CONSENSUS_SERVICE_FILE"
	grep -q "Already configured with --http-address=127.0.0.1" "$TEST_DIR/out"
	grep -q "Closing Grandine RPC Access" "$TEST_DIR/out"
	if grep -q "service consensus" "$COMMAND_LOG"; then
		echo "restart ran for an already-local unit" >&2
		cat "$COMMAND_LOG" >&2
		return 1
	fi
}

@test "exposeRpcCL reports an unknown client and leaves the unit unchanged" {
	write_unit Lighthouse --http-address 127.0.0.1
	cp "$CONSENSUS_SERVICE_FILE" "$TEST_DIR/before"
	CL=Caplin
	answer_expose yy
	cmp -s "$TEST_DIR/before" "$CONSENSUS_SERVICE_FILE"
	grep -q "Consensus client not detected." "$TEST_DIR/out"
	if grep -q "systemctl" "$COMMAND_LOG"; then
		echo "restart ran for an unsupported client" >&2
		return 1
	fi
}

@test "exposeRpcCL does not restart when the consensus unit is missing" {
	rm -f "$CONSENSUS_SERVICE_FILE"
	CL=Lodestar
	answer_expose yy
	[ ! -f "$CONSENSUS_SERVICE_FILE" ]
	grep -q "Exposing Lodestar RPC Access with flag: --rest.address" "$TEST_DIR/out"
	if grep -q "systemctl\\|service consensus" "$COMMAND_LOG"; then
		echo "restart ran without a unit file" >&2
		cat "$COMMAND_LOG" >&2
		return 1
	fi
}
