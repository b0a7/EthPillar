"""Tests for manage.service_parse canonicalize / ExecStart helpers."""

from manage.service_parse import (
    canonicalize_unit,
    get_flag_value,
    has_flag,
    normalize_cli_args,
    parse_description_client,
    parse_description_network,
    parse_exec_start,
    parse_unit,
    semantic_equal,
)


SAMPLE_UNIT = """[Unit]
Description=Lighthouse Consensus Client service for MAINNET
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=consensus
Group=consensus
Restart=on-failure
RestartSec=3
KillSignal=SIGINT
TimeoutStopSec=900
ExecStart=/usr/local/bin/lighthouse bn \\
    --network=mainnet \\
    --http-port=5052 \\
    --builder http://127.0.0.1:18550 \\
    --port=9000

[Install]
WantedBy=multi-user.target
"""


def test_parse_description_client_and_network():
    assert parse_description_client("Lighthouse Consensus Client service for MAINNET") == "Lighthouse"
    assert parse_description_network("Lighthouse Consensus Client service for MAINNET") == "mainnet"
    assert parse_description_client("MEV-Boost Service for HOODI") == "MEV-Boost"
    assert parse_description_network("MEV-Boost Service for HOODI") == "hoodi"


def test_parse_exec_start_multiline():
    start, end, args = parse_exec_start(SAMPLE_UNIT)
    assert start >= 0
    assert end > start
    assert args[0] == "/usr/local/bin/lighthouse bn"
    assert "--network=mainnet" in args
    assert "--builder http://127.0.0.1:18550" in args


def test_normalize_space_separated_flag_value():
    args = ["/usr/local/bin/lighthouse bn", "--builder http://127.0.0.1:18550", "--http-port=5052"]
    normalized = normalize_cli_args(args)
    assert normalized[0] == "/usr/local/bin/lighthouse bn"
    assert "--builder=http://127.0.0.1:18550" in normalized
    assert "--http-port=5052" in normalized


def test_canonicalize_ignores_flag_order():
    a = SAMPLE_UNIT
    # Swap two flag lines
    b = SAMPLE_UNIT.replace(
        "    --http-port=5052 \\\n    --builder http://127.0.0.1:18550 \\\n    --port=9000",
        "    --port=9000 \\\n    --builder http://127.0.0.1:18550 \\\n    --http-port=5052",
    )
    assert semantic_equal(a, b)
    assert canonicalize_unit(a) == canonicalize_unit(b)


def test_canonicalize_detects_missing_flag():
    without_builder = SAMPLE_UNIT.replace("    --builder http://127.0.0.1:18550 \\\n", "")
    assert not semantic_equal(SAMPLE_UNIT, without_builder)


def test_get_flag_value_and_has_flag():
    unit = parse_unit(SAMPLE_UNIT)
    assert get_flag_value(unit.exec_args, "--http-port") == "5052"
    assert get_flag_value(unit.exec_args, "--builder") == "http://127.0.0.1:18550"
    assert has_flag(unit.exec_args, "--builder")
    assert not has_flag(unit.exec_args, "--not-a-real-flag")
