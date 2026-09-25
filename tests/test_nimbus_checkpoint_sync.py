"""Tests for Nimbus checkpoint sync (trustedNodeSync ExecStartPre) in deploy/nimbus.py."""
import os
import shlex
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import deploy.nimbus as nimbus

SYNC_URL = "https://mainnet.checkpoint.sigp.io"
BN_ARGS = ("/secrets/jwtsecret", "5052", "9000", "9001", "100")


def _exec_start_pre(unit: str) -> str:
    lines = [l for l in unit.splitlines() if l.startswith("ExecStartPre=")]
    assert len(lines) == 1, unit
    return lines[0].removeprefix("ExecStartPre=")


def test_unit_checkpoint_syncs_only_without_existing_db():
    unit = nimbus.generate_nimbus_bn_service("mainnet", *BN_ARGS, sync_url=SYNC_URL)
    pre = _exec_start_pre(unit)
    argv = shlex.split(pre)
    assert argv[:2] == ["/bin/bash", "-c"]
    script = argv[2]
    db = f"{nimbus.NIMBUS_DATA_DIR}/db"
    staging = f"{nimbus.NIMBUS_DATA_DIR}/.checkpoint-sync"
    assert script.startswith(f"test -d {db} || ")
    assert "nimbus_beacon_node trustedNodeSync" in script
    assert "--network=mainnet" in script
    assert f"--trusted-node-url={SYNC_URL}" in script
    assert "--backfill=false" in script
    # Sync into a staging dir; only a completed db is moved into place.
    assert f"--data-dir={staging}" in script
    assert f"mv {staging}/db {db}" in script
    assert "TimeoutStartSec=1800" in unit
    # Must be valid bash.
    subprocess.run(["bash", "-n", "-c", script], check=True)


def test_unit_has_no_sync_step_without_url():
    unit = nimbus.generate_nimbus_bn_service("mainnet", *BN_ARGS)
    assert "ExecStartPre=" not in unit
    assert "TimeoutStartSec=" not in unit


def test_sync_uses_same_network_as_service():
    unit = nimbus.generate_nimbus_bn_service("ephemery", *BN_ARGS, sync_url=SYNC_URL)
    assert "--network=/opt/ethpillar/testnet/config.yaml" in _exec_start_pre(unit)

    override = "--network=/custom/config.yaml"
    unit = nimbus.generate_nimbus_bn_service("mainnet", *BN_ARGS, network_override=override, sync_url=SYNC_URL)
    assert override in _exec_start_pre(unit)


def test_install_nimbus_bn_writes_unit_with_sync_step():
    with patch("deploy.nimbus.write_service_file") as write:
        nimbus.install_nimbus_bn("mainnet", *BN_ARGS, sync_url=SYNC_URL)
    content = write.call_args.args[0]
    assert "trustedNodeSync" in _exec_start_pre(content)
