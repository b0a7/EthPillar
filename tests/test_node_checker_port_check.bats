#!/usr/bin/env bats
#
# tests/test_node_checker_port_check.bats
#
# Unit tests for inbound / troubleshoot port-check helpers in
# plugins/node-checker/networking.sh (sourced by run.sh).
# Does not start Ethereum clients.
#
# Run: bats tests/test_node_checker_port_check.bats
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
	NODE_CHECKER_TROUBLESHOOT_INBOUND="?"
	NODE_CHECKER_TROUBLESHOOT_OUTBOUND="?"
	NODE_CHECKER_PUBLIC_IPV4=""
	NODE_CHECKER_QUIC_AUTO_INSTALL=0
	unset TEKU_QUIC_IPV6_PORT || true
	unset NODE_CHECKER_EL_PEER_COUNT || true
	MOCK_EL_PID=""
}

teardown() {
	stop_mock_el_peer_count
	rm -rf "$TEST_DIR"
}

write_consensus() {
	local name="$1"
	cat > "$CONSENSUS_SERVICE_FILE" <<EOF
[Unit]
Description=${name} Beacon Node Consensus Client service for MAINNET
[Service]
ExecStart=/usr/local/bin/${name,,} --quic-port=9001
EOF
}

write_execution() {
	local name="$1"
	cat > "$EXEC_SERVICE_FILE" <<EOF
[Unit]
Description=${name} Execution Client for MAINNET
[Service]
ExecStart=/usr/local/bin/el
EOF
}

sample_peers_json() {
	cat <<'EOF'
{
  "data": [
    {"peer_id":"in-quic","direction":"inbound","state":"connected","last_seen_p2p_address":"/ip4/203.0.113.10/udp/9001/quic-v1"},
    {"peer_id":"in-tcp","direction":"inbound","state":"connected","last_seen_p2p_address":"/ip4/203.0.113.11/tcp/9000"},
    {"peer_id":"in-dead","direction":"inbound","state":"disconnected","last_seen_p2p_address":"/ip4/198.51.100.9/tcp/9000"},
    {"peer_id":"out-tcp","direction":"outbound","state":"connected","last_seen_p2p_address":"/ip4/198.51.100.20/tcp/9000"},
    {"peer_id":"out-quic6","direction":"outbound","state":"connected","last_seen_p2p_address":"/ip6/2001:db8::1/udp/9001/quic-v1"}
  ]
}
EOF
}

sample_identity_json() {
	cat <<'EOF'
{
  "data": {
    "peer_id": "16Uiu2testPeerIdOnly",
    "enr": "enr:-SECRET_DO_NOT_PRINT_IN_DEFAULT",
    "p2p_addresses": ["/ip4/0.0.0.0/tcp/9000"],
    "discovery_addresses": ["/ip4/203.0.113.5/udp/9000","/ip4/203.0.113.5/udp/9001/quic-v1"]
  }
}
EOF
}

@test "run.sh sources networking.sh for port-check helpers" {
	[ -f plugins/node-checker/networking.sh ]
	[ "$(type -t check_open_ports)" = "function" ]
	[ "$(type -t expected_cl_quic_udp_ports)" = "function" ]
	[ "$(type -t check_inbound_quic_probe)" = "function" ]
	[ "$(type -t print_port_troubleshoot_guidance)" = "function" ]
	grep -q 'source "${SOURCE_DIR}/networking.sh"' plugins/node-checker/run.sh
}

# ── classifiers / multiaddr ───────────────────────────────────────────────────

@test "is_private_ipv4 matches RFC1918 and loopback" {
	run is_private_ipv4 10.0.0.1
	[ "$status" -eq 0 ]
	run is_private_ipv4 192.168.1.1
	[ "$status" -eq 0 ]
	run is_private_ipv4 172.16.0.1
	[ "$status" -eq 0 ]
	run is_private_ipv4 127.0.0.1
	[ "$status" -eq 0 ]
	run is_private_ipv4 203.0.113.10
	[ "$status" -eq 1 ]
}

@test "is_cgnat_ipv4 matches 100.64/10 only" {
	run is_cgnat_ipv4 100.64.0.1
	[ "$status" -eq 0 ]
	run is_cgnat_ipv4 100.127.1.1
	[ "$status" -eq 0 ]
	run is_cgnat_ipv4 100.63.255.1
	[ "$status" -eq 1 ]
	run is_cgnat_ipv4 8.8.8.8
	[ "$status" -eq 1 ]
}

