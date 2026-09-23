#!/usr/bin/env python3
"""Find a usable prerelease/RC tag for each EthPillar client.

Only tags that are **semver-newer than official LATEST** qualify. Older
prereleases (Lighthouse ``v8.0.0-rc.2`` when LATEST is ``v8.0.0+``, Teku
``22.9.1-RC1``, Nimbus ``v0.6.6``, same-series RCs like ``1.48.0-rc.0`` vs
``1.48.0``) are ignored so Upgrade-matrix deploy does not install junk/ancient
binaries.

Nethermind is restricted to client version tags (``1.35.0-rc``), not other
NethermindEth artifacts such as ``bootnode-2.0.0``.

Prints JSON lines: {"client": "...", "rc_tag": "...", "latest": "...", "status": "ok|skip", "reason": "..."}
"""
from __future__ import annotations

import json
import os
import re
import sys

import requests

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO)

from client_requirements import compare_versions
from deploy.common import _github_api_headers, get_client_release_info

# client -> GitHub repo (None = Geth / special)
CLIENT_REPOS: dict[str, str | None] = {
    "besu": "besu-eth/besu",
    "nethermind": "NethermindEth/nethermind",
    "reth": "paradigmxyz/reth",
    "erigon": "erigontech/erigon",
    "ethrex": "lambdaclass/ethrex",
    "geth": "ethereum/go-ethereum",
    "lighthouse": "sigp/lighthouse",
    "lodestar": "ChainSafe/lodestar",
    "teku": "ConsenSys/teku",
    "nimbus": "status-im/nimbus-eth2",
    "grandine": "grandinetech/grandine",
    "prysm": "prysmaticlabs/prysm",
    "mevboost": "flashbots/mev-boost",
    "charon": "ObolNetwork/charon",
}

# Non-client / non-release artifacts that still appear in GitHub releases.
_SKIP_TAG = re.compile(
    r"(?i)(nightly|zisk|devnet|altair|snapshot|bootnode|genesis|fuzz|unreleased|"
    r"launcher|installer|windows-package)"
)
# Must look like a client semver (optional v, optional pre suffix). Rejects bootnode-2.0.0.
_CLIENT_VERSION_TAG = re.compile(
    r"(?i)^v?\d+\.\d+(\.\d+)?(-[0-9A-Za-z.]+)?(\+[0-9A-Za-z.-]+)?$"
)
_RC_TAG = re.compile(r"(?i)(^|[-.])(rc|alpha|beta)([.-]|\d|$)")
# Nethermind execution-client tags only (not bootnode / other org artifacts).
_NETHERMIND_CLIENT_TAG = re.compile(
    r"(?i)^v?\d+\.\d+(\.\d+)?(-(rc|alpha|beta)[0-9A-Za-z.]*)?$"
)
_NETHERMIND_BLOCK = re.compile(r"(?i)(bootnode|launcher|installer|docs|dotnet)")


def is_junk_tag(tag: str) -> bool:
    """Return True when *tag* is a known non-client or ephemeral artifact."""
    return bool(_SKIP_TAG.search(tag or ""))


def looks_like_client_version_tag(tag: str) -> bool:
    """Return True when *tag* looks like ``v1.2.3`` / ``25.9.0-rc.1``."""
    return bool(_CLIENT_VERSION_TAG.match((tag or "").strip()))


def is_nethermind_client_tag(tag: str) -> bool:
    """Return True for Nethermind execution-client tags, not bootnode/other projects."""
    text = (tag or "").strip()
    if not text or _NETHERMIND_BLOCK.search(text) or is_junk_tag(text):
        return False
    return bool(_NETHERMIND_CLIENT_TAG.match(text))


def is_newer_than_latest(candidate: str, latest: str) -> bool:
    """Return True when *candidate* is semver-newer than official *latest*.

    Same major.minor.patch with a prerelease suffix is **older** than the
    stable (``1.48.0-rc.0`` < ``1.48.0``).
    """
    if not candidate or not latest:
        return False
    if candidate == latest:
        return False
    try:
        return compare_versions(candidate, latest) > 0
    except (TypeError, ValueError):
        return False


def _releases(repo: str) -> list[dict]:
    out: list[dict] = []
    for page in range(1, 4):
        res = requests.get(
            f"https://api.github.com/repos/{repo}/releases",
            params={"per_page": 100, "page": page},
            headers=_github_api_headers(),
            timeout=30,
        )
        res.raise_for_status()
        batch = res.json()
        if not batch:
            break
        out.extend(batch)
    return out


def is_rc_candidate(rel: dict, client: str = "") -> bool:
    """Return True when *rel* is a non-draft prerelease/RC of the named client."""
    if rel.get("draft"):
        return False
    tag = rel.get("tag_name") or ""
    if is_junk_tag(tag) or not looks_like_client_version_tag(tag):
        return False
    if client == "nethermind" and not is_nethermind_client_tag(tag):
        return False
    if rel.get("prerelease") or _RC_TAG.search(tag):
        return True
    return False


def _release_info_ok(client: str, tag: str) -> bool:
    try:
        info = get_client_release_info(client, tag)
        return bool(info.get("version") and info.get("download_urls"))
    except Exception:
        return False


def find_rc(client: str, repo: str | None) -> dict:
    latest = get_client_release_info(client, "LATEST")
    latest_tag = latest["version"]

    if client == "geth":
        # EthPillar resolves Geth from geth.ethereum.org (stables only).
        return {
            "client": client,
            "rc_tag": None,
            "latest": latest_tag,
            "status": "skip",
            "reason": "Geth downloads page serves stables only; no RC via release_info",
        }

    if not repo:
        return {
            "client": client,
            "rc_tag": None,
            "latest": latest_tag,
            "status": "skip",
            "reason": "no GitHub repo mapping",
        }

    ignored_older: list[str] = []
    best_tag: str | None = None
    for rel in _releases(repo):
        if not is_rc_candidate(rel, client):
            continue
        tag = rel["tag_name"]
        if tag == latest_tag:
            continue
        if not is_newer_than_latest(tag, latest_tag):
            ignored_older.append(tag)
            continue
        if not _release_info_ok(client, tag):
            continue
        if best_tag is None or is_newer_than_latest(tag, best_tag):
            best_tag = tag

    if best_tag:
        return {
            "client": client,
            "rc_tag": best_tag,
            "latest": latest_tag,
            "status": "ok",
            "reason": "prerelease newer than LATEST and resolvable via release_info",
        }

    reason = "no resolvable RC/prerelease newer than LATEST with assets in recent releases"
    if ignored_older:
        preview = ", ".join(ignored_older[:6])
        extra = f" (+{len(ignored_older) - 6} more)" if len(ignored_older) > 6 else ""
        reason = f"{reason} (ignored older: {preview}{extra})"
    return {
        "client": client,
        "rc_tag": None,
        "latest": latest_tag,
        "status": "skip",
        "reason": reason,
    }


def main() -> int:
    clients = sys.argv[1:] or list(CLIENT_REPOS)
    for client in clients:
        client = client.lower()
        if client not in CLIENT_REPOS:
            print(json.dumps({"client": client, "status": "skip", "reason": "unknown client"}))
            continue
        try:
            row = find_rc(client, CLIENT_REPOS[client])
        except Exception as exc:  # noqa: BLE001 — surface per-client and continue
            row = {
                "client": client,
                "rc_tag": None,
                "latest": None,
                "status": "skip",
                "reason": f"error: {exc}",
            }
        print(json.dumps(row), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
