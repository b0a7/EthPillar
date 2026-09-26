#!/usr/bin/env bats
#
# tests/test_node_checker_quic.bats
#
# Unit tests for CL QUIC UDP helpers in plugins/node-checker/networking.sh
# (sourced by run.sh). Does not start Ethereum clients.
#
# Run: bats tests/test_node_checker_quic.bats
#

setup() {
	cd "$BATS_TEST_DIRNAME/.."

	export TEST_DIR
	TEST_DIR=$(mktemp -d)
	export EXEC_SERVICE_FILE="$TEST_DIR/execution.service"
	export CONSENSUS_SERVICE_FILE="$TEST_DIR/consensus.service"
	export CHARON_SERVICE_FILE="$TEST_DIR/charon.service"
	export ETHPILLAR_ENV_FILE="${PWD}/env"

	# shellcheck disable=SC1091
	source ./plugins/node-checker/run.sh

	total_checks=0
	failed_checks=0
	warning_checks=0
	tcp_check_ports="9000,30303"
	udp_check_ports="9000,30303"
	udp_check_ports_base="9000,30303"
	CL_P2P_PORT="${CL_P2P_PORT:-9000}"
	CL_P2P_PORT_2="${CL_P2P_PORT_2:-9001}"
	NODE_CHECKER_TROUBLESHOOT=0
	NODE_CHECKER_DEBUG=0
	NODE_CHECKER_AUTO_TROUBLESHOOT=0
	NODE_CHECKER_TROUBLESHOOT_PRINTED=0
	unset TEKU_QUIC_IPV6_PORT || true
}

teardown() {
	rm -rf "$TEST_DIR"
}

write_consensus() {
	local name="$1"
	local qport="${2:-${CL_P2P_PORT_2:-9001}}"
	cat > "$CONSENSUS_SERVICE_FILE" <<EOF
[Unit]
Description=${name} Beacon Node Consensus Client service for MAINNET
[Service]
ExecStart=/usr/local/bin/${name,,} --quic-port=${qport}
EOF
}

write_consensus_no_quic_flag() {
	local name="$1"
	cat > "$CONSENSUS_SERVICE_FILE" <<EOF
[Unit]
Description=${name} Beacon Node Consensus Client service for MAINNET
[Service]
ExecStart=/usr/local/bin/${name,,} --p2p-port=9000
EOF
}

write_execution() {
	local name="$1"
	local extra="${2:-}"
	cat > "$EXEC_SERVICE_FILE" <<EOF
[Unit]
Description=${name} Execution Client for MAINNET
[Service]
ExecStart=/usr/local/bin/el ${extra}
EOF
}

write_caplin_execution() {
	cat > "$EXEC_SERVICE_FILE" <<EOF
[Unit]
Description=Erigon-Caplin Integrated Execution-Consensus Client for MAINNET
[Service]
ExecStart=/usr/local/bin/erigon --caplin.discovery.port=9000
EOF
}

# ── expected ports / Caplin skip ──────────────────────────────────────────────

@test "expected_cl_quic_udp_ports is 9001 for Lighthouse Nimbus Lodestar Grandine Prysm" {
	for cl in Lighthouse Nimbus Lodestar Grandine Prysm; do
		write_consensus "$cl"
		run expected_cl_quic_udp_ports
		[ "$status" -eq 0 ]
		[ "$output" = "9001" ]
	done
}

@test "expected_cl_quic_udp_ports includes Teku IPv4 9001 and IPv6 9091" {
	write_consensus Teku
	run expected_cl_quic_udp_ports
	[ "$status" -eq 0 ]
	[ "$output" = "9001 9091" ]
}

@test "unknown CL with consensus.service still expects 9001/udp" {
	write_consensus UnknownClient
	run expected_cl_quic_udp_ports
	[ "$status" -eq 0 ]
	[ "$output" = "9001" ]
}

@test "no consensus.service and no Caplin skips QUIC ports" {
	run expected_cl_quic_udp_ports
	[ "$status" -eq 0 ]
	[ -z "$output" ]
	run cl_expects_quic
	[ "$status" -eq 1 ]
}

@test "Erigon-Caplin EL skips QUIC ports" {
	write_caplin_execution
	run is_caplin_node
	[ "$status" -eq 0 ]
	run expected_cl_quic_udp_ports
	[ -z "$output" ]
	run cl_expects_quic
	[ "$status" -eq 1 ]
}

@test "execution.service containing caplin flags is treated as Caplin" {
	write_execution Erigon "--caplin.discovery.port=9000"
	run is_caplin_node
	[ "$status" -eq 0 ]
	run expected_cl_quic_udp_ports
	[ -z "$output" ]
}

@test "Geth plus Lighthouse is not Caplin" {
	write_execution Geth
	write_consensus Lighthouse
	run is_caplin_node
	[ "$status" -eq 1 ]
	run expected_cl_quic_udp_ports
	[ "$output" = "9001" ]
}

@test "CL_P2P_PORT_2 override is used for QUIC UDP when unit has no flag" {
	write_consensus_no_quic_flag Lighthouse
	CL_P2P_PORT_2=19001
	run expected_cl_quic_udp_ports
	[ "$output" = "19001" ]
}

@test "consensus.service --quic-port wins over CL_P2P_PORT_2" {
	write_consensus Lighthouse 19002
	CL_P2P_PORT_2=9001
	run expected_cl_quic_udp_ports
	[ "$output" = "19002" ]
}

# ── udp_check_ports vs expected-4 listen accounting ───────────────────────────

