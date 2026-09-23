"""Test-only LATEST→RC remapping for Upgrade-case deploy.

Upgrade-matrix cases (``--test-updates``) may install a resolvable client
prerelease/RC first, then run the unchanged ``ethpillar upgrade`` / ``--auto``
path so it can move to official LATEST.

Lifecycle:
  1. ``prepare_rc_overrides`` discovers RCs via :mod:`find_client_rc` and writes
     this file **before** deploy. No file (or empty ``clients``) means today's
     LATEST→LATEST behavior.
  2. Integration ``sitecustomize`` wraps ``deploy.common.get_github_release`` so
     deploy-time ``get_release_info("LATEST")`` fetches the RC tag.
  3. Post-deploy version checks read the same file and expect the forced RC tags.
  4. ``clear_override`` removes the file **before** upgrade so LATEST is real.

This module is integration-test scoped. Production CLI upgrade semantics are
unchanged (always official LATEST).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Callable, Iterable

OVERRIDE_PATH = "/tmp/ethpillar-integration-latest-override.json"
ENV_VAR = "ETHPILLAR_INTEGRATION_LATEST_OVERRIDE"
_HOOK_ATTR = "_ethpillar_latest_override_wrapped"
_INTEGRATION_DIR = os.path.dirname(os.path.abspath(__file__))
if _INTEGRATION_DIR not in sys.path:
    sys.path.insert(0, _INTEGRATION_DIR)

# Role / combo tokens that are not GitHub-released clients.
_SKIP_NAMES = frozenset({"", "caplin", "same as cc", "same", "none"})


def override_path(path: str | None = None) -> str:
    """Return the override JSON path (explicit, env, or default)."""
    if path:
        return path
    return os.environ.get(ENV_VAR) or OVERRIDE_PATH


def _ensure_repo_on_path() -> str:
    """Put the EthPillar repo root on ``sys.path`` and return it."""
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    return repo_root


def load_override(path: str | None = None) -> dict[str, Any]:
    """Load ``{clients, repos}`` mappings, or empty dicts when absent/invalid."""
    target = override_path(path)
    try:
        with open(target, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {"clients": {}, "repos": {}}
    except (OSError, json.JSONDecodeError):
        return {"clients": {}, "repos": {}}
    if not isinstance(data, dict):
        return {"clients": {}, "repos": {}}
    clients = data.get("clients") if isinstance(data.get("clients"), dict) else {}
    repos = data.get("repos") if isinstance(data.get("repos"), dict) else {}
    return {
        "clients": {str(key).lower(): str(value) for key, value in clients.items() if value},
        "repos": {str(key): str(value) for key, value in repos.items() if value},
    }


def write_override(
    clients: dict[str, str],
    path: str | None = None,
    repos: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Write client and repo → RC tag mappings. Returns the payload."""
    from find_client_rc import CLIENT_REPOS

    payload: dict[str, Any] = {
        "clients": {name.lower(): tag for name, tag in clients.items() if tag},
        "repos": dict(repos or {}),
    }
    if not payload["repos"]:
        for name, tag in payload["clients"].items():
            repo = CLIENT_REPOS.get(name)
            if repo:
                payload["repos"][repo] = tag
    target = override_path(path)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write("\n")
    return payload


def clear_override(path: str | None = None) -> None:
    """Remove the override file so LATEST resolves officially again."""
    target = override_path(path)
    try:
        os.remove(target)
    except FileNotFoundError:
        pass


def remap_latest_tag(repo: str, version_tag: str, path: str | None = None) -> str:
    """Return the forced RC tag when *version_tag* is LATEST and *repo* is overridden."""
    if not version_tag or version_tag.upper() != "LATEST":
        return version_tag
    mapping = load_override(path).get("repos") or {}
    return mapping.get(repo) or version_tag


def forced_rc_tag(client: str, path: str | None = None) -> str | None:
    """Return the forced RC tag for *client*, or ``None`` when not overridden."""
    key = (client or "").lower()
    if not key:
        return None
    return load_override(path).get("clients", {}).get(key)


def normalize_deploy_clients(
    *names: str,
    mev: bool = False,
    charon: bool = False,
) -> list[str]:
    """Lowercase GitHub-released client names from deploy args (skip Caplin/empty)."""
    from find_client_rc import CLIENT_REPOS

    out: list[str] = []
    for raw in names:
        key = (raw or "").strip().lower()
        if key in _SKIP_NAMES or key not in CLIENT_REPOS:
            continue
        if key not in out:
            out.append(key)
    if mev and "mevboost" not in out:
        out.append("mevboost")
    if charon and "charon" not in out:
        out.append("charon")
    return out