@test "classify_ipv4 distinguishes public private cgnat empty" {
	[ "$(classify_ipv4 8.8.8.8)" = "public" ]
	[ "$(classify_ipv4 10.1.2.3)" = "private" ]
	[ "$(classify_ipv4 100.64.1.2)" = "cgnat" ]
	[ "$(classify_ipv4 "")" = "empty" ]
}

@test "multiaddr_first_ip4 reads the first /ip4/" {
	addrs=$'/ip4/203.0.113.5/udp/9000\n/ip4/198.51.100.1/udp/9001/quic-v1'
	[ "$(multiaddr_first_ip4 "$addrs")" = "203.0.113.5" ]
}

@test "inbound_status_kind maps counts and unknown" {
	[ "$(inbound_status_kind 3)" = "working" ]
	[ "$(inbound_status_kind 0)" = "not-working" ]
	[ "$(inbound_status_kind '?')" = "unknown" ]
}

@test "node_checker_parse_jsonrpc_quantity converts hex and decimal" {
	[ "$(node_checker_parse_jsonrpc_quantity 0x5)" -eq 5 ]
	[ "$(node_checker_parse_jsonrpc_quantity 0x23)" -eq 35 ]
	[ "$(node_checker_parse_jsonrpc_quantity 0x0)" -eq 0 ]
	[ "$(node_checker_parse_jsonrpc_quantity 7)" -eq 7 ]
	[ "$(node_checker_parse_jsonrpc_quantity '')" -eq 0 ]
}

# ── peer JSON ─────────────────────────────────────────────────────────────────

@test "cl_connected_peers_json keeps connected inbound and drops disconnected" {
	json="$(sample_peers_json)"
	in_json="$(cl_connected_peers_json "$json" inbound)"
	[ "$(count_nonempty_lines "$in_json")" -eq 2 ]
	[[ "$in_json" == *"in-quic"* ]]
	[[ "$in_json" == *"in-tcp"* ]]
	[[ "$in_json" != *"in-dead"* ]]
}

@test "cl_connected_peers_json outbound count" {
	json="$(sample_peers_json)"
	out_json="$(cl_connected_peers_json "$json" outbound)"
	[ "$(count_nonempty_lines "$out_json")" -eq 2 ]
}

@test "cl_peers_report_direction is false when direction is omitted" {
	json='{"data":[{"peer_id":"x","state":"connected"}]}'
	run cl_peers_report_direction "$json"
	[ "$status" -eq 1 ]
}

@test "peer_transport_counts splits QUIC/TCP and v4/v6" {
	addrs=$'/ip4/203.0.113.10/udp/9001/quic-v1\n/ip4/203.0.113.11/tcp/9000\n/ip6/2001:db8::1/udp/9001/quic-v1\n/ip6/2001:db8::2/tcp/9000'
	[ "$(peer_transport_counts "$addrs")" = "1 1 1 1" ]
}

@test "cl_identity helpers expose peer_id and hide nothing from the helper itself" {
	id="$(sample_identity_json)"
	[ "$(cl_identity_peer_id "$id")" = "16Uiu2testPeerIdOnly" ]
	[[ "$(cl_identity_enr "$id")" == enr:-SECRET_DO_NOT_PRINT_IN_DEFAULT ]]
	disc="$(cl_identity_discovery_address_lines "$id")"
	[[ "$disc" == *"/quic-v1"* ]]
}

# ── TCP checker JSON ──────────────────────────────────────────────────────────

@test "tcp_checker helpers parse vercel-style JSON" {
	json='{"requester_ip":"203.0.113.50","open_ports":[9000,30303]}'
	[ "$(tcp_checker_requester_ip "$json")" = "203.0.113.50" ]
	[ "$(tcp_checker_open_port_list "$json")" = $'9000\n30303' ]
}

# ── troubleshoot vs debug ENR policy ──────────────────────────────────────────

@test "print_port_troubleshoot_guidance never prints an ENR value" {
	write_consensus Lighthouse
	print_port_troubleshoot_guidance 0 4 > "$TEST_DIR/guide.out"
	[[ "$(cat "$TEST_DIR/guide.out")" == *"Inbound firewall, NAT, and port-forward tips"* ]]
	[[ "$(cat "$TEST_DIR/guide.out")" == *"UDP 9000"* ]]
	[[ "$(cat "$TEST_DIR/guide.out")" == *"UDP 9001"* ]]
	[[ "$(cat "$TEST_DIR/guide.out")" == *"TCP+UDP 30303"* ]]
	[[ "$(cat "$TEST_DIR/guide.out")" == *"only speak TCP"* ]]
	[[ "$(cat "$TEST_DIR/guide.out")" == *".venv-quic"* ]]
	[[ "$(cat "$TEST_DIR/guide.out")" == *"NODE_CHECKER_QUIC_AUTO_INSTALL=0"* ]]
	[[ "$(cat "$TEST_DIR/guide.out")" == *"complementary"* ]]
	[[ "$(cat "$TEST_DIR/guide.out")" != *"Plugins menu"* ]]
	[[ "$(cat "$TEST_DIR/guide.out")" != *"No ENR"* ]]
	[[ "$(cat "$TEST_DIR/guide.out")" != *"--debug"* ]]
	[[ "$(cat "$TEST_DIR/guide.out")" != *"enr:-"* ]]
	[[ "$(cat "$TEST_DIR/guide.out")" != *"SECRET_DO_NOT_PRINT"* ]]
}

