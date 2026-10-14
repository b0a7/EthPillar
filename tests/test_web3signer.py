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

    def fake_isfile(path: str) -> bool:
        if str(path).endswith("validator.service"):
            return True
        return real_isfile(path)

    monkeypatch.setattr(mod.os.path, "isfile", fake_isfile)

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
