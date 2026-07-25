"""Unit tests for deploy/keymanager.py (Keymanager API client, discovery, enablement).

No real HTTP, running validator, or root privileges required.
"""

from __future__ import annotations

import os
import sys
import tempfile
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deploy.keymanager import (
    KeymanagerAPIError,
    KeymanagerAuthError,
    KeymanagerClient,
    KeymanagerConnectionError,
    KeymanagerError,
    KeymanagerValidationError,
    PRYSM_VALIDATOR_HOME_AUTH_TOKEN,
    PRYSM_WALLET_NOT_READY_MSG,
    _ENABLE_FLAGS,
    _parse_token_contents,
    check_prysm_wallet_ready,
    detect_keymanager,
    enable_keymanager_api,
    ensure_nimbus_keymanager_token_file,
    ensure_prysm_wallet,
    is_prysm_empty_keymanager_response,
    main as keymanager_main,
    normalize_client_name,
    patch_service_for_keymanager,
    plan_keymanager_flags,
    prysm_auth_token_candidates,
    require_prysm_wallet_ready,
    scrape_prysm_wallet_dir,
)


# ──────────────────────────────────────────────
# Helpers / fixtures
# ──────────────────────────────────────────────


def _mock_response(
    status_code: int = 200,
    json_data: Optional[dict[str, Any]] = None,
    text: str = "",
) -> MagicMock:
    """Build a requests-like response mock."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = 200 <= status_code < 400
    resp.text = text or ("" if json_data is None else str(json_data))
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("no json")
    return resp


@pytest.fixture
def client() -> KeymanagerClient:
    return KeymanagerClient("http://127.0.0.1:5062", token="test-token", timeout=5.0)


def _patch_session_request(client: KeymanagerClient, response: MagicMock) -> MagicMock:
    """Replace client session.request and return the mock."""
    mock_request = MagicMock(return_value=response)
    client._session.request = mock_request
    return mock_request


# Minimal systemd units (one missing keymanager flags per client)
SERVICE_TEMPLATES: dict[str, str] = {
    "lighthouse": """[Unit]
Description=Lighthouse Validator Client service for MAINNET

[Service]
User=validator
ExecStart=/usr/local/bin/lighthouse vc \\
    --network=mainnet \\
    --datadir=/var/lib/lighthouse_validator \\
    --metrics \\
    --graffiti=test

[Install]
WantedBy=multi-user.target
""",
    "lodestar": """[Service]
ExecStart=/usr/local/bin/lodestar validator \\
    --network=hoodi \\
    --dataDir=/var/lib/lodestar_validator \\
    --metrics=true
""",
    "nimbus": """[Service]
ExecStart=/usr/local/bin/nimbus_validator_client \\
    --data-dir=/var/lib/nimbus_validator \\
    --non-interactive
""",
    "prysm": """[Service]
ExecStart=/usr/local/bin/prysm-validator \\
    --mainnet \\
    --datadir=/var/lib/prysm_validator \\
    --accept-terms-of-use