@test "print_port_troubleshoot_guidance mentions Teku IPv6 QUIC when CL is Teku" {
	write_consensus Teku
	print_port_troubleshoot_guidance 0 1 > "$TEST_DIR/guide.out"
	[[ "$(cat "$TEST_DIR/guide.out")" == *"9091/udp"* ]]
}

@test "print_port_debug_diagnostics includes ENR and a redact warning" {
	print_port_debug_diagnostics "$(sample_identity_json)" '{"data":[]}' > "$TEST_DIR/dbg.out"
	[[ "$(cat "$TEST_DIR/dbg.out")" == *"Redact before sharing"* ]]
	[[ "$(cat "$TEST_DIR/dbg.out")" == *"enr:-SECRET_DO_NOT_PRINT_IN_DEFAULT"* ]]
	[[ "$(cat "$TEST_DIR/dbg.out")" == *"16Uiu2testPeerIdOnly"* ]]
}

# ── check_open_ports (mocked checker) ─────────────────────────────────────────

check_open_ports_capture() {
	check_open_ports > "$TEST_DIR/open.out" 2>&1
	cat "$TEST_DIR/open.out"
}

@test "check_open_ports PASSes TCP inbound when checker lists expected ports" {
	fetch_tcp_port_checker() { echo '{"requester_ip":"203.0.113.50","open_ports":[9000,30303]}'; }
	check_open_ports_capture
	[[ "$(cat "$TEST_DIR/open.out")" == *"TCP inbound open on 9000"* ]]
	[[ "$(cat "$TEST_DIR/open.out")" == *"TCP inbound open on 30303"* ]]
	[[ "$(cat "$TEST_DIR/open.out")" == *"UDP inbound cannot be tested"* ]]
	[[ "$(cat "$TEST_DIR/open.out")" != *"203.0.113.50"* ]]
	[[ "$(cat "$TEST_DIR/open.out")" != *"Public TCP checker sees this host"* ]]
	[[ "$(cat "$TEST_DIR/open.out")" != *"[FAIL]"* ]]
	[ "$failed_checks" -eq 0 ]
	[ "$NODE_CHECKER_AUTO_TROUBLESHOOT" -eq 0 ]
}

@test "check_open_ports FAILs missing TCP inbound and does not treat localhost UDP as inbound" {
	fetch_tcp_port_checker() { echo '{"requester_ip":"203.0.113.50","open_ports":[9000]}'; }
	check_open_ports_capture
	[[ "$(cat "$TEST_DIR/open.out")" == *"TCP inbound closed on 30303"* ]]
	[[ "$(cat "$TEST_DIR/open.out")" != *"localhost"* ]]
	[ "$failed_checks" -eq 1 ]
	[ "$NODE_CHECKER_AUTO_TROUBLESHOOT" -eq 1 ]
}

@test "check_open_ports WARNs on CGNAT requester IP" {
	fetch_tcp_port_checker() { echo '{"requester_ip":"100.64.1.8","open_ports":[9000,30303]}'; }
	check_open_ports_capture
	[[ "$(cat "$TEST_DIR/open.out")" == *"Reported address is CGNAT"* ]]
	[[ "$(cat "$TEST_DIR/open.out")" != *"100.64.1.8"* ]]
	[ "$warning_checks" -ge 1 ]
}

@test "check_open_ports WARNs on private requester IP without naming it" {
	fetch_tcp_port_checker() { echo '{"requester_ip":"192.168.1.20","open_ports":[9000,30303]}'; }
	check_open_ports_capture
	[[ "$(cat "$TEST_DIR/open.out")" == *"Checker reported a non-public IPv4"* ]]
	[[ "$(cat "$TEST_DIR/open.out")" != *"192.168.1.20"* ]]
	[ "$warning_checks" -ge 1 ]
}

