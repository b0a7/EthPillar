"""Install-time builder mode, relay checklist, CSM lock, and fresh ePBS units."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

import pytest

from config import EPBS_ENABLED_BY_NETWORK, network_epbs_enabled, sepolia_relay_options
from deploy.builder_mode import (
    BUILDER_EPBS,
    BUILDER_MEVBOOST,
    BUILDER_NONE,
    CSM_EPBS_POLICY_NOTE,
    CSM_RELAY_ALLOWLIST_CONTRACT,
    BuilderModeError,
    builder_mode_choices,
    csm_offered_relays,
    default_builder_mode,
    epbs_client_supported,
    finalize_builder_install,
    partition_csm_relays,
    relay_allowlist_class,
    run_whiptail_checklist,
    validate_relay_selection,
)
from deploy.erigon import generate_erigon_service
from deploy.lighthouse import generate_lighthouse_vc_service
from deploy.lodestar import generate_lodestar_vc_service
from deploy.nimbus import generate_nimbus_vc_service
from deploy.orchestrator import resolve_role_flags
from deploy.prysm import generate_prysm_vc_service
from deploy.teku import generate_teku_vc_service
from manage.epbs import (
    EpbsError,
    EpbsFilesystem,
    RelaysConfig,
    apply_fresh_install_relays,
    export_migration,
    load_relays_for_tooling,
    main as epbs_main,
)
from manage.service_parse import get_flag_values, normalize_cli_args, parse_unit

FEE = "0x1234567890abcdef1234567890abcdef12345678"
MIN_BID = "0.006"
GRAFFITI = "EthPillar"
JWT = "/secrets/jwtsecret"
SYNC = "https://checkpoint-sync.sepolia.ethpandaops.io"
FLASHBOTS = sepolia_relay_options[0]["url"]
AESTUS = (
    "https://0xab78bf8c781c58078c3beb5710c57940874dd96aef2835e7742c866b4c7c0406"
    "754376c2c8285a36c630346aa5c5f833@sepolia.aestus.example"
)
RELAYS = [
    {"name": "Flashbots", "url": FLASHBOTS},
    {"name": "Aestus", "url": AESTUS},
]
SIDECAR = "127.0.0.1:18550"


def test_network_epbs_flag_matches_the_postponed_glamsterdam_schedule() -> None:
    """Sepolia is on; mainnet, Hoodi, Holesky, and Ephemery are not."""
    assert EPBS_ENABLED_BY_NETWORK == {
        "mainnet": False,
        "hoodi": False,
        "sepolia": True,
        "holesky": False,
        "ephemery": False,
    }
    assert network_epbs_enabled("SEPOLIA") is True
    assert network_epbs_enabled("unknown") is False


@pytest.mark.parametrize(
    ("network", "vc", "cc", "charon", "modes"),
    [
        ("mainnet", "Lighthouse", "Lighthouse", False, ["mevboost", "none"]),
        ("hoodi", "Nimbus", "Nimbus", False, ["mevboost", "none"]),
        ("holesky", "Teku", "Teku", False, ["mevboost", "none"]),
        ("ephemery", "Prysm", "Prysm", False, ["mevboost", "none"]),
        ("sepolia", "Lighthouse", "Lighthouse", False, ["epbs", "mevboost", "none"]),
        ("sepolia", "Nimbus", "Nimbus", False, ["epbs", "mevboost", "none"]),
        ("sepolia", "Lodestar", "Lodestar", False, ["epbs", "mevboost", "none"]),
        ("sepolia", "Teku", "Teku", False, ["epbs", "mevboost", "none"]),
        ("sepolia", "Prysm", "Prysm", False, ["epbs", "mevboost", "none"]),
        ("sepolia", "Caplin", "Caplin", False, ["epbs", "mevboost", "none"]),
        ("sepolia", "Grandine (integrated)", "Grandine", False, ["mevboost", "none"]),
        ("sepolia", "Lighthouse", "Lighthouse", True, ["mevboost", "none"]),
    ],
)
def test_builder_menu_hides_epbs_unless_network_and_client_allow_it(
    network: str, vc: str, cc: str, charon: bool, modes: List[str]
) -> None:
    choices = builder_mode_choices(network, vc, cc, charon=charon)
    assert [mode for mode, _label in choices] == modes


def test_csm_menu_omits_local_blocks() -> None:
    choices = builder_mode_choices(
        "sepolia", "Nimbus", "Nimbus", csm=True
    )
    assert [mode for mode, _label in choices] == ["epbs", "mevboost"]
    unforked = builder_mode_choices("mainnet", "Nimbus", "Nimbus", csm=True)
    assert [mode for mode, _label in unforked] == ["mevboost"]


def test_support_gate_matches_full_clients_and_rejects_charon() -> None:
    assert epbs_client_supported("Nimbus", "Nimbus") is True
    assert epbs_client_supported("Grandine (integrated)", "Grandine") is False
    assert epbs_client_supported("Lighthouse", "Lighthouse", charon=True) is False


@pytest.mark.parametrize(
    ("role", "network", "vc", "cc", "charon", "expected"),
    [
        ("Solo Staking Node", "mainnet", "Lighthouse", "Lighthouse", False, BUILDER_MEVBOOST),
        ("Solo Staking Node", "hoodi", "Nimbus", "Nimbus", False, BUILDER_MEVBOOST),
        ("Solo Staking Node", "sepolia", "Lighthouse", "Lighthouse", False, BUILDER_EPBS),
        ("Solo Staking Node", "sepolia", "Nimbus", "Nimbus", False, BUILDER_EPBS),
        ("Solo Staking Node", "sepolia", "Grandine (integrated)", "Grandine", False, BUILDER_MEVBOOST),
        ("Solo Staking Node", "sepolia", "Lighthouse", "Lighthouse", True, BUILDER_MEVBOOST),
        ("Solo Staking Node", "sepolia", None, "Lighthouse", False, BUILDER_MEVBOOST),
        ("Lido CSM Staking Node", "sepolia", "Teku", "Teku", False, BUILDER_EPBS),
        ("Lido CSM Staking Node", "mainnet", "Teku", "Teku", False, BUILDER_MEVBOOST),
        ("Failover Staking Node", "sepolia", None, "Lighthouse", False, BUILDER_MEVBOOST),
        ("Lido CSM Validator Client Only", "sepolia", "Prysm", "Prysm", False, BUILDER_EPBS),
        ("Lido CSM Validator Client Only", "hoodi", "Prysm", "Prysm", False, BUILDER_MEVBOOST),
        ("Full Node Only", "sepolia", None, "Lighthouse", False, BUILDER_NONE),
        ("Validator Client Only", "sepolia", "Lighthouse", None, False, BUILDER_NONE),
        ("Custom Setup", "sepolia", "Lighthouse", "Lighthouse", False, BUILDER_NONE),
    ],
)
def test_preset_defaults(
    role: str, network: str, vc: Optional[str], cc: Optional[str], charon: bool, expected: str
) -> None:
    assert default_builder_mode(role, network, vc, cc, charon=charon) == expected


def test_resolve_role_flags_sepolia_solo_uses_epbs_when_vc_supports_it() -> None:
    flags = resolve_role_flags(
        "Solo Staking Node", "sepolia", vc_name="Lighthouse", cc_name="Lighthouse"
    )
    assert flags["epbs"] is True
    assert flags["mevboost"] is False
    assert flags["builder_api"] is True


def test_resolve_role_flags_without_vc_stays_on_mevboost() -> None:
    """Support cannot be checked before the VC is chosen."""
    flags = resolve_role_flags("Solo Staking Node", "sepolia")
    assert flags["mevboost"] is True
    assert flags["epbs"] is False


def _flags(**overrides: object) -> dict:
    base = {
        "validator": True,
        "validator_only": False,
        "node_only": False,
        "charon": False,
        "mevboost": False,
        "epbs": False,
        "builder_api": False,
    }
    base.update(overrides)
    return base


def test_skip_prompts_custom_defaults_to_local_blocks() -> None:
    flags, relays = finalize_builder_install(
        role="Custom Setup",
        network="sepolia",
        vc_name="Lighthouse",
        cc_name="Lighthouse",
        flags=_flags(),
        skip_prompts=True,
    )
    assert flags["builder_mode"] == BUILDER_NONE
    assert flags["mevboost"] is False
    assert flags["epbs"] is False
    assert relays == []


def test_with_mevboost_is_an_alias_for_mevboost_even_on_sepolia() -> None:
    flags, relays = finalize_builder_install(
        role="Custom Setup",
        network="sepolia",
        vc_name="Lighthouse",
        cc_name="Lighthouse",
        flags=_flags(),
        with_mevboost=True,
        skip_prompts=True,
    )
    assert flags["builder_mode"] == BUILDER_MEVBOOST
    assert flags["mevboost"] is True
    assert flags["epbs"] is False
    assert flags["builder_api"] is True
    assert relays == sepolia_relay_options


def test_builder_mode_conflicts_with_with_mevboost() -> None:
    with pytest.raises(BuilderModeError, match="conflicts"):
        finalize_builder_install(
            role="Custom Setup",
            network="sepolia",
            vc_name="Lighthouse",
            cc_name="Lighthouse",
            flags=_flags(),
            builder_mode="epbs",
            with_mevboost=True,
            skip_prompts=True,
        )


def test_explicit_epbs_on_unforked_network_fails() -> None:
    with pytest.raises(BuilderModeError, match="not enabled"):
        finalize_builder_install(
            role="Custom Setup",
            network="mainnet",
            vc_name="Lighthouse",
            cc_name="Lighthouse",
            flags=_flags(),
            builder_mode="epbs",
            skip_prompts=True,
        )


def test_explicit_epbs_on_grandine_or_charon_fails() -> None:
    with pytest.raises(BuilderModeError, match="Grandine"):
        finalize_builder_install(
            role="Custom Setup",
            network="sepolia",
            vc_name="Grandine (integrated)",
            cc_name="Grandine",
            flags=_flags(),
            builder_mode="epbs",
            skip_prompts=True,
        )
    with pytest.raises(BuilderModeError, match="Charon"):
        finalize_builder_install(
            role="Custom Setup",
            network="sepolia",
            vc_name="Lighthouse",
            cc_name="Lighthouse",
            flags=_flags(charon=True),
            builder_mode="epbs",
            skip_prompts=True,
        )


def test_csm_rejects_local_blocks() -> None:
    with pytest.raises(BuilderModeError, match="requires MEV"):
        finalize_builder_install(
            role="Lido CSM Staking Node",
            network="mainnet",
            vc_name="Lighthouse",
            cc_name="Lighthouse",
            flags=_flags(),
            builder_mode="none",
            skip_prompts=True,
        )


def test_with_builder_api_does_not_install_native_epbs() -> None:
    flags, relays = finalize_builder_install(
        role="Solo Staking Node",
        network="sepolia",
        vc_name="Lighthouse",
        cc_name="Lighthouse",
        flags=_flags(),
        with_builder_api=True,
        skip_prompts=True,
    )
    assert flags["epbs"] is False
    assert flags["mevboost"] is False
    assert flags["builder_api"] is True
    assert relays == []


def test_solo_sepolia_skip_prompts_defaults_to_epbs_relays() -> None:
    flags, relays = finalize_builder_install(
        role="Solo Staking Node",
        network="sepolia",
        vc_name="Nimbus",
        cc_name="Nimbus",
        flags=_flags(),
        skip_prompts=True,
    )
    assert flags["epbs"] is True
    assert flags["mevboost"] is False
    assert [item["name"] for item in relays] == ["Flashbots"]


def test_interactive_custom_offers_three_ways_and_refuses_empty_relays() -> None:
    seen = {"passes": 0}

    def prompt_index(labels, title, subtitle):
        assert title == "Custom Setup"
        assert "Step 4" in subtitle
        assert labels[0] == "Native ePBS"
        assert "MEV-Boost" in labels
        assert "None (local blocks)" in labels
        return 0

    def checklist(title, prompt, items):
        seen["passes"] += 1
        assert title == "Relays"
        assert "empty" in prompt.lower()
        if seen["passes"] == 1:
            return []
        return [items[0][0]]

    flags, relays = finalize_builder_install(
        role="Custom Setup",
        network="sepolia",
        vc_name="Lighthouse",
        cc_name="Lighthouse",
        flags=_flags(),
        skip_prompts=False,
        prompt_index=prompt_index,
        checklist=checklist,
    )
    assert seen["passes"] == 2
    assert flags["builder_mode"] == BUILDER_EPBS
    assert [item["name"] for item in relays] == ["Flashbots"]


def test_unforked_custom_menu_is_mevboost_or_none() -> None:
    def prompt_index(labels, title, subtitle):
        assert "Native ePBS" not in labels
        return labels.index("None (local blocks)")

    flags, relays = finalize_builder_install(
        role="Custom Setup",
        network="hoodi",
        vc_name="Lighthouse",
        cc_name="Lighthouse",
        flags=_flags(),
        skip_prompts=False,
        prompt_index=prompt_index,
        checklist=lambda *_args: pytest.fail("checklist is not used for local blocks"),
    )
    assert flags["builder_mode"] == BUILDER_NONE
    assert relays == []


def test_csm_allowlist_classifies_v26_names() -> None:
    assert relay_allowlist_class("Agnostic Gnosis") == "must"
    assert relay_allowlist_class("bloXroute Regulated") == "must"
    assert relay_allowlist_class("Aestus") == "must"
    assert relay_allowlist_class("Ultra Sound") == "must"
    assert relay_allowlist_class("Ultra Sound filtered") == "must"
    assert relay_allowlist_class("Flashbots") == "must"
    assert relay_allowlist_class("Titan Regional") == "may"
    assert relay_allowlist_class("Titan Global") == "may"
    assert relay_allowlist_class("bloXroute") is None
    assert relay_allowlist_class("Titan Relay") is None
    assert relay_allowlist_class("Eden Network") is None
    assert CSM_RELAY_ALLOWLIST_CONTRACT.lower() == "0xf95f069f9ad107938f6ba802a3da87892298610e"


def test_csm_mainnet_locks_to_vetted_relays_and_requires_one_must() -> None:
    from config import mainnet_relay_options

    must, may, excluded = partition_csm_relays(mainnet_relay_options)
    assert excluded == []
    assert {item["name"] for item in must} == {
        "Aestus",
        "Agnostic Gnosis",
        "bloXroute Regulated",
        "Flashbots",
        "Ultra Sound",
    }
    assert [item["name"] for item in may] == ["Titan Global"]

    offered = csm_offered_relays(mainnet_relay_options)
    kept = validate_relay_selection(offered, ["Flashbots", "Titan Global"], csm=True)
    assert [item["name"] for item in kept] == ["Flashbots", "Titan Global"]
    may_only = validate_relay_selection(offered, ["Aestus"], csm=True)
    assert [item["name"] for item in may_only] == ["Aestus"]
    with pytest.raises(BuilderModeError, match="must-use-some"):
        validate_relay_selection(offered, ["Titan Global"], csm=True)
    with pytest.raises(BuilderModeError, match="empty"):
        validate_relay_selection(offered, [], csm=False)


def test_csm_hoodi_drops_unvetted_relays() -> None:
    from config import hoodi_relay_options

    offered = csm_offered_relays(hoodi_relay_options)
    assert [item["name"] for item in offered] == ["Flashbots", "Aestus"]


def test_csm_epbs_checklist_documents_the_missing_policy() -> None:
    captured = {}

    def checklist(title, prompt, items):
        captured["prompt"] = prompt
        captured["tags"] = [item[0] for item in items]
        captured["checked"] = [item[2] for item in items]
        return list(captured["tags"])

    flags, relays = finalize_builder_install(
        role="Lido CSM Staking Node",
        network="sepolia",
        vc_name="Nimbus",
        cc_name="Nimbus",
        flags=_flags(),
        skip_prompts=False,
        checklist=checklist,
    )
    assert flags["epbs"] is True
    assert flags["mevboost"] is False
    assert captured["tags"] == ["Flashbots"]
    assert captured["checked"] == [True]
    assert "must-use-some" in captured["prompt"]
    assert CSM_EPBS_POLICY_NOTE in captured["prompt"]
    assert [item["url"] for item in relays] == [FLASHBOTS]


def test_whiptail_checklist_uses_separate_output(monkeypatch) -> None:
    recorded = {}

    class _Proc:
        returncode = 0
        stderr = "bloXroute Regulated\nFlashbots\n"

    def fake_run(cmd, stderr=None, text=None, check=False):
        recorded["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr("deploy.builder_mode.subprocess.run", fake_run)
    selected = run_whiptail_checklist(
        "Relays",
        "pick",
        [("bloXroute Regulated", "host (must-use-some)", True), ("Flashbots", "host", True)],
    )
    assert selected == ["bloXroute Regulated", "Flashbots"]
    assert "--checklist" in recorded["cmd"]
    assert "--separate-output" in recorded["cmd"]
    assert "ON" in recorded["cmd"]


def test_whiptail_cancel_returns_none(monkeypatch) -> None:
    class _Proc:
        returncode = 1
        stderr = ""

    monkeypatch.setattr(
        "deploy.builder_mode.subprocess.run",
        lambda *args, **kwargs: _Proc(),
    )
    assert run_whiptail_checklist("Relays", "pick", [("Flashbots", "host", True)]) is None


def _fs(tmp_path: Path, version: str, help_text: str = "--builder.urls\n") -> EpbsFilesystem:
    systemd = tmp_path / "systemd"
    systemd.mkdir(parents=True, exist_ok=True)

    def _write(path: str, content: str) -> None:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")

    fs = EpbsFilesystem(
        systemd_dir=str(systemd),
        prysm_settings_path=str(tmp_path / "proposer-settings.json"),
        caplin_builders_path=str(tmp_path / "caplin-builders.json"),
        lighthouse_builders_path=str(tmp_path / "builder_definitions.yml"),
        read_text=lambda path: Path(path).read_text(encoding="utf-8") if Path(path).is_file() else None,
        exists=lambda path: Path(path).is_file(),
        write_unit=_write,
        write_data=_write,
        run_version=lambda _argv: version,
        run_help=lambda _argv: help_text,
    )
    return fs


def _write_unit(fs: EpbsFilesystem, key: str, content: str) -> None:
    Path(fs.unit_path(key)).write_text(
        content if content.endswith("\n") else content + "\n", encoding="utf-8"
    )


def _apply(fs: EpbsFilesystem) -> None:
    apply_fresh_install_relays(
        RelaysConfig(urls=[item["url"] for item in RELAYS], min_bid=MIN_BID, network="sepolia"),
        fs=fs,
    )


def _vc(fs: EpbsFilesystem) -> str:
    text = fs.read_text(fs.unit_path("validator"))
    assert text
    return text


@pytest.mark.parametrize(
    ("version", "writer"),
    [
        (
            "Lighthouse v8.3.0",
            lambda: generate_lighthouse_vc_service(
                "sepolia", GRAFFITI, "--beacon-nodes=http://127.0.0.1:5052",
                fee_parameters=f"--suggested-fee-recipient={FEE}",
                extra_parameters="--builder-proposals",
            ),
        ),
        (
            "Lodestar/v1.49.0",
            lambda: generate_lodestar_vc_service(
                "sepolia", GRAFFITI, "--beaconNodes=http://127.0.0.1:5052",
                fee_parameters=f"--suggestedFeeRecipient={FEE}",
                extra_parameters="--builder",
            ),
        ),
        (
            "teku/v26.9.1",
            lambda: generate_teku_vc_service(
                "sepolia", GRAFFITI, "--beacon-node-api-endpoint=http://127.0.0.1:5052",
                fee_parameters=f"--validators-proposer-default-fee-recipient={FEE}",
                extra_parameters="--validators-builder-registration-default-enabled=true",
            ),
        ),
        (
            "Nimbus v26.10.2",
            lambda: generate_nimbus_vc_service(
                "sepolia", GRAFFITI, "--beacon-node=http://127.0.0.1:5052",
                fee_parameters=f"--suggested-fee-recipient={FEE}",
                extra_parameters="--payload-builder=true",
            ),
        ),
        (
            "Prysm/v7.2.1",
            lambda: generate_prysm_vc_service(
                "sepolia", GRAFFITI, "--beacon-rest-api-provider=http://127.0.0.1:5052",
                fee_parameters=f"--suggested-fee-recipient={FEE}",
                extra_parameters="--enable-builder",
            ),
        ),
    ],
)
def test_fresh_install_writes_vc_relays_without_sidecar(tmp_path: Path, version: str, writer) -> None:
    fs = _fs(tmp_path, version)
    _write_unit(fs, "validator", writer())
    _apply(fs)
    text = _vc(fs)
    assert SIDECAR not in text
    assert not Path(fs.unit_path("mevboost")).exists()
    if "Lighthouse" in version:
        raw = Path(fs.lighthouse_builders_path).read_text(encoding="utf-8")
        assert FLASHBOTS in raw and AESTUS in raw
        assert "min_bid: 6000000" in raw or "min_bid:6000000" in raw.replace(" ", "")
        assert SIDECAR not in raw
    elif version.startswith("Lodestar"):
        assert "--builder.urls=" in text
        assert FLASHBOTS in text and AESTUS in text
        assert "--builder.minBid=6000000" in text
    elif version.startswith("teku"):
        assert "--Xbuilder-urls=" in text
        assert FLASHBOTS in text and AESTUS in text
        assert "--Xbuilder-min-bid=6000000" in text
    elif version.startswith("Nimbus"):
        urls = get_flag_values(
            normalize_cli_args(parse_unit(text).exec_args), "--payload-builder-url"
        )
        assert urls == [FLASHBOTS, AESTUS]
        assert "--builder-min-bid=6000000" in text
        assert "--payload-builder=true" in text
        assert SIDECAR not in text
    else:
        assert "--builder-urls=" in text
        assert FLASHBOTS in text and AESTUS in text
        assert "--builder-min-bid=6000000" in text
        assert "--enable-builder" not in text


def test_fresh_install_caplin_writes_builders_without_sidecar(tmp_path: Path) -> None:
    fs = _fs(tmp_path, "erigon version 3.7.1")
    unit = generate_erigon_service(
        "sepolia",
        "30303",
        "8545",
        "50",
        JWT,
        "9000",
        "5052",
        "100",
        SYNC,
        mev_parameters="",
        erigon_version="3.7.1",
    )
    _write_unit(fs, "execution", unit)
    _apply(fs)
    text = fs.read_text(fs.unit_path("execution")) or ""
    assert "--caplin.mev-relay-url" not in text
    assert SIDECAR not in text
    payload = json.loads(Path(fs.caplin_builders_path).read_text(encoding="utf-8"))
    assert [entry["url"] for entry in payload["builders"]] == [FLASHBOTS, AESTUS]
    assert not Path(fs.unit_path("mevboost")).exists()


def test_fresh_install_old_nimbus_fails_instead_of_skipping(tmp_path: Path) -> None:
    fs = _fs(tmp_path, "Nimbus v26.10.1")
    _write_unit(
        fs,
        "validator",
        generate_nimbus_vc_service(
            "sepolia", GRAFFITI, "--beacon-node=http://127.0.0.1:5052"
        ),
    )
    with pytest.raises(EpbsError, match="did not write relays"):
        _apply(fs)


def test_fresh_install_lodestar_without_builder_urls_fails(tmp_path: Path) -> None:
    fs = _fs(tmp_path, "v1.46.0", help_text="no builder flag here\n")
    _write_unit(
        fs,
        "validator",
        generate_lodestar_vc_service(
            "sepolia", GRAFFITI, "--beaconNodes=http://127.0.0.1:5052"
        ),
    )
    with pytest.raises(EpbsError, match="did not write relays"):
        _apply(fs)


def test_fresh_install_grandine_fails_loudly(tmp_path: Path) -> None:
    fs = _fs(tmp_path, "Grandine 2.0.6")
    _write_unit(
        fs,
        "validator",
        "\n".join([
            "[Service]",
            "Description=Grandine Validator Client service for SEPOLIA",
            "ExecStart=/usr/local/bin/grandine --network=sepolia",
            "",
        ]),
    )
    with pytest.raises(EpbsError, match="does not support native ePBS"):
        _apply(fs)


def test_list_relays_and_export_read_the_vc_when_mevboost_is_absent(tmp_path: Path, capsys) -> None:
    fs = _fs(tmp_path, "Lighthouse v8.3.0")
    _write_unit(
        fs,
        "validator",
        generate_lighthouse_vc_service(
            "sepolia", GRAFFITI, "--beacon-nodes=http://127.0.0.1:5052",
            extra_parameters="--builder-proposals",
        ),
    )
    _apply(fs)
    loaded = load_relays_for_tooling(fs)
    assert loaded.urls == [FLASHBOTS, AESTUS]
    assert loaded.min_bid == MIN_BID
    assert loaded.network == "sepolia"
    _path, payload = export_migration(fs, output=str(tmp_path / "out.ethpillar.epbs-migration"))
    assert payload["relays"] == [FLASHBOTS, AESTUS]
    assert payload["min_bid"] == MIN_BID
    assert not Path(fs.unit_path("mevboost")).exists()

    code = epbs_main([
        "list-relays",
        "--systemd-dir",
        fs.systemd_dir,
        "--lighthouse-builders",
        fs.lighthouse_builders_path,
    ])
    assert code == 0
    listed = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert listed == [FLASHBOTS, AESTUS]