def log_rc_override_plan(rows: Iterable[dict[str, Any]]) -> None:
    """Print a CI-readable summary of which clients use RC vs official LATEST."""
    print("=========================================", flush=True)
    print(" Upgrade-case RC deploy override", flush=True)
    print("=========================================", flush=True)
    printed = False
    for row in rows:
        printed = True
        client = row.get("client") or "unknown"
        status = row.get("status") or "skip"
        latest = row.get("latest") or "?"
        rc_tag = row.get("rc_tag")
        reason = row.get("reason") or ""
        if status == "ok" and rc_tag:
            print(
                f"  {client}: RC {rc_tag} (official LATEST is {latest}) "
                f"— deploy will install RC, then upgrade to LATEST",
                flush=True,
            )
        else:
            detail = reason or "no resolvable RC"
            print(
                f"  {client}: no resolvable RC ({detail}) — deploy LATEST {latest}",
                flush=True,
            )
    if not printed:
        print("  (no clients to consider)", flush=True)
    print("=========================================", flush=True)


def prepare_rc_overrides(
    clients: list[str],
    path: str | None = None,
    find_rc_fn: Callable[[str, str | None], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Discover RCs for *clients*, write override for those that differ from LATEST.

    Reuses :func:`find_client_rc.find_rc` (no second RC finder). Clients with
    ``status != ok`` or no RC tag are omitted so deploy stays on official LATEST.

    Returns a dict with ``rows``, ``clients``, ``repos``, and ``payload``.
    """
    from find_client_rc import CLIENT_REPOS, find_rc

    # Discover against official LATEST; a leftover override would remap LATEST→RC
    # and make find_rc skip the candidate as "same as latest".
    clear_override(path)

    finder = find_rc_fn or find_rc
    rows: list[dict[str, Any]] = []
    client_map: dict[str, str] = {}
    for client in clients:
        client = client.lower()
        try:
            row = finder(client, CLIENT_REPOS.get(client))
        except Exception as exc:  # noqa: BLE001 — surface per-client and continue
            row = {
                "client": client,
                "rc_tag": None,
                "latest": None,
                "status": "skip",
                "reason": f"error: {exc}",
            }
        rows.append(row)
        rc_tag = row.get("rc_tag")
        latest = row.get("latest")
        if row.get("status") == "ok" and rc_tag and rc_tag != latest:
            client_map[client] = str(rc_tag)

    log_rc_override_plan(rows)
    if not client_map:
        clear_override(path)
        print(
            "[Upgrade RC] No differing RC tags — deploy stays on official LATEST "
            "(LATEST→LATEST no-op upgrade is fine)",
            flush=True,
        )
        return {"rows": rows, "clients": {}, "repos": {}, "payload": {"clients": {}, "repos": {}}}

    payload = write_override(client_map, path=path)
    print(
        f"[Upgrade RC] Wrote LATEST override for {', '.join(sorted(client_map))} "
        f"at {override_path(path)}",
        flush=True,
    )
    return {"rows": rows, "clients": payload["clients"], "repos": payload["repos"], "payload": payload}


def install_github_release_hook() -> bool:
    """Wrap ``deploy.common.get_github_release`` so LATEST can remap to an RC tag.

    The wrapper reads the override file on every call (no-op when absent).
    Idempotent. Returns True when the hook is installed.
    """
    _ensure_repo_on_path()
    try:
        import deploy.common as common
    except ImportError:
        return False

    current = common.get_github_release
    if getattr(current, _HOOK_ATTR, False):
        return True

    def wrapped(repo: str, version_tag: str) -> dict:
        remapped = remap_latest_tag(repo, version_tag)
        if remapped != version_tag:
            print(
                f"[Upgrade RC] get_github_release({repo!r}, 'LATEST') → {remapped}",
                flush=True,
            )
        return current(repo, remapped)

    setattr(wrapped, _HOOK_ATTR, True)
    common.get_github_release = wrapped
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage Upgrade-case LATEST→RC overrides")
    parser.add_argument(
        "action",
        choices=("prepare", "clear", "show"),
        help="prepare: discover RCs and write override; clear: remove file; show: print file",
    )
    parser.add_argument("clients", nargs="*", help="Client names for prepare (default: none)")
    parser.add_argument(
        "--path",
        default=None,
        help=f"Override file path (default: ${ENV_VAR} or {OVERRIDE_PATH})",
    )
    args = parser.parse_args()
    target = override_path(args.path)
    if args.action == "prepare":
        prepare_rc_overrides(args.clients, path=target)
    elif args.action == "clear":
        clear_override(target)
    else:
        data = load_override(target)
        json.dump(data, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
