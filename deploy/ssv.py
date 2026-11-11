"""SSV Network operator node and DKG sidecar.

SSV is a duty client (VC alternative), not Charon-style BN middleware. The node
talks to local EL (WebSocket) and CL (beacon REST) directly. Operator secrets
(``encrypted_private_key.json`` + ``password``) live under ``/var/lib/ssv/`` and
are shared with the optional ``ssv-dkg`` systemd unit.

Upstream does not publish ``ssvnode`` GitHub binaries. EthPillar builds the
operator from source (``CGO_ENABLED=1 make build``) for amd64 and arm64, then
installs under systemd. DKG ships Linux zip assets on GitHub (``ssvlabs/ssv-dkg``).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from typing import Optional, Tuple

from deploy.common import (
    BASE_DATA_DIR,
    DOWNLOAD_DIR,
    INSTALL_DIR,
    download_release_file,
    ensure_c_build_tools,
    ensure_go_available,
    extract_and_install,
    get_machine_architecture,
    setup_client_user_and_dir,
    write_service_file,
)
from deploy.service_generators import form_exec_start, generate_systemd_template

SSV_USER = "ssv"
SSV_DATA_DIR = f"{BASE_DATA_DIR}/ssv"
SSV_CONFIG_FILE = f"{SSV_DATA_DIR}/config.yaml"
SSV_DKG_CONFIG_FILE = f"{SSV_DATA_DIR}/operator.yaml"
SSV_PRIVATE_KEY_FILE = f"{SSV_DATA_DIR}/encrypted_private_key.json"
SSV_PASSWORD_FILE = f"{SSV_DATA_DIR}/password"
SSV_DB_DIR = f"{SSV_DATA_DIR}/db"
SSV_DKG_OUTPUT_DIR = f"{SSV_DATA_DIR}/output"
SSV_NODE_SERVICE_PATH = "/etc/systemd/system/ssv-node.service"
SSV_DKG_SERVICE_PATH = "/etc/systemd/system/ssv-dkg.service"
SSV_NODE_BIN = f"{INSTALL_DIR}/ssvnode"
SSV_DKG_BIN = f"{INSTALL_DIR}/ssv-dkg"

DEFAULT_P2P_TCP_PORT = 13001
DEFAULT_P2P_UDP_PORT = 12001
DEFAULT_METRICS_PORT = 15000
DEFAULT_API_PORT = 16000
DEFAULT_DKG_PORT = 3030
DEFAULT_EL_WS = "ws://127.0.0.1:8546"

SSV_GITHUB_REPO = "ssvlabs/ssv"
SSV_DKG_GITHUB_REPO = "ssvlabs/ssv-dkg"
# Matches upstream go.mod ``go 1.26`` / toolchain go1.26.x.
SSV_MIN_GO = (1, 26)


def ssv_network_name(eth_network: str) -> str:
    """Map EthPillar network slug to SSV ``ssv.Network`` value.

    Args:
        eth_network: EthPillar network (e.g. ``mainnet``, ``hoodi``).

    Returns:
        SSV network name accepted by ``ssvnode``.
    """
    network = (eth_network or "mainnet").strip().lower()
    if network in ("holesky",):
        return "holesky"
    if network in ("hoodi",):
        return "hoodi"
    if network in ("sepolia",):
        return "sepolia"
    return "mainnet"


def generate_ssv_config(
    eth_network: str,
    beacon_node_addr: str,
    eth1_addr: str = DEFAULT_EL_WS,
    *,
    p2p_tcp_port: int = DEFAULT_P2P_TCP_PORT,
    p2p_udp_port: int = DEFAULT_P2P_UDP_PORT,
    metrics_port: int = DEFAULT_METRICS_PORT,
    api_port: int = DEFAULT_API_PORT,
    p2p_external_ip: str = "",
    private_key_file: str = SSV_PRIVATE_KEY_FILE,
    password_file: str = SSV_PASSWORD_FILE,
    db_path: str = SSV_DB_DIR,
    log_file: str = f"{SSV_DATA_DIR}/debug.log",
) -> str:
    """Generate SSV node ``config.yaml`` content.

    Args:
        eth_network: EthPillar / SSV network slug.
        beacon_node_addr: Beacon REST HTTP URL(s).
        eth1_addr: Execution WebSocket URL(s).
        p2p_tcp_port: Libp2p TCP port.
        p2p_udp_port: Discovery UDP port.
        metrics_port: Prometheus ``/metrics`` port (bind localhost via service).
        api_port: SSV health/API port.
        p2p_external_ip: Optional advertised public IP.
        private_key_file: Encrypted operator keystore path.
        password_file: Keystore password file path.
        db_path: Persistent DB directory (slashing-critical).
        log_file: Debug log path.

    Returns:
        YAML config as a string.
    """
    lines = [
        "global:",
        "  LogLevel: info",
        f"  LogFilePath: {log_file}",
        "",
        "db:",
        f"  Path: {db_path}",
        "",
        "ssv:",
        f"  Network: {ssv_network_name(eth_network)}",
        "",
        "eth2:",
        f"  BeaconNodeAddr: {beacon_node_addr}",
        "",
        "eth1:",
        f"  ETH1Addr: {eth1_addr}",
        "",
        "p2p:",
        f"  TcpPort: {int(p2p_tcp_port)}",
        f"  UdpPort: {int(p2p_udp_port)}",
    ]
    if p2p_external_ip.strip():
        lines.append(f"  HostAddress: {p2p_external_ip.strip()}")
    lines.extend(
        [
            "",
            "KeyStore:",
            f"  PrivateKeyFile: {private_key_file}",
            f"  PasswordFile: {password_file}",
            "",
            f"MetricsAPIPort: {int(metrics_port)}",
            f"SSVAPIPort: {int(api_port)}",
            "",
        ]
    )
    return "\n".join(lines)


def generate_ssv_dkg_config(
    eth_network: str,
    eth1_http_addr: str,
    operator_id: str = "",
    *,
    dkg_port: int = DEFAULT_DKG_PORT,
    private_key_file: str = SSV_PRIVATE_KEY_FILE,
    password_file: str = SSV_PASSWORD_FILE,
    output_path: str = SSV_DKG_OUTPUT_DIR,
    log_file: str = f"{SSV_DATA_DIR}/dkg-debug.log",
) -> str:
    """Generate DKG ``operator.yaml`` sharing the node operator keystore.

    Args:
        eth_network: Unused for DKG YAML today; kept for API symmetry / future use.
        eth1_http_addr: Execution HTTP URL for multisig owner lookups.
        operator_id: On-chain operator ID (empty until registration; operator fills in).
        dkg_port: DKG listen port.
        private_key_file: Shared encrypted operator key.
        password_file: Shared password file.
        output_path: Ceremony output directory.
        log_file: DKG debug log path.

    Returns:
        YAML config as a string.
    """
    _ = eth_network  # reserved for network-specific defaults
    op_id = (operator_id or "YOUR_OPERATOR_ID").strip() or "YOUR_OPERATOR_ID"
    return "\n".join(
        [
            f"privKey: {private_key_file}",
            f"privKeyPassword: {password_file}",
            f"operatorID: {op_id}",
            f"port: {int(dkg_port)}",
            "logLevel: info",
            "logFormat: json",
            "logLevelFormat: capitalColor",
            f"logFilePath: {log_file}",
            f"outputPath: {output_path}",
            f"ethEndpointURL: {eth1_http_addr}",
            "",
        ]
    )


def generate_ssv_node_service(
    eth_network: str,
    config_path: str = SSV_CONFIG_FILE,
    *,
    binary: str = SSV_NODE_BIN,
) -> str:
    """Generate ``ssv-node.service`` unit content.

    Args:
        eth_network: Network name for the unit description.
        config_path: Path to ``config.yaml``.
        binary: Path to ``ssvnode``.

    Returns:
        Systemd unit file body.
    """
    exec_start = form_exec_start(
        [
            f"{binary} start-node",
            f"--config={config_path}",
        ]
    )
    return generate_systemd_template(
        description=f"SSV Operator Node for {eth_network.upper()}",
        user=SSV_USER,
        exec_start=exec_start,
        working_dir=SSV_DATA_DIR,
        timeout_stop_sec=120,
        limit_nofile=65536,
        unit_after=["consensus.service", "execution.service"],
    )


def generate_ssv_dkg_service(
    eth_network: str,
    config_path: str = SSV_DKG_CONFIG_FILE,
    *,
    binary: str = SSV_DKG_BIN,
) -> str:
    """Generate ``ssv-dkg.service`` unit content.

    Args:
        eth_network: Network name for the unit description.
        config_path: Path to ``operator.yaml``.
        binary: Path to ``ssv-dkg``.

    Returns:
        Systemd unit file body.
    """
    exec_start = form_exec_start(
        [
            f"{binary} start-operator",
            f"--configPath={config_path}",
        ]
    )
    return generate_systemd_template(
        description=f"SSV DKG Operator for {eth_network.upper()}",
        user=SSV_USER,
        exec_start=exec_start,
        working_dir=SSV_DATA_DIR,
        timeout_stop_sec=120,
        limit_nofile=65536,
        unit_after=["execution.service"],
    )


def get_release_info(version_tag: str, arch_amd64: bool) -> dict:
    """Resolve SSV node version from GitHub (source build; no binary assets).

    Args:
        version_tag: ``LATEST`` or a specific tag (e.g. ``v2.5.1``).
        arch_amd64: Unused (source build is architecture-native).

    Returns:
        Dict with ``version``, source ``download_urls`` (tag tarball), ``filenames``.
    """
    from deploy.common import get_github_release

    _ = arch_amd64
    data = get_github_release(SSV_GITHUB_REPO, version_tag)
    version = data["tag_name"]
    tarball = (
        data.get("tarball_url")
        or f"https://github.com/{SSV_GITHUB_REPO}/archive/refs/tags/{version}.tar.gz"
    )
    info = {
        "version": version,
        "download_urls": [tarball],
        "filenames": [f"ssv-{version}.tar.gz"],
        "sha256": [""],
        "source_build": True,
    }
    if data.get("commit"):
        info["commit"] = data["commit"]
    return info


def get_dkg_release_info(version_tag: str, arch_amd64: bool) -> dict:
    """Get ssv-dkg release version, download URL, and filename.

    Args:
        version_tag: ``LATEST`` or a specific version tag.
        arch_amd64: True if amd64/x86_64, False for arm64.

    Returns:
        Dict with ``version``, ``download_urls``, and ``filenames``.
    """
    from deploy.common import get_github_release, pick_github_release_asset, release_info_from_github

    data = get_github_release(SSV_DKG_GITHUB_REPO, version_tag)
    filename, download_url = pick_github_release_asset(
        data.get("assets", []),
        arch_amd64,
        name_contains=("ssv-dkg",),
        name_excludes=("checksum",),
        prefer_extensions=(".zip", ".tar.gz", ""),
        client_label="ssv-dkg",
    )
    return release_info_from_github(data, [download_url], [filename])


def build_ssvnode_from_source(version_tag: str, dest: str = SSV_NODE_BIN) -> None:
    """Clone *version_tag*, run ``make build``, and install ``ssvnode`` to *dest*.

    Args:
        version_tag: Git tag (e.g. ``v2.5.1``).
        dest: Absolute install path for the binary.

    Raises:
        RuntimeError: When Go/build tools are missing or the build fails.
    """
    if not ensure_go_available(SSV_MIN_GO):
        raise RuntimeError(
            f"Go {SSV_MIN_GO[0]}.{SSV_MIN_GO[1]}+ is required to build ssvnode "
            "(upstream publishes no Linux binaries)."
        )
    if not ensure_c_build_tools():
        raise RuntimeError("build-essential/git/make are required to build ssvnode (CGO).")

    tag = (version_tag or "").strip()
    if not tag or tag.upper() == "LATEST":
        raise ValueError("build_ssvnode_from_source requires a concrete version tag")

    with tempfile.TemporaryDirectory(prefix="ethpillar-ssv-src-") as tmp:
        src = os.path.join(tmp, "ssv")
        print(f">> Cloning {SSV_GITHUB_REPO}@{tag}")
        subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                tag,
                f"https://github.com/{SSV_GITHUB_REPO}.git",
                src,
            ],
            check=True,
        )
        env = os.environ.copy()
        env["CGO_ENABLED"] = "1"
        # Prefer /usr/local/go when we just installed it.
        go_bin_dir = "/usr/local/go/bin"
        if os.path.isdir(go_bin_dir):
            env["PATH"] = f"{go_bin_dir}:{env.get('PATH', '')}"
        print(f">> Building ssvnode (CGO_ENABLED=1 make build) in {src}")
        subprocess.run(["make", "build"], cwd=src, check=True, env=env)
        built = os.path.join(src, "bin", "ssvnode")
        if not os.path.isfile(built):
            raise RuntimeError(f"make build succeeded but {built} is missing")
        os.chmod(built, 0o755)
        subprocess.run(["sudo", "mkdir", "-p", os.path.dirname(dest)], check=True)
        subprocess.run(["sudo", "cp", "-f", built, dest], check=True)
        subprocess.run(["sudo", "chmod", "755", dest], check=True)
        print(f">> Installed {dest}")


def operator_keystore_present(
    private_key_file: str = SSV_PRIVATE_KEY_FILE,
    password_file: str = SSV_PASSWORD_FILE,
) -> bool:
    """Return True when both operator keystore files exist."""
    return _path_exists(private_key_file) and _path_exists(password_file)


def _path_exists(path: str) -> bool:
    if os.path.isfile(path):
        return True
    return subprocess.run(["sudo", "test", "-f", path], check=False).returncode == 0


def _sudo_write_text(path: str, content: str, mode: str = "600") -> None:
    """Write *content* to *path* as root-owned for ``ssv`` user."""
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
        handle.write(content)
        tmp = handle.name
    try:
        subprocess.run(["sudo", "cp", tmp, path], check=True)
        subprocess.run(["sudo", "chown", f"{SSV_USER}:{SSV_USER}", path], check=True)
        subprocess.run(["sudo", "chmod", mode, path], check=True)
    finally:
        os.unlink(tmp)


def import_operator_keystore(
    source_dir: str,
    *,
    private_key_name: str = "encrypted_private_key.json",
    password_name: str = "password",
) -> None:
    """Copy operator key + password from *source_dir* into ``SSV_DATA_DIR``.

    Args:
        source_dir: Directory containing the encrypted key and password files.
        private_key_name: Filename of the encrypted private key JSON.
        password_name: Filename of the password file.

    Raises:
        FileNotFoundError: When either source file is missing.
    """
    src_key = os.path.join(source_dir, private_key_name)
    src_pw = os.path.join(source_dir, password_name)
    if not os.path.isfile(src_key):
        raise FileNotFoundError(f"Missing operator key: {src_key}")
    if not os.path.isfile(src_pw):
        raise FileNotFoundError(f"Missing password file: {src_pw}")
    subprocess.run(["sudo", "mkdir", "-p", SSV_DATA_DIR], check=True)
    subprocess.run(["sudo", "cp", "-f", src_key, SSV_PRIVATE_KEY_FILE], check=True)
    subprocess.run(["sudo", "cp", "-f", src_pw, SSV_PASSWORD_FILE], check=True)
    subprocess.run(
        ["sudo", "chown", f"{SSV_USER}:{SSV_USER}", SSV_PRIVATE_KEY_FILE, SSV_PASSWORD_FILE],
        check=True,
    )
    subprocess.run(
        ["sudo", "chmod", "600", SSV_PRIVATE_KEY_FILE, SSV_PASSWORD_FILE],
        check=True,
    )


def write_dkg_operator_config(
    eth_network: str,
    eth1_http_addr: str,
    operator_id: str = "",
    *,
    dkg_port: int = DEFAULT_DKG_PORT,
) -> str:
    """Write ``operator.yaml`` pointing at the shared keystore.

    Returns:
        Path to the written DKG config.
    """
    content = generate_ssv_dkg_config(
        eth_network,
        eth1_http_addr,
        operator_id=operator_id,
        dkg_port=dkg_port,
    )
    _sudo_write_text(SSV_DKG_CONFIG_FILE, content, "600")
    return SSV_DKG_CONFIG_FILE


def install_ssv(
    eth_network: str,
    beacon_node_addr: str,
    eth1_ws_addr: str = DEFAULT_EL_WS,
    eth1_http_addr: str = "http://127.0.0.1:8545",
    *,
    p2p_tcp_port: int = DEFAULT_P2P_TCP_PORT,
    p2p_udp_port: int = DEFAULT_P2P_UDP_PORT,
    metrics_port: int = DEFAULT_METRICS_PORT,
    api_port: int = DEFAULT_API_PORT,
    dkg_port: int = DEFAULT_DKG_PORT,
    p2p_external_ip: str = "",
    operator_id: str = "",
    install_dkg: bool = True,
) -> Tuple[str, str, str]:
    """Install SSV node (and optionally DKG), datadir, config, and units.

    Args:
        eth_network: Network slug.
        beacon_node_addr: Beacon REST URL.
        eth1_ws_addr: Execution WebSocket URL for the node.
        eth1_http_addr: Execution HTTP URL for DKG.
        p2p_tcp_port: Node P2P TCP port.
        p2p_udp_port: Node P2P UDP port.
        metrics_port: Metrics port.
        api_port: Health/API port.
        dkg_port: DKG listen port.
        p2p_external_ip: Optional advertised IP.
        operator_id: Optional known operator ID for DKG config.
        install_dkg: When True, also install ``ssv-dkg`` binary and unit.

    Returns:
        ``(ssv_version, ssv_node_service_path, ssv_dkg_service_path_or_empty)``
    """
    setup_client_user_and_dir(SSV_USER, "ssv")
    for path in (SSV_DATA_DIR, SSV_DB_DIR, SSV_DKG_OUTPUT_DIR):
        subprocess.run(["sudo", "mkdir", "-p", path], check=True)
    subprocess.run(
        ["sudo", "chown", "-R", f"{SSV_USER}:{SSV_USER}", SSV_DATA_DIR],
        check=True,
    )
    subprocess.run(["sudo", "chmod", "700", SSV_DATA_DIR], check=True)
    subprocess.run(["sudo", "chmod", "700", SSV_DB_DIR], check=True)

    arch_amd64 = get_machine_architecture() == "amd64"
    node_info = get_release_info("LATEST", arch_amd64)
    ssv_version = node_info["version"]
    build_ssvnode_from_source(ssv_version, SSV_NODE_BIN)

    config = generate_ssv_config(
        eth_network,
        beacon_node_addr,
        eth1_ws_addr,
        p2p_tcp_port=p2p_tcp_port,
        p2p_udp_port=p2p_udp_port,
        metrics_port=metrics_port,
        api_port=api_port,
        p2p_external_ip=p2p_external_ip,
    )
    _sudo_write_text(SSV_CONFIG_FILE, config, "600")

    node_unit = generate_ssv_node_service(eth_network)
    write_service_file(node_unit, SSV_NODE_SERVICE_PATH, "ssv_node_temp.service")

    dkg_path = ""
    if install_dkg:
        dkg_info = get_dkg_release_info("LATEST", arch_amd64)
        download_path = download_release_file(dkg_info, DOWNLOAD_DIR, "ssv-dkg")
        extract_and_install(
            download_path,
            "ssv-dkg",
            SSV_DKG_BIN,
            "binary",
            0,
            binary_name="ssv-dkg",
        )
        write_dkg_operator_config(
            eth_network,
            eth1_http_addr,
            operator_id=operator_id,
            dkg_port=dkg_port,
        )
        dkg_unit = generate_ssv_dkg_service(eth_network)
        write_service_file(dkg_unit, SSV_DKG_SERVICE_PATH, "ssv_dkg_temp.service")
        dkg_path = SSV_DKG_SERVICE_PATH

    return ssv_version, SSV_NODE_SERVICE_PATH, dkg_path


def parse_metrics_port(
    config_content: str = "",
    *,
    config_path: str = SSV_CONFIG_FILE,
    default: int = DEFAULT_METRICS_PORT,
) -> int:
    """Return ``MetricsAPIPort`` from SSV config YAML.

    Args:
        config_content: Optional YAML body; when empty, reads ``config_path``.
        config_path: Config file to read when ``config_content`` is empty.
        default: Port when the key is missing.

    Returns:
        Metrics port (1–65535) or *default*.
    """
    content = config_content
    if not content:
        try:
            with open(config_path, encoding="utf-8") as handle:
                content = handle.read()
        except OSError:
            result = subprocess.run(
                ["sudo", "cat", config_path],
                capture_output=True,
                text=True,
                check=False,
            )
            content = result.stdout if result.returncode == 0 else ""
            if not content:
                return default
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("MetricsAPIPort:"):
            raw = stripped.split(":", 1)[1].strip()
            try:
                port = int(raw)
            except ValueError:
                return default
            return port if 1 <= port <= 65535 else default
    return default


def main(argv: Optional[list] = None) -> int:
    """CLI helpers for import / config rewrite."""
    parser = argparse.ArgumentParser(description="SSV operator helpers")
    sub = parser.add_subparsers(dest="cmd", required=True)

    imp = sub.add_parser("import-keystore", help="Import operator key + password")
    imp.add_argument("source_dir", help="Directory with encrypted_private_key.json and password")

    dkg = sub.add_parser("write-dkg-config", help="Rewrite operator.yaml")
    dkg.add_argument("--network", default="mainnet")
    dkg.add_argument("--eth1-http", default="http://127.0.0.1:8545")
    dkg.add_argument("--operator-id", default="")
    dkg.add_argument("--dkg-port", type=int, default=DEFAULT_DKG_PORT)

    args = parser.parse_args(argv)
    if args.cmd == "import-keystore":
        import_operator_keystore(args.source_dir)
        print(f"Imported operator keystore into {SSV_DATA_DIR}")
        return 0
    if args.cmd == "write-dkg-config":
        path = write_dkg_operator_config(
            args.network,
            args.eth1_http,
            operator_id=args.operator_id,
            dkg_port=args.dkg_port,
        )
        print(path)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