@test "configure_cl_quic_udp_check_ports appends 9001 UDP only for Lighthouse" {
	write_consensus Lighthouse
	tcp_check_ports="9000,30303"
	configure_cl_quic_udp_check_ports
	[ "$udp_check_ports" = "9000,30303,9001" ]
	[ "$tcp_check_ports" = "9000,30303" ]
	[ "${#p2p_ports[@]}" -eq 2 ]
	[ "${p2p_ports[0]}" = "9000" ]
	[ "${p2p_ports[1]}" = "30303" ]
	[ "$ELCL_EXPECTED_LISTEN_COUNT" -eq 4 ]
}

@test "configure_cl_quic_udp_check_ports appends Teku 9001 and 9091 UDP" {
	write_consensus Teku
	configure_cl_quic_udp_check_ports
	[ "$udp_check_ports" = "9000,30303,9001,9091" ]
	[ "$tcp_check_ports" = "9000,30303" ]
	[ "$ELCL_EXPECTED_LISTEN_COUNT" -eq 4 ]
}

@test "configure_cl_quic_udp_check_ports does not add QUIC for Caplin" {
	write_caplin_execution
	configure_cl_quic_udp_check_ports
	[ "$udp_check_ports" = "9000,30303" ]
}

@test "configure_cl_quic_udp_check_ports is idempotent" {
	write_consensus Lighthouse
	configure_cl_quic_udp_check_ports
	configure_cl_quic_udp_check_ports
	[ "$udp_check_ports" = "9000,30303,9001" ]
}

# ── check_cl_quic behavior ────────────────────────────────────────────────────

# check_cl_quic mutates failed_checks/warning_checks; call it in this shell
# (bats `run` uses a subshell and would hide those counters).
check_cl_quic_capture() {
	check_cl_quic > "$TEST_DIR/quic.out" 2>&1
	cat "$TEST_DIR/quic.out"
}

@test "check_cl_quic WARNs for Caplin and does not FAIL missing 9001" {
	write_caplin_execution
	check_cl_quic_capture
	[[ "$(cat "$TEST_DIR/quic.out")" == *"Glamsterdam"* ]]
	[[ "$(cat "$TEST_DIR/quic.out")" == *"Caplin has no QUIC by default"* ]]
	[[ "$(cat "$TEST_DIR/quic.out")" != *"[FAIL]"* ]]
	[ "$failed_checks" -eq 0 ]
	[ "$warning_checks" -eq 1 ]
	[ "$NODE_CHECKER_AUTO_TROUBLESHOOT" -eq 0 ]
}

@test "check_cl_quic FAILs UFW allow miss and listen miss for Lighthouse" {
	write_consensus Lighthouse
	sudo() { "$@"; }
	ufw() {
		echo "Status: active"
		echo "9000                       ALLOW       Anywhere"
	}
	ss() { echo "tcp LISTEN 0 0 0.0.0.0:9000 0.0.0.0:*"; }
	export -f sudo ufw ss

	check_cl_quic_capture
	[[ "$(cat "$TEST_DIR/quic.out")" == *"Glamsterdam"* ]]
	[[ "$(cat "$TEST_DIR/quic.out")" == *"missing allow rule for CL QUIC 9001/udp"* ]]
	[[ "$(cat "$TEST_DIR/quic.out")" == *"CL QUIC port 9001/udp not listening"* ]]
	[ "$failed_checks" -eq 2 ]
	[ "$NODE_CHECKER_AUTO_TROUBLESHOOT" -eq 1 ]
}

@test "check_cl_quic PASSes UFW and listen when 9001/udp is open" {
	write_consensus Lighthouse
	sudo() { "$@"; }
	ufw() {
		echo "Status: active"
		echo "9001/udp                   ALLOW       Anywhere"
	}
	ss() {
		echo "udp UNCONN 0 0 0.0.0.0:9001 0.0.0.0:*"
	}
	export -f sudo ufw ss

	check_cl_quic_capture
	[[ "$(cat "$TEST_DIR/quic.out")" == *"UFW allows CL QUIC 9001/udp"* ]]
	[[ "$(cat "$TEST_DIR/quic.out")" == *"Detected UDP service on CL QUIC port 9001"* ]]
	[[ "$(cat "$TEST_DIR/quic.out")" != *"[FAIL]"* ]]
	[ "$failed_checks" -eq 0 ]
	[ "$NODE_CHECKER_AUTO_TROUBLESHOOT" -eq 0 ]
}

@test "check_cl_quic skips UFW rule check when firewall is inactive" {
	write_consensus Nimbus
	sudo() { "$@"; }
	ufw() { echo "Status: inactive"; }
	ss() { echo "udp UNCONN 0 0 0.0.0.0:9001 0.0.0.0:*"; }
	export -f sudo ufw ss

	check_cl_quic_capture
	[[ "$(cat "$TEST_DIR/quic.out")" != *"missing allow rule"* ]]
	[[ "$(cat "$TEST_DIR/quic.out")" == *"Detected UDP service on CL QUIC port 9001"* ]]
	[ "$failed_checks" -eq 0 ]
}

@test "check_cl_quic requires Teku 9001 and 9091 UFW rules" {
	write_consensus Teku
	sudo() { "$@"; }
	ufw() {
		echo "Status: active"
		echo "9001/udp                   ALLOW       Anywhere"
	}
	ss() {
		echo "udp UNCONN 0 0 0.0.0.0:9001 0.0.0.0:*"
		echo "udp UNCONN 0 0 [::]:9091 [::]:*"
	}
	export -f sudo ufw ss

	check_cl_quic_capture
	[[ "$(cat "$TEST_DIR/quic.out")" == *"UFW allows CL QUIC 9001/udp"* ]]
	[[ "$(cat "$TEST_DIR/quic.out")" == *"missing allow rule for CL QUIC 9091/udp"* ]]
	[ "$failed_checks" -eq 1 ]
}
