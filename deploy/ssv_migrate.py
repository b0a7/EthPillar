"""Plan and run a legacy systemd SSV → EthPillar migration.

Discovers ``ssvnode`` / ``ssvdkg`` unit name variants, preserves multi-endpoint
BN/EL settings from the live YAML configs, quarantines legacy units as
``*.service.bak``, and installs EthPillar ``ssv-node`` / ``ssv-dkg`` with
secrets and DB under ``/var/lib/ssv/``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from deploy.common import (
    DOWNLOAD_DIR,
    _normalize_release_version_key,
    download_release_file,
    extract_and_install,
    get_machine_architecture,
    write_service_file,
)
from deploy.ssv import (
    DEFAULT_API_PORT,
    DEFAULT_DKG_PORT,
    DEFAULT_EL_WS,
    DEFAULT_METRICS_PORT,
    DEFAULT_P2P_TCP_PORT,
    DEFAULT_P2P_UDP_PORT,
    SSV_CONFIG_FILE,
    SSV_DATA_DIR,
    SSV_DB_DIR,
    SSV_DKG_BIN,
    SSV_DKG_CONFIG_FILE,
    SSV_DKG_OUTPUT_DIR,
    SSV_DKG_SERVICE_PATH,
    SSV_NODE_BIN,
    SSV_NODE_SERVICE_PATH,
    SSV_PASSWORD_FILE,
    SSV_PRIVATE_KEY_FILE,
    SSV_USER,
    build_ssvnode_from_source,
    ensure_ssv_datadir,
    generate_ssv_config,
    generate_ssv_dkg_config,
    generate_ssv_dkg_service,
    generate_ssv_node_service,
    get_dkg_release_info,
    get_release_info,
    import_operator_keystore_files,
    install_binary_file,
    operator_keystore_present,
    _sudo_write_text,
)

SYSTEMD_UNIT_DIR = "/etc/systemd/system"
ETHPILLAR_NODE_UNIT = "ssv-node.service"
ETHPILLAR_DKG_UNIT = "ssv-dkg.service"

_NODE_NAME_RE = re.compile(
    r"^(ssvnode|ssv[-_]?node|ssv)\.service$",
    re.IGNORECASE,
)
_DKG_NAME_RE = re.compile(
    r"^(ssvdkg|ssv[-_]?dkg)\.service$",
    re.IGNORECASE,
)
_VERSION_RE = re.compile(r"v?\d+\.\d+(?:\.\d+)?", re.IGNORECASE)


@dataclass
class DiscoveredUnit:
    """One legacy systemd unit involved in the migrate."""

    role: str  # "node" | "dkg"
    unit_name: str
    unit_path: str
    working_directory: str = ""
    exec_start: str = ""
    binary_path: str = ""
    config_path: str = ""


@dataclass
class BinaryPlan:
    """How migrate will obtain an EthPillar-managed binary."""

    role: str
    action: str  # copy | rebuild | download | missing
    source_path: str = ""
    installed_version: str = ""
    latest_version: str = ""
    note: str = ""


@dataclass
class SsvMigratePlan:
    """Full migration plan for display / JSON."""

    node_unit: Optional[DiscoveredUnit] = None
    dkg_unit: Optional[DiscoveredUnit] = None
    eth_network: str = "mainnet"
    beacon_node_addr: str = ""
    eth1_ws_addr: str = DEFAULT_EL_WS
    eth1_http_addr: str = "http://127.0.0.1:8545"
    p2p_tcp_port: int = DEFAULT_P2P_TCP_PORT
    p2p_udp_port: int = DEFAULT_P2P_UDP_PORT
    metrics_port: int = DEFAULT_METRICS_PORT
    api_port: int = DEFAULT_API_PORT
    dkg_port: int = DEFAULT_DKG_PORT
    p2p_external_ip: str = ""
    operator_id: str = ""
    private_key_path: str = ""
    password_path: str = ""
    db_path: str = ""
    dkg_output_path: str = ""
    node_binary: Optional[BinaryPlan] = None
    dkg_binary: Optional[BinaryPlan] = None
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def summary(self) -> str:
        """Human-readable plan text for TUI / CLI."""
        lines = ["SSV systemd → EthPillar migration plan", ""]
        if self.node_unit:
            lines.append(
                f"Node unit: {self.node_unit.unit_name} → {ETHPILLAR_NODE_UNIT}"
            )
            lines.append(f"  config: {self.node_unit.config_path or '(none)'}")
            lines.append(f"  binary: {self.node_unit.binary_path or '(none)'}")
        else:
            lines.append("Node unit: (not found)")
        if self.dkg_unit:
            lines.append(
                f"DKG unit:  {self.dkg_unit.unit_name} → {ETHPILLAR_DKG_UNIT}"
            )
            lines.append(f"  config: {self.dkg_unit.config_path or '(none)'}")
            lines.append(f"  binary: {self.dkg_unit.binary_path or '(none)'}")
        else:
            lines.append("DKG unit:  (not found — node-only migrate)")
        lines.append("")
        lines.append(f"Network:     {self.eth_network}")
        lines.append(f"BeaconNode:  {self.beacon_node_addr or '(empty)'}")
        lines.append(f"ETH1 WS:     {self.eth1_ws_addr}")
        lines.append(f"ETH1 HTTP:   {self.eth1_http_addr}")
        lines.append(
            f"Ports:       p2p_tcp={self.p2p_tcp_port} p2p_udp={self.p2p_udp_port} "
            f"metrics={self.metrics_port} api={self.api_port} dkg={self.dkg_port}"
        )
        if self.p2p_external_ip:
            lines.append(f"HostAddress: {self.p2p_external_ip}")
        if self.operator_id:
            lines.append(f"operatorID:  {self.operator_id}")
        lines.append("")
        lines.append(f"Keystore:    {self.private_key_path or '(missing)'}")
        lines.append(f"Password:    {self.password_path or '(missing)'}")
        lines.append(f"DB:          {self.db_path or '(missing)'} → {SSV_DB_DIR}")
        if self.dkg_output_path:
            lines.append(f"DKG output:  {self.dkg_output_path} → {SSV_DKG_OUTPUT_DIR}")
        lines.append("")
        if self.node_binary:
            lines.append(
                f"ssvnode:  {self.node_binary.action} "
                f"(installed={self.node_binary.installed_version or '?'} "
                f"latest={self.node_binary.latest_version or '?'})"
            )
            if self.node_binary.note:
                lines.append(f"          {self.node_binary.note}")
        if self.dkg_binary:
            lines.append(
                f"ssv-dkg:  {self.dkg_binary.action} "
                f"(installed={self.dkg_binary.installed_version or '?'} "
                f"latest={self.dkg_binary.latest_version or '?'})"
            )
            if self.dkg_binary.note:
                lines.append(f"          {self.dkg_binary.note}")
        lines.append("")
        lines.append("Legacy units will be stop+disable+renamed to *.service.bak")
        lines.append(
            f"EthPillar datadir: {SSV_DATA_DIR} (configs keep BN/EL endpoints; "
            "no .env.overrides)"
        )
        lines.append(
            "Rollback: stop EthPillar ssv-*; rename *.service.bak → *.service; "
            "daemon-reload; start legacy"
        )
        if self.warnings:
            lines.append("")
            lines.append("Warnings:")
            lines.extend(f"  - {w}" for w in self.warnings)
        if self.errors:
            lines.append("")
            lines.append("Errors:")
            lines.extend(f"  - {e}" for e in self.errors)
        return "\n".join(lines)


def classify_unit_name(filename: str) -> Optional[str]:
    """Return ``node``, ``dkg``, or None for a systemd unit basename."""
    name = filename.strip()
    if not name.endswith(".service"):
        name = f"{name}.service"
    lower = name.lower()
    if lower in (ETHPILLAR_NODE_UNIT, ETHPILLAR_DKG_UNIT):
        return None
    if _DKG_NAME_RE.match(name):
        return "dkg"
    if "dkg" in lower:
        return None
    if _NODE_NAME_RE.match(name):
        return "node"
    return None


def _parse_unit_file(text: str) -> Dict[str, str]:
    """Parse simple key=value pairs from a systemd unit body."""
    values: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        # Last assignment wins (common for multi ExecStart=).
        values[key.strip()] = val.strip()
    return values


def _first_token(exec_start: str) -> str:
    """Return the binary path from an ExecStart line (strip systemd prefixes)."""
    text = exec_start.strip()
    for prefix in ("-", "@", ":", "+", "!"):
        while text.startswith(prefix):
            text = text[1:].lstrip()
    if not text:
        return ""
    # Handle quoted path.
    if text[0] in ("'", '"'):
        quote = text[0]
        end = text.find(quote, 1)
        if end > 0:
            return text[1:end]
    return text.split()[0]


def _config_from_exec(exec_start: str, *, flags: Sequence[str]) -> str:
    """Extract a config path after one of *flags* in ExecStart."""
    tokens = exec_start.split()
    for i, tok in enumerate(tokens):
        for flag in flags:
            if tok == flag and i + 1 < len(tokens):
                return tokens[i + 1].strip("'\"")
            if tok.startswith(f"{flag}="):
                return tok.split("=", 1)[1].strip("'\"")
    return ""


def _resolve_path(path: str, *, workdir: str, unit_dir: str) -> str:
    """Resolve relative paths against WorkingDirectory or the unit directory."""
    if not path:
        return ""
    expanded = os.path.expanduser(path)
    if os.path.isabs(expanded):
        return os.path.normpath(expanded)
    base = workdir or unit_dir or os.getcwd()
    return os.path.normpath(os.path.join(base, expanded))


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        result = subprocess.run(
            ["sudo", "cat", path],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return ""
        return result.stdout


def _yaml_scalar_or_list(content: str, key: str) -> str:
    """Return a YAML mapping value as a string (join list items with commas).

    Preserves multi-endpoint scalars verbatim. For block lists, joins with ``,``.
    """
    lines = content.splitlines()
    key_re = re.compile(rf"^\s*{re.escape(key)}\s*:\s*(.*)$")
    for i, line in enumerate(lines):
        match = key_re.match(line)
        if not match:
            continue
        rest = match.group(1).strip()
        if rest and rest not in ("|", ">", ">-", "|-"):
            # Strip optional quotes.
            if (rest.startswith('"') and rest.endswith('"')) or (
                rest.startswith("'") and rest.endswith("'")
            ):
                return rest[1:-1]
            return rest
        # Block list under this key.
        items: List[str] = []
        for follow in lines[i + 1 :]:
            stripped = follow.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # Dedent: stop when indentation returns to key level or less
            # without a list marker — approximate with leading spaces count.
            if follow[:1] not in (" ", "\t") and not stripped.startswith("-"):
                break
            if stripped.startswith("- "):
                item = stripped[2:].strip().strip("\"'")
                if item:
                    items.append(item)
            elif items and follow.startswith((" ", "\t")):
                continue
            else:
                break
        if items:
            return ",".join(items)
        return ""
    return ""


def _yaml_int(content: str, key: str, default: int) -> int:
    raw = _yaml_scalar_or_list(content, key)
    if not raw:
        return default
    try:
        value = int(raw.split()[0])
    except ValueError:
        return default
    return value if 1 <= value <= 65535 else default


def parse_node_config(content: str) -> Dict[str, object]:
    """Extract migrate-relevant fields from an SSV node config.yaml body."""
    network_raw = _yaml_scalar_or_list(content, "Network") or "mainnet"
    network = network_raw.strip().lower()
    # EthPillar uses hoodi/sepolia/mainnet; SSV may already match.
    eth_network = network if network else "mainnet"
    return {
        "eth_network": eth_network,
        "beacon_node_addr": _yaml_scalar_or_list(content, "BeaconNodeAddr"),
        "eth1_ws_addr": _yaml_scalar_or_list(content, "ETH1Addr") or DEFAULT_EL_WS,
        "p2p_tcp_port": _yaml_int(content, "TcpPort", DEFAULT_P2P_TCP_PORT),
        "p2p_udp_port": _yaml_int(content, "UdpPort", DEFAULT_P2P_UDP_PORT),
        "metrics_port": _yaml_int(content, "MetricsAPIPort", DEFAULT_METRICS_PORT),
        "api_port": _yaml_int(content, "SSVAPIPort", DEFAULT_API_PORT),
        "p2p_external_ip": _yaml_scalar_or_list(content, "HostAddress"),
        "db_path": _yaml_scalar_or_list(content, "Path"),
        "private_key_path": _yaml_scalar_or_list(content, "PrivateKeyFile"),
        "password_path": _yaml_scalar_or_list(content, "PasswordFile"),
    }


def parse_dkg_config(content: str) -> Dict[str, object]:
    """Extract migrate-relevant fields from a DKG operator config body."""
    return {
        "private_key_path": _yaml_scalar_or_list(content, "privKey"),
        "password_path": _yaml_scalar_or_list(content, "privKeyPassword"),
        "operator_id": _yaml_scalar_or_list(content, "operatorID"),
        "dkg_port": _yaml_int(content, "port", DEFAULT_DKG_PORT),
        "eth1_http_addr": _yaml_scalar_or_list(content, "ethEndpointURL")
        or "http://127.0.0.1:8545",
        "dkg_output_path": _yaml_scalar_or_list(content, "outputPath"),
    }


def discover_units(
    unit_dir: str = SYSTEMD_UNIT_DIR,
) -> Tuple[Optional[DiscoveredUnit], Optional[DiscoveredUnit]]:
    """Find legacy node/DKG units under *unit_dir* (EthPillar units skipped)."""
    node: Optional[DiscoveredUnit] = None
    dkg: Optional[DiscoveredUnit] = None
    try:
        names = sorted(os.listdir(unit_dir))
    except OSError:
        return None, None
    for name in names:
        if not name.endswith(".service"):
            continue
        role = classify_unit_name(name)
        if not role:
            continue
        path = os.path.join(unit_dir, name)
        text = _read_text(path)
        if not text:
            continue
        fields = _parse_unit_file(text)
        workdir = fields.get("WorkingDirectory", "")
        exec_start = fields.get("ExecStart", "")
        binary = _resolve_path(
            _first_token(exec_start), workdir=workdir, unit_dir=unit_dir
        )
        if role == "node":
            config = _config_from_exec(
                exec_start, flags=("--config", "-config")
            )
        else:
            config = _config_from_exec(
                exec_start, flags=("--configPath", "--config")
            )
        config_path = _resolve_path(config, workdir=workdir, unit_dir=unit_dir)
        discovered = DiscoveredUnit(
            role=role,
            unit_name=name,
            unit_path=path,
            working_directory=workdir,
            exec_start=exec_start,
            binary_path=binary,
            config_path=config_path,
        )
        if role == "node" and node is None:
            node = discovered
        elif role == "dkg" and dkg is None:
            dkg = discovered
    return node, dkg


def _find_fallback_secrets(
    *,
    workdir: str,
    config_path: str,
) -> Tuple[str, str]:
    """Locate encrypted_private_key.json + password near workdir/config."""
    candidates = []
    if config_path:
        candidates.append(os.path.dirname(config_path))
        # e.g. ~/ssv/config/config.yaml → ~/ssv
        parent = os.path.dirname(os.path.dirname(config_path))
        if parent:
            candidates.append(parent)
    if workdir:
        candidates.append(workdir)
    for base in candidates:
        key = os.path.join(base, "encrypted_private_key.json")
        pw = os.path.join(base, "password")
        if os.path.isfile(key) and os.path.isfile(pw):
            return key, pw
    return "", ""


def _find_dkg_config(
    unit: Optional[DiscoveredUnit],
) -> str:
    """Resolve DKG config path from unit or operator-config/ heuristics."""
    if unit and unit.config_path and os.path.isfile(unit.config_path):
        return unit.config_path
    search_roots: List[str] = []
    if unit:
        if unit.working_directory:
            search_roots.append(unit.working_directory)
        if unit.binary_path:
            search_roots.append(os.path.dirname(unit.binary_path))
            # bin/ → repo root
            search_roots.append(os.path.dirname(os.path.dirname(unit.binary_path)))
    for root in search_roots:
        op_dir = os.path.join(root, "operator-config")
        if os.path.isdir(op_dir):
            for name in sorted(os.listdir(op_dir)):
                if name.endswith((".yaml", ".yml")):
                    return os.path.join(op_dir, name)
        for name in ("operator.yaml", "config.yaml"):
            path = os.path.join(root, name)
            if os.path.isfile(path):
                return path
    return unit.config_path if unit else ""


def probe_binary_version(binary_path: str, *, role: str) -> str:
    """Return a semver-ish version string from a binary, or empty."""
    if not binary_path or not os.path.isfile(binary_path):
        return ""
    commands = (
        [binary_path, "version"],
        [binary_path, "--version"],
        [binary_path, "version", "--output", "simple"],
    )
    output = ""
    for cmd in commands:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False
        )
        blob = (result.stdout or "") + "\n" + (result.stderr or "")
        if blob.strip():
            output = blob
            break
    match = _VERSION_RE.search(output)
    if not match:
        return ""
    ver = match.group(0)
    if not ver.lower().startswith("v"):
        ver = f"v{ver}"
    return ver


def versions_match(installed: str, latest: str) -> bool:
    """True when normalized release keys match."""
    if not installed or not latest:
        return False
    return _normalize_release_version_key(installed) == _normalize_release_version_key(
        latest
    )


def _plan_node_binary(
    source_path: str,
    *,
    rebuild: bool,
    allow_older: bool,
) -> BinaryPlan:
    arch_amd64 = get_machine_architecture() == "amd64"
    latest = ""
    try:
        latest = get_release_info("LATEST", arch_amd64)["version"]
    except Exception as exc:  # noqa: BLE001 — plan should still render
        return BinaryPlan(
            role="node",
            action="rebuild",
            source_path=source_path,
            latest_version="",
            note=f"could not resolve LATEST ({exc}); will rebuild",
        )
    installed = probe_binary_version(source_path, role="node")
    if rebuild:
        return BinaryPlan(
            role="node",
            action="rebuild",
            source_path=source_path,
            installed_version=installed,
            latest_version=latest,
            note="--rebuild-ssvnode forced",
        )
    if source_path and os.path.isfile(source_path) and versions_match(installed, latest):
        return BinaryPlan(
            role="node",
            action="copy",
            source_path=source_path,
            installed_version=installed,
            latest_version=latest,
            note="legacy binary matches LATEST",
        )
    if source_path and os.path.isfile(source_path) and allow_older and installed:
        return BinaryPlan(
            role="node",
            action="copy",
            source_path=source_path,
            installed_version=installed,
            latest_version=latest,
            note="WARNING: allowing older binary via --allow-older-binary",
        )
    note = "missing or not LATEST — will rebuild from source"
    if installed and latest and not versions_match(installed, latest):
        note = f"legacy {installed} != LATEST {latest} — will rebuild"
    elif not source_path or not os.path.isfile(source_path):
        note = "legacy binary missing — will rebuild"
    elif not installed:
        note = "could not parse legacy version — will rebuild"
    return BinaryPlan(
        role="node",
        action="rebuild",
        source_path=source_path,
        installed_version=installed,
        latest_version=latest,
        note=note,
    )


def _plan_dkg_binary(
    source_path: str,
    *,
    allow_older: bool,
    want_dkg: bool,
) -> Optional[BinaryPlan]:
    if not want_dkg:
        return None
    arch_amd64 = get_machine_architecture() == "amd64"
    latest = ""
    try:
        latest = get_dkg_release_info("LATEST", arch_amd64)["version"]
    except Exception as exc:  # noqa: BLE001
        return BinaryPlan(
            role="dkg",
            action="download",
            source_path=source_path,
            note=f"could not resolve DKG LATEST ({exc}); will download",
        )
    installed = probe_binary_version(source_path, role="dkg")
    if source_path and os.path.isfile(source_path) and versions_match(installed, latest):
        return BinaryPlan(
            role="dkg",
            action="copy",
            source_path=source_path,
            installed_version=installed,
            latest_version=latest,
            note="legacy binary matches LATEST",
        )
    if source_path and os.path.isfile(source_path) and allow_older and installed:
        return BinaryPlan(
            role="dkg",
            action="copy",
            source_path=source_path,
            installed_version=installed,
            latest_version=latest,
            note="WARNING: allowing older binary via --allow-older-binary",
        )
    note = "will download GitHub release zip"
    if installed and latest and not versions_match(installed, latest):
        note = f"legacy {installed} != LATEST {latest} — will download"
    return BinaryPlan(
        role="dkg",
        action="download",
        source_path=source_path,
        installed_version=installed,
        latest_version=latest,
        note=note,
    )


def plan_ssv_migration(
    *,
    unit_dir: str = SYSTEMD_UNIT_DIR,
    rebuild_ssvnode: bool = False,
    allow_older_binary: bool = False,
) -> SsvMigratePlan:
    """Build a migration plan from discovered systemd units and YAML configs."""
    plan = SsvMigratePlan()
    node, dkg = discover_units(unit_dir)
    plan.node_unit = node
    plan.dkg_unit = dkg

    if not node:
        plan.errors.append(
            "No legacy SSV node unit found "
            "(expected ssvnode / ssv-node / ssv_node / ssv.service)"
        )
        return plan

    node_cfg_path = node.config_path
    node_cfg = _read_text(node_cfg_path) if node_cfg_path else ""
    if not node_cfg:
        plan.errors.append(f"Could not read node config: {node_cfg_path or '(empty)'}")
    else:
        parsed = parse_node_config(node_cfg)
        plan.eth_network = str(parsed["eth_network"])
        plan.beacon_node_addr = str(parsed["beacon_node_addr"])
        plan.eth1_ws_addr = str(parsed["eth1_ws_addr"])
        plan.p2p_tcp_port = int(parsed["p2p_tcp_port"])
        plan.p2p_udp_port = int(parsed["p2p_udp_port"])
        plan.metrics_port = int(parsed["metrics_port"])
        plan.api_port = int(parsed["api_port"])
        plan.p2p_external_ip = str(parsed["p2p_external_ip"])
        plan.db_path = _resolve_path(
            str(parsed["db_path"]),
            workdir=node.working_directory,
            unit_dir=unit_dir,
        )
        plan.private_key_path = _resolve_path(
            str(parsed["private_key_path"]),
            workdir=node.working_directory,
            unit_dir=unit_dir,
        )
        plan.password_path = _resolve_path(
            str(parsed["password_path"]),
            workdir=node.working_directory,
            unit_dir=unit_dir,
        )

    if not plan.private_key_path or not plan.password_path:
        fb_key, fb_pw = _find_fallback_secrets(
            workdir=node.working_directory, config_path=node_cfg_path
        )
        plan.private_key_path = plan.private_key_path or fb_key
        plan.password_path = plan.password_path or fb_pw

    dkg_cfg_path = _find_dkg_config(dkg)
    if dkg and dkg_cfg_path:
        dkg.config_path = dkg_cfg_path
        dkg_cfg = _read_text(dkg_cfg_path)
        if dkg_cfg:
            parsed_dkg = parse_dkg_config(dkg_cfg)
            plan.operator_id = str(parsed_dkg["operator_id"])
            plan.dkg_port = int(parsed_dkg["dkg_port"])
            plan.eth1_http_addr = str(parsed_dkg["eth1_http_addr"])
            plan.dkg_output_path = _resolve_path(
                str(parsed_dkg["dkg_output_path"]),
                workdir=dkg.working_directory,
                unit_dir=unit_dir,
            )
            if not plan.private_key_path:
                plan.private_key_path = _resolve_path(
                    str(parsed_dkg["private_key_path"]),
                    workdir=dkg.working_directory,
                    unit_dir=unit_dir,
                )
            if not plan.password_path:
                plan.password_path = _resolve_path(
                    str(parsed_dkg["password_path"]),
                    workdir=dkg.working_directory,
                    unit_dir=unit_dir,
                )
        else:
            plan.warnings.append(f"Could not read DKG config: {dkg_cfg_path}")
    elif dkg:
        plan.warnings.append("DKG unit found but no operator config located")

    if not plan.beacon_node_addr:
        plan.errors.append("BeaconNodeAddr missing from node config")
    if not plan.private_key_path or not os.path.isfile(plan.private_key_path):
        plan.errors.append("Operator encrypted_private_key.json not found")
    if not plan.password_path or not os.path.isfile(plan.password_path):
        plan.errors.append("Operator password file not found")
    if not plan.db_path or not os.path.isdir(plan.db_path):
        plan.warnings.append(
            f"DB path missing or not a directory: {plan.db_path or '(empty)'} "
            "(migrate will create an empty EthPillar db/)"
        )

    if os.path.isfile(os.path.join(unit_dir, ETHPILLAR_NODE_UNIT)) and operator_keystore_present():
        plan.warnings.append(
            "EthPillar ssv-node.service already present with keystore; "
            "run with --force to overwrite"
        )

    plan.node_binary = _plan_node_binary(
        node.binary_path,
        rebuild=rebuild_ssvnode,
        allow_older=allow_older_binary,
    )
    plan.dkg_binary = _plan_dkg_binary(
        dkg.binary_path if dkg else "",
        allow_older=allow_older_binary,
        want_dkg=dkg is not None,
    )
    return plan


def quarantine_unit(
    unit_path: str,
    *,
    dry_run: bool = False,
) -> str:
    """Stop, disable, and rename a unit to ``*.service.bak``. Return bak path."""
    if not unit_path.endswith(".service"):
        raise ValueError(f"Not a .service unit: {unit_path}")
    bak = f"{unit_path}.bak"
    unit_name = os.path.basename(unit_path)
    stem = unit_name[: -len(".service")]
    if dry_run:
        return bak
    subprocess.run(["sudo", "systemctl", "stop", stem], check=False)
    subprocess.run(["sudo", "systemctl", "disable", stem], check=False)
    if os.path.exists(bak):
        # Keep a numbered backup so we never clobber a previous quarantine.
        i = 1
        while os.path.exists(f"{bak}.{i}"):
            i += 1
        subprocess.run(["sudo", "mv", bak, f"{bak}.{i}"], check=True)
    subprocess.run(["sudo", "mv", unit_path, bak], check=True)
    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
    return bak


def _copy_tree(src: str, dest: str) -> None:
    subprocess.run(["sudo", "mkdir", "-p", dest], check=True)
    # Trailing slash copies contents into dest.
    subprocess.run(["sudo", "rsync", "-a", f"{src.rstrip('/')}/", f"{dest.rstrip('/')}/"], check=True)
    subprocess.run(["sudo", "chown", "-R", f"{SSV_USER}:{SSV_USER}", dest], check=True)


def _install_node_binary(plan: SsvMigratePlan, *, dry_run: bool) -> str:
    assert plan.node_binary is not None
    bp = plan.node_binary
    version = bp.latest_version or get_release_info(
        "LATEST", get_machine_architecture() == "amd64"
    )["version"]
    if dry_run:
        return version
    if bp.action == "copy":
        install_binary_file(bp.source_path, SSV_NODE_BIN)
        return bp.installed_version or version
    build_ssvnode_from_source(version, SSV_NODE_BIN)
    return version


def _install_dkg_binary(plan: SsvMigratePlan, *, dry_run: bool) -> None:
    if plan.dkg_binary is None:
        return
    bp = plan.dkg_binary
    if dry_run:
        return
    if bp.action == "copy":
        install_binary_file(bp.source_path, SSV_DKG_BIN)
        return
    arch_amd64 = get_machine_architecture() == "amd64"
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


def run_ssv_migration(
    *,
    dry_run: bool = False,
    force: bool = False,
    start: bool = False,
    rebuild_ssvnode: bool = False,
    allow_older_binary: bool = False,
    unit_dir: str = SYSTEMD_UNIT_DIR,
) -> SsvMigratePlan:
    """Execute (or dry-run) the SSV systemd migration."""
    plan = plan_ssv_migration(
        unit_dir=unit_dir,
        rebuild_ssvnode=rebuild_ssvnode,
        allow_older_binary=allow_older_binary,
    )
    if plan.errors:
        raise ValueError("; ".join(plan.errors))

    ethpillar_unit = os.path.join(unit_dir, ETHPILLAR_NODE_UNIT)
    if (
        not force
        and os.path.isfile(ethpillar_unit)
        and operator_keystore_present()
    ):
        raise RuntimeError(
            "EthPillar SSV already installed with keystore; pass --force to overwrite"
        )

    print(plan.summary())
    print("")

    if plan.node_unit:
        bak = quarantine_unit(plan.node_unit.unit_path, dry_run=dry_run)
        print(f"{'[dry-run] ' if dry_run else ''}Quarantined {plan.node_unit.unit_name} → {bak}")
    if plan.dkg_unit:
        bak = quarantine_unit(plan.dkg_unit.unit_path, dry_run=dry_run)
        print(f"{'[dry-run] ' if dry_run else ''}Quarantined {plan.dkg_unit.unit_name} → {bak}")

    if not dry_run:
        ensure_ssv_datadir()

    version = _install_node_binary(plan, dry_run=dry_run)
    print(
        f"{'[dry-run] ' if dry_run else ''}"
        f"ssvnode action={plan.node_binary.action if plan.node_binary else '?'} "
        f"→ {SSV_NODE_BIN} ({version})"
    )
    if plan.dkg_unit:
        _install_dkg_binary(plan, dry_run=dry_run)
        print(
            f"{'[dry-run] ' if dry_run else ''}"
            f"ssv-dkg action={plan.dkg_binary.action if plan.dkg_binary else '?'} "
            f"→ {SSV_DKG_BIN}"
        )

    # Configs with EthPillar paths + preserved multi-endpoint BN/EL strings.
    eth_network = plan.eth_network or "mainnet"
    config_body = generate_ssv_config(
        eth_network,
        plan.beacon_node_addr,
        plan.eth1_ws_addr,
        p2p_tcp_port=plan.p2p_tcp_port,
        p2p_udp_port=plan.p2p_udp_port,
        metrics_port=plan.metrics_port,
        api_port=plan.api_port,
        p2p_external_ip=plan.p2p_external_ip,
    )
    # Keep the source Network value verbatim (do not remap via ssv_network_name).
    config_body = re.sub(
        r"(?m)^(\s*Network:\s*).*$",
        rf"\g<1>{eth_network}",
        config_body,
        count=1,
    )
    dkg_body = generate_ssv_dkg_config(
        eth_network,
        plan.eth1_http_addr,
        operator_id=plan.operator_id,
        dkg_port=plan.dkg_port,
    )

    if dry_run:
        print(f"[dry-run] Would write {SSV_CONFIG_FILE}")
        print(f"[dry-run] Would write {SSV_DKG_CONFIG_FILE}" if plan.dkg_unit else "")
        print(f"[dry-run] Would import keystore from {plan.private_key_path}")
        if plan.db_path and os.path.isdir(plan.db_path):
            print(f"[dry-run] Would copy DB {plan.db_path} → {SSV_DB_DIR}")
        if plan.dkg_output_path and os.path.isdir(plan.dkg_output_path):
            print(
                f"[dry-run] Would copy DKG output {plan.dkg_output_path} → {SSV_DKG_OUTPUT_DIR}"
            )
        print(f"[dry-run] Would write {SSV_NODE_SERVICE_PATH}")
        if plan.dkg_unit:
            print(f"[dry-run] Would write {SSV_DKG_SERVICE_PATH}")
        return plan

    import_operator_keystore_files(plan.private_key_path, plan.password_path)
    if plan.db_path and os.path.isdir(plan.db_path):
        _copy_tree(plan.db_path, SSV_DB_DIR)
    if plan.dkg_output_path and os.path.isdir(plan.dkg_output_path):
        _copy_tree(plan.dkg_output_path, SSV_DKG_OUTPUT_DIR)

    _sudo_write_text(SSV_CONFIG_FILE, config_body, "600")
    node_unit = generate_ssv_node_service(eth_network)
    write_service_file(node_unit, SSV_NODE_SERVICE_PATH, "ssv_node_migrate_temp.service")

    if plan.dkg_unit:
        _sudo_write_text(SSV_DKG_CONFIG_FILE, dkg_body, "600")
        dkg_unit = generate_ssv_dkg_service(eth_network)
        write_service_file(dkg_unit, SSV_DKG_SERVICE_PATH, "ssv_dkg_migrate_temp.service")

    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
    subprocess.run(["sudo", "systemctl", "enable", "ssv-node"], check=False)
    if plan.dkg_unit:
        subprocess.run(["sudo", "systemctl", "enable", "ssv-dkg"], check=False)

    if start:
        subprocess.run(["sudo", "systemctl", "start", "ssv-node"], check=True)
        if plan.dkg_unit:
            subprocess.run(["sudo", "systemctl", "start", "ssv-dkg"], check=False)
        print("Started EthPillar ssv-node" + (" + ssv-dkg" if plan.dkg_unit else ""))
    else:
        print(
            "EthPillar units enabled but not started. When ready:\n"
            "  sudo systemctl start ssv-node"
            + ("\n  sudo systemctl start ssv-dkg" if plan.dkg_unit else "")
        )

    print(
        "\nNext steps:\n"
        "  - EthPillar → Security → UFW → SSV ports if needed\n"
        "  - Verify BN/EL endpoints in /var/lib/ssv/config.yaml\n"
        "  - Rollback: stop ssv-node/ssv-dkg; rename *.service.bak → *.service; "
        "daemon-reload; start legacy"
    )
    return plan


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry for plan / run."""
    parser = argparse.ArgumentParser(description="Migrate legacy systemd SSV to EthPillar")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_plan = sub.add_parser("plan", help="Print migration plan")
    p_plan.add_argument("--json", action="store_true", help="Emit JSON")
    p_plan.add_argument("--unit-dir", default=SYSTEMD_UNIT_DIR)
    p_plan.add_argument("--rebuild-ssvnode", action="store_true")
    p_plan.add_argument("--allow-older-binary", action="store_true")

    p_run = sub.add_parser("run", help="Apply migration")
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--force", action="store_true")
    p_run.add_argument("--start", action="store_true", help="Start EthPillar units after migrate")
    p_run.add_argument("--unit-dir", default=SYSTEMD_UNIT_DIR)
    p_run.add_argument("--rebuild-ssvnode", action="store_true")
    p_run.add_argument("--allow-older-binary", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "plan":
            plan = plan_ssv_migration(
                unit_dir=args.unit_dir,
                rebuild_ssvnode=args.rebuild_ssvnode,
                allow_older_binary=args.allow_older_binary,
            )
            if args.json:
                print(json.dumps(asdict(plan), indent=2))
            else:
                print(plan.summary())
            return 1 if plan.errors else 0
        if args.cmd == "run":
            run_ssv_migration(
                dry_run=args.dry_run,
                force=args.force,
                start=args.start,
                rebuild_ssvnode=args.rebuild_ssvnode,
                allow_older_binary=args.allow_older_binary,
                unit_dir=args.unit_dir,
            )
            return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
