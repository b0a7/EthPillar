"""Provision Charon Prometheus scrape and CDVN Grafana dashboards.

Used when Charon is installed and EthPillar monitoring (Prometheus/Grafana) is
present — or when monitoring is installed while Charon already exists. Downloads
the same dashboard JSON bundle as upstream CDVN (Overview, Cluster, Node, Logs).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Dict, Optional, Union

CHARON_OVERVIEW_URL = (
    "https://raw.githubusercontent.com/ObolNetwork/charon-distributed-validator-node/"
    "main/grafana/dashboards/charon_overview_dashboard.json"
)
CDVN_DASHBOARDS_BASE = (
    "https://raw.githubusercontent.com/ObolNetwork/charon-distributed-validator-node/"
    "main/grafana/dashboards"
)
# Same JSON files CDVN Docker Grafana provisions (Overview, Cluster, Node, Logs).
CDVN_GRAFANA_DASHBOARDS = (
    "charon_overview_dashboard.json",
    "cluster_dashboard.json",
    "node_overview_dashboard.json",
    "logs_dashboard.json",
)
CHARON_SCRAPE_JOB = (
    "   - job_name: 'charon'\n"
    "     static_configs:\n"
    "       - targets: ['localhost:3620']\n"
)
DEFAULT_PROMETHEUS_YML = Path("/etc/prometheus/prometheus.yml")
DEFAULT_GRAFANA_DASHBOARDS = Path("/etc/grafana/provisioning/dashboards")
DEFAULT_DATASOURCES_YML = Path("/etc/grafana/provisioning/datasources/datasources.yml")
_JOB_RE = re.compile(r"job_name:\s*['\"]charon['\"]")
_DEFAULT_DATASOURCE = (
    "apiVersion: 1\n"
    "datasources:\n"
    "  - name: Prometheus\n"
    "    type: prometheus\n"
    "    uid: prometheus\n"
    "    url: http://localhost:9090\n"
    "    access: proxy\n"
    "    isDefault: true\n"
)


def has_charon_scrape(text: str) -> bool:
    """Return True if prometheus.yml already defines a Charon scrape job."""
    return bool(_JOB_RE.search(text))


def ensure_prometheus_datasource_uid(datasources_yml: Path) -> bool:
    """Ensure Grafana Prometheus datasource uses ``uid: prometheus`` (CDVN dashboards).

    Returns:
        True if the file was created or modified.
    """
    grafana_etc = Path("/etc/grafana")
    if not datasources_yml.is_file():
        if not grafana_etc.is_dir():
            return False
        _write_bytes(datasources_yml, _DEFAULT_DATASOURCE)
        return True

    text = datasources_yml.read_text(encoding="utf-8")
    if re.search(r"uid:\s*['\"]?prometheus['\"]?", text):
        return False

    updated, count = re.subn(
        r"(name:\s*Prometheus\r?\n\s*type:\s*prometheus\r?\n)",
        r"\1    uid: prometheus\n",
        text,
        count=1,
    )
    if count:
        _write_bytes(datasources_yml, updated)
        return True
    return False


def _finalize_grafana_provisioned_file(path: Path) -> None:
    """Ensure Grafana (user ``grafana``) can read file-provisioned dashboard JSON."""
    if not str(path).startswith("/etc/grafana"):
        return
    subprocess.run(["sudo", "chown", "root:grafana", str(path)], check=False)
    subprocess.run(["sudo", "chmod", "644", str(path)], check=False)


def _write_bytes(path: Path, data: Union[str, bytes]) -> None:
    """Write ``data`` to ``path``, using ``sudo cp`` when needed for /etc."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        _finalize_grafana_provisioned_file(path)
        return
    except PermissionError:
        pass

    subprocess.run(["sudo", "mkdir", "-p", str(path.parent)], check=True)
    fd, tmp = tempfile.mkstemp(prefix="ethpillar_charon_")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
        subprocess.run(["sudo", "cp", tmp, str(path)], check=True)
        _finalize_grafana_provisioned_file(path)
    finally:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass


def ensure_charon_scrape(prometheus_yml: Path) -> bool:
    """Append a Charon scrape job if missing.

    Returns:
        True if the file was modified; False if unchanged or missing.
    """
    if not prometheus_yml.is_file():
        return False
    text = prometheus_yml.read_text(encoding="utf-8")
    if has_charon_scrape(text):
        return False
    suffix = "" if text.endswith("\n") else "\n"
    _write_bytes(prometheus_yml, text + suffix + CHARON_SCRAPE_JOB)
    return True


