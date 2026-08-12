"""Obol Charon DVT middleware: release lookup, systemd unit, and install."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from typing import Optional, Tuple

from deploy.common import (
    BASE_DATA_DIR,
    DOWNLOAD_DIR,
    INSTALL_DIR,
    download_file,
    extract_and_install,
    get_machine_architecture,
    setup_client_user_and_dir,
    write_service_file,
)
from deploy.service_generators import form_exec_start, generate_systemd_template

CHARON_USER = "charon"
CHARON_DATA_DIR = f"{BASE_DATA_DIR}/charon"
CHARON_CLUSTER_DIR = f"{CHARON_DATA_DIR}/.charon"
CHARON_LOCK_FILE = f"{CHARON_CLUSTER_DIR}/cluster-lock.json"
CHARON_PRIVATE_KEY_FILE = f"{CHARON_CLUSTER_DIR}/charon-enr-private-key"
CHARON_VALIDATOR_KEYS_DIR = f"{CHARON_CLUSTER_DIR}/validator_keys"
CHARON_SERVICE_PATH = "/etc/systemd/system/charon.service"

DEFAULT_VALIDATOR_API_ADDRESS = "127.0.0.1:3600"
DEFAULT_MONITORING_ADDRESS = "127.0.0.1:3620"
DEFAULT_P2P_TCP_ADDRESS = "0.0.0.0:3610"
DEFAULT_VALIDATOR_API_URL = f"http://{DEFAULT_VALIDATOR_API_ADDRESS}"

_BEACON_ENDPOINTS_RE = re.compile(
    r"--beacon-node-endpoints=(?P<url>https?://[^\s\\]+(?:,https?://[^\s\\]+)*)"
)


def charon_validator_api_url(
    host: str = "127.0.0.1",
    port: str = "3600",
) -> str:
    """Return the VC-facing Charon beacon API URL."""
    host = (host or "127.0.0.1").strip()
    port = (port or "3600").strip()
    return f"http://{host}:{port}"


def generate_charon_service(
    eth_network: str,
    beacon_node_endpoints: str,
    *,
    builder_api: bool = False,
    p2p_external_ip: str = "",
    validator_api_address: str = DEFAULT_VALIDATOR_API_ADDRESS,
    monitoring_address: str = DEFAULT_MONITORING_ADDRESS,
    p2p_tcp_address: str = DEFAULT_P2P_TCP_ADDRESS,
    feature_set_enable: str = "",
) -> str:
    """Generate Charon systemd service file content.

    Args:
        eth_network: Network name (e.g. ``mainnet``).
        beacon_node_endpoints: Comma-separated upstream beacon REST URLs.
        builder_api: Enable Charon builder API (use with MEV-Boost on the BN).
        p2p_external_ip: Optional public IP advertised to cluster peers.
        validator_api_address: Listen address for the VC-facing BN API proxy.
        monitoring_address: Listen address for ``/metrics`` / ``/readyz``.
        p2p_tcp_address: Listen address for Charon libp2p (must be public).
        feature_set_enable: Optional Charon feature set (e.g. ``json_requests``
            when the upstream beacon node is Nimbus).

    Returns:
        Service file content as a string.
    """
    _args = [
        f"{INSTALL_DIR}/charon run",
        f"--beacon-node-endpoints={beacon_node_endpoints}",
        f"--validator-api-address={validator_api_address}",
        f"--monitoring-address={monitoring_address}",
        f"--p2p-tcp-address={p2p_tcp_address}",
        f"--lock-file={CHARON_LOCK_FILE}",
        f"--private-key-file={CHARON_PRIVATE_KEY_FILE}",
    ]
    if builder_api:
        _args.append("--builder-api")
    if p2p_external_ip:
        _args.append(f"--p2p-external-ip={p2p_external_ip.strip()}")
    if feature_set_enable:
        _args.append(f"--feature-set-enable={feature_set_enable.strip()}")

    _exec_start = form_exec_start(_args)

    return generate_systemd_template(
        description=f"Obol Charon DVT middleware for {eth_network.upper()}",
        user=CHARON_USER,
        exec_start=_exec_start,
        extra_env=None,
        working_dir=CHARON_DATA_DIR,
        timeout_stop_sec=120,
        limit_nofile=65536,
    )


def get_release_info(version_tag: str, arch_amd64: bool) -> dict:
    """Get Charon release version, download URL, and filename.

    Args:
        version_tag: ``LATEST`` or a specific version tag.
        arch_amd64: True if the architecture is amd64/x86_64, False for arm64.

    Returns:
        A dictionary with keys ``version``, ``download_urls``, and ``filenames``.
    """
    from deploy.common import get_github_release, pick_github_release_asset, release_info_from_github

    repo = "ObolNetwork/charon"
    data = get_github_release(repo, version_tag)
    filename, download_url = pick_github_release_asset(
        data.get("assets", []),
        arch_amd64,
        name_contains=("charon",),
        name_excludes=("checksums", "cli-reference"),
        client_label="Charon",
    )
    return release_info_from_github(data, [download_url], [filename])


def install_charon(
    eth_network: str,
    beacon_node_endpoints: str,
    *,
    builder_api: bool = False,
    p2p_external_ip: str = "",
    validator_api_address: str = DEFAULT_VALIDATOR_API_ADDRESS,
    monitoring_address: str = DEFAULT_MONITORING_ADDRESS,
    p2p_tcp_address: str = DEFAULT_P2P_TCP_ADDRESS,
    feature_set_enable: str = "",
) -> Tuple[str, str]:
    """Install Charon binary, datadir, and systemd unit.

    Returns:
        ``(charon_version, service_file_path)``
    """
    setup_client_user_and_dir(CHARON_USER, "charon")
    subprocess.run(["sudo", "mkdir", "-p", CHARON_CLUSTER_DIR], check=True)
    subprocess.run(
        ["sudo", "chown", "-R", f"{CHARON_USER}:{CHARON_USER}", CHARON_DATA_DIR],
        check=True,
    )
    subprocess.run(["sudo", "chmod", "700", CHARON_DATA_DIR], check=True)
    subprocess.run(["sudo", "chmod", "700", CHARON_CLUSTER_DIR], check=True)

    arch_amd64 = get_machine_architecture() == "amd64"
    info = get_release_info("LATEST", arch_amd64)
    charon_version = info["version"]

    download_url = info["download_urls"][0]
    filename = info["filenames"][0]
    download_path = f"{DOWNLOAD_DIR}/{filename}"
    download_file(download_url, download_path, "charon")

    extract_and_install(
        download_path,
        "charon",
        os.path.join(INSTALL_DIR, "charon"),
        "binary",
        0,
        binary_name="charon",
    )

    service_content = generate_charon_service(
        eth_network,
        beacon_node_endpoints,
        builder_api=builder_api,
        p2p_external_ip=p2p_external_ip,
        validator_api_address=validator_api_address,
        monitoring_address=monitoring_address,
        p2p_tcp_address=p2p_tcp_address,
        feature_set_enable=feature_set_enable,
    )
    write_service_file(service_content, CHARON_SERVICE_PATH, "charon_temp.service")
    return charon_version, CHARON_SERVICE_PATH


def scrape_beacon_endpoints(content: str) -> Optional[str]:
    """Extract ``--beacon-node-endpoints`` from a Charon unit file."""
    match = _BEACON_ENDPOINTS_RE.search(content)
    return match.group("url") if match else None


def patch_beacon_endpoints(service_path: str, new_endpoints: str) -> bool:
    """Replace Charon ``--beacon-node-endpoints`` in a systemd unit.

    Returns:
        True if the file was updated.

    Raises:
        FileNotFoundError: service_path does not exist.
        ValueError: Flag missing or invalid URL.
    """
    if not os.path.isfile(service_path):
        raise FileNotFoundError(f"Charon service file not found: {service_path}")

    new_endpoints = new_endpoints.strip()
    if not re.match(r"^https?://", new_endpoints):
        raise ValueError(f"Invalid beacon endpoint URL: {new_endpoints!r}")

    with open(service_path, encoding="utf-8") as fh:
        content = fh.read()
    if not _BEACON_ENDPOINTS_RE.search(content):
        raise ValueError(f"--beacon-node-endpoints not found in {service_path}")

    new_content = _BEACON_ENDPOINTS_RE.sub(
        f"--beacon-node-endpoints={new_endpoints}",
        content,
        count=1,
    )
    if new_content == content:
        return False

    if service_path.startswith("/etc/"):
        write_service_file(new_content, service_path, temp_filename="charon_temp.service")
    else:
        with open(service_path, "w", encoding="utf-8") as fh:
            fh.write(new_content)
    return True


def _cmd_patch(args: argparse.Namespace) -> int:
    try:
        updated = patch_beacon_endpoints(args.service_path, args.endpoint)
    except (ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if updated:
        print(f"Patched Charon beacon endpoints in {args.service_path} → {args.endpoint}")
    else:
        print(f"No change needed in {args.service_path}")
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Charon systemd utilities.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    patch_parser = subparsers.add_parser(
        "patch_beacon", help="Update --beacon-node-endpoints in charon.service"
    )
    patch_parser.add_argument("--endpoint", required=True, help="Upstream beacon REST URL")
    patch_parser.add_argument(
        "--service-path",
        default=CHARON_SERVICE_PATH,
        help="Path to charon.service",
    )
    patch_parser.set_defaults(func=_cmd_patch)

    parsed = parser.parse_args(argv)
    return parsed.func(parsed)


if __name__ == "__main__":
    sys.exit(main())
