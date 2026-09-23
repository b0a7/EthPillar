"""Unit tests for Upgrade-case LATEST→RC override (integration-test scoped)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import deploy.common as common
from tests.integration.latest_override import (
    clear_override,
    forced_rc_tag,
    install_github_release_hook,
    load_override,
    normalize_deploy_clients,
    prepare_rc_overrides,
    remap_latest_tag,
    write_override,
)


def test_remap_latest_tag_noop_without_file(tmp_path):
    path = tmp_path / "override.json"
    assert remap_latest_tag("sigp/lighthouse", "LATEST", path=str(path)) == "LATEST"
    assert remap_latest_tag("sigp/lighthouse", "v7.0.0", path=str(path)) == "v7.0.0"
    assert forced_rc_tag("lighthouse", path=str(path)) is None


def test_write_clear_and_remap_lifecycle(tmp_path):
    path = str(tmp_path / "override.json")
    payload = write_override({"Lighthouse": "v7.1.0-rc.0", "geth": ""}, path=path)

    assert payload["clients"]["lighthouse"] == "v7.1.0-rc.0"
    assert "geth" not in payload["clients"]
    assert payload["repos"]["sigp/lighthouse"] == "v7.1.0-rc.0"

    assert remap_latest_tag("sigp/lighthouse", "LATEST", path=path) == "v7.1.0-rc.0"
    assert remap_latest_tag("sigp/lighthouse", "latest", path=path) == "v7.1.0-rc.0"
    assert remap_latest_tag("sigp/lighthouse", "v7.0.0", path=path) == "v7.0.0"
    assert remap_latest_tag("paradigmxyz/reth", "LATEST", path=path) == "LATEST"
    assert forced_rc_tag("Lighthouse", path=path) == "v7.1.0-rc.0"
    assert forced_rc_tag("reth", path=path) is None

    on_disk = load_override(path)
    assert on_disk["clients"]["lighthouse"] == "v7.1.0-rc.0"

    clear_override(path)
    assert remap_latest_tag("sigp/lighthouse", "LATEST", path=path) == "LATEST"
    assert load_override(path) == {"clients": {}, "repos": {}}


def test_normalize_deploy_clients_skips_caplin_and_empties():
    assert normalize_deploy_clients("Reth", "Lighthouse") == ["reth", "lighthouse"]
    assert normalize_deploy_clients("Erigon", "Caplin") == ["erigon"]
    assert normalize_deploy_clients("Geth", "", mev=True, charon=True) == [
        "geth",
        "mevboost",
        "charon",
    ]
    assert normalize_deploy_clients("same as cc") == []


def test_prepare_writes_only_differing_rcs(tmp_path):
    path = str(tmp_path / "override.json")

    def fake_find_rc(client: str, _repo):
        if client == "lighthouse":
            return {
                "client": "lighthouse",
                "rc_tag": "v7.1.0-rc.0",
                "latest": "v7.0.1",
                "status": "ok",
                "reason": "prerelease resolvable via release_info",
            }
        if client == "geth":
            return {
                "client": "geth",
                "rc_tag": None,
                "latest": "v1.16.2",
                "status": "skip",
                "reason": "Geth downloads page serves stables only; no RC via release_info",
            }
        return {
            "client": client,
            "rc_tag": "v1.0.0",
            "latest": "v1.0.0",
            "status": "ok",
            "reason": "same as latest",
        }

    result = prepare_rc_overrides(
        ["lighthouse", "geth", "reth"],
        path=path,
        find_rc_fn=fake_find_rc,
    )
    assert result["clients"] == {"lighthouse": "v7.1.0-rc.0"}
    assert result["repos"]["sigp/lighthouse"] == "v7.1.0-rc.0"
    assert remap_latest_tag("sigp/lighthouse", "LATEST", path=path) == "v7.1.0-rc.0"
    assert remap_latest_tag("ethereum/go-ethereum", "LATEST", path=path) == "LATEST"


def test_prepare_clears_leftover_override_before_discovery(tmp_path):
    path = str(tmp_path / "override.json")
    write_override({"lighthouse": "v7.1.0-rc.0"}, path=path)

    def find_rc(client: str, _repo):
        assert not Path(path).exists(), "leftover override must be cleared before find_rc"
        return {
            "client": client,
            "rc_tag": "v7.1.0-rc.0",
            "latest": "v7.0.1",
            "status": "ok",
            "reason": "prerelease resolvable via release_info",
        }

    result = prepare_rc_overrides(["lighthouse"], path=path, find_rc_fn=find_rc)
    assert result["clients"]["lighthouse"] == "v7.1.0-rc.0"
    assert Path(path).exists()


def test_prepare_clears_file_when_no_rc(tmp_path):
    path = str(tmp_path / "override.json")
    write_override({"lighthouse": "v7.1.0-rc.0"}, path=path)

    def all_skip(client: str, _repo):
        return {
            "client": client,
            "rc_tag": None,
            "latest": "v1.2.3",
            "status": "skip",
            "reason": "no resolvable RC/prerelease with assets in recent releases",
        }

    result = prepare_rc_overrides(["geth"], path=path, find_rc_fn=all_skip)
    assert result["clients"] == {}
    assert not Path(path).exists()


def test_prepare_reuses_find_client_rc(tmp_path):
    path = str(tmp_path / "override.json")
    with patch("find_client_rc.find_rc") as find_rc:
        find_rc.return_value = {
            "client": "teku",
            "rc_tag": "25.9.0-rc1",
            "latest": "25.8.0",
            "status": "ok",
            "reason": "prerelease resolvable via release_info",
        }
        result = prepare_rc_overrides(["teku"], path=path)
    find_rc.assert_called_once()
    assert find_rc.call_args[0][0] == "teku"
    assert result["clients"]["teku"] == "25.9.0-rc1"


def test_install_github_release_hook_remaps_latest(tmp_path):
    path = str(tmp_path / "override.json")
    write_override({"nimbus": "v25.9.0-rc1"}, path=path)
    original = common.get_github_release
    captured: dict[str, str] = {}

    def inner(repo: str, version_tag: str) -> dict:
        captured["repo"] = repo
        captured["tag"] = version_tag
        return {"tag_name": version_tag}

    try:
        common.get_github_release = inner
        with patch.dict("os.environ", {"ETHPILLAR_INTEGRATION_LATEST_OVERRIDE": path}):
            assert install_github_release_hook()
            result = common.get_github_release("status-im/nimbus-eth2", "LATEST")
            assert captured["tag"] == "v25.9.0-rc1"
            assert result["tag_name"] == "v25.9.0-rc1"
            result_exact = common.get_github_release("status-im/nimbus-eth2", "v25.8.0")
            assert captured["tag"] == "v25.8.0"
            assert result_exact["tag_name"] == "v25.8.0"
    finally:
        common.get_github_release = original


def test_sitecustomize_installs_override_hook():
    text = Path("tests/integration/sitecustomize.py").read_text(encoding="utf-8")
    assert "install_github_release_hook" in text
    assert "latest_override" in text


def test_check_client_versions_script_reads_clients_map():
    text = Path("tests/integration/check_client_versions.sh").read_text(encoding="utf-8")
    assert "ETHPILLAR_INTEGRATION_LATEST_OVERRIDE" in text
    assert ".clients[$k]" in text
    assert "forced RC" in text
