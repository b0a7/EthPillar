"""Obol Charon DVT middleware: release lookup, systemd unit, and install."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse, urlunparse

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
_NETWORK_FROM_DESC_RE = re.compile(
    r"Obol Charon DVT middleware for (?P<net>[A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
_ENV_LINE_RE = re.compile(
    r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$"
)

# Docker Compose service hostnames used in CDVN beacon / EL URLs.
_DOCKER_LOCAL_HOSTS = frozenset(
    {
        "lighthouse",
        "cl-lighthouse",
        "teku",
        "cl-teku",
        "nimbus",
        "cl-nimbus",
        "lodestar",
        "cl-lodestar",
        "prysm",
        "cl-prysm",
        "grandine",
        "cl-grandine",
        "beacon",
        "beacon-node",
        "host.docker.internal",
        "nethermind",
        "reth",
        "geth",
        "besu",
        "erigon",
        "el-nethermind",
        "el-reth",
    }
)

# CHARON_* keys we intentionally do not map into systemd ExecStart.
_SKIP_ENV_KEYS = frozenset(
    {
        "CHARON_VERSION",
        "CHARON_DOCKER_NETWORK",
        "CHARON_ALLOY_MONITORED",
        "CHARON_LOKI_ADDRESSES",
        "CHARON_LOKI_SERVICE",
    }
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
    extra_args: Optional[Sequence[str]] = None,
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
        extra_args: Additional ``charon run`` flags (e.g. from a CDVN ``.env``).

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
    if extra_args:
        _args.extend(a for a in extra_args if a)

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


def parse_dotenv(path: str) -> Dict[str, str]:
    """Parse a CDVN-style ``.env`` file into key/value pairs.

    Commented lines and blank lines are ignored. Values may be optionally
    quoted. Inline ``#`` comments are stripped when unquoted.
    """
    result: Dict[str, str] = {}
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            match = _ENV_LINE_RE.match(line)
            if not match:
                continue
            key, value = match.group(1), match.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            else:
                # Drop unquoted trailing comments: VALUE # comment
                if " #" in value:
                    value = value.split(" #", 1)[0].rstrip()
            result[key] = value
    return result


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _charon_env_to_flag(key: str) -> str:
    """Map ``CHARON_FOO_BAR`` to ``--foo-bar``."""
    body = key[len("CHARON_") :] if key.startswith("CHARON_") else key
    return "--" + body.lower().replace("_", "-")


def rewrite_docker_url(url: str, local_host: str = "127.0.0.1") -> Tuple[str, Optional[str]]:
    """Rewrite CDVN Docker hostnames to a host-local address.

    Returns:
        ``(rewritten_url, warning_or_none)``
    """
    url = url.strip()
    if not url:
        return url, None
    if "${" in url:
        return url, f"Unresolved variable in URL: {url}"

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        return url, f"Could not parse URL host: {url}"

    if host in _DOCKER_LOCAL_HOSTS or host.endswith(".internal"):
        port = parsed.port
        netloc = f"{local_host}:{port}" if port else local_host
        rewritten = urlunparse(parsed._replace(netloc=netloc))
        return rewritten, f"Rewrote Docker host {host!r} → {local_host} in {url}"

    return url, None


def rewrite_endpoint_list(
    value: str,
    local_host: str = "127.0.0.1",
) -> Tuple[str, List[str]]:
    """Rewrite a comma-separated URL list; collect warnings."""
    warnings: List[str] = []
    parts: List[str] = []
    for piece in value.split(","):
        piece = piece.strip()
        if not piece:
            continue
        rewritten, warn = rewrite_docker_url(piece, local_host=local_host)
        parts.append(rewritten)
        if warn:
            warnings.append(warn)
    return ",".join(parts), warnings


def _localhost_bind(address: str, default: str) -> Tuple[str, Optional[str]]:
    """Prefer loopback binds for VC API / metrics on bare-metal EthPillar."""
    address = (address or "").strip() or default
    if address.startswith("0.0.0.0:"):
        rewritten = "127.0.0.1:" + address.split(":", 1)[1]
        return rewritten, f"Bound {address} → {rewritten} (EthPillar localhost default)"
    return address, None


@dataclass
class MappingRow:
    """One paired line for side-by-side .env → systemd preview."""

    env_line: str
    systemd_line: str
    kind: str = "mapped"  # mapped | skipped | note | default


@dataclass
class CharonEnvImportPlan:
    """Result of mapping a CDVN ``.env`` onto an EthPillar Charon unit."""

    network: str
    beacon_node_endpoints: str
    builder_api: bool = False
    p2p_external_ip: str = ""
    validator_api_address: str = DEFAULT_VALIDATOR_API_ADDRESS
    monitoring_address: str = DEFAULT_MONITORING_ADDRESS
    p2p_tcp_address: str = DEFAULT_P2P_TCP_ADDRESS
    feature_set_enable: str = ""
    extra_args: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    mapped: List[str] = field(default_factory=list)
    rows: List[MappingRow] = field(default_factory=list)

    def add_row(self, env_line: str, systemd_line: str, kind: str = "mapped") -> None:
        """Append a paired preview row and keep legacy mapped/skipped lists in sync."""
        self.rows.append(MappingRow(env_line=env_line, systemd_line=systemd_line, kind=kind))
        if kind == "mapped":
            self.mapped.append(f"{env_line} → {systemd_line}")
        elif kind == "skipped":
            self.skipped.append(f"{env_line} ({systemd_line.lstrip('# ').strip()})")

    def service_content(self) -> str:
        """Render a full ``charon.service`` from this plan."""
        return generate_charon_service(
            self.network,
            self.beacon_node_endpoints,
            builder_api=self.builder_api,
            p2p_external_ip=self.p2p_external_ip,
            validator_api_address=self.validator_api_address,
            monitoring_address=self.monitoring_address,
            p2p_tcp_address=self.p2p_tcp_address,
            feature_set_enable=self.feature_set_enable,
            extra_args=self.extra_args,
        )

    def pane_texts(self) -> Tuple[str, str]:
        """Return ``(left_env_text, right_systemd_text)`` with aligned line counts."""
        left_lines = [
            "# CDVN .env (active settings)",
            "# Left pane — source values",
            "",
        ]
        right_lines = [
            "# charon.service ExecStart flags",
            "# Right pane — EthPillar systemd mapping",
            "",
        ]
        for row in self.rows:
            left_lines.append(row.env_line)
            right_lines.append(row.systemd_line)
        if self.warnings:
            left_lines.append("")
            right_lines.append("")
            left_lines.append("# warnings")
            right_lines.append("# warnings")
            for warn in self.warnings:
                left_lines.append(f"# ! {warn}")
                right_lines.append(f"# ! {warn}")
        # Keep panes equal length for tmeld alignment
        while len(left_lines) < len(right_lines):
            left_lines.append("")
        while len(right_lines) < len(left_lines):
            right_lines.append("")
        return "\n".join(left_lines) + "\n", "\n".join(right_lines) + "\n"

    def summary(self) -> str:
        """Human-readable preview (fallback when tmeld is unavailable)."""
        left, right = self.pane_texts()
        left_rows = left.splitlines()
        right_rows = right.splitlines()
        width = max((len(line) for line in left_rows), default=40)
        width = min(max(width, 24), 56)
        lines = [
            f"{'CDVN .env':<{width}} | systemd",
            f"{'-' * width}-+-{'-' * 40}",
        ]
        for l, r in zip(left_rows, right_rows):
            lines.append(f"{l:<{width}} | {r}")
        return "\n".join(lines)


def write_import_preview_panes(
    plan: CharonEnvImportPlan,
    workdir: str,
) -> Tuple[str, str]:
    """Write side-by-side preview files; return ``(left_path, right_path)``."""
    os.makedirs(workdir, exist_ok=True)
    left_path = os.path.join(workdir, "01_cdvn.env")
    right_path = os.path.join(workdir, "02_systemd.flags")
    left_text, right_text = plan.pane_texts()
    with open(left_path, "w", encoding="utf-8") as handle:
        handle.write(left_text)
    with open(right_path, "w", encoding="utf-8") as handle:
        handle.write(right_text)
    return left_path, right_path


def launch_import_preview_tmeld(workdir: str) -> int:
    """Open tmeld on the import preview panes (left=.env, right=systemd)."""
    from manage.config_compare import find_tmeld

    left = os.path.join(workdir, "01_cdvn.env")
    right = os.path.join(workdir, "02_systemd.flags")
    if not (os.path.isfile(left) and os.path.isfile(right)):
        raise FileNotFoundError(f"Preview panes missing in {workdir}")

    tmeld = find_tmeld()
    if not tmeld:
        raise RuntimeError(
            "tmeld is not installed. Run EthPillar once to bootstrap deps, "
            "or: pip install tmeld"
        )
    cmd = [tmeld, left, right, "--show-line-numbers"]
    print(f"Launching: {' '.join(cmd)}")
    print("Left = CDVN .env settings | Right = systemd flag mapping")
    print("Esc / Ctrl+Q to quit when done reviewing.")
    return subprocess.call(cmd)


def plan_cdvn_env_import(
    env: Dict[str, str],
    *,
    fallback_network: str = "mainnet",
    local_host: str = "127.0.0.1",
    prefer_localhost_binds: bool = True,
) -> CharonEnvImportPlan:
    """Build a Charon systemd plan from parsed CDVN ``.env`` values."""
    network = (env.get("NETWORK") or fallback_network).strip() or fallback_network
    plan = CharonEnvImportPlan(network=network, beacon_node_endpoints="")

    if env.get("NETWORK"):
        plan.add_row(
            f"NETWORK={env['NETWORK'].strip()}",
            f"# unit Description network → {network.upper()}",
        )
    else:
        plan.add_row(
            f"# NETWORK unset (fallback {fallback_network})",
            f"# unit Description network → {network.upper()}",
            kind="note",
        )

    raw_bn = (env.get("CHARON_BEACON_NODE_ENDPOINTS") or "").strip()
    bn_env_line = ""
    if raw_bn:
        bn_env_line = f"CHARON_BEACON_NODE_ENDPOINTS={raw_bn}"
    else:
        cl = (env.get("CL") or "").strip()
        if cl and cl != "cl-none":
            raw_bn = f"http://{cl}:5052"
            bn_env_line = f"CL={cl}  # CHARON_BEACON_NODE_ENDPOINTS unset"
            plan.warnings.append(
                "CHARON_BEACON_NODE_ENDPOINTS unset; derived from CL="
                f"{cl} → {raw_bn}"
            )
        else:
            raw_bn = f"http://{local_host}:5052"
            bn_env_line = f"# CHARON_BEACON_NODE_ENDPOINTS unset → default {raw_bn}"
            plan.warnings.append(
                "CHARON_BEACON_NODE_ENDPOINTS unset; defaulting to "
                f"{raw_bn}"
            )

    bn, bn_warns = rewrite_endpoint_list(raw_bn, local_host=local_host)
    plan.beacon_node_endpoints = bn
    plan.warnings.extend(bn_warns)
    plan.add_row(bn_env_line, f"--beacon-node-endpoints={bn}")

    builder_key = "CHARON_BUILDER_API" if env.get("CHARON_BUILDER_API") else "BUILDER_API_ENABLED"
    builder_raw = (env.get("CHARON_BUILDER_API") or env.get("BUILDER_API_ENABLED") or "").strip()
    if builder_raw:
        plan.builder_api = _truthy(builder_raw)
        systemd_builder = "--builder-api" if plan.builder_api else "# (builder-api omitted)"
        plan.add_row(f"{builder_key}={builder_raw}", systemd_builder)

    p2p_port = (env.get("CHARON_PORT_P2P_TCP") or "").strip()
    p2p_tcp = (env.get("CHARON_P2P_TCP_ADDRESS") or "").strip()
    if p2p_tcp:
        plan.p2p_tcp_address = p2p_tcp
        plan.add_row(f"CHARON_P2P_TCP_ADDRESS={p2p_tcp}", f"--p2p-tcp-address={p2p_tcp}")
    elif p2p_port:
        plan.p2p_tcp_address = f"0.0.0.0:{p2p_port}"
        plan.add_row(
            f"CHARON_PORT_P2P_TCP={p2p_port}",
            f"--p2p-tcp-address={plan.p2p_tcp_address}",
        )
    else:
        plan.add_row(
            "# CHARON_PORT_P2P_TCP unset",
            f"--p2p-tcp-address={plan.p2p_tcp_address}",
            kind="default",
        )

    raw_validator = (env.get("CHARON_VALIDATOR_API_ADDRESS") or "").strip()
    raw_monitoring = (env.get("CHARON_MONITORING_ADDRESS") or "").strip()
    validator_api = raw_validator or DEFAULT_VALIDATOR_API_ADDRESS
    monitoring = raw_monitoring or DEFAULT_MONITORING_ADDRESS
    if prefer_localhost_binds:
        validator_api, v_warn = _localhost_bind(validator_api, DEFAULT_VALIDATOR_API_ADDRESS)
        monitoring, m_warn = _localhost_bind(monitoring, DEFAULT_MONITORING_ADDRESS)
        if v_warn:
            plan.warnings.append(v_warn)
        if m_warn:
            plan.warnings.append(m_warn)
    plan.validator_api_address = validator_api
    plan.monitoring_address = monitoring
    if raw_validator:
        plan.add_row(
            f"CHARON_VALIDATOR_API_ADDRESS={raw_validator}",
            f"--validator-api-address={validator_api}",
        )
    else:
        plan.add_row(
            "# CHARON_VALIDATOR_API_ADDRESS unset",
            f"--validator-api-address={validator_api}",
            kind="default",
        )
    if raw_monitoring:
        plan.add_row(
            f"CHARON_MONITORING_ADDRESS={raw_monitoring}",
            f"--monitoring-address={monitoring}",
        )
    else:
        plan.add_row(
            "# CHARON_MONITORING_ADDRESS unset",
            f"--monitoring-address={monitoring}",
            kind="default",
        )

    ext_ip = (env.get("CHARON_P2P_EXTERNAL_IP") or "").strip()
    if ext_ip:
        plan.p2p_external_ip = ext_ip
        plan.add_row(
            f"CHARON_P2P_EXTERNAL_IP={ext_ip}",
            f"--p2p-external-ip={ext_ip}",
        )

    feature = (env.get("CHARON_FEATURE_SET_ENABLE") or "").strip()
    if feature:
        plan.feature_set_enable = feature
        plan.add_row(
            f"CHARON_FEATURE_SET_ENABLE={feature}",
            f"--feature-set-enable={feature}",
        )

    handled = {
        "CHARON_BEACON_NODE_ENDPOINTS",
        "CHARON_BUILDER_API",
        "CHARON_P2P_TCP_ADDRESS",
        "CHARON_PORT_P2P_TCP",
        "CHARON_VALIDATOR_API_ADDRESS",
        "CHARON_MONITORING_ADDRESS",
        "CHARON_P2P_EXTERNAL_IP",
        "CHARON_FEATURE_SET_ENABLE",
    }
    extra_flag_keys = (
        "CHARON_P2P_RELAYS",
        "CHARON_P2P_EXTERNAL_HOSTNAME",
        "CHARON_LOG_LEVEL",
        "CHARON_LOG_FORMAT",
        "CHARON_FALLBACK_BEACON_NODE_ENDPOINTS",
        "CHARON_BEACON_NODE_TIMEOUT",
        "CHARON_BEACON_NODE_SUBMIT_TIMEOUT",
        "CHARON_BEACON_NODE_HEADERS",
        "CHARON_NICKNAME",
        "CHARON_EXECUTION_CLIENT_RPC_ENDPOINT",
    )
    for key in extra_flag_keys:
        value = (env.get(key) or "").strip()
        if not value:
            continue
        display_value = value
        if key in (
            "CHARON_FALLBACK_BEACON_NODE_ENDPOINTS",
            "CHARON_EXECUTION_CLIENT_RPC_ENDPOINT",
        ):
            value, warns = rewrite_endpoint_list(value, local_host=local_host)
            plan.warnings.extend(warns)
        flag = _charon_env_to_flag(key)
        arg = f"{flag}={value}"
        plan.extra_args.append(arg)
        plan.add_row(f"{key}={display_value}", arg)
        handled.add(key)

    for key, value in sorted(env.items()):
        if not key.startswith("CHARON_"):
            continue
        if key in handled:
            continue
        if not value.strip():
            continue
        if key in _SKIP_ENV_KEYS:
            plan.add_row(
                f"{key}={value}",
                "# skipped: not applicable to EthPillar systemd",
                kind="skipped",
            )
            continue
        plan.add_row(
            f"{key}={value}",
            "# skipped: unmapped — add manually if needed",
            kind="skipped",
        )

    plan.add_row("# --- EthPillar always sets ---", "# --- EthPillar always sets ---", kind="note")
    plan.add_row("# (lock + private key paths)", f"--lock-file={CHARON_LOCK_FILE}", kind="default")
    plan.add_row("#", f"--private-key-file={CHARON_PRIVATE_KEY_FILE}", kind="default")

    if not plan.beacon_node_endpoints:
        raise ValueError("No beacon node endpoints resolved from .env")

    return plan


def import_cdvn_env_to_service(
    env_path: str,
    service_path: str = CHARON_SERVICE_PATH,
    *,
    prefer_localhost_binds: bool = True,
    local_host: str = "127.0.0.1",
    apply: bool = False,
) -> CharonEnvImportPlan:
    """Parse a CDVN ``.env`` and optionally write ``charon.service``.

    Args:
        env_path: Path to the CDVN ``.env`` file.
        service_path: Target systemd unit path.
        prefer_localhost_binds: Remap ``0.0.0.0`` VC/metrics binds to loopback.
        local_host: Host used when rewriting Docker Compose DNS names.
        apply: When True, write the unit (and ``daemon-reload`` for /etc paths).

    Returns:
        The import plan (always), whether or not ``apply`` was set.
    """
    if not os.path.isfile(env_path):
        raise FileNotFoundError(f"CDVN .env not found: {env_path}")

    fallback_network = "mainnet"
    if os.path.isfile(service_path):
        with open(service_path, encoding="utf-8") as handle:
            existing = handle.read()
        match = _NETWORK_FROM_DESC_RE.search(existing)
        if match:
            fallback_network = match.group("net").lower()

    env = parse_dotenv(env_path)
    plan = plan_cdvn_env_import(
        env,
        fallback_network=fallback_network,
        local_host=local_host,
        prefer_localhost_binds=prefer_localhost_binds,
    )

    if apply:
        content = plan.service_content()
        if service_path.startswith("/etc/"):
            write_service_file(content, service_path, temp_filename="charon_temp.service")
            subprocess.run(["sudo", "systemctl", "daemon-reload"], check=False)
        else:
            with open(service_path, "w", encoding="utf-8") as handle:
                handle.write(content)

    return plan


def copy_charon_cluster(
    src_charon_dir: str,
    dest_charon_dir: str = CHARON_CLUSTER_DIR,
    *,
    force: bool = False,
) -> Dict[str, str]:
    """Copy a CDVN/DKG ``.charon`` tree into EthPillar's Charon datadir.

    Returns:
        Dict with ``src``, ``dest``, and ``status`` (``copied`` / ``skipped``).
    """
    import shutil

    src = os.path.abspath(os.path.expanduser(src_charon_dir))
    dest = os.path.abspath(dest_charon_dir)
    if not os.path.isdir(src):
        raise FileNotFoundError(f".charon directory not found: {src}")
    lock_src = os.path.join(src, "cluster-lock.json")
    if not os.path.isfile(lock_src):
        raise ValueError(f"Missing cluster-lock.json in {src}")

    dest_lock = os.path.join(dest, "cluster-lock.json")
    if os.path.isfile(dest_lock) and not force:
        return {
            "src": src,
            "dest": dest,
            "status": "skipped",
            "reason": f"Destination already has cluster-lock.json ({dest_lock}); pass force=True to overwrite",
        }

    try:
        os.makedirs(dest, mode=0o700, exist_ok=True)
        # Copy tree contents into dest
        for name in os.listdir(src):
            s_item = os.path.join(src, name)
            d_item = os.path.join(dest, name)
            if os.path.isdir(s_item):
                if os.path.isdir(d_item):
                    shutil.rmtree(d_item)
                shutil.copytree(s_item, d_item)
            else:
                shutil.copy2(s_item, d_item)
        os.chmod(dest, 0o700)
    except PermissionError:
        subprocess.run(["sudo", "mkdir", "-p", dest], check=True)
        subprocess.run(["sudo", "cp", "-a", f"{src}/.", f"{dest}/"], check=True)
        subprocess.run(
            ["sudo", "chown", "-R", f"{CHARON_USER}:{CHARON_USER}", CHARON_DATA_DIR],
            check=True,
        )
        subprocess.run(["sudo", "chmod", "700", CHARON_DATA_DIR], check=True)
        subprocess.run(["sudo", "chmod", "700", dest], check=True)
    else:
        # Best-effort ownership when running as root/install path
        if dest.startswith(BASE_DATA_DIR) or dest.startswith("/var/lib/"):
            subprocess.run(
                ["sudo", "chown", "-R", f"{CHARON_USER}:{CHARON_USER}", CHARON_DATA_DIR],
                check=False,
            )

    return {"src": src, "dest": dest, "status": "copied"}


def resolve_cdvn_checkout(path: str) -> Dict[str, object]:
    """Resolve a CDVN checkout path (directory or ``.env`` file) to migrate assets.

    Returns:
        Dict with ``root``, ``env_path``, ``charon_dir``, ``has_lock``,
        ``has_keyshares``, ``compose_file``.
    """
    raw = os.path.abspath(os.path.expanduser(path.strip()))
    if not os.path.exists(raw):
        raise FileNotFoundError(f"CDVN path not found: {raw}")

    if os.path.isfile(raw):
        root = os.path.dirname(raw)
        env_path: Optional[str] = raw if os.path.basename(raw) == ".env" or raw.endswith(".env") else None
        if env_path is None and os.path.isfile(os.path.join(root, ".env")):
            env_path = os.path.join(root, ".env")
    else:
        root = raw
        env_candidate = os.path.join(root, ".env")
        env_path = env_candidate if os.path.isfile(env_candidate) else None

    charon_dir = os.path.join(root, ".charon")
    if not os.path.isdir(charon_dir):
        charon_dir_opt: Optional[str] = None
        has_lock = False
        has_keyshares = False
    else:
        charon_dir_opt = charon_dir
        has_lock = os.path.isfile(os.path.join(charon_dir, "cluster-lock.json"))
        keys = os.path.join(charon_dir, "validator_keys")
        has_keyshares = os.path.isdir(keys) and any(
            name.startswith("keystore-") and name.endswith(".json")
            for name in os.listdir(keys)
        )

    compose = None
    for name in ("docker-compose.yml", "compose.yml"):
        candidate = os.path.join(root, name)
        if os.path.isfile(candidate):
            compose = candidate
            break

    return {
        "root": root,
        "env_path": env_path,
        "charon_dir": charon_dir_opt,
        "has_lock": has_lock,
        "has_keyshares": has_keyshares,
        "compose_file": compose,
    }


def _cmd_resolve_cdvn(args: argparse.Namespace) -> int:
    try:
        info = resolve_cdvn_checkout(args.path)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    import json

    print(json.dumps(info, indent=2))
    return 0


def _cmd_copy_charon(args: argparse.Namespace) -> int:
    try:
        result = copy_charon_cluster(args.src, args.dest, force=args.force)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(result["status"])
    for key, value in result.items():
        if key != "status":
            print(f"{key}={value}")
    return 0 if result["status"] == "copied" else (0 if result["status"] == "skipped" else 1)


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


def _cmd_import_env(args: argparse.Namespace) -> int:
    try:
        plan = import_cdvn_env_to_service(
            args.env,
            service_path=args.service_path,
            prefer_localhost_binds=not args.keep_docker_binds,
            local_host=args.local_host,
            apply=args.apply,
        )
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    preview_dir = args.preview_dir
    if args.tmeld or preview_dir:
        preview_dir = preview_dir or tempfile.mkdtemp(prefix="ethpillar-charon-env-")
        left, right = write_import_preview_panes(plan, preview_dir)
        print(f"Preview panes:\n  left:  {left}\n  right: {right}")
        if args.tmeld:
            try:
                launch_import_preview_tmeld(preview_dir)
            except RuntimeError as exc:
                print(f"WARNING: {exc}", file=sys.stderr)
                print(plan.summary())
    else:
        print(plan.summary())

    print()
    if args.apply:
        print(f"Wrote {args.service_path}")
    else:
        print("Dry run only. Re-run with --apply to write charon.service.")
        if args.print_unit:
            print()
            print(plan.service_content())
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

    import_parser = subparsers.add_parser(
        "import_env",
        help="Convert a CDVN .env into charon.service flags",
    )
    import_parser.add_argument("--env", required=True, help="Path to CDVN .env file")
    import_parser.add_argument(
        "--service-path",
        default=CHARON_SERVICE_PATH,
        help="Path to charon.service",
    )
    import_parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the systemd unit (default: preview only)",
    )
    import_parser.add_argument(
        "--print-unit",
        action="store_true",
        help="Print full unit content during dry run",
    )
    import_parser.add_argument(
        "--preview-dir",
        default="",
        help="Write side-by-side preview panes into this directory",
    )
    import_parser.add_argument(
        "--tmeld",
        action="store_true",
        help="Open tmeld side-by-side preview (left=.env, right=systemd flags)",
    )
    import_parser.add_argument(
        "--local-host",
        default="127.0.0.1",
        help="Host used when rewriting Docker Compose beacon URLs",
    )
    import_parser.add_argument(
        "--keep-docker-binds",
        action="store_true",
        help="Keep 0.0.0.0 validator-api/monitoring binds from .env",
    )
    import_parser.set_defaults(func=_cmd_import_env)

    resolve_parser = subparsers.add_parser(
        "resolve_cdvn",
        help="Inspect a CDVN checkout for .env / .charon assets (JSON)",
    )
    resolve_parser.add_argument("--path", required=True, help="CDVN directory or .env path")
    resolve_parser.set_defaults(func=_cmd_resolve_cdvn)

    copy_parser = subparsers.add_parser(
        "copy_charon",
        help="Copy a .charon cluster folder into /var/lib/charon/.charon",
    )
    copy_parser.add_argument("--src", required=True, help="Source .charon directory")
    copy_parser.add_argument(
        "--dest",
        default=CHARON_CLUSTER_DIR,
        help="Destination .charon directory",
    )
    copy_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite destination even if cluster-lock.json exists",
    )
    copy_parser.set_defaults(func=_cmd_copy_charon)

    parsed = parser.parse_args(argv)
    return parsed.func(parsed)


if __name__ == "__main__":
    sys.exit(main())