@test "check_open_ports --debug names the requester IP" {
	NODE_CHECKER_DEBUG=1
	fetch_tcp_port_checker() { echo '{"requester_ip":"203.0.113.50","open_ports":[9000,30303]}'; }
	check_open_ports_capture
	[[ "$(cat "$TEST_DIR/open.out")" == *"Public TCP checker sees this host as 203.0.113.50"* ]]
	[[ "$(cat "$TEST_DIR/open.out")" == *"TCP inbound open on 9000"* ]]
}

@test "check_open_ports --debug names a CGNAT requester IP" {
	NODE_CHECKER_DEBUG=1
	fetch_tcp_port_checker() { echo '{"requester_ip":"100.64.1.8","open_ports":[9000,30303]}'; }
	check_open_ports_capture
	[[ "$(cat "$TEST_DIR/open.out")" == *"Reported address 100.64.1.8 is CGNAT"* ]]
	[[ "$(cat "$TEST_DIR/open.out")" == *"Public TCP checker sees this host as 100.64.1.8"* ]]
}

@test "check_open_ports WARNs when checker JSON is unusable" {
	fetch_tcp_port_checker() { echo ''; }
	check_open_ports_capture
	[[ "$(cat "$TEST_DIR/open.out")" == *"Could not query the public TCP port checker"* ]]
	[ "$warning_checks" -eq 1 ]
	[ "$failed_checks" -eq 0 ]
	[ "$NODE_CHECKER_AUTO_TROUBLESHOOT" -eq 1 ]
}

@test "check_open_ports includes QUIC UDP in the expected-UDP honesty line" {
	write_consensus Lighthouse
	fetch_tcp_port_checker() { echo '{"requester_ip":"203.0.113.50","open_ports":[9000,30303]}'; }
	check_open_ports_capture
	[[ "$(cat "$TEST_DIR/open.out")" == *"9000,30303,9001"* ]]
}

# ── check_peer_count (mocked Beacon / EL APIs) ────────────────────────────────

# Live JSON-RPC on :8545 returning a count the bats stubs never use (99 / 0x63).
# If isolation is broken, check_peer_count prints 99 instead of the stub.
start_mock_el_peer_count() {
	local hex="${1:-0x63}"
	MOCK_EL_HEX="$hex" python3 -c '
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
HEX = os.environ["MOCK_EL_HEX"]
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        body = ("{\"jsonrpc\":\"2.0\",\"result\":\"%s\",\"id\":1}" % HEX).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass
HTTPServer(("127.0.0.1", 8545), H).serve_forever()
' &
	MOCK_EL_PID=$!
	local i
	for i in $(seq 1 30); do
		if curl -sf -m 1 -X POST -H "Content-Type: application/json" \
			--data '{"jsonrpc":"2.0","method":"net_peerCount","params":[],"id":1}' \
			http://127.0.0.1:8545 >/dev/null; then
			return 0
		fi
		sleep 0.1
	done
	return 1
}

stop_mock_el_peer_count() {
	if [[ -n "${MOCK_EL_PID:-}" ]]; then
		kill "$MOCK_EL_PID" 2>/dev/null || true
		wait "$MOCK_EL_PID" 2>/dev/null || true
		MOCK_EL_PID=""
	fi
}

check_peer_count_capture() {
	check_peer_count > "$TEST_DIR/peers.out" 2>&1
	cat "$TEST_DIR/peers.out"
}

stub_healthy_node_apis() {
	write_execution Geth
	write_consensus Lighthouse
	fetch_cl_api() {
		case "$1" in
			*identity*) sample_identity_json ;;
			*peers*) sample_peers_json ;;
			*peer_count*) echo '{"data":{"connected":"4","disconnected":"1"}}' ;;
			*) echo '{}' ;;
		esac
	}
	NODE_CHECKER_EL_PEER_COUNT=5
	fetch_el_rpc() { echo '{"jsonrpc":"2.0","result":"0x5","id":1}'; }
}

@test "check_peer_count PASSes inbound and inbound QUIC without printing ENR" {
	stub_healthy_node_apis
	check_peer_count_capture
	[[ "$(cat "$TEST_DIR/peers.out")" == *"Inbound working"* ]]
	[[ "$(cat "$TEST_DIR/peers.out")" == *"Inbound QUIC present"* ]]
	[[ "$(cat "$TEST_DIR/peers.out")" == *"CL peer ID: 16Uiu2testPeerIdOnly"* ]]
	[[ "$(cat "$TEST_DIR/peers.out")" == *"public IPv4"* ]]
	[[ "$(cat "$TEST_DIR/peers.out")" == *"Execution layer connected peers: 5"* ]]
	[[ "$(cat "$TEST_DIR/peers.out")" != *"enr:-SECRET"* ]]
	[[ "$(cat "$TEST_DIR/peers.out")" != *"Inbound firewall, NAT, and port-forward tips"* ]]
	[[ "$(cat "$TEST_DIR/peers.out")" != *"Plugins menu"* ]]
	[[ "$(cat "$TEST_DIR/peers.out")" != *"has no flags"* ]]
	[ "$failed_checks" -eq 0 ]
	[ "$NODE_CHECKER_AUTO_TROUBLESHOOT" -eq 0 ]
}

