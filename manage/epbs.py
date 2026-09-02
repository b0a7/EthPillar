"""ePBS / Glamsterdam MEV migration helpers.

Two-step operator flow (EthStaker Glamsterdam guidance):

1. **prepare** — copy mev-boost relays (and min-bid where the VC supports it)
   onto the validator client. Keep ``mevboost.service`` and BN sidecar flags so
   pre-Gloas proposals still work.
2. **complete** — stop/disable MEV-Boost and strip BN flags that pointed at
   ``http://127.0.0.1:18550``. Keep VC builder-enable flags and the VC relay
   list from step 1. Refused unless the VC already has a relay list (or
   ``--force``).

Support levels:

* ``full`` — Prysm (proposer-settings relays) and Lodestar v1.47.0+
  (VC ``--builder.urls`` / ``--builder.minBid``). Lodestar prepare still
  probes ``lodestar validator --help`` so older binaries are skipped.
* ``placeholder`` — Lighthouse, Teku, Nimbus, Grandine: no released VC relay
  list; prepare is a documented no-op. Complete is refused without
  ``--force``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from deploy.common import BASE_DATA_DIR, write_service_file
from manage.service_parse import (
    SERVICE_FILES,
    flag_name,
    get_flag_value,
    get_flag_values,
    has_flag,
    normalize_cli_args,
    parse_unit,
    read_text_file,
    rebuild_service_content,
    unit_exists,
)

SIDECAR_MARKERS = ("127.0.0.1:18550", "localhost:18550", "[::1]:18550")
GWEI_PER_ETH = Decimal("1000000000")
PRYSM_SETTINGS_PATH = f"{BASE_DATA_DIR}/prysm_validator/proposer-settings.json"
COMPLETE_ROLLBACK_HINT = (
    "If you completed too early: restore the newest consensus.service.bak.epbs.* "
    "over consensus.service, then: sudo systemctl enable --now mevboost && "
    "sudo systemctl daemon-reload && sudo systemctl restart consensus"
)
COMPLETE_REFUSED = (
    "Complete refused: this validator has no relay list to replace MEV-Boost. "
    "Run prepare first, or pass --force to stop MEV-Boost and use local EL + "
    "P2P bids only."
)

# BN flags whose *value* is a builder/relay URL (strip only sidecar URLs).
BN_URL_FLAGS: Dict[str, Tuple[str, ...]] = {
    "Lighthouse": ("--builder",),
    "Prysm": ("--http-mev-relay",),
    "Teku": ("--builder-endpoint",),
    "Lodestar": ("--builder.urls",),
    "Nimbus": ("--payload-builder-url",),
    "Grandine": ("--builder-url", "--builder-api-url"),
    "Erigon-Caplin": ("--caplin.mev-relay-url",),
}

# BN boolean enable flags that only exist to talk to mev-boost.
BN_BOOL_FLAGS: Dict[str, Tuple[str, ...]] = {
    "Lodestar": ("--builder",),
}

SUPPORT_NOTES: Dict[str, str] = {
    "Prysm": (
        "Full: relays go in proposer-settings.json (BuilderConfig.Relays). "
        "Requires Prysm v7.1.7+."
    ),
    "Lodestar": (
        "Full: VC flags --builder.urls / --builder.minBid (v1.47.0+). "
        "Prepare writes them only when `lodestar validator --help` lists "
        "--builder.urls."
    ),
    "Lighthouse": (
        "Placeholder: VC has --builder-proposals only; no released relay-list "
        "flag. Prepare is a no-op. Complete is refused without --force "
        "(would stop MEV-Boost with no VC relay replacement)."
    ),
    "Teku": (
        "Placeholder: Staked Builder API REST client (Consensys/teku#11026) is "
        "not wired into proposing. Prepare is a no-op. Complete is refused "
        "without --force."
    ),
    "Nimbus": (
        "Placeholder: VC has --payload-builder=true only. Prepare is a no-op. "
        "Complete is refused without --force."
    ),
    "Grandine": (
        "Placeholder: integrated client; --builder-url takes a single sidecar. "
        "Prepare is a no-op. Complete is refused without --force."
    ),
}


class EpbsError(Exception):
    """Operator-facing ePBS migration error.

    Raised for missing units, invalid proposer-settings JSON, an empty
    relay list, or a refused ``complete`` when the VC has no relays.
    Caught by :func:`main` and printed to stderr.
    """


@dataclass
class RelaysConfig:
    """Relays and min-bid scraped from ``mevboost.service``.

    Attributes:
        urls: Relay URLs from ``-relay`` / ``--relay`` (comma lists expanded).
        min_bid: Optional ``-min-bid`` value in ETH; empty if unset.
        network: Network name parsed from the unit Description, if present.
    """

    urls: List[str]
    min_bid: str = ""
    network: str = ""


@dataclass
class PlanAction:
    """One planned file or systemd change.

    Attributes:
        target: Path or short label (unit file, settings JSON, or summary).
        detail: Human-readable description of the change.
    """

    target: str
    detail: str


@dataclass
class MigrationPlan:
    """Result of prepare/complete (dry-run or applied).

    Attributes:
        command: ``prepare`` or ``complete``.
        client: Detected validator (or BN for integrated Grandine).
        support: ``full`` or ``placeholder``.
        notes: Per-client support blurb from :data:`SUPPORT_NOTES`.
        actions: Files or systemd operations that would change (or did).
        warnings: Operator cautions (do not complete pre-fork, unknown flags).
        services_to_restart: Unit names to bounce after ``--apply``.
        disable_mevboost: True when complete will stop/disable MEV-Boost.
        applied: True after a successful ``--apply`` write.
    """

    command: str
    client: str
    support: str  # full | placeholder
    notes: str
    actions: List[PlanAction] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    services_to_restart: List[str] = field(default_factory=list)
    disable_mevboost: bool = False
    applied: bool = False

    def format_text(self) -> str:
        """Render a TUI/CLI dry-run or apply summary.

        Returns:
            Multi-line text ending with a newline.
        """
        lines = [
            f"ePBS {self.command}: {self.client} ({self.support})",
            self.notes,
            "",
        ]
        if self.actions:
            lines.append("Changes:")
            for action in self.actions:
                lines.append(f"  - {action.target}: {action.detail}")
            lines.append("")
        if self.warnings:
            lines.append("Warnings:")
            for warning in self.warnings:
                lines.append(f"  - {warning}")
            lines.append("")
        if self.disable_mevboost:
            lines.append("MEV-Boost will be stopped and disabled (unit file kept).")
            lines.append("")
        if self.command == "complete":
            lines.append(COMPLETE_ROLLBACK_HINT)
            lines.append("")
        if self.services_to_restart:
            lines.append("Restart after apply: " + ", ".join(self.services_to_restart))
            lines.append("")
        lines.append("Applied." if self.applied else "Dry-run (no files written).")
        return "\n".join(lines).rstrip() + "\n"


@dataclass
class EpbsFilesystem:
    """Injectable IO so unit tests do not need sudo or ``/etc``.

    Production uses :func:`read_text_file` / :func:`write_service_file` and
    sudo copies. Tests swap in plain pathlib writers and a fake mev-boost
    stopper.

    Attributes:
        systemd_dir: Directory containing ``*.service`` files.
        prysm_settings_path: Default Prysm proposer-settings JSON path.
        read_text: Read a file; return None if missing.
        exists: True when the path is a regular file.
        write_unit: Optional override for writing systemd units.
        write_data: Optional override for writing JSON/data files.
        stop_disable_mevboost: Optional override for ``systemctl stop/disable``.
        run_help: Optional ``argv -> help text`` probe (Lodestar ``--help``).
    """

    systemd_dir: str = "/etc/systemd/system"
    prysm_settings_path: str = PRYSM_SETTINGS_PATH
    read_text: Callable[[str], Optional[str]] = read_text_file
    exists: Callable[[str], bool] = unit_exists
    write_unit: Optional[Callable[[str, str], None]] = None
    write_data: Optional[Callable[[str, str], None]] = None
    stop_disable_mevboost: Optional[Callable[[], None]] = None
    run_help: Optional[Callable[[Sequence[str]], str]] = None

    def unit_path(self, key: str) -> str:
        """Return the systemd unit path for *key*.

        Args:
            key: Logical name such as ``validator``, ``consensus``, ``mevboost``.

        Returns:
            Absolute path. Uses :data:`SERVICE_FILES` when ``systemd_dir`` is
            the production directory; otherwise ``{systemd_dir}/{key}.service``.
        """
        if self.systemd_dir == "/etc/systemd/system":
            return SERVICE_FILES[key]
        return os.path.join(self.systemd_dir, f"{key}.service")


def _default_write_unit(path: str, content: str) -> None:
    """Write a systemd unit via the production sudo temp-file helper.

    Args:
        path: Destination unit path (typically under ``/etc/systemd/system``).
        content: Full unit file text.
    """
    write_service_file(content, path, temp_filename="epbs_temp.service")


def _default_write_data(path: str, content: str) -> None:
    """Write proposer-settings JSON (or a backup) as validator-owned 0644.

    Args:
        path: Destination path (for example ``/var/lib/prysm_validator/...``).
        content: File text. A trailing newline is added if missing.
    """
    import tempfile

    directory = os.path.dirname(path)
    if directory:
        subprocess.run(["sudo", "mkdir", "-p", directory], check=True)
    fd, tmp = tempfile.mkstemp(prefix="epbs_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            if not content.endswith("\n"):
                handle.write("\n")
        subprocess.run(["sudo", "cp", tmp, path], check=True)
        subprocess.run(["sudo", "chmod", "644", path], check=False)
        subprocess.run(["sudo", "chown", "validator:validator", path], check=False)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _default_stop_disable_mevboost() -> None:
    """Stop and disable ``mevboost.service`` without deleting the unit file."""
    subprocess.run(
        ["sudo", "systemctl", "stop", "mevboost"],
        check=False,
        capture_output=True,
    )
    subprocess.run(
        ["sudo", "systemctl", "disable", "mevboost"],
        check=False,
        capture_output=True,
    )


def _backup(path: str, fs: EpbsFilesystem) -> str:
    """Copy *path* to ``{path}.bak.epbs.<timestamp>`` before overwrite.

    Args:
        path: Existing unit or data file to snapshot.
        fs: IO adapter (uses ``write_unit`` or ``write_data``).

    Returns:
        Backup path that was written.

    Raises:
        EpbsError: If *path* cannot be read.
    """
    stamp = time.strftime("%Y%m%d%H%M%S")
    backup_path = f"{path}.bak.epbs.{stamp}"
    content = fs.read_text(path)
    if content is None:
        raise EpbsError(f"Cannot backup missing file: {path}")
    writer = fs.write_unit if path.endswith(".service") else fs.write_data
    if writer is None:
        writer = _default_write_unit if path.endswith(".service") else _default_write_data
    writer(backup_path, content)
    return backup_path


def upsert_flag(args: Sequence[str], name: str, value: Optional[str] = None) -> List[str]:
    """Set *name* to *value* (or as a boolean flag when *value* is None).

    Existing occurrences of *name* are replaced; duplicates after the first
    are dropped. The flag is appended if it was not present.

    Args:
        args: Normalized CLI tokens from a unit ``ExecStart``.
        name: Flag including leading dashes (for example ``--enable-builder``).
        value: If set, written as ``name=value``; otherwise a bare boolean flag.

    Returns:
        New argument list.
    """
    key = name.lower()
    out: List[str] = []
    found = False
    replacement = f"{name}={value}" if value is not None else name
    for arg in args:
        if flag_name(arg).lower() != key:
            out.append(arg)
            continue
        if found:
            continue
        out.append(replacement)
        found = True
    if not found:
        out.append(replacement)
    return out


def remove_flags(
    args: Sequence[str],
    *names: str,
    value_contains: Optional[str] = None,
) -> List[str]:
    """Drop flags matching *names*, optionally only when the value contains a marker.

    Args:
        args: Normalized CLI tokens.
        *names: Flag names to remove (for example ``--http-mev-relay``).
        value_contains: If set, keep the flag unless this substring appears in
            the value (used so non-sidecar builder URLs survive).

    Returns:
        New argument list with matching flags removed.
    """
    want = {n.lower() for n in names}
    out: List[str] = []
    for arg in args:
        if flag_name(arg).lower() not in want:
            out.append(arg)
            continue
        if value_contains:
            val = arg.split("=", 1)[1] if "=" in arg else ""
            if value_contains not in val:
                out.append(arg)
        # else: drop
    return out


def is_sidecar_url(url: str) -> bool:
    """Return True if *url* points at the local mev-boost listen address.

    Args:
        url: Single URL or a comma-separated list fragment.

    Returns:
        True when any :data:`SIDECAR_MARKERS` substring is present
        (``127.0.0.1:18550``, ``localhost:18550``, ``[::1]:18550``).
    """
    lowered = (url or "").lower()
    return any(marker in lowered for marker in SIDECAR_MARKERS)


def parse_mevboost_relays(content: str) -> RelaysConfig:
    """Extract ``-relay`` URLs and ``-min-bid`` from a mevboost unit.

    Args:
        content: Full ``mevboost.service`` text.

    Returns:
        Parsed relay URLs (comma lists expanded), min-bid, and network.
    """
    unit = parse_unit(content)
    args = normalize_cli_args(unit.exec_args)
    urls = get_flag_values(args, "-relay", "--relay", "-relays", "--relays")
    expanded: List[str] = []
    for item in urls:
        expanded.extend(part.strip() for part in item.split(",") if part.strip())
    return RelaysConfig(
        urls=expanded,
        min_bid=get_flag_value(args, "-min-bid", "--min-bid"),
        network=unit.network,
    )


def _read_required_unit(fs: EpbsFilesystem, key: str) -> Tuple[str, str]:
    """Read a systemd unit that must exist.

    Args:
        fs: IO adapter.
        key: Logical unit name (``validator``, ``consensus``, ``mevboost``).

    Returns:
        Tuple of ``(absolute_path, file_contents)``.

    Raises:
        EpbsError: If the unit is missing or unreadable.
    """
    path = fs.unit_path(key)
    if not fs.exists(path):
        raise EpbsError(f"Missing {key} unit: {path}")
    content = fs.read_text(path)
    if content is None:
        raise EpbsError(f"Unable to read {path}")
    return path, content


def detect_clients(fs: EpbsFilesystem) -> Tuple[str, str, str]:
    """Detect validator and beacon-node client names from systemd units.

    Grandine with ``keystore-dir`` on the consensus unit is treated as
    integrated (no separate ``validator.service``).

    Args:
        fs: IO adapter.

    Returns:
        ``(vc_name, bn_name, validator_mode)`` where *validator_mode* is
        ``separate``, ``integrated_grandine``, or ``none``. Names are empty
        strings when the corresponding unit is absent.
    """
    bn_name = ""
    consensus_path = fs.unit_path("consensus")
    if fs.exists(consensus_path):
        content = fs.read_text(consensus_path) or ""
        bn_name = parse_unit(content).client
        if "keystore-dir" in content:
            return "Grandine", bn_name or "Grandine", "integrated_grandine"

    vc_path = fs.unit_path("validator")
    if fs.exists(vc_path):
        content = fs.read_text(vc_path) or ""
        return parse_unit(content).client, bn_name, "separate"

    return "", bn_name, "none"


def support_level(client: str) -> str:
    """Return the VC relay-list support level for *client*.

    Args:
        client: Validator client name (``Prysm``, ``Lodestar``, …).

    Returns:
        ``full`` (Prysm, Lodestar) or ``placeholder``.
        The MEV-Boost TUI (``epbsTuiSupported`` in ``functions.sh``) is shown
        only for ``full``.
    """
    if client in ("Prysm", "Lodestar"):
        return "full"
    return "placeholder"


def _rebuild_unit(content: str, args: Sequence[str]) -> str:
    """Rewrite ``ExecStart`` in *content* using *args*.

    Args:
        content: Original unit file text.
        args: Replacement ExecStart tokens (binary plus flags).

    Returns:
        Full unit file with the ExecStart block replaced.
    """
    unit = parse_unit(content)
    return rebuild_service_content(
        content, unit.exec_start_index, unit.exec_start_end_index, list(args)
    )


def _write_unit_if_changed(
    fs: EpbsFilesystem,
    path: str,
    old_content: str,
    new_content: str,
    apply: bool,
) -> bool:
    """Write *new_content* when it differs and *apply* is True.

    Args:
        fs: IO adapter.
        path: Unit file path.
        old_content: Current on-disk text.
        new_content: Proposed text.
        apply: If False, still report whether a change exists without writing.

    Returns:
        True if *new_content* differs from *old_content* (a write happened
        only when *apply* is True).
    """
    if old_content == new_content:
        return False
    if apply:
        _backup(path, fs)
        writer = fs.write_unit or _default_write_unit
        writer(path, new_content)
    return True


def _prysm_builder_config(relays: RelaysConfig) -> dict:
    """Build Prysm ``default_config.builder`` (schema v2 BuilderConfig).

    Args:
        relays: URLs (and unused min-bid; Prysm has no min-bid field here).

    Returns:
        Dict with ``enabled``, ``relays``, and ``max_execution_payment``.
    """
    return {
        "enabled": True,
        "relays": list(relays.urls),
        # Cap on extra Gloas *execution_payment* from a builder (wei), not the
        # MEV bid / proposer reward. Public gossip bids must use
        # execution_payment=0; uint64 0 is also Prysm's proto/omitempty default
        # (v7.1.7 BuilderConfig). It does not disable builder mode or zero out
        # builder block value.
        "max_execution_payment": "0",
    }


def apply_relays_prysm(
    vc_content: str,
    relays: RelaysConfig,
    existing_settings: Optional[str],
    settings_path: str = PRYSM_SETTINGS_PATH,
) -> Tuple[str, str, str]:
    """Merge mev-boost relays into Prysm proposer-settings and VC flags.

    Sets schema version 2, enables builder, copies ``--suggested-fee-recipient``
    into ``default_config.fee_recipient`` when missing, and upserts
    ``--enable-builder`` plus ``--proposer-settings-file``.

    Args:
        vc_content: Current ``validator.service`` text.
        relays: Relays scraped from MEV-Boost.
        existing_settings: Current proposer-settings JSON, or None.
        settings_path: Default JSON path if the VC flag is absent.

    Returns:
        ``(new_vc_unit, proposer_settings_json, settings_path_used)``.

    Raises:
        EpbsError: If existing settings are invalid JSON or the wrong shape.
    """
    unit = parse_unit(vc_content)
    args = normalize_cli_args(unit.exec_args)
    fee = get_flag_value(args, "--suggested-fee-recipient")
    settings_path = get_flag_value(args, "--proposer-settings-file") or settings_path

    data: dict
    if existing_settings:
        try:
            data = json.loads(existing_settings)
        except json.JSONDecodeError as exc:
            raise EpbsError(f"Invalid proposer-settings JSON: {exc}") from exc
    else:
        data = {}

    if not isinstance(data, dict):
        raise EpbsError("proposer-settings.json must be a JSON object")

    data["version"] = 2
    default = data.setdefault("default_config", {})
    if not isinstance(default, dict):
        raise EpbsError("default_config must be an object")
    if fee and not default.get("fee_recipient"):
        default["fee_recipient"] = fee
    builder = default.setdefault("builder", {})
    if not isinstance(builder, dict):
        raise EpbsError("default_config.builder must be an object")
    builder.update(_prysm_builder_config(relays))

    args = upsert_flag(args, "--enable-builder")
    args = upsert_flag(args, "--proposer-settings-file", settings_path)
    new_unit = _rebuild_unit(vc_content, args)
    settings_json = json.dumps(data, indent=2) + "\n"
    return new_unit, settings_json, settings_path


def eth_min_bid_to_gwei(eth: str) -> str:
    """Convert MEV-Boost ``-min-bid`` (ETH) to Lodestar ``--builder.minBid`` (Gwei).

    Lodestar ``parseBuilderMinBid`` rejects decimals. Flashbots MEV-Boost
    ``-min-bid`` is ETH (EthPillar default ``0.006`` → ``6000000`` Gwei).

    Args:
        eth: Non-negative ETH amount as a decimal string.

    Returns:
        Integer Gwei string with no decimal point.

    Raises:
        EpbsError: If *eth* is not a non-negative number.
    """
    try:
        value = Decimal(str(eth).strip())
    except (InvalidOperation, AttributeError) as exc:
        raise EpbsError(f"Cannot convert MEV-Boost min-bid {eth!r} to Gwei") from exc
    if value < 0:
        raise EpbsError(f"MEV-Boost min-bid must be non-negative, got {eth!r}")
    gwei = (value * GWEI_PER_ETH).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return str(int(gwei))


def apply_relays_lodestar(vc_content: str, relays: RelaysConfig) -> str:
    """Add Lodestar VC builder URL / min-bid flags (v1.47.0+).

    Args:
        vc_content: Current ``validator.service`` text.
        relays: Relays and optional min-bid from MEV-Boost. ``min_bid`` is ETH
            and is converted to integer Gwei for ``--builder.minBid``.

    Returns:
        Unit text with ``--builder``, ``--builder.urls``, and optional
        ``--builder.minBid``. Older Lodestar may reject these flags.
    """
    unit = parse_unit(vc_content)
    args = normalize_cli_args(unit.exec_args)
    args = upsert_flag(args, "--builder")
    args = upsert_flag(args, "--builder.urls", ",".join(relays.urls))
    if relays.min_bid:
        args = upsert_flag(args, "--builder.minBid", eth_min_bid_to_gwei(relays.min_bid))
    return _rebuild_unit(vc_content, args)


def _command_help(fs: EpbsFilesystem, argv: Sequence[str]) -> str:
    """Return ``--help`` text for *argv*, or empty string on failure.

    Args:
        fs: IO adapter; ``run_help`` short-circuits subprocess in tests.
        argv: Command line including the binary and trailing ``--help``.

    Returns:
        Combined stdout and stderr, or ``""`` if the binary cannot be run.
    """
    if fs.run_help is not None:
        return fs.run_help(argv)
    try:
        result = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (result.stdout or "") + (result.stderr or "")


def lodestar_has_builder_urls_flag(fs: EpbsFilesystem, vc_content: str) -> bool:
    """True when the Lodestar VC binary documents ``--builder.urls``.

    Args:
        fs: IO adapter used to run ``lodestar validator --help``.
        vc_content: Current ``validator.service`` text (binary + subcommand).

    Returns:
        True if help text contains ``--builder.urls`` (Lodestar v1.47.0+).
    """
    args = normalize_cli_args(parse_unit(vc_content).exec_args)
    if not args:
        return False
    # First ExecStart token is the binary, or "binary subcommand" (Lodestar).
    help_cmd: List[str] = list(args[0].split())
    help_cmd.append("--help")
    return "--builder.urls" in _command_help(fs, help_cmd)


def apply_relays_placeholder(client: str) -> str:
    """Return a planned-flag blurb; do not mutate units.

    Args:
        client: Placeholder VC name (Lighthouse, Teku, Nimbus, Grandine).

    Returns:
        Human-readable description of the unreleased relay-list surface.
    """
    planned = {
        "Lighthouse": "--builder-relays=<urls> (not shipped; VC still --builder-proposals)",
        "Teku": "--validators-builder-relays=<urls> (not shipped; #11026 REST client unwired)",
        "Nimbus": "--payload-builder-relays=<urls> (not shipped; VC still --payload-builder=true)",
        "Grandine": "multi --builder-url list (not shipped; single --builder-url today)",
    }
    return planned.get(client, "no VC relay-list flag shipped")


def strip_bn_sidecar(bn_content: str, bn_client: str) -> str:
    """Remove BN flags whose value is the local mev-boost listen address.

    Non-sidecar builder URLs are kept. Lodestar's boolean ``--builder`` is
    dropped only when no builder URL remains.

    Args:
        bn_content: Current ``consensus.service`` text.
        bn_client: Beacon-node client name (keys :data:`BN_URL_FLAGS`).

    Returns:
        Unit text with sidecar URL flags removed.
    """
    unit = parse_unit(bn_content)
    args = normalize_cli_args(unit.exec_args)
    for flag in BN_URL_FLAGS.get(bn_client, ()):
        kept: List[str] = []
        key = flag.lower()
        for arg in args:
            if flag_name(arg).lower() != key:
                kept.append(arg)
                continue
            val = arg.split("=", 1)[1] if "=" in arg else ""
            urls = [part.strip() for part in val.split(",") if part.strip()]
            remaining = [u for u in urls if not is_sidecar_url(u)]
            if not remaining:
                continue
            kept.append(f"{flag}={','.join(remaining)}")
        args = kept
    for flag in BN_BOOL_FLAGS.get(bn_client, ()):
        # Only drop the BN builder-enable flag when no builder URL remains.
        url_flags = BN_URL_FLAGS.get(bn_client, ())
        still_has_url = any(
            flag_name(a).lower() == name.lower() for a in args for name in url_flags
        )
        if not still_has_url:
            args = remove_flags(args, flag)
    return _rebuild_unit(bn_content, args)


def _load_relays(fs: EpbsFilesystem) -> RelaysConfig:
    """Read and validate relays from ``mevboost.service``.

    Args:
        fs: IO adapter.

    Returns:
        Parsed :class:`RelaysConfig` with a non-empty URL list.

    Raises:
        EpbsError: If the unit is missing, unreadable, or has no ``-relay`` URLs.
    """
    path = fs.unit_path("mevboost")
    if not fs.exists(path):
        raise EpbsError(
            "mevboost.service not found. Install MEV-Boost first, or complete "
            "migration only after relays are already on the VC."
        )
    content = fs.read_text(path)
    if content is None:
        raise EpbsError(f"Unable to read {path}")
    cfg = parse_mevboost_relays(content)
    if not cfg.urls:
        raise EpbsError("No -relay URLs found in mevboost.service")
    return cfg


def prepare(fs: Optional[EpbsFilesystem] = None, apply: bool = False) -> MigrationPlan:
    """Copy mev-boost relays onto the VC. Keep the sidecar running.

    Prysm writes proposer-settings JSON and VC flags. Lodestar gets
    ``--builder.urls`` when the binary documents that flag. Other VCs are
    a documented no-op.
    Beacon-node sidecar flags are not touched.

    Args:
        fs: IO adapter; production defaults if omitted.
        apply: If True, write units/settings; otherwise dry-run.

    Returns:
        Plan including actions, warnings, and services to restart.

    Raises:
        EpbsError: If no validator unit exists or MEV-Boost relays cannot
            be loaded.
    """
    fs = fs or EpbsFilesystem()
    vc_name, bn_name, mode = detect_clients(fs)
    if mode == "none" or not vc_name:
        raise EpbsError("No validator client unit found.")

    relays = _load_relays(fs)
    level = support_level(vc_name)
    plan = MigrationPlan(
        command="prepare",
        client=vc_name,
        support=level,
        notes=SUPPORT_NOTES.get(vc_name, ""),
    )
    plan.warnings.append(
        "Do not stop MEV-Boost yet. Pre-Gloas proposals still use the sidecar."
    )
    if relays.min_bid:
        plan.actions.append(PlanAction("mevboost min-bid", relays.min_bid))
    plan.actions.append(
        PlanAction("relays", f"{len(relays.urls)} URL(s) from mevboost.service")
    )

    vc_key = "consensus" if mode == "integrated_grandine" else "validator"
    vc_path, vc_content = _read_required_unit(fs, vc_key)

    if vc_name == "Prysm":
        vc_args = normalize_cli_args(parse_unit(vc_content).exec_args)
        settings_path = (
            get_flag_value(vc_args, "--proposer-settings-file") or fs.prysm_settings_path
        )
        existing = fs.read_text(settings_path)
        new_vc, settings_json, settings_path = apply_relays_prysm(
            vc_content, relays, existing, settings_path=settings_path
        )
        changed_vc = new_vc != vc_content
        changed_json = (existing or "") != settings_json
        if changed_vc:
            plan.actions.append(
                PlanAction(
                    vc_path,
                    "add --enable-builder and --proposer-settings-file",
                )
            )
        plan.actions.append(
            PlanAction(settings_path, "write BuilderConfig.relays (schema v2)")
        )
        if apply:
            if changed_vc:
                _write_unit_if_changed(fs, vc_path, vc_content, new_vc, True)
            writer = fs.write_data or _default_write_data
            if existing:
                _backup(settings_path, fs)
            writer(settings_path, settings_json)
        if changed_vc or changed_json:
            plan.services_to_restart.append("validator")
        else:
            plan.warnings.append("Prysm VC already has these relays; nothing to change.")
    elif vc_name == "Lodestar":
        if not lodestar_has_builder_urls_flag(fs, vc_content):
            plan.actions.append(
                PlanAction(
                    "Lodestar VC",
                    "skipped: binary --help has no --builder.urls (need v1.47.0+)",
                )
            )
            plan.warnings.append(
                "Prepare: no-op on this Lodestar build — Complete will stop "
                "MEV-Boost without a VC relay replacement. Install Lodestar "
                "v1.47.0 or later."
            )
        else:
            new_vc = apply_relays_lodestar(vc_content, relays)
            if _write_unit_if_changed(fs, vc_path, vc_content, new_vc, apply):
                plan.actions.append(
                    PlanAction(
                        vc_path,
                        "add --builder --builder.urls --builder.minBid",
                    )
                )
                plan.services_to_restart.append("validator")
            else:
                plan.warnings.append(
                    "Lodestar VC already has builder.urls; nothing to change."
                )
    else:
        planned = apply_relays_placeholder(vc_name)
        plan.actions.append(PlanAction(f"{vc_name} VC (placeholder)", planned))
        plan.warnings.append(
            f"Prepare: no-op on this client — Complete will stop MEV-Boost "
            f"without a VC relay replacement. Complete is refused unless you "
            f"pass --force (local EL + P2P bids only)."
        )
        if mode == "integrated_grandine":
            plan.warnings.append("Grandine is integrated; there is no separate validator.service.")

    plan.applied = apply
    _ = bn_name  # BN sidecar stays until complete()
    return plan


def _vc_has_relays(fs: EpbsFilesystem, vc_name: str, vc_content: str) -> bool:
    """Return True if the VC already has a non-sidecar relay list.

    Args:
        fs: IO adapter (Prysm reads proposer-settings via this).
        vc_name: Validator client name.
        vc_content: ``validator.service`` text.

    Returns:
        True for Prysm when ``default_config.builder.relays`` is non-empty,
        or for Lodestar when ``--builder.urls`` is set and is not the sidecar.
        Always False for placeholder clients.
    """
    if vc_name == "Prysm":
        args = normalize_cli_args(parse_unit(vc_content).exec_args)
        settings_path = (
            get_flag_value(args, "--proposer-settings-file") or fs.prysm_settings_path
        )
        raw = fs.read_text(settings_path)
        if not raw:
            return False
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return False
        builder = (data.get("default_config") or {}).get("builder") or {}
        return bool(builder.get("relays"))
    if vc_name == "Lodestar":
        args = normalize_cli_args(parse_unit(vc_content).exec_args)
        urls = get_flag_value(args, "--builder.urls")
        return bool(urls) and not is_sidecar_url(urls)
    return False


def complete(
    fs: Optional[EpbsFilesystem] = None,
    apply: bool = False,
    force: bool = False,
) -> MigrationPlan:
    """Disable MEV-Boost and remove BN sidecar builder flags.

    Does not rewrite VC relay config from prepare. Refused when the VC has no
    relay list unless *force* is True (local EL + P2P bids only).

    Args:
        fs: IO adapter; production defaults if omitted.
        apply: If True, write the BN unit and stop/disable mevboost.
        force: Allow complete without a VC relay list.

    Returns:
        Plan including BN strip actions and ``disable_mevboost``.

    Raises:
        EpbsError: If no consensus unit exists, or the VC has no relay list
            and *force* is False.
    """
    fs = fs or EpbsFilesystem()
    vc_name, bn_name, mode = detect_clients(fs)
    if not bn_name and mode != "integrated_grandine":
        raise EpbsError("No consensus client unit found.")
    if mode == "integrated_grandine":
        vc_name = vc_name or "Grandine"
        bn_name = bn_name or "Grandine"

    level = support_level(vc_name or bn_name)
    plan = MigrationPlan(
        command="complete",
        client=vc_name or bn_name,
        support=level,
        notes=SUPPORT_NOTES.get(vc_name or bn_name, ""),
        disable_mevboost=True,
    )

    vc_content = ""
    has_relays = False
    if mode == "separate":
        _, vc_content = _read_required_unit(fs, "validator")
        has_relays = _vc_has_relays(fs, vc_name, vc_content)
    if not has_relays and not force:
        raise EpbsError(COMPLETE_REFUSED)
    if not has_relays:
        plan.warnings.append(
            "Prepare was a no-op / this VC has no relay list. After this step "
            "the node will use local EL + P2P builder bids only (no off-protocol "
            "relays)."
        )

    bn_path, bn_content = _read_required_unit(fs, "consensus")
    new_bn = strip_bn_sidecar(bn_content, bn_name or vc_name)
    if _write_unit_if_changed(fs, bn_path, bn_content, new_bn, apply):
        plan.actions.append(
            PlanAction(bn_path, f"remove mev-boost sidecar flags from {bn_name}")
        )
        plan.services_to_restart.append("consensus")
    else:
        plan.actions.append(PlanAction(bn_path, "no sidecar builder URL present"))

    mev_path = fs.unit_path("mevboost")
    if fs.exists(mev_path):
        plan.actions.append(
            PlanAction(mev_path, "stop and disable mevboost (unit file kept)")
        )
        if apply:
            stopper = fs.stop_disable_mevboost or _default_stop_disable_mevboost
            stopper()
    else:
        plan.warnings.append("mevboost.service not installed; skipping disable.")

    if "consensus" in plan.services_to_restart and mode == "integrated_grandine":
        # Integrated Grandine restarts with consensus.service only.
        pass
    elif vc_name == "Prysm" or vc_name == "Lodestar":
        # VC flags do not change on complete; BN restart is enough.
        pass

    plan.applied = apply
    return plan


def status(fs: Optional[EpbsFilesystem] = None) -> str:
    """Human-readable snapshot of ePBS migration state.

    Args:
        fs: IO adapter; production defaults if omitted.

    Returns:
        Multi-line status (VC/BN names, support level, MEV-Boost relay
        count, whether the VC has relays, whether BN sidecar flags remain).
    """
    fs = fs or EpbsFilesystem()
    vc_name, bn_name, mode = detect_clients(fs)
    level = support_level(vc_name) if vc_name else ""
    lines = [
        f"Validator: {vc_name or '(none)'}  mode={mode}",
        f"Beacon node: {bn_name or '(none)'}",
        f"Support: {level or 'n/a'}",
    ]
    if vc_name:
        lines.append(SUPPORT_NOTES.get(vc_name, ""))

    mev_path = fs.unit_path("mevboost")
    if fs.exists(mev_path):
        content = fs.read_text(mev_path) or ""
        cfg = parse_mevboost_relays(content)
        lines.append(
            f"MEV-Boost: installed  relays={len(cfg.urls)}  min-bid={cfg.min_bid or '(unset)'}"
        )
    else:
        lines.append("MEV-Boost: not installed")

    if mode == "separate":
        _, vc_content = _read_required_unit(fs, "validator")
        has_relays = _vc_has_relays(fs, vc_name, vc_content)
        lines.append("VC relays: " + ("yes" if has_relays else "no"))
        if not has_relays:
            if level == "placeholder":
                lines.append(
                    "Prepare: no-op on this client — Complete will stop "
                    "MEV-Boost without a VC relay replacement."
                )
            lines.append(
                "Complete: refused until Prepare writes a VC relay list "
                "(or --force for local EL + P2P only)."
            )
    elif mode == "integrated_grandine":
        lines.append(
            "Prepare: no-op on this client — Complete will stop MEV-Boost "
            "without a VC relay replacement."
        )
        lines.append(
            "Complete: refused unless --force (local EL + P2P only)."
        )
    if bn_name:
        _, bn_content = _read_required_unit(fs, "consensus")
        stripped = strip_bn_sidecar(bn_content, bn_name)
        lines.append(
            "BN sidecar flags: "
            + ("present" if stripped != bn_content else "already removed")
        )
    return "\n".join(lines).rstrip() + "\n"


def _print_plan(plan: MigrationPlan, as_json: bool) -> None:
    """Print *plan* as JSON or :meth:`MigrationPlan.format_text`.

    Args:
        plan: Prepare or complete result.
        as_json: If True, emit a JSON object to stdout.
    """
    if as_json:
        payload = {
            "command": plan.command,
            "client": plan.client,
            "support": plan.support,
            "notes": plan.notes,
            "actions": [{"target": a.target, "detail": a.detail} for a in plan.actions],
            "warnings": plan.warnings,
            "services_to_restart": plan.services_to_restart,
            "disable_mevboost": plan.disable_mevboost,
            "applied": plan.applied,
        }
        if plan.command == "complete":
            payload["rollback"] = COMPLETE_ROLLBACK_HINT
        print(json.dumps(payload, indent=2))
        return
    print(plan.format_text(), end="")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry for ``python -m manage.epbs``.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        ``0`` on success, ``1`` on :class:`EpbsError`.
    """
    parser = argparse.ArgumentParser(
        prog="python -m manage.epbs",
        description="Prepare a VC for ePBS, or complete MEV-Boost teardown after Gloas.",
    )
    parser.add_argument(
        "command",
        choices=("status", "prepare", "complete"),
        help="status | prepare (before fork) | complete (after fork)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write unit/settings changes (default is dry-run).",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow complete without a VC relay list (local EL + P2P bids only).",
    )
    parser.add_argument(
        "--systemd-dir",
        default=None,
        help="Override /etc/systemd/system (tests).",
    )
    parser.add_argument(
        "--prysm-settings",
        default=None,
        help="Override Prysm proposer-settings.json path (tests).",
    )
    args = parser.parse_args(argv)

    fs = EpbsFilesystem()
    if args.systemd_dir:
        fs.systemd_dir = args.systemd_dir
        fs.write_unit = lambda path, content: _write_plain(path, content)
        fs.write_data = lambda path, content: _write_plain(path, content)
        fs.stop_disable_mevboost = lambda: None
        fs.read_text = _read_plain
        fs.exists = os.path.isfile
    if args.prysm_settings:
        fs.prysm_settings_path = args.prysm_settings

    try:
        if args.command == "status":
            text = status(fs)
            if args.json:
                print(json.dumps({"status": text}))
            else:
                print(text, end="")
            return 0
        if args.command == "prepare":
            _print_plan(prepare(fs, apply=args.apply), args.json)
            return 0
        _print_plan(complete(fs, apply=args.apply, force=args.force), args.json)
        return 0
    except EpbsError as exc:
        print(f"ePBS: {exc}", file=sys.stderr)
        return 1


def _read_plain(path: str) -> Optional[str]:
    """Read UTF-8 text without sudo (used with ``--systemd-dir`` in tests).

    Args:
        path: File to read.

    Returns:
        File contents, or None if the path does not exist.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def _write_plain(path: str, content: str) -> None:
    """Write UTF-8 text without sudo (used with ``--systemd-dir`` in tests).

    Args:
        path: Destination file; parent directories are created.
        content: File text. A trailing newline is added if missing.
    """
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
        if not content.endswith("\n"):
            handle.write("\n")


if __name__ == "__main__":
    sys.exit(main())
