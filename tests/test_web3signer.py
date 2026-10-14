"""Unit tests for Web3Signer service generation, VC flags, and Lighthouse definitions."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deploy.web3signer import (
    DEFAULT_SIGNER_URL,
    PUBLIC_KEYS_URL,
    WEB3SIGNER_DB_ENV,
    WEB3SIGNER_KEYSTORE_DIR,
    generate_web3signer_service,
    merge_validator_definitions,
    signer_flags_for_vc,
)


def test_service_binds_localhost_and_enables_slashing_protection():
    result = generate_web3signer_service("hoodi")
    assert "User=web3signer" in result
    assert "Group=web3signer" in result
    assert "--http-listen-host=127.0.0.1" in result
    assert "--http-listen-port=9000" in result
    assert f"--keystores-path={WEB3SIGNER_KEYSTORE_DIR}" in result
    assert f"--keystores-passwords-path={WEB3SIGNER_KEYSTORE_DIR}" in result
    assert "--slashing-protection-enabled=true" in result
    assert "jdbc:postgresql://127.0.0.1:5432/web3signer" in result
    assert "--slashing-protection-db-password=${WEB3SIGNER_DB_PASSWORD}" in result
    assert f"EnvironmentFile={WEB3SIGNER_DB_ENV}" in result
    assert "--network=hoodi" in result
    assert "After=network-online.target postgresql.service" in result
    assert "0.0.0.0" not in result


@pytest.mark.parametrize(
    ("vc_name", "expected"),
    [
        ("Teku", f"--validators-external-signer-url={DEFAULT_SIGNER_URL}"),
        ("Prysm", "--validators-external-signer-public-keys="),
        ("Nimbus", f"--web3-signer-url={DEFAULT_SIGNER_URL}"),
        ("Lodestar", "--externalSigner.fetch=true"),
        ("Lighthouse", "--disable-slashing-protection-web3signer"),
        ("Grandine (integrated)", "--web3signer-api-urls="),
    ],
)
def test_signer_flags_for_each_vc(vc_name: str, expected: str):
    flags = signer_flags_for_vc(vc_name)
    assert expected in flags
    if vc_name in ("Teku", "Prysm"):
        assert PUBLIC_KEYS_URL in flags


def test_unknown_vc_has_no_signer_flags():
    assert signer_flags_for_vc("NotAClient") == ""


def test_merge_definitions_replaces_empty_list():
    pubkey = "0x" + "ab" * 48
    text = merge_validator_definitions("[]\n", [pubkey])
    assert "type: web3signer" in text
    assert f'voting_public_key: "{pubkey}"' in text
    assert "[]" not in text
    assert text.count("type: web3signer") == 1


def test_merge_definitions_skips_duplicate_pubkey():
    pubkey = "0x" + "cd" * 48
    first = merge_validator_definitions("", [pubkey])
    second = merge_validator_definitions(first, [pubkey.upper()])
    assert second.count("type: web3signer") == 1


def test_import_keystores_copies_password_and_definitions(tmp_path, monkeypatch):
    from deploy import web3signer as mod

    keystore = {
        "pubkey": "ab" * 48,
        "path": "m/12381/3600/0/0/0",
        "crypto": {},
    }
    src = tmp_path / "src"
    src.mkdir()
    (src / "keystore-m.json").write_text(json.dumps(keystore), encoding="utf-8")
    password = tmp_path / "password.txt"
    password.write_text("correct horse battery\n", encoding="utf-8")
    dest = tmp_path / "keystores"
    definitions = tmp_path / "validator_definitions.yml"
    writes: dict[str, str] = {}
    commands: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        commands.append(list(cmd))
        class Result:
            returncode = 0
            stdout = ""
            stderr = ""
        if cmd[:2] == ["sudo", "tee"]:
            writes[cmd[2]] = kwargs.get("input") or ""
        if cmd[:2] == ["sudo", "cat"] and cmd[2] == str(definitions):
            Result.stdout = writes.get(str(definitions), "")
        if cmd[:2] == ["sudo", "cat"] and cmd[2].endswith("validator.service"):
            Result.stdout = "ExecStart=/usr/local/bin/lighthouse vc\n"
        return Result()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    real_isfile = os.path.isfile
    real_open = open
    validator_unit = "/etc/systemd/system/validator.service"

    def fake_isfile(path: str) -> bool:
        # Pretend the unit exists even on a host that has no validator.service.
        if str(path) == validator_unit:
            return True
        return real_isfile(path)

    def fake_open(path, *args, **kwargs):
        # _read_sudo_text opens a readable unit directly and never reaches
        # `sudo cat`. A real non-Lighthouse node would hide the mock.
        if str(path) == validator_unit:
            raise OSError("use the sudo cat mock")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(mod.os.path, "isfile", fake_isfile)
    monkeypatch.setattr("builtins.open", fake_open)

    result = mod.import_keystores(
        str(src),
        password_file=str(password),
        keystore_dir=str(dest),
        definitions_path=str(definitions),
        restart=False,
    )
    assert result["count"] == 1
    assert result["pubkeys"] == ["0x" + "ab" * 48]
    assert any(cmd[:3] == ["sudo", "cp", "-a"] for cmd in commands)
    assert "type: web3signer" in writes[str(definitions)]
    assert "correct horse battery" in writes[str(dest / "keystore-m.txt")]


def test_migrations_apply_in_numeric_order_when_schema_is_missing():
    from deploy import web3signer as mod

    pending = mod.migrations_to_apply(
        ["V2__b.sql", "V10__c.sql", "V1__a.sql", "README.md"],
        None,
    )
    assert pending == ["V1__a.sql", "V2__b.sql", "V10__c.sql"]


def test_migrations_skip_versions_already_applied():
    from deploy import web3signer as mod

    pending = mod.migrations_to_apply(
        ["V1__a.sql", "V2__b.sql", "V12__d.sql"],
        2,
    )
    assert pending == ["V12__d.sql"]


def test_remote_signer_units_do_not_require_missing_local_keys():
    """Each Web3Signer VC unit must not point at a local key path that install never creates.

    Teku omits ``--validator-keys`` (that directory is not created and Teku exits if it
    is missing). Grandine already omits keystore directories. Lighthouse, Lodestar, and
    Nimbus have no local keystore path in the unit. Prysm keeps ``--wallet-dir`` and
    ``--wallet-password-file``; ``install_prysm_vc`` creates both via ``ensure_prysm_wallet``.
    Caplin is not a remote-signer client. Charon cannot be combined with Web3Signer.
    """
    from deploy.common import BASE_DATA_DIR
    from deploy.grandine import generate_grandine_bn_service
    from deploy.keymanager import PRYSM_DEFAULT_WALLET_DIR, PRYSM_PASSWORD_FILE
    from deploy.lighthouse import generate_lighthouse_vc_service
    from deploy.lodestar import generate_lodestar_vc_service
    from deploy.nimbus import generate_nimbus_vc_service
    from deploy.prysm import generate_prysm_vc_service
    from deploy.teku import generate_teku_vc_service

    graffiti = "test"
    fee = "0x0000000000000000000000000000000000000001"
    bn = "http://127.0.0.1:5052"

    teku = generate_teku_vc_service(
        "hoodi",
        graffiti,
        f"--beacon-node-api-endpoint={bn}",
        fee_parameters=f"--validators-proposer-default-fee-recipient={fee}",
        extra_parameters=signer_flags_for_vc("Teku"),
        unit_after=["web3signer.service"],
    )
    assert "--validators-external-signer-url=" in teku
    assert PUBLIC_KEYS_URL in teku
    assert "--validator-keys=" not in teku
    assert "After=network-online.target web3signer.service" in teku

    # Local-key installs still point at the keystore directory.
    local_teku = generate_teku_vc_service(
        "hoodi", graffiti, f"--beacon-node-api-endpoint={bn}"
    )
    assert f"--validator-keys={BASE_DATA_DIR}/teku_validator/validator_keys:" in local_teku

    lighthouse = generate_lighthouse_vc_service(
        "hoodi",
        graffiti,
        f"--beacon-nodes={bn}",
        extra_parameters=signer_flags_for_vc("Lighthouse"),
    )
    assert "--disable-slashing-protection-web3signer" in lighthouse
    assert "validator_keys" not in lighthouse
    assert "keystore" not in lighthouse.lower()

    lodestar = generate_lodestar_vc_service(
        "hoodi",
        graffiti,
        f"--beaconNodes={bn}",
        extra_parameters=signer_flags_for_vc("Lodestar"),
    )
    assert "--externalSigner.url=" in lodestar
    assert "--externalSigner.fetch=true" in lodestar
    assert "keystores" not in lodestar
    assert "secrets" not in lodestar

    nimbus = generate_nimbus_vc_service(
        "hoodi",
        graffiti,
        f"--beacon-node={bn}",
        extra_parameters=signer_flags_for_vc("Nimbus"),
    )
    assert "--web3-signer-url=" in nimbus
    assert "secrets-dir" not in nimbus
    assert "validators-dir" not in nimbus
    assert "keystore" not in nimbus.lower()

    prysm = generate_prysm_vc_service(
        "hoodi",
        graffiti,
        f"--beacon-rest-api-provider={bn}",
        extra_parameters=signer_flags_for_vc("Prysm"),
    )
    assert "--validators-external-signer-url=" in prysm
    assert PUBLIC_KEYS_URL in prysm
    assert f"--wallet-dir={PRYSM_DEFAULT_WALLET_DIR}" in prysm
    assert f"--wallet-password-file={PRYSM_PASSWORD_FILE}" in prysm

    grandine = generate_grandine_bn_service(
        "hoodi",
        "https://checkpoint.example",
        "/secrets/jwt.hex",
        "5052",
        "9000",
        "9001",
        "50",
        is_integrated_vc=True,
        web3signer_url=DEFAULT_SIGNER_URL,
    )
    assert "--web3signer-api-urls=" in grandine
    assert "--keystore-dir=" not in grandine
    assert "--keystore-password-dir=" not in grandine

    assert signer_flags_for_vc("Caplin") == ""
    assert signer_flags_for_vc("Caplin (integrated)") == ""


def test_lighthouse_slashing_db_registers_pubkey_once(tmp_path):
    import sqlite3

    from deploy.web3signer import (
        add_lighthouse_slashing_pubkeys,
        initialize_lighthouse_slashing_db,
    )

    db_path = tmp_path / "slashing_protection.sqlite"
    pubkey = "0x" + "ab" * 48
    initialize_lighthouse_slashing_db(str(db_path))
    add_lighthouse_slashing_pubkeys(str(db_path), [pubkey, pubkey.upper()])
    connection = sqlite3.connect(db_path)
    rows = list(connection.execute("SELECT public_key FROM validators"))
    version = connection.execute("SELECT version FROM schema_version").fetchone()
    connection.close()
    assert rows == [(pubkey,)]
    assert version == (0,)
