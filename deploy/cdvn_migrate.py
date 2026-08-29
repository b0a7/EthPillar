"""Plan and run a CDVN → EthPillar full-stack migration.

Detects EL/CL/VC/MEV profiles from CDVN ``.env``, maps them to EthPillar
clients, moves or merges Docker datadirs, copies ``.charon`` (following
symlinks), writes ``charon.service`` from ``CHARON_*`` env vars, syncs DKG key
shares into the VC, and optionally preserves CDVN Grafana port.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from deploy.charon import (
    charon_cluster_copy_only,
    copy_charon_cluster,
    import_cdvn_env_to_service,
    parse_dotenv,
    resolve_cdvn_checkout,
    rewrite_endpoint_list,
    sync_charon_keyshares_to_vc,
)
from deploy.orchestrator import lodestar_bn_vc_incompatibility_message
from deploy.common import BASE_DATA_DIR, setup_client_user_and_dir

# Stock CDVN compose profile tokens → EthPillar client names for *local* migrate.
# Upstream CDVN compose-el.yml only defines el-nethermind and el-reth; other EL=
# values (custom/external) are treated as no local EL. EthPillar itself installs
# Besu, Geth, Erigon, Ethrex, etc. via deploy/orchestrator — expand this map when
# CDVN adds matching compose profiles.
EL_PROFILE_MAP: Dict[str, str] = {
    "el-nethermind": "Nethermind",
    "el-reth": "Reth",
}
CL_PROFILE_MAP: Dict[str, str] = {
    "cl-lighthouse": "Lighthouse",
    "cl-teku": "Teku",
    "cl-lodestar": "Lodestar",
    "cl-prysm": "Prysm",
    "cl-nimbus": "Nimbus",
    "cl-grandine": "Grandine",
}
VC_PROFILE_MAP: Dict[str, str] = {
    "vc-lodestar": "Lodestar",
    "vc-teku": "Teku",
    "vc-prysm": "Prysm",
    "vc-nimbus": "Nimbus",
}
MEV_LOCAL = frozenset({"mev-mevboost"})
MEV_NONE = frozenset({"mev-none", "none", ""})
# CDVN VC datadirs merged (not moved wholesale); keys come from .charon/validator_keys.
VC_DATADIR_RELS = frozenset({
    "data/vc-teku",
    "data/vc-nimbus",
    "data/vc-prysm",
    "data/lodestar",
})
VC_DATADIR_SKIP_NAMES = frozenset({"logs", "run.sh"})
VC_DATADIR_SKIP_SUFFIXES = (".log",)
VC_DATADIR_SKIP_FILES = frozenset({".log_rotate_audit.json"})

# Relative CDVN ./data path → (EthPillar datadir under BASE_DATA_DIR, systemd user)
DATADIR_MOVES: Dict[str, Tuple[str, str]] = {
    "data/nethermind": ("nethermind", "execution"),
    "data/reth": ("reth", "execution"),
    "data/lighthouse": ("lighthouse", "consensus"),
    "data/cl-teku": ("teku", "consensus"),
    "data/cl-lodestar": ("lodestar", "consensus"),
    "data/cl-prysm": ("prysm", "consensus"),
    "data/cl-nimbus": ("nimbus", "consensus"),
    "data/cl-grandine": ("grandine", "consensus"),
    "data/lodestar": ("lodestar_validator", "validator"),
    "data/vc-teku": ("teku_validator", "validator"),
    "data/vc-nimbus": ("nimbus_validator", "validator"),
    "data/vc-prysm": ("prysm_validator", "validator"),
}

# Soft-warn dirs when profile is *-none but data still exists.
_ORPHAN_DATA_HINTS: Dict[str, str] = {
    "data/nethermind": "EL",
    "data/reth": "EL",
    "data/lighthouse": "CL",
    "data/cl-teku": "CL",
    "data/cl-lodestar": "CL",
    "data/cl-prysm": "CL",
    "data/cl-nimbus": "CL",
    "data/cl-grandine": "CL",
}


@dataclass
class DatadirMove:
    """One proposed CDVN → EthPillar datadir move."""

    relative_src: str
    src: str
    dest: str
    owner: str
    skip_reason: str = ""

    @property
    def will_move(self) -> bool:
        """True when this datadir move is eligible and not skipped."""
        return not self.skip_reason


@dataclass
class CdvnMigrationPlan:
    """Resolved migration plan from a CDVN checkout."""

    root: str
    env_path: Optional[str]
    network: str
    role: str
    ec_name: Optional[str]
    cc_name: Optional[str]
    vc_name: Optional[str]
    with_charon: bool
    with_mevboost: bool
    with_builder_api: bool
    bn_address: str
    charon_dir: Optional[str]
    has_lock: bool
    has_keyshares: bool
    compose_file: Optional[str]
    docker_running: bool
    charon_link: Optional[str] = None
    charon_is_symlink: bool = False
    datadir_moves: List[DatadirMove] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    el_profile: str = ""
    cl_profile: str = ""
    vc_profile: str = ""
    mev_profile: str = ""
    grafana_port: Optional[int] = None

    def summary(self) -> str:
        """Human-readable plan for confirmation / dry-run."""
        lines = [
            "CDVN → EthPillar migration plan",
            f"  Checkout:     {self.root}",
            f"  .env:         {self.env_path or '(none)'}",
            f"  Network:      {self.network}",
            f"  Role:         {self.role}",
            f"  EL profile:   {self.el_profile or '(unset)'} → {self.ec_name or 'none'}",
            f"  CL profile:   {self.cl_profile or '(unset)'} → {self.cc_name or 'none'}",
            f"  VC profile:   {self.vc_profile or '(unset)'} → {self.vc_name or 'none'}",
            f"  MEV profile:  {self.mev_profile or '(unset)'} "
            f"(local_mevboost={self.with_mevboost}, builder_api={self.with_builder_api})",
            f"  Charon:       {self.with_charon} (lock={self.has_lock}, keys={self.has_keyshares})",
            (
                f"  Charon path:  {self.charon_link} → {self.charon_dir}"
                if self.charon_is_symlink and self.charon_link and self.charon_dir
                else f"  Charon path:  {self.charon_dir or '(none)'}"
            ),
            (
                f"  Key shares:   auto-sync to {self.vc_name} on migrate"
                if self.has_keyshares and self.vc_name
                else "  Key shares:   (none)"
            ),
            f"  BN address:   {self.bn_address or '(local via EthPillar CC)'}",
            f"  Grafana port: {self.grafana_port or '(EthPillar default 3000)'}",
            f"  Compose:      {self.compose_file or '(none)'}",
            f"  Docker up:    {self.docker_running}",
            "",
            "Datadir moves:",
        ]
        if not self.datadir_moves:
            lines.append("  (none)")
        for move in self.datadir_moves:
            if move.will_move:
                lines.append(f"  MOVE  {move.src}")
                lines.append(f"    →   {move.dest}  (owner={move.owner})")
            else:
                lines.append(f"  SKIP  {move.src}  ({move.skip_reason})")
        if self.warnings:
            lines.append("")
            lines.append("Warnings:")
            for warn in self.warnings:
                lines.append(f"  ! {warn}")
        lines.append("")
        lines.append("Deploy argv:")
        lines.append("  " + " ".join(self.deploy_argv()))
        return "\n".join(lines)

    def deploy_argv(self) -> List[str]:
        """Build ``deploy-node.py`` arguments for this plan."""
        argv = [
            "--skip_prompts",
            "true",
            "--install_config",
            self.role,
            "--network",
            self.network.upper(),
            "--with_charon",
            "--vc",
            self.vc_name or "Lodestar",
        ]
        if self.role == "Custom Setup":
            if self.ec_name:
                argv.extend(["--ec", self.ec_name])
            if self.cc_name:
                argv.extend(["--cc", self.cc_name])
            if self.with_mevboost:
                argv.append("--with_mevboost")
        if self.with_builder_api and not self.with_mevboost:
            argv.append("--with_builder_api")
        if self.role == "Validator Client Only" and self.bn_address:
            argv.extend(["--vc_only_bn_address", self.bn_address])
        return argv


def _norm_profile(value: str) -> str:
    return (value or "").strip().lower()


def _is_none_profile(value: str, kind: str) -> bool:
    v = _norm_profile(value)
    return not v or v in {f"{kind}-none", "none"}


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_ec_name(el_raw: str) -> Tuple[Optional[str], List[str]]:
    """Map CDVN ``EL`` profile to a local EthPillar EC name, or none (external).

    Stock CDVN compose may list profiles EthPillar does not install locally;
    unmapped values are treated as external EL with a plan warning.

    Args:
        el_raw: Raw ``EL=`` value from CDVN ``.env``.

    Returns:
        ``(ec_name, warnings)`` — ``ec_name`` is None for external/unmapped profiles.
    """
    warnings: List[str] = []
    if _is_none_profile(el_raw, "el"):
        return None, warnings
    mapped = EL_PROFILE_MAP.get(el_raw)
    if mapped:
        return mapped, warnings
    warnings.append(
        f"EL profile {el_raw!r} is not a supported local client "
        f"({', '.join(sorted(EL_PROFILE_MAP))}); treating as external (no local EL)."
    )
    return None, warnings


def _resolve_cc_name(cl_raw: str) -> Tuple[Optional[str], List[str]]:
    """Map CDVN ``CL`` profile to a local EthPillar CC name, or none (external).

    Args:
        cl_raw: Raw ``CL=`` value from CDVN ``.env``.

    Returns:
        ``(cc_name, warnings)`` — ``cc_name`` is None for external/unmapped profiles.
    """
    warnings: List[str] = []
    if _is_none_profile(cl_raw, "cl"):
        return None, warnings
    mapped = CL_PROFILE_MAP.get(cl_raw)
    if mapped:
        return mapped, warnings
    warnings.append(
        f"CL profile {cl_raw!r} is not a supported local client "
        f"({', '.join(sorted(CL_PROFILE_MAP))}); treating as external (no local CL)."
    )
    return None, warnings


def _resolve_local_mevboost(
    mev_raw: str, *, has_local_el: bool, has_local_cl: bool
) -> Tuple[bool, List[str]]:
    """Return whether to install local ``mevboost.service`` from the MEV profile.

    Args:
        mev_raw: Raw ``MEV=`` value from CDVN ``.env``.
        has_local_el: Whether migrate plans a local execution client.
        has_local_cl: Whether migrate plans a local consensus client.

    Returns:
        ``(with_mevboost, warnings)``.
    """
    warnings: List[str] = []
    if mev_raw in MEV_LOCAL:
        return has_local_el and has_local_cl, warnings
    if mev_raw in MEV_NONE or _is_none_profile(mev_raw, "mev"):
        return False, warnings
    warnings.append(
        f"MEV profile {mev_raw!r} is not local mev-mevboost; "
        "treating as external/no local MEV-Boost."
    )
    return False, warnings


def grafana_port_from_env(env: Dict[str, str]) -> Optional[int]:
    """Parse CDVN ``MONITORING_PORT_GRAFANA`` when set to a valid TCP port.

    Args:
        env: Parsed CDVN ``.env`` key/value map.

    Returns:
        Port number 1–65535, or None when unset/invalid.
    """
    raw = (env.get("MONITORING_PORT_GRAFANA") or "").strip()
    if not raw:
        return None
    try:
        port = int(raw)
    except ValueError:
        return None
    if port < 1 or port > 65535:
        return None
    return port


def apply_grafana_http_port(port: int) -> bool:
    """Set Grafana ``http_port`` in ``/etc/grafana/grafana.ini`` and restart.

    Returns True when the port was applied (ini updated or already matched).
    """
    ini = Path("/etc/grafana/grafana.ini")
    if not ini.is_file():
        return False
    try:
        content = ini.read_text(encoding="utf-8")
    except OSError:
        return False

    tmp = Path("/tmp/ethpillar-grafana.ini")
    tmp.write_text(_grafana_ini_with_http_port(content, port), encoding="utf-8")
    subprocess.run(["sudo", "cp", str(tmp), str(ini)], check=True)
    subprocess.run(
        ["sudo", "systemctl", "try-restart", "grafana-server"],
        check=False,
    )
    return True


def _grafana_ini_with_http_port(content: str, port: int) -> str:
    """Return ``grafana.ini`` content with ``[server] http_port`` set to *port*.

    Args:
        content: Existing ``grafana.ini`` text.
        port: TCP port for Grafana HTTP UI.

    Returns:
        Updated ini text (inserts or replaces ``http_port`` under ``[server]``).
    """
    new_line = f"http_port = {port}\n"
    out: List[str] = []
    in_server = False
    replaced = False
    for line in content.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_server and not replaced:
                out.append(new_line)
                replaced = True
            in_server = stripped.lower() == "[server]"
            out.append(line)
            continue
        if in_server and stripped.startswith("http_port"):
            out.append(new_line)
            replaced = True
            continue
        out.append(line)
    if in_server and not replaced:
        out.append(new_line)
    return "".join(out)


def apply_cdvn_monitoring_from_env(env_path: str) -> Optional[int]:
    """Apply CDVN monitoring settings (Grafana port) after EthPillar monitoring install.

    Args:
        env_path: Path to CDVN ``.env``.

    Returns:
        Grafana ``http_port`` when ``MONITORING_PORT_GRAFANA`` was applied, else None.
    """
    env = parse_dotenv(env_path)
    port = grafana_port_from_env(env)
    if port is None:
        return None
    apply_grafana_http_port(port)
    return port


def _vc_datadir_useful_entries(src: str) -> List[str]:
    """Return top-level CDVN VC datadir entries worth merging into EthPillar.

    Skips logs, ``run.sh``, ``*.log`` files, and log-rotate audit metadata.

    Args:
        src: CDVN ``data/vc-*`` or ``data/lodestar`` path.

    Returns:
        Basenames to copy (e.g. ``keystores``, ``validator-db``, ``validators``).
    """
    if not os.path.isdir(src):
        return []
    useful: List[str] = []
    for name in os.listdir(src):
        if name in VC_DATADIR_SKIP_NAMES or name in VC_DATADIR_SKIP_FILES:
            continue
        if any(name.endswith(suffix) for suffix in VC_DATADIR_SKIP_SUFFIXES):
            continue
        if os.path.exists(os.path.join(src, name)):
            useful.append(name)
    return useful


def _vc_datadir_skip_reason(src: str) -> str:
    """Return a skip reason when a CDVN VC datadir has nothing useful to merge.

    Args:
        src: CDVN VC datadir path.

    Returns:
        Empty string when merge should proceed; otherwise a human-readable reason.
    """
    if not os.path.isdir(src):
        return "source empty or missing"
    useful = _vc_datadir_useful_entries(src)
    if not useful:
        return "only logs present (keys synced from .charon/validator_keys on migrate)"
    return ""


def merge_cdvn_vc_datadir(src: str, dest: str, owner: str) -> List[str]:
    """Merge useful CDVN VC datadir entries into EthPillar's VC data path.

    Used for ``data/lodestar``, ``data/vc-nimbus``, etc. Keys still sync from
    ``.charon/validator_keys`` when the VC datadir is logs-only (Teku).

    Args:
        src: CDVN VC datadir under the checkout.
        dest: EthPillar path under ``/var/lib`` (e.g. ``lodestar_validator``).
        owner: Systemd service user for ``chown`` (usually ``validator``).

    Returns:
        List of merged entry basenames (empty when *src* is missing or useless).
    """
    merged: List[str] = []
    if not os.path.isdir(src):
        return merged
    subprocess.run(["sudo", "mkdir", "-p", dest], check=True)
    for name in _vc_datadir_useful_entries(src):
        s_item = os.path.join(src, name)
        d_item = os.path.join(dest, name)
        if os.path.isdir(s_item):
            subprocess.run(["sudo", "rm", "-rf", d_item], check=False)
            subprocess.run(["sudo", "cp", "-a", s_item, d_item], check=True)
        else:
            subprocess.run(["sudo", "cp", "-a", s_item, d_item], check=True)
        merged.append(name)
    if merged:
        client_name = os.path.basename(dest.rstrip(os.sep)) or owner
        setup_client_user_and_dir(owner, client_name)
        subprocess.run(["sudo", "chown", "-R", f"{owner}:{owner}", dest], check=True)
    return merged


def _dir_nonempty(path: str) -> bool:
    """Return True when *path* is a directory with at least one entry."""
    if not os.path.isdir(path):
        return False
    try:
        return any(os.scandir(path))
    except OSError:
        return False


def _dest_has_data(path: str) -> bool:
    """True when destination already looks occupied (skip move)."""
    if not os.path.isdir(path):
        return False
    # Ignore empty dirs created by setup_client_user_and_dir
    try:
        entries = list(os.scandir(path))
    except OSError:
        return True
    return len(entries) > 0


def detect_docker_compose_running(compose_file: Optional[str], root: str) -> bool:
    """Return True if ``docker compose`` reports running services for this CDVN.

    Args:
        compose_file: Path to ``docker-compose.yml`` (or None to skip check).
        root: CDVN checkout directory used as compose working directory.

    Returns:
        True when at least one service is running.
    """
    if not compose_file or not shutil.which("docker"):
        return False
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", compose_file, "ps", "--status", "running", "-q"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return bool(result.stdout.strip())


def plan_cdvn_migration(path: str, *, local_host: str = "127.0.0.1") -> CdvnMigrationPlan:
    """Build a migration plan from a CDVN checkout path or ``.env`` file.

    Args:
        path: CDVN checkout directory or ``.env`` file path.
        local_host: Host used when rewriting Docker Compose service URLs to loopback.

    Returns:
        :class:`CdvnMigrationPlan` with datadir moves, warnings, and deploy argv.

    Raises:
        ValueError: When ``.env``/profiles are missing or incompatible.
        FileNotFoundError: When *path* does not exist.
    """
    info = resolve_cdvn_checkout(path)
    root = str(info["root"])
    env_path = info.get("env_path")
    if not env_path or not os.path.isfile(str(env_path)):
        raise ValueError(
            f"No .env found under {root}. Copy .env.sample.* to .env or pass a path that contains one."
        )

    env = parse_dotenv(str(env_path))
    network = (env.get("NETWORK") or "").strip().lower()
    if not network:
        raise ValueError("NETWORK is unset in CDVN .env")

    el_raw = _norm_profile(env.get("EL", ""))
    cl_raw = _norm_profile(env.get("CL", ""))
    vc_raw = _norm_profile(env.get("VC", ""))
    mev_raw = _norm_profile(env.get("MEV", ""))

    warnings: List[str] = []
    ec_name, el_warns = _resolve_ec_name(el_raw)
    cc_name, cl_warns = _resolve_cc_name(cl_raw)
    warnings.extend(el_warns)
    warnings.extend(cl_warns)

    el_none = ec_name is None
    cl_none = cc_name is None

    if _is_none_profile(vc_raw, "vc"):
        raise ValueError("VC profile is unset or vc-none; Charon migrate requires a signer VC.")
    if vc_raw not in VC_PROFILE_MAP:
        raise ValueError(
            f"Unsupported CDVN VC profile {vc_raw!r}. "
            f"Supported: {', '.join(sorted(VC_PROFILE_MAP))}."
        )
    vc_name = VC_PROFILE_MAP[vc_raw]

    if not el_none and cl_none:
        raise ValueError(
            "Local EL with CL=cl-none is unsupported for migrate. "
            "Use el-none+cl-none (VC-only) or enable both EL and CL."
        )
    if el_none and not cl_none:
        raise ValueError(
            "Local CL with EL=el-none is unsupported for migrate v1. "
            "Use el-none+cl-none (external BN) or enable both EL and CL."
        )

    bn_address = ""
    bn_vc_warn = lodestar_bn_vc_incompatibility_message(cc_name, vc_name)
    if bn_vc_warn:
        warnings.append(bn_vc_warn)
    if el_none and cl_none:
        role = "Validator Client Only"
        raw_bn = (env.get("CHARON_BEACON_NODE_ENDPOINTS") or "").strip()
        if not raw_bn:
            raise ValueError(
                "CL=cl-none requires CHARON_BEACON_NODE_ENDPOINTS for the remote beacon node."
            )
        bn_address, bn_warns = rewrite_endpoint_list(raw_bn, local_host=local_host)
        warnings.extend(bn_warns)
    else:
        role = "Custom Setup"

    charon_dir = info.get("charon_dir")
    charon_link = info.get("charon_link")
    charon_is_symlink = bool(info.get("charon_is_symlink"))
    has_lock = bool(info.get("has_lock"))
    has_keyshares = bool(info.get("has_keyshares"))
    if not charon_dir and not has_lock:
        # Still allow if CHARON_* present / .charon dir without lock yet
        if not any(k.startswith("CHARON_") for k in env):
            raise ValueError(
                "No .charon cluster found and no CHARON_* settings in .env. "
                "This migrate path is for Charon DV nodes."
            )

    with_charon = True
    with_mevboost, mev_warns = _resolve_local_mevboost(
        mev_raw, has_local_el=not el_none, has_local_cl=not cl_none
    )
    warnings.extend(mev_warns)
    builder_raw = env.get("CHARON_BUILDER_API") or env.get("BUILDER_API_ENABLED") or ""
    with_builder_api = with_mevboost or (bool(builder_raw) and _truthy(builder_raw))
    grafana_port = grafana_port_from_env(env)

    compose_file = info.get("compose_file")
    docker_running = detect_docker_compose_running(
        str(compose_file) if compose_file else None, root
    )

    # Orphan data warnings
    for rel, kind in _ORPHAN_DATA_HINTS.items():
        full = os.path.join(root, rel.replace("/", os.sep))
        if not _dir_nonempty(full):
            continue
        if kind == "EL" and el_none:
            label = el_raw or "unset"
            hint = "el-none" if _is_none_profile(el_raw, "el") else "external/unmapped EL"
            warnings.append(f"Found {rel} but EL={label} ({hint}); not migrating that dir.")
        if kind == "CL" and cl_none:
            label = cl_raw or "unset"
            hint = "cl-none" if _is_none_profile(cl_raw, "cl") else "external/unmapped CL"
            warnings.append(f"Found {rel} but CL={label} ({hint}); not migrating that dir.")

    datadir_moves: List[DatadirMove] = []
    active_rels: List[str] = []
    if ec_name == "Nethermind":
        active_rels.append("data/nethermind")
    elif ec_name == "Reth":
        active_rels.append("data/reth")
    if cc_name == "Lighthouse":
        active_rels.append("data/lighthouse")
    elif cc_name == "Teku":
        active_rels.append("data/cl-teku")
    elif cc_name == "Lodestar":
        active_rels.append("data/cl-lodestar")
    elif cc_name == "Prysm":
        active_rels.append("data/cl-prysm")
    elif cc_name == "Nimbus":
        active_rels.append("data/cl-nimbus")
    elif cc_name == "Grandine":
        active_rels.append("data/cl-grandine")
    if vc_name == "Lodestar":
        active_rels.append("data/lodestar")
    elif vc_name == "Teku":
        active_rels.append("data/vc-teku")
    elif vc_name == "Nimbus":
        active_rels.append("data/vc-nimbus")
    elif vc_name == "Prysm":
        active_rels.append("data/vc-prysm")

    for rel in active_rels:
        dest_name, owner = DATADIR_MOVES[rel]
        src = os.path.join(root, rel.replace("/", os.sep))
        dest = os.path.join(BASE_DATA_DIR, dest_name)
        skip = ""
        if rel in VC_DATADIR_RELS:
            skip = _vc_datadir_skip_reason(src)
        elif not _dir_nonempty(src):
            skip = "source empty or missing"
        elif _dest_has_data(dest):
            skip = f"destination already has data ({dest}); clear it manually to move"
        datadir_moves.append(
            DatadirMove(
                relative_src=rel,
                src=src,
                dest=dest,
                owner=owner,
                skip_reason=skip,
            )
        )

    # Prefer move for .charon when present (shown in plan; apply via copy_charon if move fails)
    if charon_dir and has_lock:
        dest_charon = os.path.join(BASE_DATA_DIR, "charon", ".charon")
        skip = ""
        if os.path.isfile(os.path.join(dest_charon, "cluster-lock.json")):
            skip = f"destination already has cluster-lock.json ({dest_charon})"
        datadir_moves.append(
            DatadirMove(
                relative_src=".charon",
                src=str(charon_dir),
                dest=dest_charon,
                owner="charon",
                skip_reason=skip,
            )
        )

    return CdvnMigrationPlan(
        root=root,
        env_path=str(env_path),
        network=network,
        role=role,
        ec_name=ec_name,
        cc_name=cc_name,
        vc_name=vc_name,
        with_charon=with_charon,
        with_mevboost=with_mevboost,
        with_builder_api=with_builder_api,
        bn_address=bn_address,
        charon_dir=str(charon_dir) if charon_dir else None,
        charon_link=str(charon_link) if charon_link else None,
        charon_is_symlink=charon_is_symlink,
        has_lock=has_lock,
        has_keyshares=has_keyshares,
        compose_file=str(compose_file) if compose_file else None,
        docker_running=docker_running,
        datadir_moves=datadir_moves,
        warnings=warnings,
        el_profile=el_raw,
        cl_profile=cl_raw,
        vc_profile=vc_raw,
        mev_profile=mev_raw,
        grafana_port=grafana_port,
    )


def move_client_datadir(src: str, dest: str, owner: str) -> None:
    """Move ``src`` contents into ``dest`` and chown to ``owner``."""
    if not os.path.isdir(src):
        raise FileNotFoundError(src)
    subprocess.run(["sudo", "mkdir", "-p", dest], check=True)
    # Move children into dest (dest may already exist empty from setup)
    for name in os.listdir(src):
        s_item = os.path.join(src, name)
        d_item = os.path.join(dest, name)
        subprocess.run(["sudo", "mv", s_item, d_item], check=True)
    subprocess.run(["sudo", "chown", "-R", f"{owner}:{owner}", dest], check=True)
    # Remove empty source dir if possible
    try:
        os.rmdir(src)
    except OSError:
        subprocess.run(["sudo", "rmdir", src], check=False)


def apply_datadir_moves(plan: CdvnMigrationPlan, selected: Optional[Sequence[str]] = None) -> List[str]:
    """Apply planned CDVN datadir moves and VC merges.

    ``selected``:
      * ``None`` — move all eligible dirs
      * empty sequence — move none
      * otherwise — only listed ``relative_src`` values

    ``.charon`` is handled separately by :func:`run_migration`.

    Args:
        plan: Plan from :func:`plan_cdvn_migration`.
        selected: Optional subset of ``DatadirMove.relative_src`` values.

    Returns:
        ``relative_src`` values that were moved or merged.
    """
    done: List[str] = []
    allow: Optional[set] = None if selected is None else set(selected)
    for move in plan.datadir_moves:
        if move.relative_src == ".charon":
            continue
        if allow is not None and move.relative_src not in allow:
            continue
        if not move.will_move:
            continue
        if move.relative_src in VC_DATADIR_RELS:
            merged = merge_cdvn_vc_datadir(move.src, move.dest, move.owner)
            if merged:
                done.append(move.relative_src)
            continue
        move_client_datadir(move.src, move.dest, move.owner)
        done.append(move.relative_src)
    return done


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_deploy(plan: CdvnMigrationPlan, *, dry_run: bool = False) -> int:
    """Invoke ``deploy/install-node.sh`` with the plan's argv."""
    root = _repo_root()
    script = os.path.join(root, "deploy", "install-node.sh")
    cmd = ["bash", script, *plan.deploy_argv()]
    print("Running:", " ".join(cmd))
    if dry_run:
        return 0
    env = os.environ.copy()
    env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return subprocess.call(cmd, cwd=root, env=env)


