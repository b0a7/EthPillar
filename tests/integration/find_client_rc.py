#!/usr/bin/env python3
"""Find a usable prerelease/RC tag for each EthPillar client.

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

_SKIP_TAG = re.compile(r"(?i)(nightly|zisk|devnet|altair|snapshot)")
_RC_TAG = re.compile(r"(?i)(^|[-.])(rc|alpha|beta)([.-]|\d|$)")


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


def _is_rc_candidate(rel: dict) -> bool:
    if rel.get("draft"):
        return False
    tag = rel.get("tag_name") or ""
    if _SKIP_TAG.search(tag):
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

    for rel in _releases(repo):
        if not _is_rc_candidate(rel):
            continue
        tag = rel["tag_name"]
        if tag == latest_tag:
            continue
        if _release_info_ok(client, tag):
            return {
                "client": client,
                "rc_tag": tag,
                "latest": latest_tag,
                "status": "ok",
                "reason": "prerelease resolvable via release_info",
            }

    return {
        "client": client,
        "rc_tag": None,
        "latest": latest_tag,
        "status": "skip",
        "reason": "no resolvable RC/prerelease with assets in recent releases",
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
