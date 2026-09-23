#!/usr/bin/env python3
"""Find a usable prerelease/RC or previous-stable tag for each EthPillar client.

``find_rc`` only accepts prereleases that are **semver-newer than official
LATEST**. Older prereleases (Lighthouse ``v8.0.0-rc.2`` when LATEST is
``v8.0.0+``, Teku ``22.9.1-RC1``, Nimbus ``v0.6.6``, same-series RCs like
``1.48.0-rc.0`` vs ``1.48.0``) are ignored so Upgrade-matrix deploy does not
install junk/ancient binaries.

``find_upgrade_seed`` prefers that newer RC when one exists, otherwise a
**previous resolvable stable** that is strictly older than LATEST, client-shaped,
and within a small semver window (same major, at most two minors behind) so
ancient tags are not used as upgrade bait.

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

from client_requirements import compare_versions, parse_version
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
_GETH_DOWNLOAD_VERSION = re.compile(
    r"geth-linux-(?:amd64|arm64)-([0-9.]+)-[a-f0-9]+\.tar\.gz"
)
# Previous-stable window: same major, at most this many minors behind LATEST.
_MAX_PREVIOUS_MINOR_DELTA = 2


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


def is_strictly_older_than_latest(candidate: str, latest: str) -> bool:
    """Return True when *candidate* is semver-older than official *latest*."""
    if not candidate or not latest:
        return False
    if candidate == latest:
        return False
    try:
        return compare_versions(candidate, latest) < 0
    except (TypeError, ValueError):
        return False


def is_sane_previous_stable(
    candidate: str,
    latest: str,
    max_minor_delta: int = _MAX_PREVIOUS_MINOR_DELTA,
) -> bool:
    """Return True when *candidate* is a close previous stable, not ancient junk.

    Requires a strictly older tag, the same major as *latest*, and a minor
    distance of at most *max_minor_delta* (default 2). That rejects Nimbus
    ``v0.6.6`` vs ``v25.9.2`` and Lodestar ``1.0.0`` vs ``1.48.0`` while still
    allowing one skipped minor (Teku ``25.7.0`` vs ``25.9.3``).
    """
    if not is_strictly_older_than_latest(candidate, latest):
        return False
    try:
        cand_parts = parse_version(candidate)
        latest_parts = parse_version(latest)
    except (TypeError, ValueError):
        return False
    if cand_parts[0] != latest_parts[0]:
        return False
    if latest_parts[1] - cand_parts[1] > max_minor_delta:
        return False
    return True


def base_semver(version: str) -> str:
    """Return ``major.minor.patch`` from *version*, dropping pre/build suffixes."""
    text = (version or "").strip()
    if text.lower().startswith("v") and len(text) > 1 and text[1].isdigit():
        text = text[1:]
    text = text.split("-", 1)[0]
    text = text.split("+", 1)[0]
    return text


def commits_compatible(installed: str = "", expected: str = "") -> bool:
    """Return True when commits are missing or one is a prefix of the other."""
    inst = (installed or "").strip().lower()
    tag = (expected or "").strip().lower()
    if not inst or not tag:
        return True
    return tag.startswith(inst) or inst.startswith(tag)


def matches_forced_seed(
    installed: str,
    expected: str,
    inst_commit: str = "",
    tag_commit: str = "",
) -> bool:
    """Return True when *installed* matches a forced deploy seed.

    Some RC/nightly binaries print final-release semver without ``-rc.N``
    (Ethrex ``28.1.0`` vs tag ``v28.1.0-rc.1``). Accept the same base version
    and, when both commits are known, a prefix match either way.
    """
    if not installed or not expected:
        return False
    if base_semver(installed) != base_semver(expected):
        return False
    return commits_compatible(inst_commit, tag_commit)


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


def is_stable_candidate(rel: dict, client: str = "") -> bool:
    """Return True when *rel* is a non-draft, non-prerelease client stable."""
    if rel.get("draft") or rel.get("prerelease"):
        return False
    tag = rel.get("tag_name") or ""
    if is_junk_tag(tag) or not looks_like_client_version_tag(tag):
        return False
    if _RC_TAG.search(tag):
        return False
    if client == "nethermind" and not is_nethermind_client_tag(tag):
        return False
    return True


def _release_info_ok(client: str, tag: str) -> bool:
    try:
        info = get_client_release_info(client, tag)
        return bool(info.get("version") and info.get("download_urls"))
    except Exception:
        return False


def _skip_row(client: str, latest_tag: str | None, reason: str) -> dict:
    """Build a skip-status discovery row."""
    return {
        "client": client,
        "rc_tag": None,
        "seed_tag": None,
        "seed_kind": None,
        "latest": latest_tag,
        "status": "skip",
        "reason": reason,
    }


def parse_geth_download_versions(html: str) -> list[str]:
    """Return unique ``vX.Y.Z`` tags parsed from geth.ethereum.org HTML."""
    seen: set[str] = set()
    out: list[str] = []
    for ver in _GETH_DOWNLOAD_VERSION.findall(html or ""):
        tag = f"v{ver}"
        if tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    return out


def _geth_download_html() -> str:
    """Fetch the Geth downloads page (stables only)."""
    res = requests.get("https://geth.ethereum.org/downloads", timeout=30)
    res.raise_for_status()
    return res.text


def _pick_best_previous_stable(
    client: str,
    latest_tag: str,
    tags: list[str],
) -> tuple[str | None, list[str]]:
    """Return (best sane older stable, ignored tags) from *tags*."""
    ignored: list[str] = []
    best_tag: str | None = None
    for tag in tags:
        if tag == latest_tag:
            continue
        if not is_sane_previous_stable(tag, latest_tag):
            ignored.append(tag)
            continue
        if not _release_info_ok(client, tag):
            continue
        if best_tag is None or is_newer_than_latest(tag, best_tag):
            best_tag = tag
    return best_tag, ignored


def _previous_stable_reason(ignored: list[str]) -> str:
    """Explain why no previous stable was selected."""
    reason = (
        "no resolvable previous stable strictly older than LATEST "
        "within the same major / two-minor window"
    )
    if ignored:
        preview = ", ".join(ignored[:6])
        extra = f" (+{len(ignored) - 6} more)" if len(ignored) > 6 else ""
        reason = f"{reason} (ignored ancient/out-of-window: {preview}{extra})"
    return reason


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


def _find_previous_stable(client: str, repo: str | None, latest_tag: str) -> dict:
    """Find the newest sane previous stable for *client*."""
    if client == "geth":
        tags = parse_geth_download_versions(_geth_download_html())
        best_tag, ignored = _pick_best_previous_stable(client, latest_tag, tags)
    elif not repo:
        return _skip_row(client, latest_tag, "no GitHub repo mapping")
    else:
        tags = [
            rel["tag_name"]
            for rel in _releases(repo)
            if is_stable_candidate(rel, client)
        ]
        best_tag, ignored = _pick_best_previous_stable(client, latest_tag, tags)

    if best_tag:
        return {
            "client": client,
            "rc_tag": None,
            "seed_tag": best_tag,
            "seed_kind": "stable",
            "latest": latest_tag,
            "status": "ok",
            "reason": "previous stable older than LATEST and resolvable via release_info",
        }
    return _skip_row(client, latest_tag, _previous_stable_reason(ignored))


def find_upgrade_seed(client: str, repo: str | None) -> dict:
    """Find a deploy seed: newer RC if possible, else a sane previous stable.

    Soft-skips (``status=skip``) when neither exists so Upgrade-matrix deploy
    stays on official LATEST instead of grabbing ancient junk.
    """
    rc_row = find_rc(client, repo)
    latest_tag = rc_row.get("latest")
    if rc_row.get("status") == "ok" and rc_row.get("rc_tag"):
        return {
            **rc_row,
            "seed_tag": rc_row["rc_tag"],
            "seed_kind": "rc",
        }

    if not latest_tag:
        return {
            **rc_row,
            "seed_tag": None,
            "seed_kind": None,
        }

    stable_row = _find_previous_stable(client, repo, str(latest_tag))
    if stable_row.get("status") == "ok":
        return stable_row

    rc_reason = rc_row.get("reason") or "no newer RC"
    stable_reason = stable_row.get("reason") or "no sane previous stable"
    return _skip_row(
        client,
        str(latest_tag),
        f"{rc_reason}; {stable_reason}",
    )


def main() -> int:
    clients = sys.argv[1:] or list(CLIENT_REPOS)
    use_seed = False
    if clients and clients[0] == "--seed":
        use_seed = True
        clients = clients[1:] or list(CLIENT_REPOS)
    finder = find_upgrade_seed if use_seed else find_rc
    for client in clients:
        client = client.lower()
        if client not in CLIENT_REPOS:
            print(json.dumps({"client": client, "status": "skip", "reason": "unknown client"}))
            continue
        try:
            row = finder(client, CLIENT_REPOS[client])
        except Exception as exc:  # noqa: BLE001 — surface per-client and continue
            row = {
                "client": client,
                "rc_tag": None,
                "seed_tag": None,
                "latest": None,
                "status": "skip",
                "reason": f"error: {exc}",
            }
        print(json.dumps(row), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
