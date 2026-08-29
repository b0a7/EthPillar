"""Tests for deploy/charon.py beacon-endpoint patching and CDVN .env import."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deploy.charon import (
    generate_charon_service,
    import_cdvn_env_to_service,
    parse_dotenv,
    parse_p2p_tcp_port,
    patch_beacon_endpoints,
    plan_cdvn_env_import,
    rewrite_docker_url,
    scrape_beacon_endpoints,
)

CHARON_UNIT = generate_charon_service("mainnet", "http://127.0.0.1:5052", builder_api=True)


def test_scrape_beacon_endpoints():
    assert scrape_beacon_endpoints(CHARON_UNIT) == "http://127.0.0.1:5052"


def test_patch_beacon_endpoints_updates_url(tmp_path):
    service_path = tmp_path / "charon.service"
    service_path.write_text(CHARON_UNIT, encoding="utf-8")
    assert patch_beacon_endpoints(str(service_path), "http://192.168.1.20:5052")
    updated = service_path.read_text(encoding="utf-8")
    assert "--beacon-node-endpoints=http://192.168.1.20:5052" in updated
    assert "http://127.0.0.1:5052" not in updated
    assert "--builder-api" in updated


def test_patch_beacon_endpoints_no_change(tmp_path):
    service_path = tmp_path / "charon.service"
    service_path.write_text(CHARON_UNIT, encoding="utf-8")
    assert patch_beacon_endpoints(str(service_path), "http://127.0.0.1:5052") is False


def test_patch_beacon_endpoints_missing_file():
    with pytest.raises(FileNotFoundError):
        patch_beacon_endpoints("/tmp/does-not-exist-charon.service", "http://127.0.0.1:5052")


def test_parse_dotenv_strips_comments_and_quotes(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "NETWORK=mainnet\n"
        "CHARON_BEACON_NODE_ENDPOINTS=\"http://lighthouse:5052\"\n"
        "CHARON_LOG_LEVEL=info # trailing\n"
        "BUILDER_API_ENABLED=true\n",
        encoding="utf-8",
    )
    parsed = parse_dotenv(str(env_file))
    assert parsed["NETWORK"] == "mainnet"
    assert parsed["CHARON_BEACON_NODE_ENDPOINTS"] == "http://lighthouse:5052"
    assert parsed["CHARON_LOG_LEVEL"] == "info"
    assert parsed["BUILDER_API_ENABLED"] == "true"


def test_rewrite_docker_url():
    url, warn = rewrite_docker_url("http://lighthouse:5052")
    assert url == "http://127.0.0.1:5052"
    assert warn is not None
    url2, warn2 = rewrite_docker_url("http://192.168.1.8:5052")
    assert url2 == "http://192.168.1.8:5052"
    assert warn2 is None


def test_parse_p2p_tcp_port_from_service_content():
    unit = generate_charon_service(
        "mainnet",
        "http://127.0.0.1:5052",
        p2p_tcp_address="0.0.0.0:3812",
    )
    assert parse_p2p_tcp_port(unit) == 3812
    assert parse_p2p_tcp_port("") == 3610
    assert parse_p2p_tcp_port("[Service]\nExecStart=charon run", default=3610) == 3610


def test_plan_cdvn_env_import_maps_core_flags():
    plan = plan_cdvn_env_import(
        {
            "NETWORK": "hoodi",
            "CHARON_BEACON_NODE_ENDPOINTS": "http://host.docker.internal:5052",
            "BUILDER_API_ENABLED": "true",
            "CHARON_PORT_P2P_TCP": "3610",
            "CHARON_VALIDATOR_API_ADDRESS": "0.0.0.0:3600",
            "CHARON_MONITORING_ADDRESS": "0.0.0.0:3620",
            "CHARON_P2P_RELAYS": "https://0.relay.obol.tech",
            "CHARON_LOG_LEVEL": "debug",
            "CHARON_LOKI_ADDRESSES": "http://loki:3100",
            "CHARON_FEATURE_SET_ENABLE": "json_requests",
        }
    )
    assert plan.network == "hoodi"
    assert plan.beacon_node_endpoints == "http://127.0.0.1:5052"
    assert plan.builder_api is True
    assert plan.p2p_tcp_address == "0.0.0.0:3610"
    assert plan.validator_api_address == "127.0.0.1:3600"
    assert plan.monitoring_address == "127.0.0.1:3620"
    assert plan.feature_set_enable == "json_requests"
    assert "--p2p-relays=https://0.relay.obol.tech" in plan.extra_args
    assert "--log-level=debug" in plan.extra_args
    assert any(r.kind == "skipped" and "LOKI" in r.env_line for r in plan.rows)

    unit = plan.service_content()
    assert "--beacon-node-endpoints=http://127.0.0.1:5052" in unit
    assert "--builder-api" in unit
    assert "--feature-set-enable=json_requests" in unit
    assert "--log-level=debug" in unit


def test_import_preview_panes_are_line_aligned(tmp_path):
    from deploy.charon import write_import_preview_panes

    plan = plan_cdvn_env_import(
        {
            "NETWORK": "mainnet",
            "CHARON_BEACON_NODE_ENDPOINTS": "http://lighthouse:5052",
            "BUILDER_API_ENABLED": "true",
        }
    )
    left, right = write_import_preview_panes(plan, str(tmp_path))
    left_lines = (tmp_path / "01_cdvn.env").read_text(encoding="utf-8").splitlines()
    right_lines = (tmp_path / "02_systemd.flags").read_text(encoding="utf-8").splitlines()
    assert left == str(tmp_path / "01_cdvn.env")
    assert right == str(tmp_path / "02_systemd.flags")
    assert len(left_lines) == len(right_lines)
    assert any("CHARON_BEACON_NODE_ENDPOINTS=" in line for line in left_lines)
    assert any("--beacon-node-endpoints=http://127.0.0.1:5052" in line for line in right_lines)


def test_import_cdvn_env_apply(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "NETWORK=sepolia\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://192.168.1.50:5052\n"
        "BUILDER_API_ENABLED=false\n",
        encoding="utf-8",
    )
    service_path = tmp_path / "charon.service"
    plan = import_cdvn_env_to_service(
        str(env_file),
        service_path=str(service_path),
        apply=True,
    )
    assert plan.network == "sepolia"
    assert service_path.is_file()
    content = service_path.read_text(encoding="utf-8")
    assert "SEPOLIA" in content
    assert "--beacon-node-endpoints=http://192.168.1.50:5052" in content
    assert "--builder-api" not in content


def test_plan_derives_bn_from_cl_when_unset():
    plan = plan_cdvn_env_import({"CL": "cl-lighthouse", "NETWORK": "mainnet"})
    assert plan.beacon_node_endpoints == "http://127.0.0.1:5052"
    assert any("derived from CL" in w for w in plan.warnings)


def test_resolve_cdvn_checkout_directory(tmp_path):
    from deploy.charon import resolve_cdvn_checkout

    root = tmp_path / "cdvn"
    root.mkdir()
    (root / ".env").write_text("NETWORK=mainnet\n", encoding="utf-8")
    charon = root / ".charon"
    charon.mkdir()
    (charon / "cluster-lock.json").write_text("{}", encoding="utf-8")
    keys = charon / "validator_keys"
    keys.mkdir()
    (keys / "keystore-0.json").write_text("{}", encoding="utf-8")
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    info = resolve_cdvn_checkout(str(root))
    assert info["root"] == str(root)
    assert info["env_path"] == str(root / ".env")
    assert info["charon_dir"] == str(charon)
    assert info["has_lock"] is True
    assert info["has_keyshares"] is True
    assert info["compose_file"] == str(root / "docker-compose.yml")


def test_resolve_cdvn_checkout_from_env_file(tmp_path):
    from deploy.charon import resolve_cdvn_checkout

    root = tmp_path / "cdvn"
    root.mkdir()
    env = root / ".env"
    env.write_text("NETWORK=hoodi\n", encoding="utf-8")
    info = resolve_cdvn_checkout(str(env))
    assert info["root"] == str(root)
    assert info["env_path"] == str(env)
    assert info["charon_dir"] is None


def test_copy_charon_cluster_and_skip(tmp_path):
    from deploy.charon import copy_charon_cluster

    src = tmp_path / "src" / ".charon"
    src.mkdir(parents=True)
    (src / "cluster-lock.json").write_text('{"cluster":1}', encoding="utf-8")
    (src / "validator_keys").mkdir()
    (src / "validator_keys" / "keystore-0.json").write_text("{}", encoding="utf-8")

    dest = tmp_path / "dest" / ".charon"
    result = copy_charon_cluster(str(src), str(dest))
    assert result["status"] == "copied"
    assert (dest / "cluster-lock.json").is_file()
    assert (dest / "validator_keys" / "keystore-0.json").is_file()

    skipped = copy_charon_cluster(str(src), str(dest), force=False)
    assert skipped["status"] == "skipped"

    (src / "cluster-lock.json").write_text('{"cluster":2}', encoding="utf-8")
    forced = copy_charon_cluster(str(src), str(dest), force=True)
    assert forced["status"] == "copied"
    assert (dest / "cluster-lock.json").read_text(encoding="utf-8") == '{"cluster":2}'
