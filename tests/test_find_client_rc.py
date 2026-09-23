"""Offline tests for Upgrade-matrix RC discovery filters."""

from __future__ import annotations

from unittest.mock import patch

from tests.integration.find_client_rc import (
    find_rc,
    is_junk_tag,
    is_nethermind_client_tag,
    is_newer_than_latest,
    is_rc_candidate,
    looks_like_client_version_tag,
)
from tests.integration.latest_override import prepare_rc_overrides


def test_looks_like_client_version_tag():
    assert looks_like_client_version_tag("v8.0.0-rc.2")
    assert looks_like_client_version_tag("1.48.0-rc.0")
    assert looks_like_client_version_tag("22.9.1-RC1")
    assert looks_like_client_version_tag("25.9.3")
    assert looks_like_client_version_tag("v0.6.6")
    assert not looks_like_client_version_tag("bootnode-2.0.0")
    assert not looks_like_client_version_tag("nightly")
    assert not looks_like_client_version_tag("")


def test_junk_and_nethermind_client_allowlist():
    assert is_junk_tag("bootnode-2.0.0")
    assert is_junk_tag("v1.2.3-nightly")
    assert is_junk_tag("devnet-foo")
    assert not is_junk_tag("1.35.0-rc")
    assert not is_junk_tag("v8.0.1-rc.0")

    assert is_nethermind_client_tag("1.35.0-rc")
    assert is_nethermind_client_tag("v1.35.0-rc.1")
    assert is_nethermind_client_tag("1.34.1")
    assert not is_nethermind_client_tag("bootnode-2.0.0")
    assert not is_nethermind_client_tag("nethermind-bootnode-2.0.0")
    assert not is_nethermind_client_tag("1.35.0-bootnode")


def test_is_newer_than_latest_semver():
    # Same series RC is older than the stable.
    assert not is_newer_than_latest("1.48.0-rc.0", "1.48.0")
    assert not is_newer_than_latest("28.0.0-rc.1", "28.0.0")
    assert not is_newer_than_latest("v8.0.0-rc.2", "v8.0.0")
    assert not is_newer_than_latest("v8.0.0-rc.2", "v8.0.1")
    assert not is_newer_than_latest("22.9.1-RC1", "25.9.3")
    assert not is_newer_than_latest("v0.6.6", "v25.9.2")
    assert not is_newer_than_latest("v1.0.0-rc.2", "v1.8.2")
    assert not is_newer_than_latest("v1.8.2", "v1.8.2")

    assert is_newer_than_latest("1.48.1-rc.0", "1.48.0")
    assert is_newer_than_latest("v8.0.1-rc.0", "v8.0.0")
    assert is_newer_than_latest("1.35.0-rc", "1.34.1")
    assert is_newer_than_latest("25.10.0-RC1", "25.9.3")


def test_is_rc_candidate_filters_nethermind_bootnode():
    assert not is_rc_candidate(
        {"tag_name": "bootnode-2.0.0", "prerelease": True},
        "nethermind",
    )
    assert is_rc_candidate(
        {"tag_name": "1.35.0-rc", "prerelease": True},
        "nethermind",
    )
    assert not is_rc_candidate(
        {"tag_name": "1.35.0-rc", "prerelease": True, "draft": True},
        "nethermind",
    )
    assert not is_rc_candidate(
        {"tag_name": "v8.0.0", "prerelease": False},
        "lighthouse",
    )
    assert is_rc_candidate(
        {"tag_name": "v8.0.1-rc.0", "prerelease": True},
        "lighthouse",
    )


def _rel(tag: str, *, pre: bool = True) -> dict:
    return {"tag_name": tag, "prerelease": pre, "draft": False}


def test_find_rc_skips_older_than_latest_and_picks_newer():
    releases = [
        _rel("v8.0.0"),  # current stable, not an RC
        _rel("v8.0.0-rc.2"),  # older than LATEST — CI bad pick
        _rel("v8.0.1-rc.0"),  # newer than LATEST
        _rel("v7.1.0-rc.0"),
    ]

    def fake_info(client, tag):
        if tag == "LATEST":
            return {"version": "v8.0.0", "download_urls": ["https://example/stable"]}
        return {"version": tag, "download_urls": [f"https://example/{tag}"]}

    with (
        patch("tests.integration.find_client_rc.get_client_release_info", side_effect=fake_info),
        patch("tests.integration.find_client_rc._releases", return_value=releases),
    ):
        row = find_rc("lighthouse", "sigp/lighthouse")
    assert row["status"] == "ok"
    assert row["rc_tag"] == "v8.0.1-rc.0"
    assert row["latest"] == "v8.0.0"


def test_find_rc_skips_ci_bad_picks_when_no_newer_exists():
    cases = [
        ("nethermind", "1.34.1", [_rel("bootnode-2.0.0"), _rel("1.34.0-rc")]),
        ("nimbus", "v25.9.2", [_rel("v0.6.6"), _rel("v25.9.0-rc")]),
        ("teku", "25.9.3", [_rel("22.9.1-RC1")]),
        ("reth", "v1.8.2", [_rel("v1.0.0-rc.2")]),
        ("lodestar", "1.48.0", [_rel("1.48.0-rc.0")]),
        ("ethrex", "28.0.0", [_rel("28.0.0-rc.1")]),
    ]

    for client, latest, releases in cases:
        def fake_info(_client, tag, latest=latest):
            if tag == "LATEST":
                return {"version": latest, "download_urls": ["https://example/stable"]}
            return {"version": tag, "download_urls": [f"https://example/{tag}"]}

        with (
            patch("tests.integration.find_client_rc.get_client_release_info", side_effect=fake_info),
            patch("tests.integration.find_client_rc._releases", return_value=releases),
        ):
            row = find_rc(client, "org/repo")
        assert row["status"] == "skip", (client, row)
        assert row["rc_tag"] is None
        assert row["latest"] == latest
        assert "newer than LATEST" in row["reason"]


def test_prepare_overrides_rejects_older_rc_from_finder(tmp_path):
    path = str(tmp_path / "override.json")

    def fake_find_rc(client: str, _repo):
        return {
            "client": client,
            "rc_tag": "1.48.0-rc.0",
            "latest": "1.48.0",
            "status": "ok",
            "reason": "should be rejected by prepare",
        }

    result = prepare_rc_overrides(["lodestar"], path=path, find_rc_fn=fake_find_rc)
    assert result["clients"] == {}
    assert not (tmp_path / "override.json").exists()