@test "check_peer_count WARNs when outbound exists but inbound is zero and prints troubleshoot" {
	write_consensus Lighthouse
	fetch_cl_api() {
		case "$1" in
			*identity*) echo '{"data":{"peer_id":"16Uiu2only","enr":"enr:-HIDDEN","p2p_addresses":[],"discovery_addresses":[]}}' ;;
			*peers*) echo '{"data":[{"peer_id":"o","direction":"outbound","state":"connected","last_seen_p2p_address":"/ip4/198.51.100.20/tcp/9000"}]}' ;;
			*peer_count*) echo '{"data":{"connected":"1"}}' ;;
			*) echo '{}' ;;
		esac
	}
	NODE_CHECKER_EL_PEER_COUNT=2
	fetch_el_rpc() { echo '{"result":"0x2"}'; }
	check_peer_count_capture
	[[ "$(cat "$TEST_DIR/peers.out")" == *"No inbound CL peers"* ]]
	[[ "$(cat "$TEST_DIR/peers.out")" == *"Inbound firewall, NAT, and port-forward tips"* ]]
	[[ "$(cat "$TEST_DIR/peers.out")" == *"UDP 9001"* ]]
	[[ "$(cat "$TEST_DIR/peers.out")" != *"enr:-HIDDEN"* ]]
	[ "$warning_checks" -ge 1 ]
	[ "$failed_checks" -eq 0 ]
}

@test "check_peer_count FAILs when CL has no peers at all" {
	write_consensus Lighthouse
	fetch_cl_api() {
		case "$1" in
			*identity*) echo '{"data":{"peer_id":"x","enr":"enr:-HIDDEN","p2p_addresses":[],"discovery_addresses":[]}}' ;;
			*peers*) echo '{"data":[]}' ;;
			*peer_count*) echo '{"data":{"connected":"0"}}' ;;
			*) echo '{}' ;;
		esac
	}
	NODE_CHECKER_EL_PEER_COUNT=0
	fetch_el_rpc() { echo '{"result":"0x0"}'; }
	check_peer_count_capture
	[[ "$(cat "$TEST_DIR/peers.out")" == *"Consensus client has no peers"* ]]
	[[ "$(cat "$TEST_DIR/peers.out")" == *"Execution layer connected peers: 0"* ]]
	[[ "$(cat "$TEST_DIR/peers.out")" == *"Inbound firewall, NAT, and port-forward tips"* ]]
	[[ "$(cat "$TEST_DIR/peers.out")" != *"enr:-HIDDEN"* ]]
	[ "$failed_checks" -ge 2 ]
}

@test "check_peer_count WARNs TCP-only inbound when CL expects QUIC" {
	write_consensus Lighthouse
	fetch_cl_api() {
		case "$1" in
			*identity*) sample_identity_json ;;
			*peers*) echo '{"data":[{"peer_id":"t","direction":"inbound","state":"connected","last_seen_p2p_address":"/ip4/203.0.113.11/tcp/9000"},{"peer_id":"o","direction":"outbound","state":"connected","last_seen_p2p_address":"/ip4/198.51.100.20/tcp/9000"}]}' ;;
			*peer_count*) echo '{"data":{"connected":"2"}}' ;;
			*) echo '{}' ;;
		esac
	}
	NODE_CHECKER_EL_PEER_COUNT=3
	fetch_el_rpc() { echo '{"result":"0x3"}'; }
	check_peer_count_capture
	[[ "$(cat "$TEST_DIR/peers.out")" == *"Inbound working"* ]]
	[[ "$(cat "$TEST_DIR/peers.out")" == *"Peers dialed you over TCP only"* ]]
	[[ "$(cat "$TEST_DIR/peers.out")" == *"Inbound firewall, NAT, and port-forward tips"* ]]
	[ "$failed_checks" -eq 0 ]
	[ "$warning_checks" -ge 1 ]
}

