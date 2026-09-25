"""Pure helpers for Nimbus first-start trustedNodeSync integration checks."""
from __future__ import annotations

import os
import subprocess
from typing import FrozenSet, Tuple

NIMBUS_DB_DIR = "/var/lib/nimbus/db"
START_TIMEOUT_RESULTS: FrozenSet[str] = frozenset({"timeout", "timeout-abort"})
START_TIMEOUT_JOURNAL_MARKERS: Tuple[str, ...] = (
    "failed because a timeout was exceeded",
    "Failed with result 'timeout'",
    "Failed with result 'timeout-abort'",
    "start-pre operation timed out",
)


def unit_uses_nimbus_checkpoint_sync(unit_text: str) -> bool:
    """Return True when the unit runs Nimbus ``trustedNodeSync`` on first start."""
    return "nimbus_beacon_node" in unit_text and "trustedNodeSync" in unit_text


def systemd_result_is_start_timeout(result: str) -> bool:
    """Return True when systemd ``Result=`` is a start-timeout outcome."""
    return result.strip().lower() in START_TIMEOUT_RESULTS


def journal_indicates_start_timeout(text: str) -> bool:
    """Return True when journal/stderr shows TimeoutStartSec or ExecStartPre timeout."""
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in START_TIMEOUT_JOURNAL_MARKERS)


def nimbus_checkpoint_db_ready(db_dir: str = NIMBUS_DB_DIR, use_sudo: bool = False) -> bool:
    """Return True when the Nimbus beacon ``db`` directory exists.

    The datadir is typically mode 700 ``consensus``, so the integration user
    may need ``sudo test -d`` rather than a direct ``os.path.isdir``.
    """
    if os.path.isdir(db_dir):
        return True
    if use_sudo:
        check = subprocess.run(["sudo", "test", "-d", db_dir], capture_output=True)
        return check.returncode == 0
    return False
