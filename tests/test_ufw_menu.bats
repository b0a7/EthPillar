#!/usr/bin/env bats
#
# tests/test_ufw_menu.bats
#
# UFW Firewall menu helpers in functions.sh (QUIC allow + Charon visibility).
# Does not start Ethereum clients.
#
# Run: bats tests/test_ufw_menu.bats
#

setup() {
	cd "$BATS_TEST_DIRNAME/.."
	# shellcheck disable=SC1091
	source ./functions.sh

	export TEST_DIR
	TEST_DIR=$(mktemp -d)
	export CONSENSUS_SERVICE_FILE="$TEST_DIR/consensus.service"
	export EXEC_SERVICE_FILE="$TEST_DIR/execution.service"
	export CHARON_SERVICE_FILE="$TEST_DIR/charon.service"
	export COMMAND_LOG="$TEST_DIR/sudo.log"
	CL_P2P_PORT="${CL_P2P_PORT:-9000}"
	CL_P2P_PORT_2="${CL_P2P_PORT_2:-9001}"
	unset TEKU_QUIC_IPV6_PORT || true

	sudo() {
		echo "sudo $*" >> "$COMMAND_LOG"
	}
	export -f sudo
	: > "$COMMAND_LOG"
}

teardown() {
	rm -rf "$TEST_DIR"
}

write_consensus() {
	local name="$1"
	local qport="${2:-9001}"
	cat > "$CONSENSUS_SERVICE_FILE" <<EOF
[Unit]
Description=${name} Beacon Node Consensus Client service for MAINNET
[Service]
ExecStart=/usr/local/bin/${name,,} --quic-port=${qport}
EOF
}

write_charon() {
	cat > "$CHARON_SERVICE_FILE" <<EOF
[Service]
ExecStart=/usr/local/bin/charon run --p2p-tcp-address=0.0.0.0:3610
EOF
}

menu_labels() {
	ufwBuildFirewallMenu
	local i
	for (( i = 0; i < ${#UFW_MENU_PAIRS[@]}; i += 2 )); do
		echo "${UFW_MENU_PAIRS[i]}|${UFW_MENU_PAIRS[i+1]}"
	done
}

@test "getExpectedClQuicUdpPorts is 9001 for Lighthouse from unit flag" {
	write_consensus Lighthouse 9001
	run getExpectedClQuicUdpPorts
	[ "$status" -eq 0 ]
	[ "$output" = "9001" ]
}

@test "getExpectedClQuicUdpPorts reads --quic-port from consensus.service" {
	write_consensus Nimbus 19001
	CL_P2P_PORT_2=9001
	run getExpectedClQuicUdpPorts
	[ "$output" = "19001" ]
}

@test "getExpectedClQuicUdpPorts includes Teku IPv4 and IPv6" {
	write_consensus Teku 9001
	run getExpectedClQuicUdpPorts
	[ "$output" = "9001 9091" ]
}

@test "getExpectedClQuicUdpPorts is empty for Caplin" {
	cat > "$EXEC_SERVICE_FILE" <<EOF
[Unit]
Description=Erigon-Caplin Integrated Execution-Consensus Client for MAINNET
EOF
	run getExpectedClQuicUdpPorts
	[ -z "$output" ]
}

@test "ufwAllowClQuic allows resolved QUIC UDP ports" {
	write_consensus Lighthouse 19001
	run ufwAllowClQuic
	[ "$status" -eq 0 ]
	grep -q "ufw allow 19001/udp" "$COMMAND_LOG"
}

@test "ufwAllowClQuic allows Teku 9001 and 9091" {
	write_consensus Teku 9001
	run ufwAllowClQuic
	grep -q "ufw allow 9001/udp" "$COMMAND_LOG"
	grep -q "ufw allow 9091/udp" "$COMMAND_LOG"
}

@test "UFW menu includes CL QUIC and hides Charon when charon.service is missing" {
	rm -f "$CHARON_SERVICE_FILE"
	labels="$(menu_labels)"
	[[ "$labels" == *"CL QUIC: Allow UDP (from consensus / expected QUIC port)"* ]]
	[[ "$labels" != *"Allow P2P port (from charon.service)"* ]]
	ufwBuildFirewallMenu
	[[ "$(ufwFirewallMenuAction 9)" == "cl_quic" ]]
	[[ "$(ufwFirewallMenuAction 10)" == "disable" ]]
}

@test "UFW menu shows Charon P2P only when charon.service exists" {
	write_charon
	labels="$(menu_labels)"
	[[ "$labels" == *"CL QUIC: Allow UDP (from consensus / expected QUIC port)"* ]]
	[[ "$labels" == *"Allow P2P port (from charon.service)"* ]]
	ufwBuildFirewallMenu
	[[ "$(ufwFirewallMenuAction 9)" == "cl_quic" ]]
	[[ "$(ufwFirewallMenuAction 10)" == "charon" ]]
	[[ "$(ufwFirewallMenuAction 11)" == "disable" ]]
}
