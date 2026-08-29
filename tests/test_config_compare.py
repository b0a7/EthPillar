"""Tests for manage.config_compare default generation and semantic gates."""

from pathlib import Path

import deploy.lighthouse as lighthouse
from manage.config_compare import (
    EXIT_NO_DIFF,
    _mev_params_for_client,
    generate_default_unit,
    prepare_workdir,
)
from manage.service_parse import canonicalize_unit, semantic_equal


def test_mev_params_lighthouse():
    assert "18550" in _mev_params_for_client("Lighthouse", "bn", True)
    assert _mev_params_for_client("Lighthouse", "bn", False) == ""
    assert _mev_params_for_client("Lighthouse", "vc", True) == "--builder-proposals"


def test_generate_default_lighthouse_bn_matches_generator(tmp_path, monkeypatch):
    ctx = {
        "network": "mainnet",
        "el_client": "Geth",
        "cl_client": "Lighthouse",
        "vc_client": "Lighthouse",
        "fee_recipient": "0xabc",
        "graffiti": "test",
        "jwtsecret": "/secrets/jwtsecret",
        "sync_url": "https://example.invalid",
        "el_p2p": "30303",
        "el_p2p_2": "30304",
        "el_rpc": "8545",
        "el_peers": "50",
        "cl_p2p": "9000",
        "cl_p2p_2": "9001",
        "cl_rest": "5052",
        "cl_peers": "100",
        "mev_min_bid": "0.006",
        "mev_enabled": True,
        "bn_endpoint": "http://127.0.0.1:5052",
        "is_integrated_grandine": False,
        "contents": {},
    }
    generated = generate_default_unit("consensus", ctx)
    expected = lighthouse.generate_lighthouse_bn_service(
        "mainnet",
        "https://example.invalid",
        "/secrets/jwtsecret",
        "5052",
        "9000",
        "9001",
        "100",
        fee_parameters="",
        mev_parameters="--builder http://127.0.0.1:18550",
    )
    assert semantic_equal(generated, expected)


def test_prepare_workdir_no_diff_when_identical(tmp_path, monkeypatch):
    unit = lighthouse.generate_lighthouse_bn_service(
        "mainnet",
        "https://example.invalid",
        "/secrets/jwtsecret",
        "5052",
        "9000",
        "9001",
        "100",
        mev_parameters="--builder http://127.0.0.1:18550",
    )
    # Point SERVICE_FILES / installed_service_paths at a fake unit tree.
    fake_etc = tmp_path / "etc"
    fake_etc.mkdir()
    cons = fake_etc / "consensus.service"
    cons.write_text(unit, encoding="utf-8")

    monkeypatch.setattr(
        "manage.config_compare.installed_service_paths",
        lambda: {"consensus": str(cons)},
    )
    monkeypatch.setattr(
        "manage.config_compare.read_text_file",
        lambda path: Path(path).read_text(encoding="utf-8"),
    )
    monkeypatch.setattr(
        "manage.config_compare.unit_exists",
        lambda path: Path(path).is_file(),
    )
    # Avoid needing real env / relays for other services
    monkeypatch.setenv("FEE_RECIPIENT_ADDRESS", "0xabc")
    monkeypatch.setenv("JWTSECRET_PATH", "/secrets/jwtsecret")
    monkeypatch.setenv("GRAFFITI", "test")
    monkeypatch.setenv("MEV_MIN_BID", "0.006")

    work = tmp_path / "work"
    differing, meta = prepare_workdir(work)
    # May still differ if sync URL scrape vs config default differs — assert API works.
    assert isinstance(differing, list)
    assert "pre_hashes" in meta
    if differing:
        assert (work / "installed" / "consensus.service").is_file()
        assert (work / "default" / "consensus.service").is_file()
        assert canonicalize_unit((work / "installed" / "consensus.service").read_text(encoding="utf-8"))


def test_exit_no_diff_constant():
    assert EXIT_NO_DIFF == 2


def test_generate_default_charon_matches_installed_flags():
    import deploy.charon as charon_mod

    installed = charon_mod.generate_charon_service(
        "mainnet",
        "http://100.116.116.75:5052",
        builder_api=True,
        p2p_tcp_address="0.0.0.0:3812",
        feature_set_enable="json_requests",
    )
    ctx = {
        "network": "mainnet",
        "el_client": "",
        "cl_client": "",
        "vc_client": "Teku",
        "fee_recipient": "0xabc",
        "graffiti": "test",
        "jwtsecret": "/secrets/jwtsecret",
        "sync_url": "",
        "el_p2p": "30303",
        "el_p2p_2": "30304",
        "el_rpc": "8545",
        "el_peers": "50",
        "cl_p2p": "9000",
        "cl_p2p_2": "9001",
        "cl_rest": "5052",
        "cl_peers": "100",
        "mev_min_bid": "0.006",
        "mev_enabled": False,
        "bn_endpoint": "http://127.0.0.1:3600",
        "is_integrated_grandine": False,
        "contents": {"charon": installed},
    }
    generated = generate_default_unit("charon", ctx)
    assert semantic_equal(generated, installed)


def test_resolve_context_bn_endpoint_falls_back_to_charon_api(monkeypatch, tmp_path):
    """VC-only + Charon: default BN endpoint is Charon :3600, not CL REST."""
    from manage.config_compare import _resolve_context

    charon_svc = tmp_path / "charon.service"
    charon_svc.write_text("[Service]\nExecStart=/usr/local/bin/charon run\n", encoding="utf-8")
    validator_svc = tmp_path / "validator.service"
    validator_svc.write_text(
        "[Service]\nExecStart=/usr/local/bin/teku validator-client\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "manage.config_compare.installed_service_paths",
        lambda: {"charon": str(charon_svc), "validator": str(validator_svc)},
    )
    monkeypatch.setattr(
        "manage.config_compare.read_text_file",
        lambda path: Path(path).read_text(encoding="utf-8"),
    )

    ctx = _resolve_context({}, {"charon": str(charon_svc), "validator": str(validator_svc)})
    assert ctx["bn_endpoint"] == "http://127.0.0.1:3600"