@test "check_peer_count --debug / NODE_CHECKER_DEBUG prints ENR" {
	stub_healthy_node_apis
	NODE_CHECKER_DEBUG=1
	NODE_CHECKER_TROUBLESHOOT=1
	check_peer_count_capture
	[[ "$(cat "$TEST_DIR/peers.out")" == *"enr:-SECRET_DO_NOT_PRINT_IN_DEFAULT"* ]]
	[[ "$(cat "$TEST_DIR/peers.out")" == *"Redact before sharing"* ]]
	[[ "$(cat "$TEST_DIR/peers.out")" == *"Inbound firewall, NAT, and port-forward tips"* ]]
}

@test "check_peer_count uses NODE_CHECKER_EL_PEER_COUNT instead of fetch_el_rpc" {
	write_consensus Lighthouse
	fetch_cl_api() {
		case "$1" in
			*identity*) sample_identity_json ;;
			*peers*) sample_peers_json ;;
			*peer_count*) echo '{"data":{"connected":"4"}}' ;;
			*) echo '{}' ;;
		esac
	}
	fetch_el_rpc() { echo '{"result":"0x0"}'; }
	NODE_CHECKER_EL_PEER_COUNT=5
	check_peer_count_capture
	[[ "$(cat "$TEST_DIR/peers.out")" == *"Execution layer connected peers: 5"* ]]
	[ "$failed_checks" -eq 0 ]
}

@test "check_peer_count hex stub 0x5 is 5 without NODE_CHECKER_EL_PEER_COUNT (gawk-safe)" {
	stub_healthy_node_apis
	unset NODE_CHECKER_EL_PEER_COUNT || true
	fetch_el_rpc() { echo '{"jsonrpc":"2.0","result":"0x5","id":1}'; }
	check_peer_count_capture
	[[ "$(cat "$TEST_DIR/peers.out")" == *"Execution layer connected peers: 5"* ]]
	[ "$failed_checks" -eq 0 ]
}

@test "unstubbed EL peer count reads a live mock on :8545 (99, not the bats stub)" {
	start_mock_el_peer_count 0x63
	write_consensus Lighthouse
	fetch_cl_api() {
		case "$1" in
			*identity*) sample_identity_json ;;
			*peers*) sample_peers_json ;;
			*peer_count*) echo '{"data":{"connected":"4"}}' ;;
			*) echo '{}' ;;
		esac
	}
	unset NODE_CHECKER_EL_PEER_COUNT || true
	[ "$(node_checker_el_connected_peers)" -eq 99 ]
	check_peer_count_capture
	[[ "$(cat "$TEST_DIR/peers.out")" == *"Execution layer connected peers: 99"* ]]
	[[ "$(cat "$TEST_DIR/peers.out")" != *"Execution layer connected peers: 5"* ]]
}

@test "NODE_CHECKER_EL_PEER_COUNT and fetch_el_rpc stub ignore live mock EL on :8545" {
	start_mock_el_peer_count 0x63
	unset NODE_CHECKER_EL_PEER_COUNT || true
	[ "$(node_checker_el_connected_peers)" -eq 99 ]
	stub_healthy_node_apis
	check_peer_count_capture
	[[ "$(cat "$TEST_DIR/peers.out")" == *"Execution layer connected peers: 5"* ]]
	[[ "$(cat "$TEST_DIR/peers.out")" != *"Execution layer connected peers: 99"* ]]
	[ "$failed_checks" -eq 0 ]
	unset NODE_CHECKER_EL_PEER_COUNT || true
	fetch_el_rpc() { echo '{"jsonrpc":"2.0","result":"0x5","id":1}'; }
	total_checks=0
	failed_checks=0
	warning_checks=0
	NODE_CHECKER_AUTO_TROUBLESHOOT=0
	NODE_CHECKER_TROUBLESHOOT_PRINTED=0
	check_peer_count_capture
	[[ "$(cat "$TEST_DIR/peers.out")" == *"Execution layer connected peers: 5"* ]]
	[[ "$(cat "$TEST_DIR/peers.out")" != *"Execution layer connected peers: 99"* ]]
	[ "$failed_checks" -eq 0 ]
}

@test "check_peer_count FAILs when Beacon peers API is unreachable" {
	fetch_cl_api() { echo ''; }
	NODE_CHECKER_EL_PEER_COUNT=1
	fetch_el_rpc() { echo '{"result":"0x1"}'; }
	check_peer_count_capture
	[[ "$(cat "$TEST_DIR/peers.out")" == *"Unable to list consensus peers"* ]]
	[ "$failed_checks" -ge 1 ]
}

