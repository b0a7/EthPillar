"""Grandine Sepolia Gloas RC pin. Stable networks stay on LATEST."""

from __future__ import annotations

from deploy.grandine import (
    GRANDINE_SEPOLIA_GLOAS_EPOCH,
    GRANDINE_SEPOLIA_GLOAS_GAS_LIMIT,
    GRANDINE_SEPOLIA_GLOAS_TAG,
    grandine_release_tag,
    grandine_update_tag,
    network_from_consensus_unit,
    resolve_grandine_install_tag,
)


SEPOLIA_UNIT = """[Unit]
Description=Grandine Consensus Client service for SEPOLIA
[Service]
ExecStart=/usr/local/bin/grandine --network=sepolia
"""


def test_non_sepolia_stays_on_latest() -> None:
    """Mainnet and other networks do not install the Gloas RC."""
    for network in ("", "mainnet", "MAINNET", "hoodi", "holesky", "ephemery"):
        assert grandine_release_tag(network) == "LATEST"
        assert grandine_release_tag(network, latest_tag="2.0.6") == "LATEST"


def test_sepolia_uses_rc_until_stable_gloas() -> None:
    """Sepolia stays on 3.0.0-rc.0 while GitHub latest is the 2.0.6 line."""
    assert grandine_release_tag("sepolia") == GRANDINE_SEPOLIA_GLOAS_TAG
    assert grandine_release_tag("SEPOLIA", latest_tag="2.0.6") == "3.0.0-rc.0"
    assert grandine_release_tag("sepolia", latest_tag="v2.0.6") == "3.0.0-rc.0"
    assert GRANDINE_SEPOLIA_GLOAS_EPOCH == 353024
    assert GRANDINE_SEPOLIA_GLOAS_GAS_LIMIT == 200_000_000


def test_sepolia_pin_drops_when_stable_3_ships(monkeypatch) -> None:
    """A stable 3.0.0 latest replaces the RC so Sepolia is not stuck on rc.0."""
    assert grandine_release_tag("sepolia", latest_tag="3.0.0") == "LATEST"
    assert grandine_release_tag("sepolia", latest_tag="v3.0.1") == "LATEST"

    def fake_latest(version_tag: str, arch_amd64: bool) -> dict:
        assert version_tag == "LATEST"
        assert arch_amd64 is True
        return {"version": "3.0.0"}

    monkeypatch.setattr("deploy.grandine.get_release_info", fake_latest)
    assert resolve_grandine_install_tag("sepolia") == "LATEST"
    assert resolve_grandine_install_tag("mainnet") == "LATEST"


def test_sepolia_keeps_rc_when_latest_probe_fails(monkeypatch) -> None:
    """A failed latest lookup still installs the mandatory Sepolia RC."""

    def boom(version_tag: str, arch_amd64: bool) -> dict:
        raise OSError("github down")

    monkeypatch.setattr("deploy.grandine.get_release_info", boom)
    assert resolve_grandine_install_tag("SEPOLIA") == "3.0.0-rc.0"


def test_update_tag_reads_consensus_unit(tmp_path, monkeypatch) -> None:
    """Updates follow the installed unit, not a global Grandine pin."""
    unit = tmp_path / "consensus.service"
    unit.write_text(SEPOLIA_UNIT, encoding="utf-8")
    assert network_from_consensus_unit(SEPOLIA_UNIT) == "sepolia"

    monkeypatch.setattr(
        "deploy.grandine.get_release_info",
        lambda version_tag, arch_amd64: {"version": "2.0.6"},
    )
    assert grandine_update_tag(str(unit)) == "3.0.0-rc.0"

    unit.write_text(SEPOLIA_UNIT.replace("SEPOLIA", "MAINNET").replace("sepolia", "mainnet"), encoding="utf-8")
    assert grandine_update_tag(str(unit)) == "LATEST"
    assert grandine_update_tag(str(tmp_path / "missing.service")) == "LATEST"