def download_charon_dashboard(dest: Path, url: str) -> None:
    """Download a Grafana dashboard JSON to ``dest``."""
    req = urllib.request.Request(url, headers={"User-Agent": "ethpillar"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    if not data:
        raise RuntimeError(f"Empty response downloading Charon dashboard from {url}")
    _write_bytes(dest, data)


def download_charon_overview_dashboard(
    dest: Path,
    url: str = CHARON_OVERVIEW_URL,
) -> None:
    """Download Obol Charon Overview dashboard JSON to ``dest``."""
    download_charon_dashboard(dest, url)


def provision_cdvn_grafana_dashboards(
    dashboards_dir: Path,
    base_url: str = CDVN_DASHBOARDS_BASE,
    filenames: tuple[str, ...] = CDVN_GRAFANA_DASHBOARDS,
) -> int:
    """Download and write the CDVN Charon Grafana dashboard bundle.

    Args:
        dashboards_dir: Grafana file-provisioning directory.
        base_url: Raw GitHub base URL for CDVN dashboard JSON files.
        filenames: Dashboard basenames to fetch (see :data:`CDVN_GRAFANA_DASHBOARDS`).

    Returns:
        Count of dashboard files written.
    """
    written = 0
    for name in filenames:
        download_charon_dashboard(dashboards_dir / name, f"{base_url.rstrip('/')}/{name}")
        written += 1
    return written


def provision_charon_overview_dashboard(
    dashboards_dir: Path,
    url: str = CHARON_OVERVIEW_URL,
) -> bool:
    """Write Charon Overview into Grafana's file-provisioning directory.

    Returns:
        True if the dashboard file was written.
    """
    dest = dashboards_dir / "charon_overview_dashboard.json"
    download_charon_overview_dashboard(dest, url=url)
    return True


def provision_charon_dashboards(
    dashboards_dir: Path,
    *,
    all_cdvn: bool = True,
    dashboard_url: str = CHARON_OVERVIEW_URL,
) -> bool:
    """Write Charon Grafana dashboards (full CDVN bundle or Overview-only).

    Args:
        dashboards_dir: Grafana file-provisioning directory.
        all_cdvn: When True, provision :data:`CDVN_GRAFANA_DASHBOARDS`; else Overview only.
        dashboard_url: Overview JSON URL when ``all_cdvn`` is False.

    Returns:
        True if at least one dashboard file was written.
    """
    if all_cdvn:
        return provision_cdvn_grafana_dashboards(dashboards_dir) > 0
    return provision_charon_overview_dashboard(dashboards_dir, url=dashboard_url)


def provision_charon_monitoring(
    prometheus_yml: Path = DEFAULT_PROMETHEUS_YML,
    grafana_dashboards: Path = DEFAULT_GRAFANA_DASHBOARDS,
    datasources_yml: Path = DEFAULT_DATASOURCES_YML,
    restart: bool = False,
    dashboard_url: str = CHARON_OVERVIEW_URL,
    all_cdvn_dashboards: bool = True,
) -> Dict[str, bool]:
    """Ensure Charon scrape job and CDVN Grafana dashboards when monitoring exists.

    No-ops quietly when Prometheus/Grafana paths are absent (monitoring not
    installed yet).

    Args:
        prometheus_yml: Prometheus config path.
        grafana_dashboards: Grafana dashboard provisioning directory.
        datasources_yml: Grafana datasources provisioning file.
        restart: Restart prometheus/grafana after changes.
        dashboard_url: Overview-only JSON URL (when ``all_cdvn_dashboards`` is False).
        all_cdvn_dashboards: When True, provision the full CDVN dashboard bundle.

    Returns:
        Dict with ``scrape``, ``dashboard``, and ``datasource`` booleans.
    """
    results = {"scrape": False, "dashboard": False, "datasource": False}

    results["scrape"] = ensure_charon_scrape(prometheus_yml)

    grafana_etc = Path("/etc/grafana")
    if grafana_dashboards.is_dir() or grafana_etc.is_dir():
        results["datasource"] = ensure_prometheus_datasource_uid(datasources_yml)
        try:
            results["dashboard"] = provision_charon_dashboards(
                grafana_dashboards,
                all_cdvn=all_cdvn_dashboards,
                dashboard_url=dashboard_url,
            )
        except Exception as exc:  # noqa: BLE001 — surface soft failure to caller
            print(f"Warning: Charon Grafana dashboard provision failed: {exc}", file=sys.stderr)

    if restart and results["scrape"]:
        subprocess.run(
            ["sudo", "systemctl", "try-restart", "prometheus"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    if restart and (results["datasource"] or results["dashboard"]):
        subprocess.run(
            ["sudo", "systemctl", "try-restart", "grafana-server"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    return results


def main(argv: Optional[list] = None) -> int:
    """CLI entry for Bash install hooks."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("provision", help="Ensure Charon scrape job + Overview dashboard")
    p.add_argument("--prometheus-yml", type=Path, default=DEFAULT_PROMETHEUS_YML)
    p.add_argument("--grafana-dashboards", type=Path, default=DEFAULT_GRAFANA_DASHBOARDS)
    p.add_argument("--dashboard-url", default=CHARON_OVERVIEW_URL)
    p.add_argument(
        "--overview-only",
        action="store_true",
        help="Provision Charon Overview only (default: full CDVN dashboard bundle)",
    )
    p.add_argument("--restart", action="store_true", help="try-restart prometheus if scrape changed")

    args = parser.parse_args(argv)
    if args.cmd == "provision":
        results = provision_charon_monitoring(
            prometheus_yml=args.prometheus_yml,
            grafana_dashboards=args.grafana_dashboards,
            restart=args.restart,
            dashboard_url=args.dashboard_url,
            all_cdvn_dashboards=not args.overview_only,
        )
        print(
            "charon monitoring: "
            f"scrape_updated={results['scrape']} "
            f"dashboard_written={results['dashboard']} "
            f"datasource_uid={results['datasource']}"
        )
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