@test "node_checker_parse_args enables troubleshoot and debug" {
	NODE_CHECKER_TROUBLESHOOT=0
	NODE_CHECKER_DEBUG=0
	node_checker_parse_args --troubleshoot
	[ "$NODE_CHECKER_TROUBLESHOOT" -eq 1 ]
	[ "$NODE_CHECKER_DEBUG" -eq 0 ]
	node_checker_parse_args --debug
	[ "$NODE_CHECKER_TROUBLESHOOT" -eq 1 ]
	[ "$NODE_CHECKER_DEBUG" -eq 1 ]
}

@test "UFW QUIC miss auto-prints troubleshoot without --troubleshoot and without ENR" {
	write_consensus Lighthouse
	sudo() { "$@"; }
	ufw() {
		echo "Status: active"
		echo "9000                       ALLOW       Anywhere"
	}
	ss() { echo "tcp LISTEN 0 0 0.0.0.0:9000 0.0.0.0:*"; }
	export -f sudo ufw ss
	check_cl_quic > "$TEST_DIR/quic.out" 2>&1
	[ "$NODE_CHECKER_AUTO_TROUBLESHOOT" -eq 1 ]
	maybe_print_port_troubleshoot "?" "?" > "$TEST_DIR/guide.out" 2>&1
	[[ "$(cat "$TEST_DIR/guide.out")" == *"Inbound firewall, NAT, and port-forward tips"* ]]
	[[ "$(cat "$TEST_DIR/guide.out")" == *"Forward UDP 9000"* ]]
	[[ "$(cat "$TEST_DIR/guide.out")" != *"Plugins menu"* ]]
	[[ "$(cat "$TEST_DIR/guide.out")" != *"has no flags"* ]]
	[[ "$(cat "$TEST_DIR/guide.out")" != *"No ENR"* ]]
	[[ "$(cat "$TEST_DIR/guide.out")" != *"--troubleshoot"* ]]
	[[ "$(cat "$TEST_DIR/guide.out")" != *"enr:-"* ]]
	[[ "$(cat "$TEST_DIR/guide.out")" != *"SECRET"* ]]
}

@test "forced --troubleshoot prints guidance on a green node without ENR" {
	stub_healthy_node_apis
	NODE_CHECKER_TROUBLESHOOT=1
	check_peer_count_capture
	[[ "$(cat "$TEST_DIR/peers.out")" == *"Inbound working"* ]]
	[[ "$(cat "$TEST_DIR/peers.out")" == *"Inbound firewall, NAT, and port-forward tips"* ]]
	[[ "$(cat "$TEST_DIR/peers.out")" == *"reachability is still in doubt"* ]]
	[[ "$(cat "$TEST_DIR/peers.out")" != *"--troubleshoot"* ]]
	[[ "$(cat "$TEST_DIR/peers.out")" != *"Plugins menu"* ]]
	[[ "$(cat "$TEST_DIR/peers.out")" != *"enr:-SECRET"* ]]
	[ "$NODE_CHECKER_DEBUG" -eq 0 ]
}

# ── Port Checker menu wiring ──────────────────────────────────────────────────

@test "node_checker_inbound_checks runs QUIC listen, TCP checker, then QUIC probe" {
	local order="$TEST_DIR/order"
	: > "$order"
	check_cl_quic() { echo CL >> "$order"; }
	check_open_ports() { echo TCP >> "$order"; }
	check_inbound_quic_probe() { echo PROBE >> "$order"; }
	node_checker_inbound_checks >/dev/null
	[ "$(paste -sd, "$order")" = "CL,TCP,PROBE" ]
}

@test "node_checker_main keeps the inbound subset inside the full suite" {
	awk '
		/^node_checker_main\(\)/ { in_fn=1 }
		in_fn && /^# Allow sourcing/ { exit }
		in_fn { print }
	' plugins/node-checker/run.sh > "$TEST_DIR/main.body"
	grep -q 'check_listening_ports' "$TEST_DIR/main.body"
	grep -q 'check_elcl_listening_ports' "$TEST_DIR/main.body"
	grep -q 'node_checker_inbound_checks' "$TEST_DIR/main.body"
	grep -q 'check_peer_count' "$TEST_DIR/main.body"
	grep -q 'check_systemd_services' "$TEST_DIR/main.body"
	! grep -q 'check_open_ports' "$TEST_DIR/main.body"
	! grep -q 'check_cl_quic' "$TEST_DIR/main.body"
	! grep -q 'check_inbound_quic_probe' "$TEST_DIR/main.body"
}

