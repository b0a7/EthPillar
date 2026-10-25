"""Install-time block-builder mode and relay checklist.

Custom Setup asks for one of ``mevboost`` (local MEV-Boost sidecar), ``epbs``
(native validator-client relays, no sidecar), or ``none`` (local blocks).
Native ePBS is offered only on networks in :data:`config.EPBS_ENABLED_BY_NETWORK`
and only when the validator client is in ``manage.epbs.support_level``
``full``. Grandine and Obol Charon are not offered. An explicit ``epbs``
choice that the client or network cannot do raises :class:`BuilderModeError`
instead of falling back.

Lido CSM locks MEV on and filters relays to the on-chain allowlist. Lido has
not published an ePBS CSM policy; native ePBS CSM installs reuse that same
MEV-Boost allowlist until one exists.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from config import network_epbs_enabled
from manage.epbs import support_level

BUILDER_MEVBOOST = "mevboost"
BUILDER_EPBS = "epbs"
BUILDER_NONE = "none"
BUILDER_MODES = (BUILDER_MEVBOOST, BUILDER_EPBS, BUILDER_NONE)
# Cancel / Esc on the relay checklist or builder menu. Non-zero so
# install-node.sh does not print INSTALL COMPLETE.
CANCELLED_EXIT_CODE = 130

# Lido CSM module relay allowlist, contract
# 0xF95f069F9AD107938F6ba802a3da87892298610E, version 26.
# must-use-some: the operator must keep at least one of these.
# may-use: optional vetted relays.
# Names match config.py relay ``name`` fields (case-insensitive, prefix on a
# word boundary so "Agnostic" covers "Agnostic Gnosis"). Relays that are not
# in either tuple are excluded from CSM installs (for example Hoodi's plain
# "bloXroute" and "Titan Relay", which are not the regulated / Regional /
# Global entries). "Ultra Sound filtered" and "Titan Regional" are on the
# allowlist but have no config.py URL yet, so they cannot be selected.
CSM_RELAY_ALLOWLIST_CONTRACT = "0xF95f069F9AD107938F6ba802a3da87892298610E"
CSM_RELAY_ALLOWLIST_VERSION = 26
CSM_MUST_USE_SOME = (
    "agnostic",
    "bloxroute regulated",
    "aestus",
    "ultra sound",
    "ultra sound filtered",
    "flashbots",
)
CSM_MAY_USE = (
    "titan regional",
    "titan global",
)

# No ePBS-specific CSM policy has been published. Fresh ePBS CSM installs
# apply the MEV-Boost allowlist above (mandatory MEV, vetted relays only,
# at least one must-use-some relay) until Lido defines a Gloas policy.
CSM_EPBS_POLICY_NOTE = (
    "Lido has not published an ePBS CSM relay policy. Native ePBS CSM "
    f"installs reuse the MEV-Boost allowlist "
    f"{CSM_RELAY_ALLOWLIST_CONTRACT} v{CSM_RELAY_ALLOWLIST_VERSION} "
    "(must-use-some: Agnostic, bloXroute Regulated, Aestus, Ultra Sound, "
    "Ultra Sound filtered, Flashbots; may-use: Titan Regional, Titan Global)."
)

_LOCAL_MEV_ROLES = (
    "Solo Staking Node",
    "Lido CSM Staking Node",
    "Failover Staking Node",
)
_PROMPT_ROLES = (
    "Custom Setup",
    "Validator Client Only",
    "Lido CSM Validator Client Only",
)
_CSM_ROLES = (
    "Lido CSM Staking Node",
    "Lido CSM Validator Client Only",
)


class BuilderModeError(ValueError):
    """Install cannot continue with this builder mode or relay selection."""


class BuilderModeCancelled(BuilderModeError):
    """Operator cancelled the relay checklist or builder-mode menu."""


def is_csm_role(role: str) -> bool:
    """Return True for Lido CSM staking and CSM validator-only roles."""
    return role in _CSM_ROLES


def epbs_relay_client(vc_name: Optional[str], cc_name: Optional[str]) -> str:
    """Return the client that would hold native ePBS relays.

    Args:
        vc_name: Selected validator client, if any.
        cc_name: Selected consensus client, if any.

    Returns:
        A ``support_level`` client name, or ``""`` when no VC was chosen.
        Integrated Caplin is ``Erigon-Caplin``. Integrated Grandine is
        ``Grandine``.
    """
    vc = (vc_name or "").strip()
    cc = (cc_name or "").strip()
    if vc == "Same as CC":
        vc = cc
    if vc in ("Grandine (integrated)", "Grandine"):
        return "Grandine"
    caplin_names = {"Caplin", "Caplin (integrated)"}
    if vc in caplin_names or (cc in caplin_names and vc in ("", cc, *caplin_names)):
        return "Erigon-Caplin"
    return vc


def epbs_client_supported(
    vc_name: Optional[str],
    cc_name: Optional[str],
    *,
    charon: bool = False,
) -> bool:
    """Return True when this VC may be offered native ePBS.

    Mirrors the MEV-Boost TUI gate (``support_level == "full"`` /
    ``epbsTuiSupported``): Prysm, Lodestar, Teku, Nimbus, Erigon-Caplin, and
    Lighthouse. Grandine is placeholder. Charon has no ePBS release, so the
    option stays hidden. Binary version floors are enforced when relays are
    written (a too-old binary raises; it is not skipped).

    Args:
        vc_name: Selected validator client.
        cc_name: Selected consensus client.
        charon: True when Obol Charon middleware is part of the install.
    """
    if charon:
        return False
    client = epbs_relay_client(vc_name, cc_name)
    if not client:
        return False
    return support_level(client) == "full"


def epbs_unsupported_reason(
    vc_name: Optional[str],
    cc_name: Optional[str],
    *,
    charon: bool = False,
) -> str:
    """Explain why native ePBS cannot be installed for this client pair."""
    if charon:
        return (
            "Obol Charon has no ePBS support yet. Native ePBS was not applied. "
            "Install MEV-Boost, or pick a solo validator client."
        )
    client = epbs_relay_client(vc_name, cc_name) or "this validator client"
    if client == "Grandine":
        return (
            "Grandine has no released ePBS relay list. Native ePBS was not "
            "applied."
        )
    if support_level(client) != "full":
        return (
            f"{client} does not support native ePBS "
            f"(support level: {support_level(client) or 'unknown'}). "
            "Native ePBS was not applied."
        )
    return f"{client} does not support native ePBS. Native ePBS was not applied."


def builder_mode_choices(
    network: str,
    vc_name: Optional[str],
    cc_name: Optional[str],
    *,
    charon: bool = False,
    csm: bool = False,
    validator_only: bool = False,
) -> List[Tuple[str, str]]:
    """Return ``(mode, label)`` rows for the builder-mode menu.

    ePBS is included only when the network flag is true and the VC is
    supported. CSM omits local blocks (MEV is mandatory). On a forked
    network the ePBS row is first so it is the highlighted default.

    Args:
        network: Network slug.
        vc_name: Validator client name.
        cc_name: Consensus client name.
        charon: Charon middleware selected.
        csm: Lido CSM role.
        validator_only: VC-only install (MEV-Boost means the remote BN).
    """
    choices: List[Tuple[str, str]] = []
    if network_epbs_enabled(network) and epbs_client_supported(
        vc_name, cc_name, charon=charon
    ):
        choices.append((BUILDER_EPBS, "Native ePBS"))
    mev_label = (
        "MEV-Boost (remote beacon node builder API)"
        if validator_only
        else "MEV-Boost"
    )
    choices.append((BUILDER_MEVBOOST, mev_label))
    if not csm:
        choices.append((BUILDER_NONE, "None (local blocks)"))
    return choices


def default_builder_mode(
    role: str,
    network: str,
    vc_name: Optional[str] = None,
    cc_name: Optional[str] = None,
    *,
    charon: bool = False,
) -> str:
    """Return the preset builder mode for *role* on *network*.

    Solo and CSM staking nodes use MEV-Boost until the network flag is true
    and the chosen VC supports ePBS, then native ePBS. Failover has no VC,
    so it stays on MEV-Boost. CSM validator-only keeps MEV mandatory
    (remote builder API, or native ePBS when the VC supports it). Other
    roles default to local blocks.

    Args:
        role: Install role label.
        network: Network slug.
        vc_name: Validator client, when already chosen.
        cc_name: Consensus client, when already chosen.
        charon: Charon middleware selected.
    """
    supported = epbs_client_supported(vc_name, cc_name, charon=charon)
    forked = network_epbs_enabled(network)
    if role == "Failover Staking Node":
        return BUILDER_MEVBOOST
    if role in ("Solo Staking Node", "Lido CSM Staking Node"):
        if forked and supported:
            return BUILDER_EPBS
        return BUILDER_MEVBOOST
    if role == "Lido CSM Validator Client Only":
        if forked and supported:
            return BUILDER_EPBS
        return BUILDER_MEVBOOST
    return BUILDER_NONE


def validate_builder_mode(
    mode: str,
    *,
    role: str,
    network: str,
    vc_name: Optional[str],
    cc_name: Optional[str],
    charon: bool,
    has_validator: bool,
) -> None:
    """Reject a builder mode the network or client cannot honor.

    Args:
        mode: ``mevboost``, ``epbs``, or ``none``.
        role: Install role.
        network: Network slug.
        vc_name: Validator client.
        cc_name: Consensus client.
        charon: Charon middleware selected.
        has_validator: True when a VC unit (or integrated Caplin) will exist.

    Raises:
        BuilderModeError: The mode is unknown, CSM picked local blocks, or
            ePBS was requested where it cannot be installed.
    """
    if mode not in BUILDER_MODES:
        raise BuilderModeError(
            f"Unknown builder mode {mode!r}. Use mevboost, epbs, or none."
        )
    if is_csm_role(role) and mode == BUILDER_NONE:
        message = (
            "Lido CSM requires MEV. Choose MEV-Boost"
            + (
                " or, on a forked network with a supported validator client, "
                "native ePBS"
                if network_epbs_enabled(network)
                else ""
            )
            + ". Local blocks are not allowed."
        )
        # The ePBS allowlist assumption only matters where native ePBS exists.
        if network_epbs_enabled(network):
            message += " " + CSM_EPBS_POLICY_NOTE
        raise BuilderModeError(message)
    if mode != BUILDER_EPBS:
        return
    if not network_epbs_enabled(network):
        raise BuilderModeError(
            f"Native ePBS is not enabled on {network or 'this network'} "
            "(Glamsterdam is not live here). Choose MEV-Boost or local blocks. "
            "The existing ePBS migration switches a MEV-Boost node after the fork."
        )
    if charon:
        raise BuilderModeError(epbs_unsupported_reason(vc_name, cc_name, charon=True))
    relay_client = epbs_relay_client(vc_name, cc_name)
    if not relay_client or (not has_validator and relay_client != "Erigon-Caplin"):
        raise BuilderModeError(
            "Native ePBS needs a supported validator client. This install "
            "has nowhere to write relays."
        )
    if not epbs_client_supported(vc_name, cc_name, charon=False):
        raise BuilderModeError(epbs_unsupported_reason(vc_name, cc_name, charon=False))


def _normalize_relay_name(name: str) -> str:
    return " ".join((name or "").casefold().split())


def relay_allowlist_class(name: str) -> Optional[str]:
    """Classify a relay name against the CSM allowlist.

    Args:
        name: ``config.py`` relay name.

    Returns:
        ``"must"`` or ``"may"``, or None when the relay is not vetted.
    """
    key = _normalize_relay_name(name)
    if not key:
        return None
    for label in sorted(CSM_MUST_USE_SOME, key=len, reverse=True):
        if key == label or key.startswith(label + " "):
            return "must"
    for label in sorted(CSM_MAY_USE, key=len, reverse=True):
        if key == label or key.startswith(label + " "):
            return "may"
    return None


def partition_csm_relays(
    relays: Sequence[dict],
) -> Tuple[List[dict], List[dict], List[dict]]:
    """Split relays into must-use-some, may-use, and excluded.

    Args:
        relays: ``config.py`` relay dicts (``name`` and ``url``).

    Returns:
        ``(must, may, excluded)`` preserving input order inside each group.
    """
    must: List[dict] = []
    may: List[dict] = []
    excluded: List[dict] = []
    for relay in relays:
        kind = relay_allowlist_class(str(relay.get("name", "")))
        if kind == "must":
            must.append(relay)
        elif kind == "may":
            may.append(relay)
        else:
            excluded.append(relay)
    return must, may, excluded


def csm_offered_relays(relays: Sequence[dict]) -> List[dict]:
    """Return vetted CSM relays, must-use-some first.

    Raises:
        BuilderModeError: No must-use-some relay is present in *relays*.
    """
    must, may, _excluded = partition_csm_relays(relays)
    if not must:
        raise BuilderModeError(
            "Lido CSM requires at least one must-use-some relay from allowlist "
            f"{CSM_RELAY_ALLOWLIST_CONTRACT} v{CSM_RELAY_ALLOWLIST_VERSION}, "
            "but none of this network's configured relays match. "
            "Refusing to install with a non-vetted list. "
            + CSM_EPBS_POLICY_NOTE
        )
    return list(must) + list(may)


def relay_options_for_network(network: str) -> List[dict]:
    """Return ``config.py`` relay dicts for *network* (possibly empty)."""
    import config

    relays = getattr(config, f"{(network or '').strip().lower()}_relay_options", [])
    return [dict(item) for item in relays]


def default_relay_selection(relays: Sequence[dict], *, csm: bool) -> List[dict]:
    """Return the skip-prompts selection: every offered relay, all checked.

    Args:
        relays: Relays already filtered for CSM when *csm* is true.
        csm: When true, require a must-use-some relay in the result.

    Raises:
        BuilderModeError: *csm* is true and no must-use-some relay remains.
    """
    chosen = [dict(item) for item in relays]
    if csm:
        return csm_offered_relays(chosen)
    return chosen


def empty_selection_message(*, csm: bool, allow_local_blocks: bool) -> str:
    """Explain an empty checklist using only options this install can take.

    Args:
        csm: Lido CSM role (local blocks are forbidden).
        allow_local_blocks: True when the builder menu includes ``none``.
    """
    if csm:
        return (
            "Select at least one relay. Lido CSM requires at least one "
            "must-use-some relay. Local blocks are not available."
        )
    if allow_local_blocks:
        return (
            "Select at least one relay, or cancel and choose local blocks (none). "
            "An empty relay list is refused."
        )
    return (
        "Select at least one relay. This install does not offer local blocks. "
        "An empty relay list is refused."
    )


def validate_relay_selection(
    offered: Sequence[dict],
    selected_names: Sequence[str],
    *,
    csm: bool,
    allow_local_blocks: bool = True,
    mention_epbs_policy: bool = False,
) -> List[dict]:
    """Map checklist tags back to relay dicts and enforce the CSM lock.

    Args:
        offered: Relays that were shown (CSM callers pass vetted relays only).
        selected_names: Tags the operator left checked, in any order.
        csm: Enforce must-use-some and reject non-vetted names.
        allow_local_blocks: Mention local blocks only when that menu choice exists.
        mention_epbs_policy: Append the ePBS CSM assumption. False for
            MEV-Boost-only networks.

    Returns:
        Selected relays in *offered* order.

    Raises:
        BuilderModeError: The selection is empty, unknown, or violates CSM.
    """
    by_name = {str(relay.get("name", "")): relay for relay in offered}
    unknown = [name for name in selected_names if name not in by_name]
    if unknown:
        raise BuilderModeError(
            "Unknown relay selection: " + ", ".join(unknown)
        )
    chosen_names = set(selected_names)
    chosen = [relay for relay in offered if str(relay.get("name", "")) in chosen_names]
    if not chosen:
        raise BuilderModeError(
            empty_selection_message(csm=csm, allow_local_blocks=allow_local_blocks)
        )
    if csm:
        must, may, _excluded = partition_csm_relays(offered)
        vetted = {str(relay.get("name", "")) for relay in must + may}
        rejected = [
            str(relay.get("name", ""))
            for relay in chosen
            if str(relay.get("name", "")) not in vetted
        ]
        if rejected:
            raise BuilderModeError(
                "Lido CSM only allows vetted relays. Rejected: "
                + ", ".join(rejected)
            )
        must_names = {str(relay.get("name", "")) for relay in must}
        if not any(str(relay.get("name", "")) in must_names for relay in chosen):
            readable = ", ".join(sorted(must_names)) or "(none configured)"
            message = (
                "Lido CSM requires at least one must-use-some relay "
                f"({readable}). Local blocks are not available."
            )
            if mention_epbs_policy:
                message += " " + CSM_EPBS_POLICY_NOTE
            raise BuilderModeError(message)
    return [dict(relay) for relay in chosen]


def relay_checklist_title(mode: str) -> str:
    """Name the builder mode in the checklist window title."""
    if mode == BUILDER_EPBS:
        return "Relays (native ePBS, configured on VC)"
    if mode == BUILDER_MEVBOOST:
        return "Relays (MEV-Boost)"
    return "Relays"


def relay_checklist_item(
    relay: dict, *, csm: bool, checked: bool = True
) -> Tuple[str, str, bool]:
    """Build one whiptail checklist row.

    Args:
        relay: Relay dict with ``name`` and ``url``.
        csm: Append must-use-some / may-use to the item text.
        checked: Initial checkbox state. A refused selection is redrawn
            with the operator's previous checks, not all-on.

    Returns:
        ``(tag, item_text, checked)``.
    """
    name = str(relay.get("name", ""))
    url = str(relay.get("url", ""))
    host = url.split("@", 1)[1] if "@" in url else url
    if csm:
        kind = relay_allowlist_class(name)
        tag = "must-use-some" if kind == "must" else "may-use"
        text = f"{host} ({tag})"
    else:
        text = host
    return name, text[:70], checked


def relay_checklist_prompt(*, csm: bool, mode: str) -> str:
    """Return the whiptail checklist body for this builder mode."""
    base = (
        "Select relays. All are checked. Uncheck any you want to opt out of. "
        "An empty selection is refused."
    )
    if not csm:
        return base
    extra = (
        " Lido CSM locks MEV on and lists only vetted relays from allowlist "
        f"{CSM_RELAY_ALLOWLIST_CONTRACT} v{CSM_RELAY_ALLOWLIST_VERSION}. "
        "Keep at least one must-use-some relay."
    )
    if mode == BUILDER_EPBS:
        extra += " " + CSM_EPBS_POLICY_NOTE
    return base + extra


def show_install_notice(title: str, message: str) -> None:
    """Show *message* in a whiptail msgbox so the next checklist does not cover it.

    Args:
        title: Window title.
        message: Operator-facing text.
    """
    try:
        subprocess.run(
            ["whiptail", "--title", title, "--msgbox", message, "16", "78"],
            check=False,
        )
    except FileNotFoundError:
        print(f"{title}: {message}", file=sys.stderr)


def run_whiptail_checklist(
    title: str,
    prompt: str,
    items: Sequence[Tuple[str, str, bool]],
) -> Optional[List[str]]:
    """Show a whiptail checklist and return the checked tags.

    Args:
        title: Window title.
        prompt: Checklist text.
        items: ``(tag, item_text, checked)`` rows.

    Returns:
        Selected tags (one per line; names may contain spaces), or None when
        the operator cancels.

    Raises:
        BuilderModeError: ``whiptail`` is not installed or fails to start.
    """
    if not items:
        raise BuilderModeError("No relays to show in the checklist.")
    cmd = [
        "whiptail",
        "--title",
        title,
        "--checklist",
        prompt,
        "22",
        "78",
        str(min(len(items), 12)),
        "--separate-output",
    ]
    for tag, text, checked in items:
        cmd.extend([tag, text, "ON" if checked else "OFF"])
    try:
        proc = subprocess.run(cmd, stderr=subprocess.PIPE, text=True, check=False)
    except FileNotFoundError as exc:
        raise BuilderModeError(
            "whiptail is required to pick relays. Install whiptail (newt) "
            "or re-run with --skip_prompts true."
        ) from exc
    if proc.returncode != 0:
        return None
    return [line.strip() for line in (proc.stderr or "").splitlines() if line.strip()]


def _select_relays(
    *,
    network: str,
    csm: bool,
    mode: str,
    skip_prompts: bool,
    allow_local_blocks: bool,
    checklist: Callable[
        [str, str, Sequence[Tuple[str, str, bool]]],
        Optional[List[str]],
    ],
    notice: Callable[[str, str], None],
) -> List[dict]:
    """Resolve the relay list for a builder mode.

    A network with a single configured relay skips the checklist: opting out
    is always refused, so the dialog cannot change the result. The operator
    is told which relay and which builder mode were used.

    Raises:
        BuilderModeError: No relays are configured, or a checklist selection
            violates the rules.
        BuilderModeCancelled: The operator cancelled the checklist.
    """
    configured = relay_options_for_network(network)
    offered = csm_offered_relays(configured) if csm else list(configured)
    if not offered:
        raise BuilderModeError(
            f"No relays are configured for {network or 'this network'}. "
            f"Refusing to install builder mode {mode} with an empty list."
        )
    if skip_prompts:
        return default_relay_selection(offered, csm=csm)
    mention_epbs_policy = csm and mode == BUILDER_EPBS
    if len(offered) == 1:
        only = offered[0]
        where = (
            "native ePBS (configured on the validator client)"
            if mode == BUILDER_EPBS
            else "MEV-Boost"
        )
        message = (
            f"{only.get('name')} is the only relay configured for {network}. "
            f"It will be used for {where}. "
            "The checklist is skipped because opting out would leave no relay."
        )
        if mention_epbs_policy:
            message += " " + CSM_EPBS_POLICY_NOTE
        notice(relay_checklist_title(mode), message)
        return [dict(only)]
    prompt = relay_checklist_prompt(csm=csm, mode=mode)
    checked = {str(relay.get("name", "")): True for relay in offered}
    while True:
        items = [
            relay_checklist_item(
                relay,
                csm=csm,
                checked=checked.get(str(relay.get("name", "")), True),
            )
            for relay in offered
        ]
        selected = checklist(relay_checklist_title(mode), prompt, items)
        if selected is None:
            raise BuilderModeCancelled("Relay selection cancelled.")
        try:
            return validate_relay_selection(
                offered,
                selected,
                csm=csm,
                allow_local_blocks=allow_local_blocks,
                mention_epbs_policy=mention_epbs_policy,
            )
        except BuilderModeError as exc:
            selected_names = set(selected)
            checked = {
                str(relay.get("name", "")): str(relay.get("name", "")) in selected_names
                for relay in offered
            }
            notice("Relay selection", str(exc))


def finalize_builder_install(
    *,
    role: str,
    network: str,
    vc_name: Optional[str],
    cc_name: Optional[str],
    flags: Dict[str, object],
    builder_mode: str = "",
    with_mevboost: bool = False,
    with_builder_api: bool = False,
    skip_prompts: bool = False,
    prompt_index: Optional[Callable[[Sequence[str], str, str], int]] = None,
    checklist: Optional[
        Callable[[str, str, Sequence[Tuple[str, str, bool]]], Optional[List[str]]]
    ] = None,
    notice: Optional[Callable[[str, str], None]] = None,
) -> Tuple[Dict[str, object], List[dict]]:
    """Resolve builder mode, flags, and the relay checklist.

    ``--builder_mode`` wins. ``--with_mevboost`` is an alias for ``mevboost``.
    ``--with_builder_api`` keeps external builder flags and does not install
    local MEV-Boost or native ePBS relays. Preset roles otherwise use
    :func:`default_builder_mode`. Custom Setup and validator-only prompt
    unless ``skip_prompts`` is set.

    Args:
        role: Install role.
        network: Network slug.
        vc_name: Validator client, if chosen.
        cc_name: Consensus client, if chosen.
        flags: Capability flags (copied; ``charon`` / ``validator`` are read).
        builder_mode: CLI ``--builder_mode`` value, or empty.
        with_mevboost: CLI ``--with_mevboost``.
        with_builder_api: CLI ``--with_builder_api``.
        skip_prompts: Non-interactive install.
        prompt_index: ``(labels, title, subtitle) -> index`` for the mode menu.
        checklist: Whiptail checklist callback. Required when a checklist
            would be shown.
        notice: ``(title, message)`` for msgboxes (single-choice, single
            relay, and a refused selection). Defaults to a whiptail msgbox.

    Returns:
        ``(flags, relays)``. ``relays`` is empty when the mode is ``none``.
        ``flags['mevboost']`` is true only for a local sidecar. ``flags['epbs']``
        is true for native ePBS. ``flags['builder_api']`` is true for either
        builder mode and for ``--with_builder_api``.

    Raises:
        BuilderModeError: The mode or relay list cannot be installed.
        BuilderModeCancelled: The operator cancelled a prompt.
    """
    updated = dict(flags)
    tell = notice or show_install_notice
    csm = is_csm_role(role)
    charon = bool(updated.get("charon"))
    validator_only = bool(updated.get("validator_only"))
    has_validator = bool(updated.get("validator")) or (
        epbs_relay_client(vc_name, cc_name) == "Erigon-Caplin"
    )
    explicit = (builder_mode or "").strip().lower()
    if explicit and with_mevboost and explicit != BUILDER_MEVBOOST:
        raise BuilderModeError(
            "--with_mevboost selects MEV-Boost, which conflicts with "
            f"--builder_mode {explicit}."
        )
    if explicit:
        mode = explicit
    elif with_mevboost:
        mode = BUILDER_MEVBOOST
    elif with_builder_api:
        # External builder flags (CDVN). Do not invent a local sidecar or
        # an ePBS relay list the source node did not ask for.
        mode = BUILDER_NONE
    elif role not in _PROMPT_ROLES or (skip_prompts and csm):
        # Presets (and non-interactive CSM) take the role default. CSM never
        # falls through to local blocks just because prompts were skipped.
        mode = default_builder_mode(
            role, network, vc_name, cc_name, charon=charon
        )
    elif skip_prompts:
        mode = BUILDER_NONE
    else:
        choices = builder_mode_choices(
            network,
            vc_name,
            cc_name,
            charon=charon,
            csm=csm,
            validator_only=validator_only,
        )
        if len(choices) == 1:
            mode = choices[0][0]
            tell(
                role,
                (
                    f"{choices[0][1]} is the only builder mode available, "
                    "so it was selected."
                ),
            )
        else:
            if prompt_index is None:
                raise BuilderModeError(
                    "Builder mode prompt is required but no menu callback was provided."
                )
            if csm and validator_only:
                title = "Lido CSM Validator Client Only"
            elif validator_only:
                title = "Validator Client Only"
            else:
                title = "Custom Setup"
            subtitle = "Step 4: How should this node get blocks?"
            if validator_only:
                subtitle = (
                    "How should this validator client get blocks?\n"
                    "(Does not install a local MEV-Boost service)"
                )
            index = prompt_index([label for _mode, label in choices], title, subtitle)
            if index < 0 or index >= len(choices):
                raise BuilderModeCancelled("Builder mode selection cancelled.")
            mode = choices[index][0]

    validate_builder_mode(
        mode,
        role=role,
        network=network,
        vc_name=vc_name,
        cc_name=cc_name,
        charon=charon,
        has_validator=has_validator,
    )

    updated["builder_mode"] = mode
    updated["epbs"] = mode == BUILDER_EPBS
    if validator_only:
        updated["mevboost"] = False
        updated["builder_api"] = mode in (BUILDER_MEVBOOST, BUILDER_EPBS) or bool(
            with_builder_api
        )
    else:
        updated["mevboost"] = mode == BUILDER_MEVBOOST
        if mode in (BUILDER_MEVBOOST, BUILDER_EPBS) or with_builder_api:
            updated["builder_api"] = True
        else:
            updated["builder_api"] = False

    relays: List[dict] = []
    # Validator-only MEV-Boost means the remote beacon node already runs the
    # sidecar. Relays are collected only for a local sidecar or native ePBS.
    if mode == BUILDER_EPBS or (mode == BUILDER_MEVBOOST and not validator_only):
        allow_local_blocks = (not csm) and role in (
            "Custom Setup",
            "Validator Client Only",
        )
        relays = _select_relays(
            network=network,
            csm=csm,
            mode=mode,
            skip_prompts=skip_prompts,
            allow_local_blocks=allow_local_blocks,
            checklist=checklist or run_whiptail_checklist,
            notice=tell,
        )
    return updated, relays
