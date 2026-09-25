"""Tests for Nimbus checkpoint sync (trustedNodeSync ExecStartPre) in deploy/nimbus.py."""
import os
import re
import shlex
import subprocess
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import deploy.nimbus as nimbus

SYNC_URL = "https://mainnet.checkpoint.sigp.io"
# Query strings / fragments must survive systemd + bash untouched.
TRICKY_URL = "https://host.example/eth/?apikey=a1b2&x=1;y=2#frag"
BN_ARGS = ("/secrets/jwtsecret", "5052", "9000", "9001", "100")


def _exec_start_pre(unit: str) -> str:
    lines = [l for l in unit.splitlines() if l.startswith("ExecStartPre=")]
    assert len(lines) == 1, unit
    return lines[0].removeprefix("ExecStartPre=")


def _script(pre: str) -> str:
    # systemd only accepts a quote at the start of a word: the script must be
    # exactly one single-quoted word with no embedded single quote.
    assert re.fullmatch(r"/bin/bash -c '[^']*'", pre), pre
    return shlex.split(pre)[2]


def test_unit_checkpoint_syncs_only_without_existing_db():
    unit = nimbus.generate_nimbus_bn_service("mainnet", *BN_ARGS, sync_url=SYNC_URL)
    script = _script(_exec_start_pre(unit))
    db = f"{nimbus.NIMBUS_DATA_DIR}/db"
    staging = f"{nimbus.NIMBUS_DATA_DIR}/.checkpoint-sync"
    assert script.startswith(f'test -d "{db}" || ')
    assert '"trustedNodeSync"' in script
    assert '"--network=mainnet"' in script
    assert f'"--trusted-node-url={SYNC_URL}"' in script
    assert '"--backfill=false"' in script
    # Sync into a staging dir; only a completed db is moved into place.
    assert f'"--data-dir={staging}"' in script
    assert f'mv "{staging}/db" "{db}"' in script
    assert "TimeoutStartSec=1800" in unit
    subprocess.run(["bash", "-n", "-c", script], check=True)


def test_url_with_query_string_reaches_nimbus_verbatim(tmp_path):
    unit = nimbus.generate_nimbus_bn_service("mainnet", *BN_ARGS, sync_url=TRICKY_URL)
    script = _script(_exec_start_pre(unit))
    # Run the script with a stub binary that echoes its URL argument.
    stub = tmp_path / "nimbus_beacon_node"
    stub.write_text('#!/bin/bash\nfor a; do case $a in --trusted-node-url=*) echo "${a#*=}";; esac; done\nexit 1\n')
    stub.chmod(0o755)
    script = script.replace(f"{nimbus.INSTALL_DIR}/nimbus_beacon_node", str(stub))
    script = script.replace(nimbus.NIMBUS_DATA_DIR, str(tmp_path / "data"))
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert out.stdout.strip() == TRICKY_URL


@pytest.mark.parametrize("bad", ["https://h/a b", "https://h/'x", 'https://h/"x', "https://h/$HOME",
                                 "https://h/%40", "https://h/`id`", "https://h/\\x"])
def test_unsafe_url_is_rejected(bad):
    with pytest.raises(ValueError):
        nimbus.generate_nimbus_bn_service("mainnet", *BN_ARGS, sync_url=bad)


def test_unit_has_no_sync_step_without_url():
    unit = nimbus.generate_nimbus_bn_service("mainnet", *BN_ARGS)
    assert "ExecStartPre=" not in unit
    assert "TimeoutStartSec=" not in unit


def test_sync_uses_same_network_as_service():
    unit = nimbus.generate_nimbus_bn_service("ephemery", *BN_ARGS, sync_url=SYNC_URL)
    assert '"--network=/opt/ethpillar/testnet/config.yaml"' in _exec_start_pre(unit)

    override = "--network=/custom/config.yaml"
    unit = nimbus.generate_nimbus_bn_service("mainnet", *BN_ARGS, network_override=override, sync_url=SYNC_URL)
    assert f'"{override}"' in _exec_start_pre(unit)


def test_install_nimbus_bn_writes_unit_with_sync_step():
    with patch("deploy.nimbus.write_service_file") as write:
        nimbus.install_nimbus_bn("mainnet", *BN_ARGS, sync_url=SYNC_URL)
    content = write.call_args.args[0]
    assert "trustedNodeSync" in _exec_start_pre(content)
