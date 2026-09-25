#!/bin/bash

# Author: coincashew.eth | coincashew.com
# License: GNU GPL
# Source: https://github.com/coincashew/ethpillar
# Description: EthPillar is a one-liner setup tool and node management TUI
#
# Made for home and solo stakers 🏠🥩

SOURCE_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ETHPILLAR_ROOT="$(cd "${SOURCE_DIR}/../.." && pwd)"
# shellcheck disable=SC1091
source "${ETHPILLAR_ROOT}/functions.sh"

# Node configuration
# CL P2P 9000 and EL P2P 30303 (TCP+UDP) are the "expected 4" listen ports.
# CL QUIC UDP (typically 9001; Teku also 9091) is tracked separately so that
# count stays stable — see configure_cl_quic_udp_check_ports / check_cl_quic.
p2p_ports=("9000" "30303")
p2p_processes=("geth" "besu" "teku" "lighthouse" "prysm" "nimbus_beacon_node" "nimbus_validator" "lodestar" "erigon" "nethermind" "reth" "mev-boost" "charon")
services=("consensus" "execution" "validator" "mevboost")
tcp_check_ports="9000,30303"
udp_check_ports="9000,30303"
udp_check_ports_base="$udp_check_ports"
ELCL_EXPECTED_LISTEN_COUNT=4
charon_p2p_port=""

if isCharonEnabled; then
    charon_p2p_port="$(getCharonP2pPort)"
    if [[ -n "$charon_p2p_port" ]]; then
        p2p_ports+=("$charon_p2p_port")
        tcp_check_ports="${tcp_check_ports},${charon_p2p_port}"
    fi
    services+=("charon")
fi
API_BN_ENDPOINT="http://localhost:5052"
EL_RPC_ENDPOINT="http://localhost:8545"

declare -A client_github_url
client_github_url['Lighthouse']='https://api.github.com/repos/sigp/lighthouse/releases/latest'
client_github_url['Lodestar']='https://api.github.com/repos/ChainSafe/lodestar/releases/latest'
client_github_url['Teku']='https://api.github.com/repos/ConsenSys/teku/releases/latest'
client_github_url['Nimbus']='https://api.github.com/repos/status-im/nimbus-eth2/releases/latest'
client_github_url['Prysm']='https://api.github.com/repos/OffchainLabs/prysm/releases/latest'
client_github_url['Nethermind']='https://api.github.com/repos/NethermindEth/nethermind/releases/latest'
client_github_url['Besu']='https://api.github.com/repos/hyperledger/besu/releases/latest'
client_github_url['Erigon']='https://api.github.com/repos/erigontech/erigon/releases/latest'
client_github_url['Geth']='https://api.github.com/repos/ethereum/go-ethereum/releases/latest'
client_github_url['Reth']='https://api.github.com/repos/paradigmxyz/reth/releases/latest'
client_github_url['mev-boost']='https://api.github.com/repos/flashbots/mev-boost/releases/latest'
client_github_url['Charon']='https://api.github.com/repos/ObolNetwork/charon/releases/latest'

# Load environment variables overrides
if [[ -f "$SOURCE_DIR"/../../.env.overrides ]]; then
    # shellcheck source=/dev/null
    source "$SOURCE_DIR"/../../.env.overrides

    # Handle consensus layer endpoint overrides
    if [[ -n "${CL_IP_ADDRESS:-}" || -n "${CL_REST_PORT:-}" ]]; then
        # Use default values if not overridden
        local_cl_ip="${CL_IP_ADDRESS:-localhost}"
        local_cl_port="${CL_REST_PORT:-5052}"
        API_BN_ENDPOINT="http://${local_cl_ip}:${local_cl_port}"
    fi

    # Handle execution layer endpoint overrides
    if [[ -n "${EL_IP_ADDRESS:-}" || -n "${EL_RPC_PORT:-}" ]]; then
        # Use default values if not overridden
        local_el_ip="${EL_IP_ADDRESS:-localhost}"
        local_el_port="${EL_RPC_PORT:-8545}"
        EL_RPC_ENDPOINT="http://${local_el_ip}:${local_el_port}"
    fi
fi

# Thresholds
MEMORY_WARN=90
CPU_WARN=90
DISK_WARN=90

total_checks=0
failed_checks=0
warning_checks=0

# --troubleshoot / --debug (also NODE_CHECKER_TROUBLESHOOT=1, NODE_CHECKER_DEBUG=1).
# Debug may print ENR; default and troubleshoot paths never do.
NODE_CHECKER_TROUBLESHOOT="${NODE_CHECKER_TROUBLESHOOT:-0}"
NODE_CHECKER_DEBUG="${NODE_CHECKER_DEBUG:-0}"
TCP_PORT_CHECKER_URL="${TCP_PORT_CHECKER_URL:-https://eth2-client-port-checker.vercel.app/api/checker?ports=}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[35m'
BOLD='\033[1m'
NC='\033[0m' # No Color