""",
}


# ═══════════════════════════════════════════════
# Core client (mocked HTTP)
# ═══════════════════════════════════════════════


class TestListKeys:
    def test_list_keys_success(self, client: KeymanagerClient) -> None:
        keys = [
            {
                "validating_pubkey": "0xabc",
                "derivation_path": "",
                "readonly": False,
            }
        ]
        mock_req = _patch_session_request(
            client, _mock_response(200, {"data": keys})
        )

        result = client.list_keys()

        assert result == keys
        mock_req.assert_called_once()
        args, kwargs = mock_req.call_args
        assert args[0] == "GET"
        assert args[1].endswith("/eth/v1/keystores")
        assert client._session.headers["Authorization"] == "Bearer test-token"

    def test_list_keys_prysm_empty_wallet_returns_empty(self, client: KeymanagerClient) -> None:
        body = '{"message":"keymanager is not initialized","code":500}'
        _patch_session_request(
            client,
            _mock_response(
                500,
                json_data={"message": "keymanager is not initialized", "code": 500},
                text=body,
            ),
        )

        result = client.list_keys()
        assert result == []
        keys, meta = client.list_keys_with_meta()
        assert keys == []
        assert meta["empty_uninitialized"] is True
        assert meta["available"] is True

    def test_list_keys_other_500_still_raises(self, client: KeymanagerClient) -> None:
        _patch_session_request(
            client,
            _mock_response(500, json_data={"message": "internal boom"}, text='{"message":"internal boom"}'),
        )
        with pytest.raises(KeymanagerAPIError) as exc_info:
            client.list_keys()
        assert exc_info.value.status_code == 500
        assert not is_prysm_empty_keymanager_response(
            exc_info.value.status_code, exc_info.value.body
        )


class TestImportKeystores:
    def test_import_keystores_success(self, client: KeymanagerClient) -> None:
        statuses = [{"status": "imported", "message": ""}]
        mock_req = _patch_session_request(
            client, _mock_response(200, {"data": statuses})
        )

        result = client.import_keystores(
            keystores=['{"crypto":{}}'],
            passwords=["secret"],
            slashing_protection='{"metadata":{}}',
        )

        assert result == statuses
        _, kwargs = mock_req.call_args
        body = kwargs["json"]
        assert body["keystores"] == ['{"crypto":{}}']
        assert body["passwords"] == ["secret"]
        assert body["slashing_protection"] == '{"metadata":{}}'
        assert mock_req.call_args[0][0] == "POST"

    def test_import_raises_on_length_mismatch(self, client: KeymanagerClient) -> None:
        with pytest.raises(KeymanagerValidationError, match="length mismatch"):
            client.import_keystores(keystores=["ks1", "ks2"], passwords=["only-one"])

    def test_import_raises_on_empty_keystores(self, client: KeymanagerClient) -> None:
        with pytest.raises(KeymanagerValidationError, match="non-empty"):
            client.import_keystores(keystores=[], passwords=[])


class TestDeleteKeys:
    def test_delete_keys_success(self, client: KeymanagerClient) -> None:
        payload = {
            "data": [{"status": "deleted", "message": ""}],
            "slashing_protection": '{"metadata":{}}',
        }
        mock_req = _patch_session_request(client, _mock_response(200, payload))

        result = client.delete_keys(["0xabc"])

        assert result == payload
        assert mock_req.call_args[0][0] == "DELETE"
        assert mock_req.call_args[1]["json"] == {"pubkeys": ["0xabc"]}

    def test_delete_raises_on_empty_pubkeys(self, client: KeymanagerClient) -> None:
        with pytest.raises(KeymanagerValidationError, match="non-empty"):
            client.delete_keys([])


class TestPrysmEmptyKeymanagerClassification:
    def test_detector_matches_message(self) -> None:
        assert is_prysm_empty_keymanager_response(
            500, '{"message":"keymanager is not initialized","code":500}'
        )
        assert is_prysm_empty_keymanager_response(
            500, '{"message":"Keymanager Is Not Initialized"}'
        )
        assert not is_prysm_empty_keymanager_response(500, '{"message":"other"}')
        assert not is_prysm_empty_keymanager_response(401, "keymanager is not initialized")

    def test_probe_treats_empty_prysm_as_found(self) -> None:
        body = '{"message":"keymanager is not initialized","code":500}'
        with patch("deploy.keymanager.requests.Session") as mock_session_cls:
            session = MagicMock()
            mock_session_cls.return_value = session
            session.request.return_value = _mock_response(
                500,
                json_data={"message": "keymanager is not initialized", "code": 500},
                text=body,
            )
            found = KeymanagerClient._probe_endpoint(
                "http://127.0.0.1:7500", token="t", timeout=1.0
            )
        assert found is not None
        assert found.base_url == "http://127.0.0.1:7500"

    def test_status_cli_available_with_zero_keys(self, capsys) -> None:
        body = '{"message":"keymanager is not initialized","code":500}'

        def fake_detect(client, network="mainnet", host="127.0.0.1"):
            return f"http://{host}:7500", "tok"

        km = KeymanagerClient("http://127.0.0.1:7500", token="tok")
        km._session.request = MagicMock(
            return_value=_mock_response(
                500,
                json_data={"message": "keymanager is not initialized", "code": 500},
                text=body,
            )
        )

        with patch("deploy.keymanager.detect_keymanager", side_effect=fake_detect), patch(
            "deploy.keymanager.check_prysm_wallet_ready",
            return_value={
                "ready": True,
                "wallet_dir": "/var/lib/prysm_validator/validator_keys",
                "auth_token_path": "/x/auth-token",
                "wallet_dir_exists": True,
                "auth_token_exists": True,
                "message": "ready",
            },
        ), patch(
            "deploy.keymanager.KeymanagerClient.discover", return_value=km
        ):
            rc = keymanager_main(
                ["--client", "prysm", "--network", "mainnet", "status"]
            )
        assert rc == 0
        out = capsys.readouterr().out
        assert '"available": true' in out
        assert '"key_count": 0' in out
        assert "empty_uninitialized" in out or "prysm_empty_wallet" in out


class TestClientErrors:
    def test_auth_error_401(self, client: KeymanagerClient) -> None:
        _patch_session_request(client, _mock_response(401, text="unauthorized"))
        with pytest.raises(KeymanagerAuthError, match="401"):
            client.list_keys()

    def test_auth_error_403(self, client: KeymanagerClient) -> None:
        _patch_session_request(client, _mock_response(403, text="forbidden"))
        with pytest.raises(KeymanagerAuthError, match="403"):
            client.list_keys()

    def test_connection_error(self, client: KeymanagerClient) -> None:
        client._session.request = MagicMock(
            side_effect=requests.exceptions.ConnectionError("refused")
        )
        with pytest.raises(KeymanagerConnectionError, match="Cannot connect"):
            client.list_keys()

    def test_api_error_500(self, client: KeymanagerClient) -> None:
        _patch_session_request(client, _mock_response(500, text="boom"))
        with pytest.raises(KeymanagerAPIError) as exc_info:
            client.list_keys()
        assert exc_info.value.status_code == 500

    def test_token_optional_no_auth_header(self) -> None:
        km = KeymanagerClient("http://127.0.0.1:5062")
        assert "Authorization" not in km._session.headers


# ═══════════════════════════════════════════════
# Discovery helpers
# ═══════════════════════════════════════════════


class TestNormalizeClientName:
    def test_casing_and_aliases(self) -> None:
        assert normalize_client_name("Lighthouse") == "lighthouse"
        assert normalize_client_name("NIMBUS") == "nimbus"
        assert normalize_client_name("Prysm") == "prysm"
        assert normalize_client_name("lodestar") == "lodestar"
        assert normalize_client_name("lh") == "lighthouse"
        assert normalize_client_name("ls") == "lodestar"

    def test_validator_suffix_stripped(self) -> None:
        assert normalize_client_name("lighthouse_validator") == "lighthouse"
        assert normalize_client_name("prysm-vc") == "prysm"

    def test_empty_raises(self) -> None:
        with pytest.raises(KeymanagerValidationError, match="non-empty"):
            normalize_client_name("")

    def test_unknown_raises(self) -> None:
        with pytest.raises(KeymanagerValidationError, match="Unsupported"):
            normalize_client_name("not-a-client")


class TestDetectKeymanager:
    @pytest.mark.parametrize(
        "client,expected_port",
        [
            ("lighthouse", 5062),
            ("lodestar", 5062),
            ("nimbus", 7500),
            ("prysm", 7500),
        ],
    )
    def test_preferred_ports(self, client: str, expected_port: int) -> None:
        # Avoid depending on host token files (incl. /home/validator Prysm token)
        with patch("deploy.keymanager.read_keymanager_token", return_value=None), patch(
            "deploy.keymanager.find_prysm_auth_token", return_value=None
        ):
            base_url, token = detect_keymanager(client, network="hoodi")
        assert base_url == f"http://127.0.0.1:{expected_port}"
        assert token is None

    def test_custom_host(self) -> None:
        with patch("deploy.keymanager.read_keymanager_token", return_value="tok"):
            base_url, token = detect_keymanager("lighthouse", host="10.0.0.5")
        assert base_url == "http://10.0.0.5:5062"
        assert token == "tok"


class TestParseTokenContents:
    def test_plain_token(self) -> None:
        assert _parse_token_contents("my-secret-token\n", "api-token.txt") == (
            "my-secret-token"
        )

    def test_prysm_multiline_url_and_jwt(self) -> None:
        raw = "http://127.0.0.1:7500\neyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test\n"
        assert _parse_token_contents(raw, "auth-token") == (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test"
        )

    def test_prysm_host_port_and_jwt(self) -> None:
        raw = "localhost:7500\nabc.def.ghi\n"
        assert _parse_token_contents(raw, "auth-token") == "abc.def.ghi"

    def test_bearer_prefix(self) -> None:
        assert _parse_token_contents("Bearer abc.def\n", "t") == "abc.def"

    def test_comments_and_blank_lines(self) -> None:
        raw = "# comment\n\n  real-token  \n"
        assert _parse_token_contents(raw, "t") == "real-token"

    def test_url_only_returns_none(self) -> None:
        assert _parse_token_contents("http://127.0.0.1:7500\n", "t") is None


# ═══════════════════════════════════════════════
# Enablement (pure / dry-run only)
# ═══════════════════════════════════════════════


class TestPlanAndPatchFlags:
    @pytest.mark.parametrize("client", ["lighthouse", "lodestar", "nimbus", "prysm"])
    def test_adds_missing_flags(self, client: str) -> None:
        content = SERVICE_TEMPLATES[client]
        desired, already, to_add = plan_keymanager_flags(content, client)

        assert desired
        assert to_add  # templates intentionally omit keymanager flags
        # Lighthouse template has no --http; all desired should be missing
        assert set(to_add) == set(desired) - set(already)

        new_content, flags_added, flags_already = patch_service_for_keymanager(
            content, client
        )
        assert flags_added == to_add
        assert flags_already == already
        for flag in flags_added:
            # Flag name must appear in patched ExecStart
            name = flag.split("=", 1)[0]
            assert name in new_content
        assert new_content.count("ExecStart=") == 1

    @pytest.mark.parametrize("client", ["lighthouse", "lodestar", "nimbus", "prysm"])
    def test_idempotent_second_patch(self, client: str) -> None:
        content = SERVICE_TEMPLATES[client]
        once, added, _ = patch_service_for_keymanager(content, client)
        assert added
        twice, added_again, present = patch_service_for_keymanager(once, client)
        assert added_again == []
        assert twice == once
        assert present  # all desired now present

    def test_rejects_unsupported_client(self) -> None:
        with pytest.raises(KeymanagerValidationError, match="not supported"):
            plan_keymanager_flags(SERVICE_TEMPLATES["lighthouse"], "teku")

    def test_lighthouse_skips_existing_http(self) -> None:
        content = """[Service]