def ensure_ethpillar_installed() -> None:
    """Create ``/usr/local/bin/ethpillar`` symlink if missing (from this checkout)."""
    link = "/usr/local/bin/ethpillar"
    repo = _repo_root()
    target = os.path.join(repo, "ethpillar.sh")
    if os.path.islink(link) or os.path.isfile(link):
        return
    if not os.path.isfile(target):
        raise FileNotFoundError(f"ethpillar.sh not found at {target}")
    print(f"Installing ethpillar symlink → {target}")
    subprocess.run(["sudo", "ln", "-s", target, link], check=True)
    subprocess.run(["bash", os.path.join(repo, "install.sh")], check=False)


def run_migration(
    path: str,
    *,
    dry_run: bool = False,
    apply_moves: Optional[Sequence[str]] = None,
    skip_deploy: bool = False,
    skip_charon_overlay: bool = False,
) -> CdvnMigrationPlan:
    """Execute CDVN → EthPillar migration (or dry-run plan only).

    Args:
        path: CDVN checkout or ``.env`` path (same as :func:`plan_cdvn_migration`).
        dry_run: When True, print the plan and return without writing the system.
        apply_moves: Subset of datadir ``relative_src`` to move/merge; ``None`` = all.
        skip_deploy: Skip ``deploy/install-node.sh`` (datadir/charon overlay only).
        skip_charon_overlay: Skip ``.charon`` copy, ``charon.service`` import, and key sync.

    Returns:
        The migration plan (same object whether or not ``dry_run``).

    Raises:
        RuntimeError: When Docker Compose is still up or deploy fails.
        ValueError: When the plan cannot be built (see :func:`plan_cdvn_migration`).
    """
    plan = plan_cdvn_migration(path)
    if plan.docker_running:
        raise RuntimeError(
            f"Docker Compose still has running services for {plan.root}. "
            "Stop CDVN (`docker compose down`) before migrating."
        )
    print(plan.summary())
    if dry_run:
        return plan

    if not skip_deploy:
        rc = run_deploy(plan, dry_run=False)
        if rc != 0:
            raise RuntimeError(f"deploy/install-node.sh failed with exit code {rc}")

    apply_datadir_moves(plan, selected=apply_moves)

    charon_selected = True
    if apply_moves is not None:
        charon_selected = ".charon" in apply_moves
    if (
        plan.charon_dir
        and plan.has_lock
        and not skip_charon_overlay
        and charon_selected
    ):
        # Prefer move; fall back to copy if move fails (cross-device, etc.)
        dest = os.path.join(BASE_DATA_DIR, "charon", ".charon")
        dest_lock = os.path.join(dest, "cluster-lock.json")
        if not os.path.isfile(dest_lock):
            copy_only = charon_cluster_copy_only(plan.root, plan.charon_dir)
            if copy_only:
                copy_charon_cluster(plan.charon_dir, force=False)
                print(f"Copied {plan.charon_dir} → {dest} (.charon symlink/outside checkout)")
            else:
                try:
                    subprocess.run(["sudo", "mkdir", "-p", os.path.dirname(dest)], check=True)
                    if os.path.exists(dest) and not _dir_nonempty(dest):
                        subprocess.run(["sudo", "rmdir", dest], check=False)
                    if not os.path.exists(dest):
                        subprocess.run(["sudo", "mv", plan.charon_dir, dest], check=True)
                        subprocess.run(
                            ["sudo", "chown", "-R", "charon:charon", os.path.dirname(dest)],
                            check=False,
                        )
                        print(f"Moved {plan.charon_dir} → {dest}")
                    else:
                        copy_charon_cluster(plan.charon_dir, force=False)
                except (OSError, subprocess.CalledProcessError):
                    print("Charon move failed; falling back to copy.")
                    copy_charon_cluster(plan.charon_dir, force=False)
    if plan.env_path and not skip_charon_overlay:
        import_cdvn_env_to_service(plan.env_path, apply=True)

    if plan.vc_name and plan.has_keyshares and not skip_charon_overlay:
        sync = sync_charon_keyshares_to_vc(plan.vc_name)
        if sync.get("status") == "copied":
            print(
                f"Synced {sync.get('count', 0)} key share(s) from .charon/validator_keys "
                f"→ {sync.get('dest')}"
            )
        elif sync.get("status") == "skipped":
            print(f"Key share sync skipped: {sync.get('reason')}")
        elif sync.get("status") == "failed":
            print(f"Key share sync failed: {sync.get('reason')}")
            print("  Fallback: Validator → Import Obol Charon key shares")
        elif sync.get("status") == "unsupported":
            print(
                f"Key shares present; auto-sync not implemented for {plan.vc_name}. "
                "Use Validator → Import Obol Charon key shares."
            )

    return plan


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_plan = sub.add_parser("plan", help="Print migration plan (JSON or text)")
    p_plan.add_argument("--path", required=True, help="CDVN checkout or .env path")
    p_plan.add_argument("--json", action="store_true", help="Emit JSON")

    p_run = sub.add_parser("run", help="Run migration (aborts if Docker is up)")
    p_run.add_argument("--path", required=True, help="CDVN checkout or .env path")
    p_run.add_argument(
        "--dry-run",
        action="store_true",
        help="Print plan only (for tests); do not write the system",
    )
    p_run.add_argument(
        "--skip-deploy",
        action="store_true",
        help="Skip deploy-node (datadir/charon only); for advanced use",
    )
    p_run.add_argument(
        "--moves",
        default=None,
        help="Comma-separated relative datadir paths to move. "
        "Omit for all eligible; pass empty string for none.",
    )

    args = parser.parse_args(argv)
    try:
        if args.cmd == "plan":
            plan = plan_cdvn_migration(args.path)
            if args.json:
                payload = asdict(plan)
                print(json.dumps(payload, indent=2))
            else:
                print(plan.summary())
            if plan.docker_running:
                print("\nERROR: Docker Compose is running — migrate will abort.", file=sys.stderr)
                return 2
            return 0
        if args.cmd == "run":
            if args.moves is None:
                moves = None
            else:
                moves = [m.strip() for m in args.moves.split(",") if m.strip()]
            run_migration(
                args.path,
                dry_run=args.dry_run,
                apply_moves=moves,
                skip_deploy=args.skip_deploy,
            )
            return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