display_banner() {
cat << 'EOF'
             ,----------------,              ,---------,
        ,-----------------------,          ,"        ,"|
      ,"                      ,"|        ,"        ,"  |
     +-----------------------+  |      ,"        ,"    |
     |  .-----------------.  |  |     +---------+      |
     |  |                 |  |  |     | -==----'|      |
     |  |  Running        |  |  |     |         |      |
     |  |  Node Checker   |  |  |/----|`---=    |      |
     |  |  > ethpillar    |  |  |   ,/|==== ooo |      ;
     |  |                 |  |  |  // |(((( [33]|    ,"
     |  `-----------------'  |," .;'| |((((     |  ,"
     +-----------------------+  ;;  | |         |,"
        /_)______________(_/  //'   | +---------+
   ___________________________/___  `,
  /  oooooooooooooooo  .o.  oooo /,   \,"-----------
 / ==ooooooooooooooo==.o.  ooo= //   ,`\---)B     ,"
/_==__==========__==_ooo__ooo=_/'   /___________,"
`-----------------------------'
EOF
}

print_section_header() {
    local title="$1"
    local width=80
    local padding=$(( (width - ${#title}) / 2 ))
    echo -e "\n${BLUE}${BOLD}╔$(printf '═%.0s' $(seq 1 $((width-2))))╗${NC}"
    echo -e "${BLUE}${BOLD}║$(printf '%*s' $padding '')${YELLOW}${BOLD}$title${BLUE}${BOLD}$(printf '%*s' $((width-2-padding-${#title})) '')║${NC}"
    echo -e "${BLUE}${BOLD}╚$(printf '═%.0s' $(seq 1 $((width-2))))╝${NC}\n"
}

print_check_result() {
    local status="$1"
    local message="$2"
    local icon=""
    local color=""
    local prefix=""

    case "$status" in
        "PASS")
            icon="✓"
            color="$GREEN"
            prefix="[PASS]"
            ;;
        "FAIL")
            icon="✗"
            color="$RED"
            prefix="[FAIL]"
            ;;
        "WARN")
            icon="⚠"
            color="$YELLOW"
            prefix="[WARN]"
            ;;
        "INFO")
            icon="ℹ"
            color="$PURPLE"
            prefix="[INFO]"
            ;;
    esac

    echo -e "${color}${prefix} ${icon} ${message}${NC}"
}

node_checker_exec_service() {
    echo "${EXEC_SERVICE_FILE:-/etc/systemd/system/execution.service}"
}

node_checker_consensus_service() {
    echo "${CONSENSUS_SERVICE_FILE:-/etc/systemd/system/consensus.service}"
}

# First word of Description= — same heuristic as getClient / check_consensus_version.
node_checker_unit_client() {
    local path="$1"
    [[ -f "$path" ]] || return 0
    grep "Description=" "$path" 2>/dev/null | awk -F'=' '{print $2}' | awk '{print $1}'
}

# Caplin is integrated into execution.service (EL Erigon-Caplin); no QUIC by default.
is_caplin_node() {
    local exec_svc el cl consensus_svc
    exec_svc="$(node_checker_exec_service)"
    el="$(node_checker_unit_client "$exec_svc")"
    if [[ "$el" == "Erigon-Caplin" || "$el" == "Caplin" ]]; then
        return 0
    fi
    if [[ -f "$exec_svc" ]] && grep -qiE 'caplin' "$exec_svc" 2>/dev/null; then
        return 0
    fi
    consensus_svc="$(node_checker_consensus_service)"
    cl="$(node_checker_unit_client "$consensus_svc")"
    if [[ "$cl" == "Caplin" || "$cl" == "Erigon-Caplin" ]]; then
        return 0
    fi
    return 1
}

node_checker_cl_name() {
    node_checker_unit_client "$(node_checker_consensus_service)"
}

# True when a local consensus.service exists and the CL is not Caplin.
# Known QUIC CLs: Lighthouse, Teku, Nimbus, Lodestar, Grandine, Prysm.
# Unknown CL with consensus.service: still expect 9001/udp.
cl_expects_quic() {
    is_caplin_node && return 1
    local cl
    cl="$(node_checker_cl_name)"
    [[ -n "$cl" ]]
}

# Prints space-separated UDP ports. Empty when QUIC is not expected (Caplin / no CL).
expected_cl_quic_udp_ports() {
    cl_expects_quic || return 0
    local cl quic_port ipv6_port
    cl="$(node_checker_cl_name)"
    quic_port="${CL_P2P_PORT_2:-9001}"
    if [[ "$cl" == "Teku" ]]; then
        ipv6_port="${TEKU_QUIC_IPV6_PORT:-$(( ${CL_P2P_PORT:-9000} + 91 ))}"
        echo "${quic_port} ${ipv6_port}"
    else
        echo "${quic_port}"
    fi
}

csv_has_item() {
    local csv="$1" item="$2"
    [[ ",${csv}," == *",${item},"* ]]
}

csv_append_unique() {
    local csv="$1" item="$2"
    if [[ -z "$item" ]]; then
        echo "$csv"
        return
    fi
    if csv_has_item "$csv" "$item"; then
        echo "$csv"
        return
    fi
    if [[ -z "$csv" ]]; then
        echo "$item"
    else
        echo "${csv},${item}"
    fi
}

# Append expected CL QUIC UDP ports to udp_check_ports (not TCP, not p2p_ports).
# Resets from udp_check_ports_base so repeated calls stay idempotent.
configure_cl_quic_udp_check_ports() {
    local base port
    base="${udp_check_ports_base:-9000,30303}"
    udp_check_ports="$base"
    for port in $(expected_cl_quic_udp_ports); do
        udp_check_ports="$(csv_append_unique "$udp_check_ports" "$port")"
    done
}

check_cl_quic_ufw_rules() {
    local ufw_status port
    if ! sudo ufw status 2>/dev/null | grep -q "Status: active"; then
        return 0
    fi
    ufw_status="$(sudo ufw status 2>/dev/null)"
    for port in $(expected_cl_quic_udp_ports); do
        total_checks=$((total_checks + 1))
        if echo "$ufw_status" | grep -qE "${port}/udp"; then
            print_check_result "PASS" "UFW allows CL QUIC ${port}/udp"
        else
            print_check_result "FAIL" "UFW is active but missing allow rule for CL QUIC ${port}/udp"
            failed_checks=$((failed_checks + 1))
        fi
    done
}

check_cl_quic_listening() {
    local port pid process
    for port in $(expected_cl_quic_udp_ports); do
        total_checks=$((total_checks + 1))
        if sudo ss -lntu | grep -qE "udp.*:${port}([^0-9]|$)"; then
            print_check_result "PASS" "Detected UDP service on CL QUIC port ${port}"
            if [ "$EUID" -eq 0 ]; then
                pid=$(sudo ss -lntup "sport = :${port}" | awk -Fpid= '/users:/ {print $2}' | cut -d, -f1 | head -1)
                if [ -n "$pid" ]; then
                    process=$(ps -p "$pid" -o comm=)
                    echo -e "${YELLOW}          Process: ${process} (PID ${pid})${NC}"
                fi
            fi
        else
            print_check_result "FAIL" "CL QUIC port ${port}/udp not listening"
            failed_checks=$((failed_checks + 1))
        fi
    done
}

check_cl_quic() {
    print_check_result "INFO" "CL QUIC: after Glamsterdam, libp2p MPlex/TCP P2P is deprecated — verify QUIC UDP (typically ${CL_P2P_PORT_2:-9001}/udp)"
    if is_caplin_node; then
        total_checks=$((total_checks + 1))
        print_check_result "WARN" "Caplin has no QUIC by default; skipping ${CL_P2P_PORT_2:-9001}/udp requirement"
        warning_checks=$((warning_checks + 1))
        return 0
    fi
    if ! cl_expects_quic; then
        return 0
    fi
    check_cl_quic_ufw_rules
    check_cl_quic_listening
    print_check_result "INFO" "QUIC listen/UFW is local. Inbound QUIC is inferred from peers that dialed you (peer-direction section)."
}

# Port-check helpers.
# Approach adapted from ethstaker/eth-docker `port-check` (Apache-2.0): Beacon
# API inbound vs outbound, QUIC multiaddrs, CGNAT honesty, and operator
# guidance that does not leak ENR. EthPillar is systemd/bare-metal — no
# Docker/compose probes, no discv5/quicmap containers.

node_checker_usage() {
    cat <<'EOF'
Usage: run.sh [--troubleshoot] [--debug]

  --troubleshoot  Print inbound port-forward and firewall guidance (no ENR)
  --debug         Troubleshoot plus ENR/identity diagnostics (redact before sharing)

Default node-checker still prints a short troubleshoot section when inbound
looks broken. Local listen (ss/UFW) is not the same as inbound reachability.
EOF
}

node_checker_parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --troubleshoot)
                NODE_CHECKER_TROUBLESHOOT=1
                ;;
            --debug)
                NODE_CHECKER_TROUBLESHOOT=1
                NODE_CHECKER_DEBUG=1
                ;;
            -h|--help)
                node_checker_usage
                exit 0
                ;;
            *)
                echo "Unknown option: $1" >&2
                node_checker_usage >&2
                exit 1
                ;;
        esac
        shift
    done
}

# RFC1918 / loopback / link-local / this-host. Returns 0 when not Internet-routable.
is_private_ipv4() {
    case "$1" in
        10.*|127.*|0.*|169.254.*|192.168.*|172.1[6-9].*|172.2[0-9].*|172.3[01].*) return 0 ;;
        *) return 1 ;;
    esac
}

# Carrier-grade NAT 100.64.0.0/10 — no customer port-forward can work.
is_cgnat_ipv4() {
    case "$1" in
        100.6[4-9].*|100.[7-9][0-9].*|100.1[01][0-9].*|100.12[0-7].*) return 0 ;;
        *) return 1 ;;
    esac
}

# Echo public | private | cgnat | empty
classify_ipv4() {
    local ip="$1"
    if [[ -z "$ip" ]]; then
        echo "empty"
        return
    fi
    if is_cgnat_ipv4 "$ip"; then
        echo "cgnat"
        return
    fi
    if is_private_ipv4 "$ip"; then
        echo "private"
        return
    fi
    echo "public"
}

count_nonempty_lines() {
    local text="$1"
    if [[ -z "$text" ]]; then
        echo 0
        return
    fi
    printf '%s\n' "$text" | grep -c .
}

count_multiaddr_matching() {
    local addresses="$1"
    local pattern="$2"
    if [[ -z "$addresses" ]]; then
        echo 0
        return
    fi
    printf '%s\n' "$addresses" | grep -cE "$pattern" || true
}

# First /ip4/X from newline-separated multiaddrs.
multiaddr_first_ip4() {
    local addresses="$1"
    printf '%s\n' "$addresses" | sed -n 's|.*/ip4/\([^/]*\).*|\1|p' | head -1
}

# Connected peers in one direction as compact JSON objects (one per line).
# Filter locally: Teku/Grandine ignore Beacon API direction query parameters.
# A peer is dropped only when it says it is not connected.
cl_connected_peers_json() {
    local json="$1"
    local direction="$2"
    jq -c --arg d "$direction" '
        (.data // [])
        | map(select(
            (.direction == $d)
            and (
              (.state // "connected") as $s
              | ($s != "disconnected" and $s != "disconnecting" and $s != "connecting")
            )
          ))
        | .[]
    ' <<< "$json" 2>/dev/null || true
}

cl_peers_report_direction() {
    local json="$1"
    # Empty peer list is a real measurement (inbound=0). Missing direction on
    # present peers means the client does not report which way they were dialed.
    jq -e '
        (.data // []) as $d
        | ($d | length == 0)
          or ([$d[] | select(.direction != null and .direction != "")] | length > 0)
    ' <<< "$json" >/dev/null 2>&1
}

cl_peer_address_lines() {
    local peer_objects="$1"
    if [[ -z "$peer_objects" ]]; then
        return 0
    fi
    printf '%s\n' "$peer_objects" | jq -r '.last_seen_p2p_address // empty' 2>/dev/null || true
}

# Echo: quic4 quic6 tcp4 tcp6
peer_transport_counts() {
    local addresses="$1"
    local quic_total quic_v6 tcp_total tcp_v6
    quic_total="$(count_multiaddr_matching "$addresses" '/quic')"
    quic_v6="$(count_multiaddr_matching "$addresses" '/ip6/.*/quic')"
    tcp_total="$(count_multiaddr_matching "$addresses" '/tcp/')"
    tcp_v6="$(count_multiaddr_matching "$addresses" '/ip6/.*/tcp/')"
    echo "$((quic_total - quic_v6)) ${quic_v6} $((tcp_total - tcp_v6)) ${tcp_v6}"
}

# Echo working | not-working | unknown
inbound_status_kind() {
    local inbound="$1"
    if [[ "$inbound" == "?" ]]; then
        echo "unknown"
    elif [[ "$inbound" =~ ^[0-9]+$ && "$inbound" -gt 0 ]]; then
        echo "working"
    else
        echo "not-working"
    fi
}

cl_identity_peer_id() {
    jq -r '.data.peer_id // empty' <<< "$1" 2>/dev/null || true
}

cl_identity_enr() {
    jq -r '.data.enr // empty' <<< "$1" 2>/dev/null || true
}

cl_identity_p2p_address_lines() {
    jq -r '.data.p2p_addresses[]? // empty' <<< "$1" 2>/dev/null || true
}

cl_identity_discovery_address_lines() {
    jq -r '.data.discovery_addresses[]? // empty' <<< "$1" 2>/dev/null || true
}

tcp_checker_open_port_list() {
    jq -r '.open_ports[]? // empty' <<< "$1" 2>/dev/null || true
}

tcp_checker_requester_ip() {
    jq -r '.requester_ip // empty' <<< "$1" 2>/dev/null || true
}

# Overridable in bats (do not start clients).
node_checker_http_get() {
    curl -m 2 -s "$@"
}

fetch_cl_api() {
    local path="$1"
    node_checker_http_get -X GET "${API_BN_ENDPOINT}${path}" -H "accept: application/json"
}

fetch_el_rpc() {
    local method="$1"
    node_checker_http_get -X POST -H "Content-Type: application/json" \
        --data "{\"jsonrpc\":\"2.0\",\"method\":\"${method}\",\"params\":[],\"id\":1}" \
        "${EL_RPC_ENDPOINT}"
}

fetch_tcp_port_checker() {
    local ports="$1"
    node_checker_http_get "${TCP_PORT_CHECKER_URL}${ports}"
}

print_peer_transport_table() {
    local inbound_addrs="$1"
    local outbound_addrs="$2"
    local iq4 iq6 it4 it6 oq4 oq6 ot4 ot6
    read -r iq4 iq6 it4 it6 <<< "$(peer_transport_counts "$inbound_addrs")"
    read -r oq4 oq6 ot4 ot6 <<< "$(peer_transport_counts "$outbound_addrs")"
    print_check_result "INFO" "Peer transports as the client reports them (last_seen_p2p_address):"
    printf '  %-10s %8s %8s %8s %8s\n' "direction" "QUIC v4" "QUIC v6" "TCP v4" "TCP v6"
    printf '  %-10s %8s %8s %8s %8s\n' "inbound" "$iq4" "$iq6" "$it4" "$it6"
    printf '  %-10s %8s %8s %8s %8s\n' "outbound" "$oq4" "$oq6" "$ot4" "$ot6"
}

# Actionable next steps. Never prints ENR or identity JSON.
print_port_troubleshoot_guidance() {
    local inbound="${1:-?}"
    local outbound="${2:-?}"
    local cl_p2p="${CL_P2P_PORT:-9000}"
    local cl_quic="${CL_P2P_PORT_2:-9001}"
    local el_p2p="${EL_P2P_PORT:-30303}"
    local teku_extra=""

    print_check_result "INFO" "Inbound troubleshoot (no ENR in this section):"
    if [[ "$(inbound_status_kind "$inbound")" == "working" ]]; then
        echo "  Inbound peering is working. This guidance prints because you asked for it (--troubleshoot)."
    elif [[ "$inbound" == "?" ]]; then
        echo "  This consensus client does not report peer direction, so inbound cannot be measured here."
    else
        echo "  No inbound consensus peers yet. A node started in the last few minutes may not have been dialed."
        echo "  Wait, then re-run node-checker before changing firewall rules."
    fi
    if [[ "$outbound" =~ ^[0-9]+$ && "$outbound" -eq 0 && "$inbound" != "?" ]]; then
        echo "  Outbound is also zero — consensus may still be starting, or UDP ${cl_p2p} is blocked outbound too."
    fi
    echo "  Local listen (ss) and UFW allow rules are necessary but not sufficient."
    echo "  Forward UDP ${cl_p2p} (discv5) and UDP ${cl_quic} (QUIC) to this host."
    echo "  Forward TCP+UDP ${el_p2p} for the execution client."
    echo "  The consensus layer needs UDP for transport. A TCP-only forward will not do."
    echo "  No website can test a UDP port — checkers that offer a green result only speak TCP."
    echo "  A green TCP result for ${cl_p2p} or ${el_p2p} proves nothing about QUIC ${cl_quic}/udp."
    echo "  If your public IPv4 is CGNAT (100.64.0.0/10), no IPv4 port-forward can work; use IPv6 or ask the ISP for a public IPv4."
    echo "  UFW (when active) should allow ${cl_p2p}/tcp, ${cl_p2p}/udp, ${cl_quic}/udp, ${el_p2p}/tcp, ${el_p2p}/udp."
    if [[ "$(node_checker_cl_name)" == "Teku" ]]; then
        teku_extra="${TEKU_QUIC_IPV6_PORT:-$(( cl_p2p + 91 ))}"
        echo "  Teku also needs UFW allow ${teku_extra}/udp (IPv6 QUIC)."
    fi
    echo "  Do not share your ENR when asking for help — it contains your IP. Share peer ID and these steps instead."
    echo "  Re-run with --debug only on this host if you need the ENR/identity dump; redact before pasting."
}

# ENR and identity live only here (NODE_CHECKER_DEBUG / --debug).
print_port_debug_diagnostics() {
    local identity_json="$1"
    local peers_json="$2"
    local enr peer_id disc p2p
    print_check_result "INFO" "Debug diagnostics — this names your ENR and addresses. Redact before sharing."
    peer_id="$(cl_identity_peer_id "$identity_json")"
    enr="$(cl_identity_enr "$identity_json")"
    disc="$(cl_identity_discovery_address_lines "$identity_json")"
    p2p="$(cl_identity_p2p_address_lines "$identity_json")"
    echo "  Peer ID: ${peer_id:-none}"
    echo "  ENR: ${enr:-none}"
    echo "  Discovery addresses:"
    if [[ -n "$disc" ]]; then
        printf '    %s\n' "$disc"
    else
        echo "    none"
    fi
    echo "  Libp2p listen addresses:"
    if [[ -n "$p2p" ]]; then
        printf '    %s\n' "$p2p"
    else
        echo "    none"
    fi
    echo "  Raw /eth/v1/node/peers data length: $(printf '%s' "$peers_json" | wc -c) bytes"
}

check_firewall() {
    ((total_checks++))
    if sudo ufw status | grep -q "Status: active"; then
        print_check_result "PASS" "Firewall is active"
    else
        print_check_result "FAIL" "Firewall is not active. Install found in Security & Node Checks."
        ((failed_checks++))
    fi
}

check_ssh_keys() {
    ((total_checks++))
    if grep -q "^PasswordAuthentication no" /etc/ssh/sshd_config; then
        print_check_result "PASS" "SSH key authentication enforced"
    else
        print_check_result "WARN" "Password authentication allowed. SSH key authentication is best."
    fi
}

check_ssh_port() {
    ((total_checks++))
    if grep -q "^Port 22" /etc/ssh/sshd_config; then
        print_check_result "WARN" "SSH is using default port 22. Consider changing to a non-standard port for better security."
        ((warning_checks++))
    else
        print_check_result "PASS" "SSH is not using default port 22"
    fi
}

check_fail2ban() {
    ((total_checks++))
    if systemctl is-active --quiet fail2ban; then
        print_check_result "PASS" "Fail2ban is running"
    else
        print_check_result "FAIL" "Fail2ban not active. Install found in Security & Node Checks."
        ((failed_checks++))
    fi
}

check_updates() {
    ((total_checks++))
    updates=$(apt list --upgradable 2>/dev/null | wc -l)
    if [ "$updates" -gt 1 ]; then
        print_check_result "FAIL" "$((updates-1)) pending system updates. Go to System Admin > Update system"
        ((failed_checks++))
    else
         print_check_result "PASS" "System up to date"
    fi
}

check_unattended_upgrades() {
    ((total_checks++))
    if dpkg -l | grep -q unattended-upgrades; then
        if grep -q "Unattended-Upgrade::Allowed-Origins" /etc/apt/apt.conf.d/50unattended-upgrades; then
            print_check_result "PASS" "Unattended upgrades configured"
        else
            print_check_result "WARN" "Unattended upgrades installed but not configured"
            ((warning_checks++))
        fi
    else
        print_check_result "FAIL" "Unattended upgrades not installed. Install found in Security & Node Checks."
        ((failed_checks++))
    fi
}

check_reboot_required() {
    ((total_checks++))
    if [ -f /var/run/reboot-required ]; then
        print_check_result "FAIL" "Restart needed to apply updates"
        ((failed_checks++))
    else
        print_check_result "PASS" "System reboot not required"
    fi
}

check_listening_ports() {
    ((total_checks++))
    open_ports=$(sudo ss -tunlp | grep -c -E 'LISTEN|UNCONN')
    if [ "$open_ports" -gt 0 ]; then
        print_check_result "INFO" "Local listening sockets (ss) — not inbound:"
        sudo ss -tunlp | grep -E 'LISTEN|UNCONN'
    else
        print_check_result "WARN" "No listening ports."
        ((warning_checks++))
    fi
}

check_resources() {
    print_check_result "INFO" "Resources:"
    # Memory check
    ((total_checks++))
    memory_usage=$(LC_NUMERIC=C free | LC_NUMERIC=C awk '/Mem/{printf("%.2f"), $3/$2*100}')
    if (( $(LC_NUMERIC=C echo "$memory_usage > $MEMORY_WARN" | bc -l) )); then
        print_check_result "WARN" "High memory usage: ${memory_usage}%"
        ((warning_checks++))
    else
        print_check_result "PASS" "Memory usage: ${memory_usage}%"
    fi

    # CPU check
    ((total_checks++))
    cpu_usage=$(LC_NUMERIC=C top -bn1 | LC_NUMERIC=C awk '/load/ {printf "%.2f", $(NF-2)}')
    cpu_cores=$(nproc)
    if (( $(LC_NUMERIC=C echo "$cpu_usage > $cpu_cores * $CPU_WARN / 100" | bc -l) )); then
        print_check_result "WARN" "High CPU load: ${cpu_usage}"
        ((warning_checks++))
    else
        print_check_result "PASS" "CPU load: ${cpu_usage}"
    fi

    # Disk check
    ((total_checks++))
    disk_usage=$(df / | awk '/\// {print $5}' | tr -d '%')
    if [ "$disk_usage" -gt $DISK_WARN ]; then
        print_check_result "WARN" "High disk usage: ${disk_usage}%"
        print_check_result "INFO" "If you need the EL to fit a ~2TB drive, review history expiry / prune flags (Execution Client → Suggest pruning parameters)."
        ((warning_checks++))
    else
        print_check_result "PASS" "Disk usage: ${disk_usage}%"
    fi
}

check_ssh_2fa() {
    ((total_checks++))
    if grep -q "auth required pam_google_authenticator.so" /etc/pam.d/sshd; then
        print_check_result "PASS" "SSH 2FA configured"
    else
        print_check_result "WARN" "SSH 2FA not configured. Install found in Security & Node Checks."
        ((warning_checks++))
    fi
}

check_ssh_key_presence() {
    ((total_checks++))
    found_keys=0
    problematic_perms=0

    # Check for root user's SSH key
    if [ -f /root/.ssh/authorized_keys ]; then
        if [ -s /root/.ssh/authorized_keys ]; then
            found_keys=1
            # Check permissions
            perms=$(stat -c %a /root/.ssh/authorized_keys)
            if [ "$perms" -ne 600 ] && [ "$perms" -ne 644 ]; then
                print_check_result "FAIL" "Root SSH key has insecure permissions: ${perms}. Change to 600."
                ((problematic_perms++))
            fi
        fi
    fi

    # Check all user home directories
    while IFS= read -r user_dir; do
        auth_file="${user_dir}/.ssh/authorized_keys"
        if [ -f "$auth_file" ]; then
            if [ -s "$auth_file" ]; then
                found_keys=1
                # Check permissions
                perms=$(stat -c %a "$auth_file")
                if [ "$perms" -ne 600 ] && [ "$perms" -ne 644 ]; then
                    print_check_result "FAIL" "Insecure permissions (${perms}) on ${auth_file}. Change to 600."
                    ((problematic_perms++))
                fi
            else
                print_check_result "FAIL" "Empty authorized_keys file in ${user_dir}/.ssh"
                ((failed_checks++))
            fi
        fi
    done < <(find /home -maxdepth 1 -type d)

    if [ $found_keys -eq 1 ]; then
        if [ $problematic_perms -eq 0 ]; then
            print_check_result "PASS" "🔑 SSH keys present with proper permissions"
        else
            ((failed_checks+=problematic_perms))
        fi
    else
        print_check_result "FAIL" "No SSH keys found in the authorized_keys file. Add your SSH public key to the file"
        ((failed_checks++))
    fi
}

check_chrony() {
    print_check_result "INFO" "Time synchronization:"
    ((total_checks++))
    chrony_installed=0
    conflicts_found=0

    # Check Chrony installation
    if command -v chronyc &> /dev/null; then
        chrony_installed=1
        print_check_result "PASS" "📥 Chrony is installed"
    else
        print_check_result "FAIL" "❌ Chrony not installed"
        ((failed_checks++))
    fi

    if [ $chrony_installed -eq 1 ]; then
        # Service status check
        ((total_checks++))
        if systemctl is-active --quiet chrony; then
            print_check_result "PASS" "🏃 Chrony service is running"
        else
            print_check_result "FAIL" "🛑 Chrony service is not running"
            ((failed_checks++))
        fi

        # Service enabled check
        ((total_checks++))
        if systemctl is-enabled --quiet chrony; then
            print_check_result "PASS" "⚡ Chrony service is enabled"
        else
            print_check_result "FAIL" "⚠️ Chrony service is not enabled on boot"
            ((failed_checks++))
        fi

        # Time sync status check
        ((total_checks++))
        if chronyc tracking | grep -q "Leap status\s*:\s*Normal"; then
            print_check_result "PASS" "🕺 Chrony time synchronization active"
        else
            print_check_result "FAIL" "⏳ Chrony not synchronized"
            ((failed_checks++))
        fi
    fi

    # Check for conflicting time services
    conflicting_services=("ntpd" "systemd-timesyncd")
    for service in "${conflicting_services[@]}"; do
        ((total_checks++))
        if systemctl is-active --quiet "$service" &> /dev/null; then
            print_check_result "FAIL" "Conflicting time service running: ${service}"
            ((failed_checks++))
            conflicts_found=1
        fi
        ((total_checks++))
        if systemctl is-enabled --quiet "$service" &> /dev/null; then
            print_check_result "FAIL" "Conflicting time service enabled: ${service}"
            ((failed_checks++))
            conflicts_found=1
        fi
    done

    if [ $conflicts_found -eq 0 ] && [ $chrony_installed -eq 1 ]; then
        print_check_result "PASS" "🕒 No conflicting time services detected"
    fi
}

check_charon_listening_port() {
    [[ -n "$charon_p2p_port" ]] || return 0
    ((total_checks+=1))
    if sudo ss -lnt | grep -qE "tcp.*:${charon_p2p_port}"; then
        print_check_result "PASS" "Detected TCP service on Charon P2P port ${charon_p2p_port}"
        if [ "$EUID" -eq 0 ]; then
            pid=$(sudo ss -lntup "sport = :${charon_p2p_port}" | awk -Fpid= '/users:/ {print $2}' | cut -d, -f1 | head -1)
            if [ -n "$pid" ]; then
                process=$(ps -p "$pid" -o comm=)
                echo -e "${YELLOW}          Process: ${process} (PID ${pid})${NC}"
            fi
        fi
    else
        print_check_result "FAIL" "Charon P2P port ${charon_p2p_port} (TCP) not listening"
        ((failed_checks++))
    fi
}

check_elcl_listening_ports() {
    ((total_checks+=2))
    detected=0
    declare -a p2p_protocols=("tcp" "udp")

    print_check_result "INFO" "Local listen (ss): execution & consensus on 9000 tcp/udp and 30303 tcp/udp — not inbound"
    # Check standard ports for other clients
    for port in "${p2p_ports[@]}"; do
        for proto in "${p2p_protocols[@]}"; do
            if sudo ss -lntu | grep -qE "${proto}.*:${port}"; then
                print_check_result "PASS" "Detected ${proto^^} service on port ${port}"
                ((detected++))
                if [ "$EUID" -eq 0 ]; then
                    pid=$(sudo ss -lntup "sport = :${port}" | awk -Fpid= '/users:/ {print $2}' | cut -d, -f1 | head -1)
                    if [ -n "$pid" ]; then
                        process=$(ps -p "$pid" -o comm=)
                        echo -e "${YELLOW}          Process: ${process} (PID ${pid})${NC}"
                    fi
                else
                    echo -e "${YELLOW}          Run as root to identify process${NC}"
                fi
            fi
        done
    done

    # QUIC UDP (9001/9091) is checked in check_cl_quic, not counted here.
    if [ $detected -gt 0 ]; then
        if [ $detected -eq "$ELCL_EXPECTED_LISTEN_COUNT" ]; then
            print_check_result "PASS" "Found all ${ELCL_EXPECTED_LISTEN_COUNT} expected ports (9000 tcp/udp, 30303 tcp/udp) for execution & consensus services"
        else
            print_check_result "FAIL" "Found ${detected} ports, expected ${ELCL_EXPECTED_LISTEN_COUNT} ports (9000 tcp/udp, 30303 tcp/udp) for execution & consensus services"
            ((failed_checks++))
        fi
    else
        print_check_result "FAIL" "No execution & consensus services detected on expected ports"
        ((failed_checks++))
    fi
    check_charon_listening_port
}

check_elcl_processes() {
    print_check_result "INFO" "Ethereum node processes:"
    # Additional check for running processes
    running_p2p=0
    ((total_checks++))
    for process in "${p2p_processes[@]}"; do
        if pgrep -f "$process" >/dev/null; then
            echo -e "${BLUE}${BOLD} 🔍 Detected Ethereum node process: ${process} ${NC}"
            ((running_p2p++))
        fi
    done

    if [ $running_p2p -gt 0 ]; then
        print_check_result "PASS" "Found ${running_p2p} Ethereum node processes running"
    else
        ((failed_checks++))
        print_check_result "FAIL" "No Ethereum node processes detected"
    fi
}

check_open_ports() {
    local tcp_ports udp_ports tcp_json requester open_list port missing=0

    configure_cl_quic_udp_check_ports
    tcp_ports="$tcp_check_ports"
    udp_ports="$udp_check_ports"

    print_check_result "INFO" "TCP inbound (public checker) vs local listen: a process bound on ss is not proof the Internet can dial you."
    print_check_result "INFO" "UDP inbound cannot be tested by a web checker (they only speak TCP). Expected UDP: ${udp_ports}."

    tcp_json="$(fetch_tcp_port_checker "$tcp_ports")"
    if ! jq -e 'type == "object"' <<< "$tcp_json" >/dev/null 2>&1; then
        total_checks=$((total_checks + 1))
        print_check_result "WARN" "Could not query the public TCP port checker. Local listen still applies; re-run later or use --troubleshoot."
        warning_checks=$((warning_checks + 1))
        return 0
    fi

    requester="$(tcp_checker_requester_ip "$tcp_json")"
    if [[ -n "$requester" ]]; then
        print_check_result "INFO" "Public TCP checker sees this host as ${requester}"
        case "$(classify_ipv4 "$requester")" in
            cgnat)
                total_checks=$((total_checks + 1))
                print_check_result "WARN" "Public address ${requester} is CGNAT (100.64.0.0/10). No IPv4 port-forward can work."
                warning_checks=$((warning_checks + 1))
                ;;
            private)
                total_checks=$((total_checks + 1))
                print_check_result "WARN" "Checker reported a non-public IPv4 (${requester}). Inbound IPv4 peers cannot dial that."
                warning_checks=$((warning_checks + 1))
                ;;
        esac
    fi

    open_list="$(tcp_checker_open_port_list "$tcp_json")"
    for port in ${tcp_ports//,/ }; do
        total_checks=$((total_checks + 1))
        if [[ -n "$open_list" ]] && grep -qx "$port" <<< "$open_list"; then
            print_check_result "PASS" "TCP inbound open on ${port} (Internet can complete a TCP handshake)"
        else
            print_check_result "FAIL" "TCP inbound closed on ${port}. Forward ${port}/tcp on the router and allow it in UFW."
            failed_checks=$((failed_checks + 1))
            missing=1
        fi
    done

    if [[ "$missing" -eq 1 ]]; then
        print_check_result "INFO" "TCP inbound miss is not a UDP/QUIC result. See the inbound troubleshoot notes after the peer-direction check."
    fi
}

check_peer_count() {
    local identity_json peers_json peer_count_json el_json
    local cl_connected el_connected
    local inbound outbound inbound_addrs outbound_addrs
    local in_json out_json peer_id disc first_ip kind quic_in=0
    local need_guide=0 peers_listed=0

    identity_json="$(fetch_cl_api /eth/v1/node/identity)"
    peers_json="$(fetch_cl_api /eth/v1/node/peers)"
    peer_count_json="$(fetch_cl_api /eth/v1/node/peer_count)"
    el_json="$(fetch_el_rpc net_peerCount)"

    cl_connected="$(jq -r '.data.connected // empty' <<< "$peer_count_json" 2>/dev/null || true)"
    el_connected="$(jq -r '.result // empty' <<< "$el_json" 2>/dev/null | awk '{printf "%d\n", $1}')"

    print_check_result "INFO" "Peer direction (Beacon API). Local listen is necessary; inbound peers prove the Internet can dial you."

    inbound="?"
    outbound="?"
    inbound_addrs=""
    outbound_addrs=""
    if ! jq -e '.data' <<< "$peers_json" >/dev/null 2>&1; then
        total_checks=$((total_checks + 1))
        print_check_result "FAIL" "Unable to list consensus peers. Is consensus.service running and REST reachable at ${API_BN_ENDPOINT}?"
        failed_checks=$((failed_checks + 1))
    elif ! cl_peers_report_direction "$peers_json"; then
        inbound="?"
        outbound="?"
        peers_listed=1
    else
        in_json="$(cl_connected_peers_json "$peers_json" inbound)"
        out_json="$(cl_connected_peers_json "$peers_json" outbound)"
        inbound="$(count_nonempty_lines "$in_json")"
        outbound="$(count_nonempty_lines "$out_json")"
        inbound_addrs="$(cl_peer_address_lines "$in_json")"
        outbound_addrs="$(cl_peer_address_lines "$out_json")"
        peers_listed=1
    fi

    peer_id="$(cl_identity_peer_id "$identity_json")"
    if [[ -n "$peer_id" ]]; then
        print_check_result "INFO" "CL peer ID: ${peer_id}"
    fi

    disc="$(cl_identity_discovery_address_lines "$identity_json")"
    first_ip="$(multiaddr_first_ip4 "$disc")"
    kind="$(classify_ipv4 "$first_ip")"
    case "$kind" in
        public)
            print_check_result "PASS" "CL discovery advertises a public IPv4 (port-forwards can work)"
            ;;
        cgnat)
            total_checks=$((total_checks + 1))
            print_check_result "WARN" "CL discovery advertises a CGNAT IPv4. IPv6 or a public IPv4 from the ISP is required."
            warning_checks=$((warning_checks + 1))
            ;;
        private)
            total_checks=$((total_checks + 1))
            print_check_result "WARN" "CL discovery advertises a private IPv4. Peers cannot dial that address."
            warning_checks=$((warning_checks + 1))
            ;;
        empty)
            print_check_result "INFO" "CL discovery has no IPv4 address yet (node may still be learning its external address)."
            ;;
    esac

    if printf '%s\n' "$disc" | grep -q '/quic'; then
        print_check_result "INFO" "CL discovery addresses include QUIC"
    elif cl_expects_quic; then
        print_check_result "INFO" "CL discovery addresses do not list QUIC yet (listen/UFW still required)"
    fi

    if [[ "$peers_listed" -eq 1 ]]; then
        total_checks=$((total_checks + 1))
        case "$(inbound_status_kind "$inbound")" in
            working)
                print_check_result "PASS" "Inbound working — ${inbound} peer(s) dialed this consensus client (you dialed ${outbound})"
                ;;
            unknown)
                print_check_result "WARN" "CL does not report peer direction, so inbound cannot be measured."
                warning_checks=$((warning_checks + 1))
                ;;
            *)
                if [[ "$outbound" =~ ^[0-9]+$ && "$outbound" -gt 0 ]]; then
                    print_check_result "WARN" "No inbound CL peers (you dialed ${outbound}). Local listen can still pass while the Internet cannot reach you."
                    warning_checks=$((warning_checks + 1))
                else
                    print_check_result "FAIL" "Consensus client has no peers. It may still be starting, or outbound UDP ${CL_P2P_PORT:-9000} is blocked too."
                    failed_checks=$((failed_checks + 1))
                fi
                ;;
        esac
    fi

    if [[ "$inbound" != "?" ]]; then
        print_peer_transport_table "$inbound_addrs" "$outbound_addrs"
        quic_in="$(count_multiaddr_matching "$inbound_addrs" '/quic')"
        if cl_expects_quic; then
            total_checks=$((total_checks + 1))
            if [[ "$quic_in" -gt 0 ]]; then
                print_check_result "PASS" "Inbound QUIC present — ${quic_in} inbound peer address(es) use QUIC"
            elif [[ "$inbound" =~ ^[0-9]+$ && "$inbound" -gt 0 ]]; then
                print_check_result "WARN" "Peers dialed you over TCP only. After Glamsterdam, QUIC UDP (typically ${CL_P2P_PORT_2:-9001}/udp) must be forwarded and allowed."
                warning_checks=$((warning_checks + 1))
            else
                print_check_result "INFO" "No inbound QUIC peers yet. Forward and allow UDP ${CL_P2P_PORT_2:-9001} (and discv5 UDP ${CL_P2P_PORT:-9000})."
            fi
        fi
    fi

    total_checks=$((total_checks + 1))
    if [[ -n "$el_connected" && "$el_connected" -gt 0 ]]; then
        print_check_result "PASS" "Execution layer connected peers: ${el_connected}"
    else
        print_check_result "FAIL" "Execution layer connected peers: ${el_connected:-0}. Check execution.service and ${EL_P2P_PORT:-30303} TCP/UDP."
        failed_checks=$((failed_checks + 1))
    fi

    if [[ -n "$cl_connected" ]]; then
        print_check_result "INFO" "Consensus layer connected peers (API count): ${cl_connected}"
    fi

    if [[ "$NODE_CHECKER_TROUBLESHOOT" -eq 1 ]]; then
        need_guide=1
    elif [[ "$inbound" == "0" || "$inbound" == "?" ]]; then
        need_guide=1
    elif cl_expects_quic && [[ "$quic_in" -eq 0 ]]; then
        need_guide=1
    fi

    if [[ "$need_guide" -eq 1 ]]; then
        print_port_troubleshoot_guidance "$inbound" "$outbound"
    fi

    if [[ "$NODE_CHECKER_DEBUG" -eq 1 ]]; then
        print_port_debug_diagnostics "$identity_json" "$peers_json"
    fi
}

check_systemd_services() {
check_elcl_processes
echo
    print_check_result "INFO" "Systemd Services:"
    for service in "${services[@]}"; do
        service_installed=0
        ((total_checks+=3))  # Three checks per service (installed + active + enabled)

        # Print service header
        echo -e "\n${BLUE}${BOLD}📦 ${service^} Service${NC}"
        echo -e "${BLUE}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

        # Check installation
        if [ -f /etc/systemd/system/"${service}".service ]; then
            service_installed=1
            echo -e "${GREEN}[PASS] 📥 Installed${NC}"
        else
            echo -e "${BLUE}${BOLD}[INFO] ❌ Not installed${NC}"
        fi

        if [ $service_installed -eq 1 ]; then
            # Check if service is active
            if systemctl is-active --quiet "$service"; then
                echo -e "${GREEN}[PASS] 🏃 Running${NC}"
            else
                echo -e "${BLUE}${BOLD}[INFO] 🛑 Not running${NC}"
            fi

            # Check if service is enabled
            if systemctl is-enabled --quiet "$service"; then
                echo -e "${GREEN}[PASS] ⚡ Enabled${NC}"
            else
                echo -e "${BLUE}${BOLD}[INFO] ⚠️ Not enabled, will not autostart at boot. To change, go to System Administration.${NC}"
            fi
        fi
    done
}

check_execution_version() {
    [[ ! -f /etc/systemd/system/execution.service ]] && return
    EL=$(grep "Description=" /etc/systemd/system/execution.service | awk -F'=' '{print $2}' | awk '{print $1}')
    tag_url=${client_github_url["$EL"]}
    name="Execution client ($EL)"

    if [[ -z "$tag_url" ]]; then
      print_check_result "FAIL" "$name no GitHub URL mapping found"
      ((failed_checks++))
      return
    fi
    check_client_version "$name" "$tag_url"
}

check_consensus_version() {
    [[ ! -f /etc/systemd/system/consensus.service ]] && return
    CL=$(grep "Description=" /etc/systemd/system/consensus.service | awk -F'=' '{print $2}' | awk '{print $1}')
    tag_url=${client_github_url["$CL"]}
    name="Consensus client ($CL)"

    check_client_version "$name" "$tag_url"
}

check_validator_version() {
    [[ ! -f /etc/systemd/system/validator.service ]] && return
    VAL=$(grep "Description=" /etc/systemd/system/validator.service | awk -F'=' '{print $2}' | awk '{print $1}')
    tag_url=${client_github_url["$VAL"]}
    name="Validator client ($VAL)"

    check_client_version "$name" "$tag_url"
}

check_client_version() {
  ((total_checks++))
  local name=$1 tag_url=$2
  # Validate mapping
  if [[ -z "$tag_url" ]]; then
    print_check_result "FAIL" "$name no GitHub URL mapping found"
    ((failed_checks++))
    return
  fi

  if [[ "$name" =~ "Consensus" || "$name" =~ "Validator" ]]; then
    version=$(curl -s -X GET "${API_BN_ENDPOINT}/eth/v1/node/version" \
      -H "accept: application/json" \
      | jq -r '.data.version' \
      | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+')
  else
    version=$(curl -s -X POST \
      -H "Content-Type: application/json" \
      --data '{"jsonrpc":"2.0","method":"web3_clientVersion","params":[],"id":2}' \
      "${EL_RPC_ENDPOINT}" \
      | jq -r '.result' \
      | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+')
  fi

  if [[ -z $version ]]; then
    print_check_result "FAIL" "$name not running or unable to query version"
    ((failed_checks++))
    return
  fi

  latest=$(curl -s "$tag_url" \
    | jq -r .tag_name \
    | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')

  if [[ -n "$latest" && "${version#v}" == "${latest#v}" ]]; then
    print_check_result "PASS" "$name version: $version (latest)"
  else
    print_check_result "WARN" "$name version: $version (latest: $latest)"
    ((warning_checks++))
  fi
}

check_mevboost_version() {
    ((total_checks++))
    if command -v mev-boost &> /dev/null; then
        MEV_VERSION=$(mev-boost --version 2>&1 | sed 's/.*\s\([0-9]*\.[0-9]*\).*/\1/')
        if [[ $MEV_VERSION ]]; then
            # Get latest version
            TAG_URL=${client_github_url["mev-boost"]}
            LATEST_VERSION=$(curl -s "$TAG_URL" | jq -r .tag_name | sed 's/.*v\([0-9]*\.[0-9]*\).*/\1/')
            
            if [[ -n "$LATEST_VERSION" && "${MEV_VERSION#v}" == "${LATEST_VERSION#v}" ]]; then
                print_check_result "PASS" "MEV-Boost version: $MEV_VERSION (latest)"
            else
                print_check_result "WARN" "MEV-Boost version: $MEV_VERSION (latest: $LATEST_VERSION)"
                ((warning_checks++))
            fi
        else
            print_check_result "FAIL" "MEV-Boost installed but unable to query version"
            ((failed_checks++))
        fi
    else
        print_check_result "WARN" "MEV-Boost not installed"
        ((warning_checks++))
    fi
}

check_charon_version() {
    if ! isCharonEnabled; then
        return 0
    fi
    ((total_checks++))
    if getCharonCurrentVersion; then
        TAG_URL=${client_github_url["Charon"]}
        LATEST_VERSION=$(curl -s "$TAG_URL" | jq -r .tag_name 2>/dev/null || true)
        if [[ -n "$LATEST_VERSION" && "${VERSION}" == "${LATEST_VERSION}" ]]; then
            print_check_result "PASS" "Charon version: ${VERSION} (latest)"
        elif [[ -n "$LATEST_VERSION" ]]; then
            print_check_result "WARN" "Charon version: ${VERSION} (latest: ${LATEST_VERSION})"
            ((warning_checks++))
        else
            print_check_result "PASS" "Charon version: ${VERSION}"
        fi
    else
        print_check_result "FAIL" "Charon installed but unable to query version"
        ((failed_checks++))
    fi
}

check_history_expiry() {
    local helper="${ETHPILLAR_ROOT}/helpers/history_expiry_suggestions.sh"
    local unit="${EXEC_SERVICE_FILE:-/etc/systemd/system/execution.service}"
    local cl_unit="${CONSENSUS_SERVICE_FILE:-/etc/systemd/system/consensus.service}"
    local unit_text="" cl_text="" description execstart client status level summary
    [[ -f "$helper" ]] || return 0
    # shellcheck disable=SC1091
    source "$helper"
    [[ -f "$unit" ]] || return 0

    ((total_checks++))
    unit_text=$(history_expiry_read_unit "$unit" 2>/dev/null || true)
    cl_text=$(history_expiry_read_unit "$cl_unit" 2>/dev/null || true)
    description=$(history_expiry_extract_description "$unit_text")
    execstart=$(history_expiry_extract_execstart "$unit_text")
    client=$(history_expiry_detect_client "$description" "$execstart")
    status=$(history_expiry_status "$client" "$execstart")
    level=$(history_expiry_checker_level "$status")
    summary=$(history_expiry_checker_summary "$status" "$client")
    print_check_result "$level" "$summary"
    case "$level" in
        WARN) ((warning_checks++)) ;;
    esac
    # Never FAIL: archive / Caplin archive / missing flags stay WARN or INFO.
    if [[ "$status" == "missing" || "$status" == "archive" || "$status" == "caplin_archive" ]]; then
        history_expiry_print_checker_detail "$client" "$status"
    fi
}

check_noatime() {
    ((total_checks++))
    if grep -q "noatime" /etc/fstab; then
        print_check_result "PASS" "noatime is active"
    else
        print_check_result "FAIL" "noatime is not active. To change, use Toolbox."
        ((failed_checks++))
    fi
}

check_swappiness() {
    ((total_checks++))
    swappiness=$(cat /proc/sys/vm/swappiness)
    if [ "$swappiness" -le 10 ] ; then
        print_check_result "PASS" "swappiness is good. value is $swappiness "
    else
        print_check_result "FAIL" "swappiness is not optimized. value is $swappiness. To change, use Toolbox."
        ((failed_checks++))
    fi
}

print_system_information() {
    print_section_header "System Information"

    # Get system information
    os_name=$(grep PRETTY_NAME /etc/os-release | cut -d'"' -f2)
    hostname=$(uname -n)
    kernel=$(uname -r)
    uptime=$(uptime -p | sed 's/up //')
    uptime_since=$(uptime -s)
    cpu_name=$(grep "model name" /proc/cpuinfo | head -1 | cut -d':' -f2 | xargs)
    cores=$(nproc)
    freq=$(awk -F: ' /cpu MHz/ {freq=$2} END {print freq}' /proc/cpuinfo | xargs)
    load=$(uptime | awk -F'load average:' '{print $2}' | xargs)
    ip=$(hostname -I | awk '{print $1}')
    ram_total=$(free -b | awk '/Mem/{printf "%.2f GB", $2/1024/1024/1024}')
    swap=$(free -b | awk '/Swap/{printf "%.2f GB", $2/1024/1024/1024}')
    disk_total=$(df -h / | awk 'NR==2 {print $2}')
    io=$( (dd if=/dev/zero of=test_$$ bs=64k count=16k conv=fdatasync && rm -f test_$$ ) 2>&1 | awk -F, '{io=$NF} END {print io}' )
    case "$(uname -m)" in
        x86_64|amd64) arch="amd64" ;;
        aarch64|arm64) arch="arm64" ;;
        *) arch="Unknown architecture"
    esac

     # Output Display with better formatting
    printf "${PURPLE}%-20s${NC} %s\n" "OS Name:" "$os_name"
    printf "${PURPLE}%-20s${NC} %s\n" "Hostname:" "$hostname"
    printf "${PURPLE}%-20s${NC} %s\n" "Kernel Version:" "$kernel"
    printf "${PURPLE}%-20s${NC} %s\n" "Uptime:" "$uptime"
    printf "${PURPLE}%-20s${NC} %s\n" "Since:" "$uptime_since"
    printf "${PURPLE}%-20s${NC} %s\n" "CPU Model:" "$cpu_name"
    printf "${PURPLE}%-20s${NC} %s\n" "CPU Cores:" "$cores"
    printf "${PURPLE}%-20s${NC} %s MHz\n" "CPU Speed:" "$freq"
    printf "${PURPLE}%-20s${NC} %s\n" "Architecture:" "$arch"
    printf "${PURPLE}%-20s${NC} %s\n" "Load Average:" "$load"
    printf "${PURPLE}%-20s${NC} %s\n" "IP Address:" "$ip"
    printf "${PURPLE}%-20s${NC} %s\n" "Total RAM:" "$ram_total"
    printf "${PURPLE}%-20s${NC} %s\n" "Swap:" "$swap"
    printf "${PURPLE}%-20s${NC} %s\n" "Disk Space:" "$disk_total"
    printf "${PURPLE}%-20s${NC} %s\n" "I/O Speed:" "$io"
}

node_checker_main() {
    if [ "$EUID" -ne 0 ]; then
        print_check_result "WARN" "Some checks require root privileges"
    fi

    local start_time end_time duration
    start_time=$(date +%s)
    echo -e "\n${YELLOW}${BOLD}=== Starting Node Security Scanner and Health Checkup ===${NC}\n"
    display_banner

    # Execute checks
    print_section_header "Security Checks"

    # Network Security
    print_check_result "INFO" "Network Security:"
    check_firewall
    check_fail2ban
    echo
    # SSH Security
    print_check_result "INFO" "SSH Security:"
    check_ssh_key_presence
    check_ssh_keys
    check_ssh_port
    check_ssh_2fa
    echo
    # System Updates
    print_check_result "INFO" "System Updates:"
    check_updates
    check_unattended_upgrades
    check_reboot_required

    print_section_header "Node Health Checks"
    check_listening_ports
    echo
    check_elcl_listening_ports
    check_cl_quic
    echo
    check_open_ports
    echo
    check_peer_count
    echo
    check_systemd_services

    print_section_header "Client Version Checks"
    check_execution_version
    check_consensus_version
    check_validator_version
    check_charon_version
    check_mevboost_version

    print_section_header "Performance Checks"
    check_resources
    echo
    print_check_result "INFO" "History expiry / prune (suitable for a ~2TB drive):"
    check_history_expiry
    echo
    check_chrony
    echo
    print_check_result "INFO" "Tuning:"
    check_swappiness
    check_noatime

    print_system_information

    # Summary
    print_section_header "Summary"
    printf "${BLUE}${BOLD}%-20s${NC} %d\n" "Total checks:" "$total_checks"
    printf "${GREEN}%-20s${NC} %d\n" "Passed checks:" "$((total_checks - failed_checks - warning_checks))"
    printf "${YELLOW}%-20s${NC} %d\n" "Warning checks:" "$warning_checks"
    printf "${RED}%-20s${NC} %d\n" "Failed checks:" "$failed_checks"

    # Duration
    end_time=$(date +%s)
    duration=$((end_time - start_time))
    echo -e "\n${YELLOW}${BOLD}Duration: $duration seconds${NC}"
    echo -e "\n${GREEN}${BOLD}=== Node Checker Complete: Press enter to exit ===${NC}"
    read -r
}

# Allow sourcing for bats tests without auto-running the interactive scanner.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    node_checker_parse_args "$@"
    node_checker_main
fi