ExecStart=/usr/local/bin/lighthouse vc \\
    --network=mainnet \\
    --http
"""
        _, already, to_add = plan_keymanager_flags(content, "lighthouse")
        assert "--http" in already
        assert "--http-port=5062" in to_add
        assert "--http" not in to_add


class TestPrysmWalletReady:
    def test_scrape_wallet_dir_from_service(self) -> None:
        content = SERVICE_TEMPLATES["prysm"]
        # Template uses --datadir, not wallet-dir; add explicit flag
        content = content.replace(
            "--datadir=/var/lib/prysm_validator \\",
            "--datadir=/var/lib/prysm_validator \\\n"
            "    --wallet-dir=/custom/prysm/wallet \\",
        )
        assert scrape_prysm_wallet_dir(content) == "/custom/prysm/wallet"

    def test_missing_wallet_dir_not_ready(self, tmp_path, monkeypatch) -> None:
        missing = tmp_path / "no_such_wallet"
        # Isolate from host /home/validator/.../auth-token if present
        monkeypatch.setattr(
            "deploy.keymanager.PRYSM_VALIDATOR_HOME_AUTH_TOKEN",
            str(tmp_path / "no_home_auth_token"),
        )
        monkeypatch.setattr(
            "deploy.keymanager.os.path.expanduser",
            lambda _p: str(tmp_path / "empty_home"),
        )
        info = check_prysm_wallet_ready(wallet_dir=str(missing))
        assert info["ready"] is False
        assert info["wallet_dir_exists"] is False
        assert "wallet not initialized" in info["message"].lower()

    def test_wallet_dir_without_auth_token_not_ready(
        self, tmp_path, monkeypatch
    ) -> None:
        wallet = tmp_path / "validator_keys"
        wallet.mkdir()
        # Ensure home-path fallback does not accidentally exist on the host.
        monkeypatch.setattr(
            "deploy.keymanager.PRYSM_VALIDATOR_HOME_AUTH_TOKEN",
            str(tmp_path / "no_home_auth_token"),
        )
        monkeypatch.setattr(
            "deploy.keymanager.os.path.expanduser",
            lambda _p: str(tmp_path / "empty_home"),
        )
        info = check_prysm_wallet_ready(wallet_dir=str(wallet))
        assert info["ready"] is False
        assert info["wallet_dir_exists"] is True
        assert info["auth_token_exists"] is False
        assert info["message"] == PRYSM_WALLET_NOT_READY_MSG

    def test_wallet_with_auth_token_ready(self, tmp_path) -> None:
        wallet = tmp_path / "validator_keys"
        wallet.mkdir()
        (wallet / "auth-token").write_text(
            "http://127.0.0.1:7500\neyJhbGciOiJIUzI1NiJ9.test\n",
            encoding="utf-8",
        )
        info = check_prysm_wallet_ready(wallet_dir=str(wallet))
        assert info["ready"] is True
        assert info["auth_token_exists"] is True
        assert info["auth_token_path"] == str(wallet / "auth-token")
        require_prysm_wallet_ready(wallet_dir=str(wallet))  # no raise

    def test_home_validator_auth_token_ready_without_wallet_dir(
        self, tmp_path, monkeypatch
    ) -> None:
        """Prysm often stores auth-token under /home/validator/.eth2validators/…"""
        home_token = tmp_path / "validator_home" / "auth-token"
        home_token.parent.mkdir(parents=True)
        home_token.write_text(
            "http://127.0.0.1:7500\neyJhbGciOiJIUzI1NiJ9.home\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "deploy.keymanager.PRYSM_VALIDATOR_HOME_AUTH_TOKEN",
            str(home_token),
        )
        missing_wallet = tmp_path / "missing_wallet_dir"
        info = check_prysm_wallet_ready(wallet_dir=str(missing_wallet))
        assert info["ready"] is True
        assert info["wallet_dir_exists"] is False
        assert info["auth_token_exists"] is True
        assert info["auth_token_path"] == str(home_token)
        assert "wallet not initialized" not in info["message"].lower()

    def test_prefers_wallet_dir_token_over_home(
        self, tmp_path, monkeypatch
    ) -> None:
        wallet = tmp_path / "validator_keys"
        wallet.mkdir()
        wallet_token = wallet / "auth-token"
        wallet_token.write_text("wallet-jwt-token\n", encoding="utf-8")
        home_token = tmp_path / "home_auth"
        home_token.write_text("home-jwt-token\n", encoding="utf-8")
        monkeypatch.setattr(
            "deploy.keymanager.PRYSM_VALIDATOR_HOME_AUTH_TOKEN",
            str(home_token),
        )
        info = check_prysm_wallet_ready(wallet_dir=str(wallet))
        assert info["ready"] is True
        assert info["auth_token_path"] == str(wallet_token)

    def test_require_raises_clear_message(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            "deploy.keymanager.PRYSM_VALIDATOR_HOME_AUTH_TOKEN",
            str(tmp_path / "no_home_token"),
        )
        monkeypatch.setattr(
            "deploy.keymanager.os.path.expanduser",
            lambda _p: str(tmp_path / "empty_home"),
        )
        with pytest.raises(KeymanagerError, match="Prysm wallet not initialized"):
            require_prysm_wallet_ready(wallet_dir=str(tmp_path / "missing"))

    def test_auth_token_candidates_include_validator_home(self) -> None:
        cands = prysm_auth_token_candidates(
            "/var/lib/prysm_validator/validator_keys"
        )
        assert cands[0].endswith("validator_keys/auth-token")
        assert PRYSM_VALIDATOR_HOME_AUTH_TOKEN in cands
        assert cands.index(PRYSM_VALIDATOR_HOME_AUTH_TOKEN) == 1


class TestEnsurePrysmWallet:
    def test_dry_run_reports_create_when_missing(self, tmp_path) -> None:
        wallet = tmp_path / "validator_keys"
        password = tmp_path / "password.txt"
        data = tmp_path / "prysm_validator"
        result = ensure_prysm_wallet(
            network="mainnet",
            wallet_dir=str(wallet),
            password_file=str(password),
            data_dir=str(data),
            dry_run=True,
        )
        assert result["dry_run"] is True
        assert result["password_created"] is True
        assert result["wallet_created"] is True
        assert not wallet.exists()
        assert not password.exists()

    def test_does_not_overwrite_existing_password_or_wallet(
        self, tmp_path, monkeypatch
    ) -> None:
        data = tmp_path / "prysm_validator"
        wallet = data / "validator_keys"
        wallet.mkdir(parents=True)
        # Non-empty wallet-dir → already present
        marker = wallet / "direct"
        marker.mkdir()
        password = data / "password.txt"
        password.write_text("existing-secret\n", encoding="utf-8")
        original = password.read_text(encoding="utf-8")

        # Avoid touching real /home/validator; own files as current user
        monkeypatch.setattr(
            "deploy.keymanager.PRYSM_VALIDATOR_HOME", str(tmp_path / "home_validator")
        )
        monkeypatch.setattr(
            "deploy.keymanager.PRYSM_VALIDATOR_HOME_AUTH_TOKEN",
            str(tmp_path / "no_token"),
        )
        # Skip chown to system `validator` user in unit tests
        monkeypatch.setattr("deploy.keymanager._chown_path", lambda *_a, **_k: None)

        result = ensure_prysm_wallet(
            network="hoodi",
            wallet_dir=str(wallet),
            password_file=str(password),
            data_dir=str(data),
            dry_run=False,
            owner=os.environ.get("USER", "nobody"),
        )
        assert result["wallet_already_existed"] is True
        assert result["password_already_existed"] is True
        assert result["wallet_created"] is False
        assert result["password_created"] is False
        assert password.read_text(encoding="utf-8") == original
        # wallet create must not have been invoked
        assert marker.is_dir()

    def test_discover_prysm_raises_when_wallet_missing(self, tmp_path) -> None:
        with patch(
            "deploy.keymanager.check_prysm_wallet_ready",
            return_value={
                "ready": False,
                "wallet_dir": str(tmp_path / "missing"),
                "auth_token_path": str(tmp_path / "missing" / "auth-token"),
                "wallet_dir_exists": False,
                "auth_token_exists": False,
                "message": PRYSM_WALLET_NOT_READY_MSG,
            },
        ):
            with pytest.raises(KeymanagerError, match="Prysm wallet not initialized"):
                KeymanagerClient.discover(client="prysm", timeout=0.2, ports=[39997])

    def test_other_clients_discover_unaffected(self) -> None:
        # Missing service / closed ports → None, not Prysm wallet error
        result = KeymanagerClient.discover(
            client="lighthouse", timeout=0.15, ports=[39996]
        )
        assert result is None


class TestEnsureNimbusTokenFile:
    def test_creates_token_file_once(self, tmp_path) -> None:
        token_path = tmp_path / "nimbus_validator" / "api-token.txt"
        result = ensure_nimbus_keymanager_token_file(
            token_path=str(token_path),
            owner=os.environ.get("USER", "nobody"),
        )
        assert result["created"] is True
        assert result["already_existed"] is False
        assert token_path.is_file()
        assert (token_path.stat().st_mode & 0o777) == 0o600
        token = token_path.read_text(encoding="utf-8").strip()
        assert len(token) == 64  # 32 bytes hex
        assert all(c in "0123456789abcdef" for c in token)

        # Idempotent: do not overwrite
        again = ensure_nimbus_keymanager_token_file(
            token_path=str(token_path),
            owner=os.environ.get("USER", "nobody"),
        )
        assert again["created"] is False
        assert again["already_existed"] is True
        assert token_path.read_text(encoding="utf-8").strip() == token

    def test_dry_run_does_not_create(self, tmp_path) -> None:
        token_path = tmp_path / "api-token.txt"
        result = ensure_nimbus_keymanager_token_file(
            token_path=str(token_path),
            dry_run=True,
        )
        assert result["created"] is True  # would create
        assert result["dry_run"] is True
        assert not token_path.exists()

    def test_nimbus_enable_flags_include_token_file(self) -> None:
        flags = _ENABLE_FLAGS["nimbus"]
        assert any(
            f.startswith("--keymanager-token-file=") for f in flags
        )
        # Other clients unchanged (no token-file flag)
        for client in ("lighthouse", "lodestar", "prysm"):
            assert not any("token-file" in f for f in _ENABLE_FLAGS[client])


class TestEnableKeymanagerDryRun:
    def test_dry_run_returns_summary_without_writing(self) -> None:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".service", delete=False
        ) as fh:
            fh.write(SERVICE_TEMPLATES["lodestar"])
            path = fh.name
        try:
            before = open(path, encoding="utf-8").read()
            summary = enable_keymanager_api(
                "lodestar",
                network="hoodi",
                dry_run=True,
                service_path=path,
            )
            after = open(path, encoding="utf-8").read()

            assert after == before
            assert summary["dry_run"] is True
            assert summary["client"] == "lodestar"
            assert summary["network"] == "hoodi"
            assert summary["service_path"] == path
            assert summary["backup_path"] is None
            assert summary["changed"] is True
            assert summary["restarted"] is False
            assert "--keymanager" in summary["flags_added"]
            assert summary["base_url"] == "http://127.0.0.1:5062"
            assert "dry_run" in summary["message"]
        finally:
            os.remove(path)

    def test_dry_run_already_enabled(self) -> None:
        content, added, _ = patch_service_for_keymanager(
            SERVICE_TEMPLATES["prysm"], "prysm"
        )
        assert added
        with tempfile.NamedTemporaryFile(
            "w", suffix=".service", delete=False
        ) as fh:
            fh.write(content)
            path = fh.name
        try:
            ready = {
                "ready": True,
                "wallet_dir": "/var/lib/prysm_validator/validator_keys",
                "auth_token_path": "/var/lib/prysm_validator/validator_keys/auth-token",
                "wallet_dir_exists": True,
                "auth_token_exists": True,
                "message": "Prysm wallet ready",
            }
            bootstrap = {
                "dry_run": True,
                "wallet_created": False,
                "password_created": False,
                "wallet_already_existed": True,
                "password_already_existed": True,
                "message": "dry_run: wallet already present",
            }
            with patch(
                "deploy.keymanager.check_prysm_wallet_ready", return_value=ready
            ), patch(
                "deploy.keymanager.ensure_prysm_wallet", return_value=bootstrap
            ):
                summary = enable_keymanager_api(
                    "prysm", dry_run=True, service_path=path
                )
            assert summary["changed"] is False
            assert summary["flags_added"] == []
            assert "already present" in summary["message"]
            assert summary["base_url"] == "http://127.0.0.1:7500"
        finally:
            os.remove(path)

    def test_enable_prysm_fails_when_wallet_missing(self) -> None:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".service", delete=False
        ) as fh:
            fh.write(SERVICE_TEMPLATES["prysm"])
            path = fh.name
        try:
            bootstrap = {
                "dry_run": False,
                "wallet_created": False,
                "password_created": False,
                "wallet_already_existed": False,
                "password_already_existed": False,
                "message": "failed",
            }
            with patch(
                "deploy.keymanager.ensure_prysm_wallet", return_value=bootstrap
            ), patch(
                "deploy.keymanager.check_prysm_wallet_ready",
                return_value={
                    "ready": False,
                    "wallet_dir": "/var/lib/prysm_validator/validator_keys",
                    "auth_token_path": (
                        "/var/lib/prysm_validator/validator_keys/auth-token"
                    ),
                    "wallet_dir_exists": False,
                    "auth_token_exists": False,
                    "message": PRYSM_WALLET_NOT_READY_MSG,
                },
            ):
                with pytest.raises(
                    KeymanagerError, match="Prysm wallet not initialized"
                ):
                    enable_keymanager_api(
                        "prysm", dry_run=False, service_path=path
                    )
        finally:
            os.remove(path)

    def test_prysm_enable_flags_include_rpc_and_rest(self) -> None:
        flags = _ENABLE_FLAGS["prysm"]
        assert "--rpc" in flags
        assert "--rpc-host=127.0.0.1" in flags
        assert "--rpc-port=7500" in flags
        assert "--enable-beacon-rest-api" in flags

    def test_dry_run_rejects_teku(self) -> None:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".service", delete=False
        ) as fh:
            fh.write(SERVICE_TEMPLATES["nimbus"])
            path = fh.name
        try:
            with pytest.raises(KeymanagerValidationError, match="not supported"):
                enable_keymanager_api("teku", dry_run=True, service_path=path)
        finally:
            os.remove(path)
