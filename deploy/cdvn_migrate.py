"""Plan and run a CDVN → EthPillar full-stack migration."""

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
    copy_charon_cluster,
    import_cdvn_env_to_service,
    parse_dotenv,
    resolve_cdvn_checkout,
    rewrite_endpoint_list,
)
from deploy.orchestrator import lodestar_bn_vc_incompatibility_message
from deploy.common import BASE_DATA_DIR

# Stock CDVN compose profile tokens → EthPillar client names.
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
MEV_NONE = frozenset({"mev-none", ""})

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
    datadir_moves: List[DatadirMove] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    el_profile: str = ""
    cl_profile: str = ""
    vc_profile: str = ""
    mev_profile: str = ""

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
            f"  BN address:   {self.bn_address or '(local via EthPillar CC)'}",
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


def _dir_nonempty(path: str) -> bool:
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
    """Return True if ``docker compose`` reports running services for this CDVN."""
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
    """Build a migration plan from a CDVN checkout path or ``.env`` file."""
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

    el_none = _is_none_profile(el_raw, "el")
    cl_none = _is_none_profile(cl_raw, "cl")

    ec_name: Optional[str] = None
    cc_name: Optional[str] = None
    if not el_none:
        if el_raw not in EL_PROFILE_MAP:
            raise ValueError(
                f"Unsupported CDVN EL profile {el_raw!r}. "
                f"Supported: {', '.join(sorted(EL_PROFILE_MAP))} or el-none."
            )
        ec_name = EL_PROFILE_MAP[el_raw]
    if not cl_none:
        if cl_raw not in CL_PROFILE_MAP:
            raise ValueError(
                f"Unsupported CDVN CL profile {cl_raw!r}. "
                f"Supported: {', '.join(sorted(CL_PROFILE_MAP))} or cl-none."
            )
        cc_name = CL_PROFILE_MAP[cl_raw]

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
    warnings: List[str] = []
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
    with_mevboost = mev_raw in MEV_LOCAL and not el_none and not cl_none
    builder_raw = env.get("CHARON_BUILDER_API") or env.get("BUILDER_API_ENABLED") or ""
    with_builder_api = with_mevboost or (bool(builder_raw) and _truthy(builder_raw))

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
            warnings.append(f"Found {rel} but EL={el_raw or 'unset'} (el-none); not migrating that dir.")
        if kind == "CL" and cl_none:
            warnings.append(f"Found {rel} but CL={cl_raw or 'unset'} (cl-none); not migrating that dir.")

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
        if not _dir_nonempty(src):
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
    """Apply datadir moves.

    ``selected``:
      * ``None`` — move all eligible dirs
      * empty sequence — move none
      * otherwise — only listed ``relative_src`` values

    ``.charon`` is handled separately by ``copy_charon_cluster`` / move in ``run_migration``.
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
    """Execute migration (or dry-run). Raises on hard failures including docker running."""
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
