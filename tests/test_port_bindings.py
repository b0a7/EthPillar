"""Unit tests for integration port binding helpers."""
from tests.integration.port_bindings import (
    check_port_scope,
    cl_enables_quic_by_default,
    cl_supports_rpc_expose,
    default_port_expectations,
    expected_cl_quic_unit_flag,
    parse_ss_listeners,
    PortBinding,
)


SS_SAMPLE = """
Netid State Recv-Q Send-Q Local Address:Port Peer Address:Port
udp   UNCONN 0      0      0.0.0.0:30303      0.0.0.0:*
udp   UNCONN 0      0      0.0.0.0:9000       0.0.0.0:*
udp   UNCONN 0      0      0.0.0.0:9001       0.0.0.0:*
tcp   LISTEN 0      4096   127.0.0.1:8545     0.0.0.0:*
tcp   LISTEN 0      4096   127.0.0.1:8551     0.0.0.0:*
tcp   LISTEN 0      4096   127.0.0.1:5052     0.0.0.0:*
tcp   LISTEN 0      4096   0.0.0.0:30303      0.0.0.0:*
tcp   LISTEN 0      4096   0.0.0.0:9000       0.0.0.0:*
"""


def test_parse_ss_listeners_extracts_addresses_and_ports():
    bindings = parse_ss_listeners(SS_SAMPLE)
    assert PortBinding("tcp", "127.0.0.1", 8545) in bindings
    assert PortBinding("udp", "0.0.0.0", 30303) in bindings


def test_check_port_scope_localhost_and_public():
    bindings = parse_ss_listeners(SS_SAMPLE)
    ok, _ = check_port_scope(bindings, 8545, "localhost", label="EL RPC")
    assert ok
    ok, _ = check_port_scope(bindings, 30303, "public", protocols=("tcp", "udp"), label="EL P2P")
    assert ok
    ok, _ = check_port_scope(bindings, 9001, "public", protocols=("udp",), label="CL QUIC")
    assert ok


def test_cl_supports_rpc_expose_includes_grandine():
    assert cl_supports_rpc_expose("Grandine")


def test_cl_enables_quic_by_default_covers_deployed_beacon_clients():
    for name in ("Lighthouse", "Teku", "Nimbus", "Lodestar", "Grandine", "Prysm"):
        assert cl_enables_quic_by_default(name)
    assert not cl_enables_quic_by_default("Caplin")


def test_expected_cl_quic_unit_flag_pins_ethpillar_flags():
    assert expected_cl_quic_unit_flag("Lighthouse", 9001) == "--quic-port=9001"
    assert expected_cl_quic_unit_flag("Nimbus", 9001) == "--quic-port=9001"
    assert expected_cl_quic_unit_flag("Grandine", 9001) == "--quic-port=9001"
    assert expected_cl_quic_unit_flag("Lodestar", 9001) == "--quicPort=9001"
    assert expected_cl_quic_unit_flag("Prysm", 9001) == "--p2p-quic-port=9001"
    assert expected_cl_quic_unit_flag("Teku", 9001) is None


def test_default_port_expectations_include_cl_quic_when_requested():
    items = default_port_expectations(has_consensus=True, expect_cl_quic=True)
    quic = [i for i in items if i.label == "CL QUIC"]
    assert len(quic) == 1
    assert quic[0].port == 9001
    assert quic[0].protocols == ("udp",)
    assert quic[0].scope == "public"

    without = default_port_expectations(has_consensus=True, expect_cl_quic=False)
    assert all(i.label != "CL QUIC" for i in without)


def test_check_port_scope_detects_public_rpc_binding():
    bindings = [
        PortBinding("tcp", "0.0.0.0", 8545),
    ]
    ok, message = check_port_scope(bindings, 8545, "localhost", label="EL RPC")
    assert not ok
    assert "expected localhost only" in message


def test_check_port_scope_accepts_ipv4_mapped_addresses():
    bindings = [
        PortBinding("tcp", "[::ffff:127.0.0.1]", 8545),
        PortBinding("tcp", "[::ffff:0.0.0.0]", 30303),
        PortBinding("udp", "[::ffff:0.0.0.0]", 30303),
        PortBinding("tcp", "172.17.0.2", 30303),
    ]
    ok, _ = check_port_scope(bindings, 8545, "localhost", label="EL RPC")
    assert ok
    ok, _ = check_port_scope(bindings, 30303, "public", protocols=("tcp", "udp"), label="EL P2P")
    assert ok
