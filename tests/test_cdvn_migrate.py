"""Tests for deploy/cdvn_migrate planning and deploy argv."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from deploy.cdvn_migrate import (
    DATADIR_MOVES,
    _fee_recipient_from_cdvn,
    _grafana_ini_with_http_port,
    grafana_port_from_env,
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


def test_plan_lodestar_datadir_merges_state(tmp_path: Path):
    root = _write_cdvn(
        tmp_path,
        "NETWORK=mainnet\n"
        "EL=el-none\n"
        "CL=cl-none\n"
        "VC=vc-lodestar\n"
        "MEV=mev-none\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://127.0.0.1:5052\n",
    )
    base = root / "data" / "lodestar"
    (base / "keystores").mkdir(parents=True)
    (base / "keystores" / "keystore-0.json").write_text("{}", encoding="utf-8")
    (base / "validator-db").mkdir()
    (base / "validator-db" / "db").write_text("x", encoding="utf-8")
    (base / "validator-2026-08-28.log").write_text("log", encoding="utf-8")

    plan = plan_cdvn_migration(str(root))
    move = next(m for m in plan.datadir_moves if m.relative_src == "data/lodestar")
    assert move.will_move
    assert "auto-sync to Lodestar" in plan.summary()


def test_plan_vc_teku_logs_only_skips_datadir_move(tmp_path: Path):
    root = _write_cdvn(
        tmp_path,
        "NETWORK=mainnet\n"
        "EL=rp-external\n"
        "CL=rp-external\n"
        "VC=vc-teku\n"
        "MEV=rp-external\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://100.116.116.75:5052\n",
    )
    logs = root / "data" / "vc-teku" / "logs"
    logs.mkdir(parents=True)
    (logs / "teku.log").write_text("x", encoding="utf-8")

    plan = plan_cdvn_migration(str(root))
    vc_move = next(m for m in plan.datadir_moves if m.relative_src == "data/vc-teku")
    assert not vc_move.will_move
    assert "only logs present" in vc_move.skip_reason
    assert plan.has_keyshares is True
    assert "auto-sync to Teku" in plan.summary()


def test_plan_symlink_charon(tmp_path: Path):
    real = tmp_path / "node2"
    real.mkdir()
    (real / "cluster-lock.json").write_text("{}", encoding="utf-8")
    keys = real / "validator_keys"
    keys.mkdir()
    (keys / "keystore-0.json").write_text("{}", encoding="utf-8")

    root = tmp_path / "cdvn"
    root.mkdir()
    (root / ".env").write_text(
        "NETWORK=mainnet\n"
        "EL=rp-external\n"
        "CL=rp-external\n"
        "VC=vc-teku\n"
        "MEV=rp-external\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://100.116.116.75:5052\n",
        encoding="utf-8",
    )
    (root / ".charon").symlink_to(real, target_is_directory=True)

    plan = plan_cdvn_migration(str(root))
    assert plan.charon_is_symlink is True
    assert plan.charon_dir == str(real.resolve())
    assert "→" in plan.summary()
    charon_move = next(m for m in plan.datadir_moves if m.relative_src == ".charon")
    assert charon_move.src == str(real.resolve())


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
    assert "--skip_prompts" in argv
    assert argv[argv.index("--skip_prompts") + 1] == "true"
    assert "--install_config" in argv
    assert "Validator Client Only" in argv
    assert "--with_charon" in argv
    assert "--with_builder_api" in argv
    assert "--with_mevboost" not in argv
    assert "--vc_only_bn_address" in argv
    assert "http://192.168.1.50:5052" in argv


def test_fee_recipient_from_cluster_lock(tmp_path: Path):
    root = _write_cdvn(
        tmp_path,
        "NETWORK=mainnet\n"
        "EL=el-none\n"
        "CL=cl-none\n"
        "VC=vc-lodestar\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://127.0.0.1:5052\n",
    )
    fee = "0x388C818CA8B9251b393131C08a736A67ccB19297"
    lock = {
        "cluster_definition": {
            "validators": [{"fee_recipient_address": fee, "withdrawal_address": fee}]
        },
        "distributed_validators": [],
    }
    (root / ".charon" / "cluster-lock.json").write_text(
        __import__("json").dumps(lock),
        encoding="utf-8",
    )
    plan = plan_cdvn_migration(str(root))
    assert plan.fee_recipient.lower() == fee.lower()
    argv = plan.deploy_argv()
    assert argv[argv.index("--fee_address") + 1].lower() == fee.lower()


def test_fee_recipient_from_deposit_data_withdrawal_credentials(tmp_path: Path):
    root = _write_cdvn(
        tmp_path,
        "NETWORK=mainnet\n"
        "EL=el-none\n"
        "CL=cl-none\n"
        "VC=vc-lodestar\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://127.0.0.1:5052\n",
    )
    fee = "0x388C818CA8B9251b393131C08a736A67ccB19297"
    creds = "0x01" + "0" * 22 + fee[2:].lower()
    (root / ".charon" / "deposit-data.json").write_text(
        f'[{{"withdrawal_credentials": "{creds}"}}]',
        encoding="utf-8",
    )
    plan = plan_cdvn_migration(str(root))
    assert plan.fee_recipient.lower() == fee.lower()
    argv = plan.deploy_argv()
    assert "--fee_address" in argv
    assert argv[argv.index("--fee_address") + 1].lower() == fee.lower()


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


def test_plan_unknown_el_treated_as_external(tmp_path: Path):
    root = _write_cdvn(
        tmp_path,
        "NETWORK=mainnet\n"
        "EL=el-geth\n"
        "CL=cl-lighthouse\n"
        "VC=vc-lodestar\n"
        "MEV=mev-none\n",
    )
    with pytest.raises(ValueError, match="unsupported"):
        plan_cdvn_migration(str(root))


def test_plan_custom_external_profiles_vc_only(tmp_path: Path):
    """Unmapped EL/CL/MEV profiles → external stack (e.g. rp-external)."""
    root = _write_cdvn(
        tmp_path,
        "NETWORK=mainnet\n"
        "EL=rp-external\n"
        "CL=rp-external\n"
        "VC=vc-teku\n"
        "MEV=rp-external\n"
        "BUILDER_API_ENABLED=true\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://100.116.116.75:5052\n"
        "MONITORING_PORT_GRAFANA=3701\n",
    )
    plan = plan_cdvn_migration(str(root))
    assert plan.role == "Validator Client Only"
    assert plan.ec_name is None
    assert plan.cc_name is None
    assert plan.vc_name == "Teku"
    assert plan.with_mevboost is False
    assert plan.with_builder_api is True
    assert plan.grafana_port == 3701
    assert plan.bn_address == "http://100.116.116.75:5052"
    assert any("rp-external" in w and "EL" in w for w in plan.warnings)
    assert any("rp-external" in w and "CL" in w for w in plan.warnings)
    assert any("rp-external" in w and "MEV" in w for w in plan.warnings)


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


def test_run_migration_applies_charon_overlay_with_empty_moves(tmp_path, monkeypatch):
    root = _write_cdvn(
        tmp_path,
        "NETWORK=mainnet\n"
        "EL=el-none\n"
        "CL=cl-none\n"
        "VC=vc-lodestar\n"
        "CHARON_BEACON_NODE_ENDPOINTS=http://127.0.0.1:5052\n",
    )
    overlay_calls: list[str] = []

    def fake_overlay(plan, *, skip=False):
        overlay_calls.append(plan.root)

    monkeypatch.setattr("deploy.cdvn_migrate.run_deploy", lambda *a, **k: 0)
    monkeypatch.setattr("deploy.cdvn_migrate.apply_datadir_moves", lambda *a, **k: [])
    monkeypatch.setattr("deploy.cdvn_migrate._apply_charon_cluster_overlay", fake_overlay)
    monkeypatch.setattr("deploy.cdvn_migrate.import_cdvn_env_to_service", lambda *a, **k: None)
    monkeypatch.setattr(
        "deploy.cdvn_migrate.sync_charon_keyshares_to_vc",
        lambda *a, **k: {"status": "skipped"},
    )

    from deploy.cdvn_migrate import run_migration

    run_migration(str(root), skip_deploy=True, apply_moves=[])
    assert overlay_calls == [str(root)]


def test_runtime_path_exists_uses_sudo_for_root_owned_file(tmp_path, monkeypatch):
    target = tmp_path / "cluster-lock.json"
    target.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("deploy.cdvn_migrate.os.path.isfile", lambda _path: False)

    def fake_run(args, **kwargs):
        check = kwargs.get("check", True)
        rc = 0 if args[:3] == ["sudo", "test", "-f"] else 1
        class _Result:
            returncode = rc

        result = _Result()
        if check and rc != 0:
            raise subprocess.CalledProcessError(rc, args)
        return result

    monkeypatch.setattr("deploy.cdvn_migrate.subprocess.run", fake_run)
    from deploy.cdvn_migrate import _runtime_path_exists

    assert _runtime_path_exists(str(target)) is True


def test_grafana_port_from_env():
    assert grafana_port_from_env({"MONITORING_PORT_GRAFANA": "3701"}) == 3701
    assert grafana_port_from_env({}) is None
    assert grafana_port_from_env({"MONITORING_PORT_GRAFANA": "0"}) is None


def test_grafana_ini_http_port_rewrite():
    content = "[server]\nhttp_port = 3000\ndomain = localhost\n"
    updated = _grafana_ini_with_http_port(content, 3701)
    assert "http_port = 3701" in updated
    assert "http_port = 3000" not in updated
