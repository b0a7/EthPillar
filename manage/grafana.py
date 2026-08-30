"""Grafana ini helpers (http_port read/write) used by monitoring and CDVN migrate."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

GRAFANA_INI_PATH = Path("/etc/grafana/grafana.ini")
DEFAULT_GRAFANA_HTTP_PORT = 3000


def read_grafana_ini() -> Optional[str]:
    """Read ``/etc/grafana/grafana.ini`` (directly or via sudo when needed)."""
    if GRAFANA_INI_PATH.is_file():
        try:
            return GRAFANA_INI_PATH.read_text(encoding="utf-8")
        except OSError:
            pass
    result = subprocess.run(
        ["sudo", "cat", str(GRAFANA_INI_PATH)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0 and result.stdout:
        return result.stdout
    return None


def _parse_grafana_http_port_from_ini(content: str) -> Optional[int]:
    """Return active ``http_port`` from ``grafana.ini`` ``[server]`` section."""
    in_server = False
    commented: Optional[int] = None
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_server = stripped.lower() == "[server]"
            continue
        if not in_server:
            continue
        match = re.match(r"^;?\s*http_port\s*=\s*(\d+)", stripped)
        if not match:
            continue
        port = int(match.group(1))
        if stripped.startswith(";"):
            commented = port
        else:
            return port
    return commented


def read_grafana_http_port(default: int = DEFAULT_GRAFANA_HTTP_PORT) -> int:
    """Return Grafana ``http_port`` from ``grafana.ini``, or *default* when unset."""
    content = read_grafana_ini()
    if content is None:
        return default
    port = _parse_grafana_http_port_from_ini(content)
    return port if port is not None else default


def apply_grafana_http_port(port: int) -> bool:
    """Set Grafana ``http_port`` in ``grafana.ini`` and restart.

    Returns True when the port was applied (ini updated or already matched).
    """
    content = read_grafana_ini()
    if content is None:
        return False

    fd, tmp_name = tempfile.mkstemp(prefix="ethpillar-grafana-", suffix=".ini")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(_grafana_ini_with_http_port(content, port))
        subprocess.run(["sudo", "cp", tmp_name, str(GRAFANA_INI_PATH)], check=True)
    finally:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
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
        if in_server and re.match(r"^;?\s*http_port", stripped):
            out.append(new_line)
            replaced = True
            continue
        out.append(line)
    if in_server and not replaced:
        out.append(new_line)
    return "".join(out)