@test "node_checker_parse_args --ports selects the port checker" {
	NODE_CHECKER_PORTS_ONLY=0
	NODE_CHECKER_TROUBLESHOOT=0
	NODE_CHECKER_DEBUG=0
	node_checker_parse_args --ports --debug
	[ "$NODE_CHECKER_PORTS_ONLY" -eq 1 ]
	[ "$NODE_CHECKER_TROUBLESHOOT" -eq 1 ]
	[ "$NODE_CHECKER_DEBUG" -eq 1 ]
	NODE_CHECKER_PORTS_ONLY=0
	node_checker_parse_args --port-checker
	[ "$NODE_CHECKER_PORTS_ONLY" -eq 1 ]
}

@test "print_port_troubleshoot_guidance skipped does not claim the CL hides direction" {
	print_port_troubleshoot_guidance skipped skipped > "$TEST_DIR/guide.out"
	[[ "$(cat "$TEST_DIR/guide.out")" == *"Peer direction was not measured"* ]]
	[[ "$(cat "$TEST_DIR/guide.out")" == *"full Node Checker"* ]]
	[[ "$(cat "$TEST_DIR/guide.out")" != *"does not report peer direction"* ]]
	[[ "$(cat "$TEST_DIR/guide.out")" != *"No inbound consensus peers yet"* ]]
}

@test "node_checker_port_checker runs the inbound subset, then pauses" {
	local order="$TEST_DIR/order"
	: > "$order"
	clear() { echo CLEARED >> "$order"; }
	read() { echo PAUSED >> "$order"; }
	check_cl_quic() { echo CL >> "$order"; }
	check_open_ports() {
		echo TCP >> "$order"
		NODE_CHECKER_AUTO_TROUBLESHOOT=1
	}
	check_inbound_quic_probe() { echo PROBE >> "$order"; }
	NODE_CHECKER_TROUBLESHOOT=0
	NODE_CHECKER_TROUBLESHOOT_PRINTED=0
	node_checker_port_checker > "$TEST_DIR/port.out" 2>&1
	[ "$(paste -sd, "$order")" = "CLEARED,CL,TCP,PROBE,PAUSED" ]
	[[ "$(cat "$TEST_DIR/port.out")" == *"Port Checker: Test for Incoming Connections"* ]]
	[[ "$(cat "$TEST_DIR/port.out")" == *"not a manual prompt"* ]]
	[[ "$(cat "$TEST_DIR/port.out")" == *"Peer direction was not measured"* ]]
	[[ "$(cat "$TEST_DIR/port.out")" == *"Press enter to exit"* ]]
	[[ "$(cat "$TEST_DIR/port.out")" != *"does not report peer direction"* ]]
}

@test "node_checker_port_checker warns when installed clients are stopped" {
	write_execution Nethermind
	write_consensus Lighthouse
	cat > "$CHARON_SERVICE_FILE" <<'EOF'
[Unit]
Description=Charon
[Service]
ExecStart=/usr/bin/charon run --p2p-tcp-address=0.0.0.0:3610
EOF
	clear() { :; }
	read() { :; }
	systemctl() { return 1; }
	check_cl_quic() { :; }
	check_open_ports() { :; }
	check_inbound_quic_probe() { :; }
	NODE_CHECKER_AUTO_TROUBLESHOOT=0
	NODE_CHECKER_TROUBLESHOOT=0
	NODE_CHECKER_TROUBLESHOOT_PRINTED=0
	node_checker_port_checker > "$TEST_DIR/port.out" 2>&1
	[[ "$(cat "$TEST_DIR/port.out")" == *"Execution client service not running"* ]]
	[[ "$(cat "$TEST_DIR/port.out")" == *"Consensus client service not running"* ]]
	[[ "$(cat "$TEST_DIR/port.out")" == *"Charon service not running"* ]]
}

@test "Port Checker menu calls checkOpenPorts which runs run.sh --ports" {
	grep -q 'Port Checker: Test for Incoming Connections' ethpillar.sh
	grep -A3 '🔄)' ethpillar.sh | grep -q 'checkOpenPorts'
	grep -A3 '🛡️)' ethpillar.sh | grep -q 'plugins/node-checker/run.sh'
	! grep -A3 '🛡️)' ethpillar.sh | grep -q -- '--ports'
	awk '
		/^checkOpenPorts\(\)/ { in_fn=1 }
		in_fn { print }
		in_fn && /^}/ { exit }
	' functions.sh > "$TEST_DIR/checkopen.body"
	grep -q 'plugins/node-checker/run.sh --ports' "$TEST_DIR/checkopen.body"
	! grep -q 'vercel.app' "$TEST_DIR/checkopen.body"
	! grep -q 'read -r -p' "$TEST_DIR/checkopen.body"
	grep -q 'node_checker_port_checker' plugins/node-checker/run.sh
	grep -q 'NODE_CHECKER_PORTS_ONLY' plugins/node-checker/run.sh
}
