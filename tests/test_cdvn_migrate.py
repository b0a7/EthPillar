"""Tests for deploy/cdvn_migrate planning and deploy argv."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from deploy.cdvn_migrate import (
    DATADIR_MOVES,
    plan_cdvn_migration,
)


def _write_cdvn(tmp_path: Path, env: str, *, with_lock: bool = True, data_dirs: list | None = None) -> Path:
    root = tmp_path / "cdvn"
    root.mkdir()
    (root / ".env").write_text(env, encoding="utf-8")
    charon = root / ".charon"
    charon.mkdir()
    if with_lock:
        (charon / "cluster-lock.json").write_text("{}", encoding="utf-8")
        keys = charon / "validator_keys"
        keys.mkdir()
        (keys / "keystore-0.json").write_text("{}", encoding="utf-8")
    for rel in data_dirs or []:
        d = root / Path(rel)
        d.mkdir(parents=True, exist_ok=True)
        (d / "marker").write_text("x", encoding="utf-8")
    return root


def test_plan_validator_only_external_bn(tmp_path: Path):
    root = _write_cdvn(
        tmp_path,
        "NETWORK=mainnet\n"
        "EL=el-none\n"
        "CL=cl-none\n"
        "VC=vc-lodestar\n"
        "MEV=mev-none\n"
        "BUILDER_API_ENABLED=true\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://192.168.1.50:5052\n",
    )
    plan = plan_cdvn_migration(str(root))
    assert plan.role == "Validator Client Only"
    assert plan.network == "mainnet"
    assert plan.ec_name is None
    assert plan.cc_name is None
    assert plan.vc_name == "Lodestar"
    assert plan.with_charon is True
    assert plan.with_mevboost is False
    assert plan.with_builder_api is True
    assert plan.bn_address == "http://192.168.1.50:5052"
    argv = plan.deploy_argv()
    assert "--install_config" in argv
    assert "Validator Client Only" in argv
    assert "--with_charon" in argv
    assert "--with_builder_api" in argv
    assert "--with_mevboost" not in argv
    assert "--vc_only_bn_address" in argv
    assert "http://192.168.1.50:5052" in argv


def test_plan_full_stack_with_local_mev(tmp_path: Path):
    root = _write_cdvn(
        tmp_path,
        "NETWORK=hoodi\n"
        "EL=el-nethermind\n"
        "CL=cl-lighthouse\n"
        "VC=vc-lodestar\n"
        "MEV=mev-mevboost\n"
        "BUILDER_API_ENABLED=true\n",
        data_dirs=["data/nethermind", "data/lighthouse", "data/lodestar"],
    )
    plan = plan_cdvn_migration(str(root))
    assert plan.role == "Custom Setup"
    assert plan.network == "hoodi"
    assert plan.ec_name == "Nethermind"
    assert plan.cc_name == "Lighthouse"
    assert plan.vc_name == "Lodestar"
    assert plan.with_mevboost is True
    assert plan.with_builder_api is True
    argv = plan.deploy_argv()
    assert "--ec" in argv and "Nethermind" in argv
    assert "--cc" in argv and "Lighthouse" in argv
    assert "--with_mevboost" in argv
    movable = [m for m in plan.datadir_moves if m.will_move]
    assert {m.relative_src for m in movable} >= {
        "data/nethermind",
        "data/lighthouse",
        "data/lodestar",
    }


def test_plan_rewrites_docker_bn(tmp_path: Path):
    root = _write_cdvn(
        tmp_path,
        "NETWORK=sepolia\n"
        "EL=el-none\n"
        "CL=cl-none\n"
        "VC=vc-teku\n"
        "MEV=mev-none\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://lighthouse:5052\n",
    )
    plan = plan_cdvn_migration(str(root))
    assert plan.vc_name == "Teku"
    assert plan.bn_address == "http://127.0.0.1:5052"
    assert any("Rewrote" in w or "lighthouse" in w for w in plan.warnings)


def test_plan_unknown_el_fails(tmp_path: Path):
    root = _write_cdvn(
        tmp_path,
        "NETWORK=mainnet\n"
        "EL=el-geth\n"
        "CL=cl-lighthouse\n"
        "VC=vc-lodestar\n"
        "MEV=mev-none\n",
    )
    with pytest.raises(ValueError, match="Unsupported CDVN EL"):
        plan_cdvn_migration(str(root))


def test_plan_cl_without_el_fails(tmp_path: Path):
    root = _write_cdvn(
        tmp_path,
        "NETWORK=mainnet\n"
        "EL=el-none\n"
        "CL=cl-lighthouse\n"
        "VC=vc-lodestar\n"
        "MEV=mev-none\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://127.0.0.1:5052\n",
    )
    with pytest.raises(ValueError, match="unsupported"):
        plan_cdvn_migration(str(root))


def test_plan_orphan_data_warning(tmp_path: Path):
    root = _write_cdvn(
        tmp_path,
        "NETWORK=mainnet\n"
        "EL=el-none\n"
        "CL=cl-none\n"
        "VC=vc-lodestar\n"
        "MEV=mev-none\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://10.0.0.2:5052\n",
        data_dirs=["data/lighthouse"],
    )
    plan = plan_cdvn_migration(str(root))
    assert any("data/lighthouse" in w and "cl-none" in w for w in plan.warnings)
    assert not any(m.relative_src == "data/lighthouse" and m.will_move for m in plan.datadir_moves)


def test_plan_lodestar_bn_incompatible_vc_warns(tmp_path: Path):
    root = _write_cdvn(
        tmp_path,
        "NETWORK=mainnet\n"
        "EL=el-nethermind\n"
        "CL=cl-lodestar\n"
        "VC=vc-prysm\n"
        "MEV=mev-mevboost\n",
    )
    plan = plan_cdvn_migration(str(root))
    assert plan.cc_name == "Lodestar"
    assert plan.vc_name == "Prysm"
    assert any("Lodestar beacon node" in w and "Prysm" in w for w in plan.warnings)


def test_datadir_map_covers_stock_clients():
    assert "data/nethermind" in DATADIR_MOVES
    assert "data/reth" in DATADIR_MOVES
    assert DATADIR_MOVES["data/lodestar"][0] == "lodestar_validator"
