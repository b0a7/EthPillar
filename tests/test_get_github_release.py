"""Unit tests for GitHub release tag resolution in deploy.common."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hashlib

from deploy.common import (
    _fetch_github_release_by_tag,
    _find_github_release_by_normalized_tag,
    _github_release_tag_candidates,
    _normalize_release_version_key,
    _validsig_fingerprints,
    download_file,
    get_github_release,
    get_github_tag_commit,
    release_info_from_github,
    verify_detached_signature,
)


class TestNormalizeReleaseVersionKey:
    @pytest.mark.parametrize(
        ("tag", "expected"),
        [
            ("v1.11.0", "1.11"),
            ("v1.11", "1.11"),
            ("1.11.0", "1.11"),
            ("v1.12-alpha1", "1.12-alpha1"),
            ("V2.0.0", "2.0"),
        ],
    )
    def test_normalizes_patch_zero_and_prefix(self, tag: str, expected: str):
        assert _normalize_release_version_key(tag) == expected


class TestGithubReleaseTagCandidates:
    def test_v1_11_0_includes_major_minor_aliases(self):
        assert _github_release_tag_candidates("v1.11.0") == [
            "v1.11.0",
            "1.11.0",
            "v1.11",
            "1.11",
        ]

    def test_preserves_exact_tag_first(self):
        assert _github_release_tag_candidates("v1.12") == ["v1.12", "1.12"]


class TestGetGithubRelease:
    @pytest.fixture(autouse=True)
    def _mock_tag_commit(self):
        with patch("deploy.common.get_github_tag_commit", return_value="abc123commit"):
            yield

    @patch("deploy.common._fetch_github_release_by_tag")
    def test_returns_exact_tag_match(self, mock_fetch):
        mock_fetch.return_value = {"tag_name": "v1.12", "assets": []}
        release = get_github_release("flashbots/mev-boost", "v1.12")
        assert release["tag_name"] == "v1.12"
        assert release["commit"] == "abc123commit"
        mock_fetch.assert_called_once_with("flashbots/mev-boost", "v1.12")

    @patch("deploy.common._find_github_release_by_normalized_tag")
    @patch("deploy.common._fetch_github_release_by_tag")
    def test_resolves_v1_11_0_to_v1_11_release(self, mock_fetch, mock_find):
        mock_fetch.side_effect = [None, None, {"tag_name": "v1.11", "assets": []}, None]
        release = get_github_release("flashbots/mev-boost", "v1.11.0")
        assert release["tag_name"] == "v1.11"
        assert release["commit"] == "abc123commit"
        mock_find.assert_not_called()

    @patch("deploy.common._find_github_release_by_normalized_tag")
    @patch("deploy.common._fetch_github_release_by_tag")
    def test_falls_back_to_release_scan(self, mock_fetch, mock_find):
        mock_fetch.return_value = None
        mock_find.return_value = {"tag_name": "v1.11", "assets": []}
        release = get_github_release("flashbots/mev-boost", "v1.11.0")
        assert release["tag_name"] == "v1.11"
        mock_find.assert_called_once_with("flashbots/mev-boost", "v1.11.0")

    @patch("deploy.common.requests.get")
    def test_latest_uses_latest_endpoint(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {"tag_name": "v1.12"})
        release = get_github_release("flashbots/mev-boost", "LATEST")
        assert release["tag_name"] == "v1.12"
        assert release["commit"] == "abc123commit"
        mock_get.assert_called_once()
        assert mock_get.call_args.args[0].endswith("/releases/latest")


class TestFindGithubReleaseByNormalizedTag:
    @patch("deploy.common.requests.get")
    def test_skips_drafts_and_matches_normalized_tag(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [
                {"tag_name": "v1.12", "draft": False},
                {"tag_name": "v1.11", "draft": False},
                {"tag_name": "v1.10.0", "draft": True},
            ],
        )
        release = _find_github_release_by_normalized_tag("flashbots/mev-boost", "v1.11.0")
        assert release["tag_name"] == "v1.11"


class TestGetGithubTagCommit:
    @patch("deploy.common.requests.get")
    def test_lightweight_tag_returns_commit_sha(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "ref": "refs/tags/v1.0.0",
                "object": {"type": "commit", "sha": "abc123lightweight"},
            },
        )
        assert get_github_tag_commit("org/repo", "v1.0.0") == "abc123lightweight"

    @patch("deploy.common.requests.get")
    def test_annotated_tag_peels_to_commit(self, mock_get):
        ref_resp = MagicMock(
            status_code=200,
            json=lambda: {
                "ref": "refs/tags/v1.0.0",
                "object": {
                    "type": "tag",
                    "sha": "tagobjsha",
                    "url": "https://api.github.com/repos/org/repo/git/tags/tagobjsha",
                },
            },
        )
        tag_resp = MagicMock(
            status_code=200,
            json=lambda: {"object": {"type": "commit", "sha": "peeledcommitsha"}},
        )
        mock_get.side_effect = [ref_resp, tag_resp]
        assert get_github_tag_commit("org/repo", "v1.0.0") == "peeledcommitsha"
        assert mock_get.call_count == 2

    @patch("deploy.common.requests.get")
    def test_missing_tag_returns_none(self, mock_get):
        mock_get.return_value = MagicMock(status_code=404)
        assert get_github_tag_commit("org/repo", "v9.9.9") is None

    def test_empty_tag_returns_none(self):
        assert get_github_tag_commit("org/repo", "") is None
        assert get_github_tag_commit("org/repo", "   ") is None

    def test_release_info_from_github_copies_commit(self):
        digest = "ab" * 32
        info = release_info_from_github(
            {
                "tag_name": "v1.0.0",
                "commit": "deadbeef",
                "assets": [{"name": "f", "browser_download_url": "u", "digest": f"sha256:{digest}"}],
            },
            ["u"],
            ["f"],
        )
        assert info == {
            "version": "v1.0.0",
            "download_urls": ["u"],
            "filenames": ["f"],
            "sha256": [digest],
            "commit": "deadbeef",
        }

    def test_release_info_from_github_omits_missing_commit(self):
        digest = "cd" * 32
        info = release_info_from_github(
            {
                "tag_name": "v1.0.0",
                "assets": [{"name": "f", "browser_download_url": "u", "digest": f"sha256:{digest}"}],
            },
            ["u"],
            ["f"],
        )
        assert "commit" not in info
        assert info["sha256"] == [digest]

    def test_release_info_refuses_asset_without_checksum(self):
        with pytest.raises(ValueError, match="No sha256 digest"):
            release_info_from_github(
                {
                    "tag_name": "v1.0.0",
                    "assets": [{"name": "f", "browser_download_url": "u"}],
                },
                ["u"],
                ["f"],
            )

    @patch("deploy.common.requests.get")
    def test_release_info_uses_sha256_sidecar_when_digest_missing(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            text="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef  client.tar.gz\n",
            raise_for_status=lambda: None,
        )
        info = release_info_from_github(
            {
                "tag_name": "v1.0.0",
                "assets": [
                    {"name": "client.tar.gz", "browser_download_url": "https://example/client.tar.gz"},
                    {
                        "name": "client.tar.gz.sha256",
                        "browser_download_url": "https://example/client.tar.gz.sha256",
                    },
                ],
            },
            ["https://example/client.tar.gz"],
            ["client.tar.gz"],
        )
        assert info["sha256"] == ["0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"]
        mock_get.assert_called_once()
        assert "Authorization" not in (mock_get.call_args.kwargs.get("headers") or {})

    @patch("deploy.common.requests.get")
    def test_release_info_rejects_sidecar_that_disagrees_with_github_digest(self, mock_get):
        digest = "ab" * 32
        mock_get.return_value = MagicMock(
            text=f"{'cd' * 32}  client.tar.gz\n",
            raise_for_status=lambda: None,
        )
        with pytest.raises(ValueError, match="does not match"):
            release_info_from_github(
                {
                    "tag_name": "v1.0.0",
                    "assets": [
                        {
                            "name": "client.tar.gz",
                            "browser_download_url": "https://example/client.tar.gz",
                            "digest": f"sha256:{digest}",
                        },
                        {
                            "name": "client.tar.gz.sha256",
                            "browser_download_url": "https://example/client.tar.gz.sha256",
                        },
                    ],
                },
                ["https://example/client.tar.gz"],
                ["client.tar.gz"],
            )

    @patch("deploy.common.get_github_tag_commit", return_value="peeledsha")
    @patch("deploy.common.requests.get")
    def test_get_github_release_attaches_commit(self, mock_get, _mock_commit):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"tag_name": "v1.0.0", "assets": []},
        )
        release = get_github_release("org/repo", "LATEST")
        assert release["tag_name"] == "v1.0.0"
        assert release["commit"] == "peeledsha"


class _Body:
    def __init__(self, payload: bytes):
        self.headers = {"content-length": str(len(payload))}
        self._payload = payload

    def raise_for_status(self):
        return None

    def iter_content(self, block_size):
        yield self._payload


class TestDownloadFileChecksum:
    @patch("deploy.common.requests.get")
    def test_matching_sha256_keeps_file(self, mock_get, tmp_path):
        payload = b"client-bytes"
        mock_get.return_value = _Body(payload)
        dest = tmp_path / "client.tar.gz"
        download_file(
            "https://example/client.tar.gz",
            str(dest),
            "Client",
            expected_sha256=hashlib.sha256(payload).hexdigest(),
        )
        assert dest.read_bytes() == payload

    @patch("deploy.common.requests.get")
    def test_sha256_mismatch_deletes_file(self, mock_get, tmp_path):
        mock_get.return_value = _Body(b"tampered")
        dest = tmp_path / "client.tar.gz"
        with pytest.raises(SystemExit):
            download_file(
                "https://example/client.tar.gz",
                str(dest),
                "Client",
                expected_sha256="ab" * 32,
            )
        assert not dest.exists()


GETH_FINGERPRINT = "FDE5A1A044FA13D2F7ADA019A61A13569BA28146"


class TestDetachedSignature:
    def test_validsig_parser_reads_fingerprint(self):
        status = f"[GNUPG:] VALIDSIG {GETH_FINGERPRINT} 1 2 3 4 5 6 7 8\n"
        assert _validsig_fingerprints(status) == [GETH_FINGERPRINT]

    @patch("deploy.common._ensure_gpg", return_value="gpg")
    @patch("deploy.common.subprocess.run")
    def test_accepts_validsig_from_pinned_key(self, mock_run, _gpg, tmp_path):
        payload = tmp_path / "geth.tar.gz"
        signature = tmp_path / "geth.tar.gz.asc"
        payload.write_bytes(b"archive")
        signature.write_text("-----BEGIN PGP SIGNATURE-----\n-----END PGP SIGNATURE-----\n")
        mock_run.side_effect = [
            MagicMock(returncode=0, stderr=""),
            MagicMock(returncode=0, stdout=f"fpr:::::::::{GETH_FINGERPRINT}:\n"),
            MagicMock(
                returncode=0,
                stdout=f"[GNUPG:] VALIDSIG {GETH_FINGERPRINT} 1 2 3 4 5 6 7 8\n",
                stderr="",
            ),
        ]
        verify_detached_signature(str(payload), str(signature), "geth")

    @patch("deploy.common._ensure_gpg", return_value="gpg")
    @patch("deploy.common.subprocess.run")
    def test_rejects_signature_from_a_different_key(self, mock_run, _gpg, tmp_path):
        payload = tmp_path / "geth.tar.gz"
        signature = tmp_path / "geth.tar.gz.asc"
        payload.write_bytes(b"archive")
        signature.write_text("sig")
        mock_run.side_effect = [
            MagicMock(returncode=0, stderr=""),
            MagicMock(returncode=0, stdout=f"fpr:::::::::{GETH_FINGERPRINT}:\n"),
            MagicMock(
                returncode=0,
                stdout="[GNUPG:] VALIDSIG " + ("ab" * 20) + " 1 2 3 4 5 6 7 8\n",
                stderr="",
            ),
        ]
        with pytest.raises(ValueError, match="rejected"):
            verify_detached_signature(str(payload), str(signature), "geth")
