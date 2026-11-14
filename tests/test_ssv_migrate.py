"""Tests for deploy/ssv_migrate discovery, YAML parse, and dry-run apply."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from deploy.ssv_migrate import (
    classify_unit_name,
    discover_units,
    parse_dkg_config,
    parse_node_config,
    plan_ssv_migration,
    quarantine_unit,
    run_ssv_migration,
    versions_match,
    _config_from_exec,
    _first_token,
    _parse_unit_file,
    _resolve_path,
    _yaml_scalar_or_list,
)


def test_classify_unit_name_variants():
    assert classify_unit_name("ssvnode.service") == "node"
    assert classify_unit_name("ssv_node.service") == "node"
    assert classify_unit_name("SSV.service") == "node"
    assert classify_unit_name("ssvdkg.service") == "dkg"
    assert classify_unit_name("ssv_dkg.service") == "dkg"
    assert classify_unit_name("ssv-dkg.service") is None  # EthPillar target
    assert classify_unit_name("ssv-node.service") is None  # EthPillar target
    assert classify_unit_name("something.service") is None


def test_parse_unit_and_exec_helpers():
    text = """
[Unit]
Description=SSV
[Service]
WorkingDirectory=/home/u/ssv
ExecStart=/home/u/ssv/bin/ssvnode start-node --config=/home/u/ssv/config/config.yaml
"""
    fields = _parse_unit_file(text)
    assert fields["WorkingDirectory"] == "/home/u/ssv"
    assert _first_token(fields["ExecStart"]).endswith("/ssvnode")
    assert _config_from_exec(
        fields["ExecStart"], flags=("--config",)
    ) == "/home/u/ssv/config/config.yaml"
    assert (
        _resolve_path("data/db", workdir="/home/u/ssv", unit_dir="/etc/systemd/system")
        == "/home/u/ssv/data/db"
    )


def test_yaml_multi_endpoint_scalar():
    content = """
eth2:
  BeaconNodeAddr: http://a:5052,http://b:5052,http://c:5052
eth1:
  ETH1Addr: ws://a:8546,ws://b:8546,ws://c:8546
"""
    assert (
        _yaml_scalar_or_list(content, "BeaconNodeAddr")
        == "http://a:5052,http://b:5052,http://c:5052"
    )
    assert _yaml_scalar_or_list(content, "ETH1Addr").count("ws://") == 3


def test_yaml_block_list_joined():
    content = """
eth2:
  BeaconNodeAddr:
    - http://a:5052
    - http://b:5052
    - http://c:5052
"""
    assert _yaml_scalar_or_list(content, "BeaconNodeAddr") == (
        "http://a:5052,http://b:5052,http://c:5052"
    )


def test_parse_node_and_dkg_config_multi():
    node = """
global:
  LogLevel: info
db:
  Path: /home/u/ssv/data/db
ssv:
  Network: hoodi
eth2:
  BeaconNodeAddr: http://1:5052,http://2:5052,http://3:5052
eth1:
  ETH1Addr: ws://1:8546,ws://2:8546,ws://3:8546
p2p:
  TcpPort: 13001
  UdpPort: 12001
  HostAddress: 203.0.113.9
KeyStore:
  PrivateKeyFile: /home/u/ssv/encrypted_private_key.json
  PasswordFile: /home/u/ssv/password
MetricsAPIPort: 15000
SSVAPIPort: 16000
"""
    parsed = parse_node_config(node)
    assert parsed["eth_network"] == "hoodi"
    assert parsed["beacon_node_addr"].count("http://") == 3
    assert parsed["eth1_ws_addr"].count("ws://") == 3
    assert parsed["p2p_external_ip"] == "203.0.113.9"
    assert parsed["db_path"] == "/home/u/ssv/data/db"

    dkg = """
privKey: /home/u/ssv/encrypted_private_key.json
privKeyPassword: /home/u/ssv/password
operatorID: 42
port: 3030
ethEndpointURL: http://1:8545,http://2:8545,http://3:8545
outputPath: /home/u/ssv-dkg/data
"""
    dparsed = parse_dkg_config(dkg)
    assert dparsed["operator_id"] == "42"
    assert dparsed["eth1_http_addr"].count("http://") == 3
    assert dparsed["dkg_port"] == 3030


def test_versions_match_normalizes_v_prefix():
    assert versions_match("v2.5.1", "2.5.1")
    assert versions_match("v2.5.1", "v2.5.1")
    assert not versions_match("v2.5.0", "v2.5.1")
    assert not versions_match("", "v2.5.1")


def test_eth1_ws_to_http_preserves_semicolon_list():
    from deploy.ssv_migrate import _eth1_ws_to_http

    ws = "ws://a:8546;ws://b:8546;ws://c:8546"
    assert _eth1_ws_to_http(ws) == "http://a:8545;http://b:8545;http://c:8545"


def test_parse_cli_flags_from_run_ssv_dkg_wrapper(tmp_path: Path):
    from deploy.ssv_migrate import _dkg_settings_from_wrapper, _parse_cli_flags

    script = tmp_path / "run_ssv-dkg.sh"
    (tmp_path / "data").mkdir()
    (tmp_path / "ssl").mkdir()
    (tmp_path / "data" / "encrypted_private_key.json").write_text("{}", encoding="utf-8")
    (tmp_path / "data" / "password").write_text("pw", encoding="utf-8")
    (tmp_path / "ssl" / "tls.crt").write_text("cert", encoding="utf-8")
    (tmp_path / "ssl" / "tls.key").write_text("key", encoding="utf-8")
    script.write_text(
        """#!/bin/bash
./bin/ssv-dkg start-operator \\
            --privKey ./data/encrypted_private_key.json  \\
            --privKeyPassword ./data/password \\
            --operatorID 42 \\
            --port 3030 \\
            --outputPath ./operator-config/output \\
            --serverTLSCertPath ./ssl/tls.crt \\
            --serverTLSKeyPath ./ssl/tls.key \\
            --ethEndpointURL http://100.118.113.11:8545
""",
        encoding="utf-8",
    )
    flags = _parse_cli_flags(script.read_text(encoding="utf-8"))
    assert flags["operatorID"] == "42"
    assert flags["ethEndpointURL"] == "http://100.118.113.11:8545"
    settings = _dkg_settings_from_wrapper(str(script), workdir=str(tmp_path))
    assert settings["operator_id"] == "42"
    assert settings["tls_cert_path"].endswith("ssl/tls.crt")
    assert settings["eth1_http_addr"] == "http://100.118.113.11:8545"


def test_relative_db_path_resolves_via_config_dir(tmp_path: Path):
    from deploy.ssv_migrate import _resolve_path

    cfg_dir = tmp_path / "ssv" / "config"
    db = tmp_path / "ssv" / "data" / "db"
    db.mkdir(parents=True)
    cfg_dir.mkdir(parents=True)
    resolved = _resolve_path(
        "./data/db",
        bases=[str(cfg_dir), str(tmp_path / "ssv")],
    )
    assert resolved == str(db)


def test_config_from_script_skips_elf_binary(tmp_path: Path):
    from deploy.ssv_migrate import _config_from_script

    elf = tmp_path / "ssvnode"
    elf.write_bytes(b"\x7fELF" + b"\xb7" * 64)
    assert _config_from_script(str(elf)) == ""


def _write_legacy_stack(tmp_path: Path) -> Path:
    """Create a mini legacy ssvnode + ssvdkg layout under tmp_path."""
    root = tmp_path / "home"
    ssv = root / "ssv"
    dkg = root / "ssv-dkg"
    (ssv / "bin").mkdir(parents=True)
    (ssv / "config").mkdir()
    (ssv / "data" / "db").mkdir(parents=True)
    (dkg / "bin").mkdir(parents=True)
    (dkg / "operator-config").mkdir(parents=True)
    (dkg / "data").mkdir()

    (ssv / "encrypted_private_key.json").write_text('{"key":1}', encoding="utf-8")
    (ssv / "password").write_text("secret-pass", encoding="utf-8")
    (ssv / "data" / "db" / "marker").write_text("db", encoding="utf-8")
    (ssv / "bin" / "ssvnode").write_text("#!/bin/true\n", encoding="utf-8")
    (ssv / "bin" / "ssvnode").chmod(0o755)
    (dkg / "bin" / "ssv-dkg").write_text("#!/bin/true\n", encoding="utf-8")
    (dkg / "bin" / "ssv-dkg").chmod(0o755)

    (ssv / "config" / "config.yaml").write_text(
        f"""
db:
  Path: {ssv / "data" / "db"}
ssv:
  Network: sepolia
eth2:
  BeaconNodeAddr: http://el1:5052,http://el2:5052,http://el3:5052
eth1:
  ETH1Addr: ws://el1:8546,ws://el2:8546,ws://el3:8546
p2p:
  TcpPort: 13001
  UdpPort: 12001
KeyStore:
  PrivateKeyFile: {ssv / "encrypted_private_key.json"}
  PasswordFile: {ssv / "password"}
MetricsAPIPort: 15000
SSVAPIPort: 16000
""",
        encoding="utf-8",
    )
    (dkg / "operator-config" / "operator.yaml").write_text(
        f"""
privKey: {ssv / "encrypted_private_key.json"}
privKeyPassword: {ssv / "password"}
operatorID: 99
port: 3030
ethEndpointURL: http://el1:8545,http://el2:8545,http://el3:8545
outputPath: {dkg / "data"}
""",
        encoding="utf-8",
    )

    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    (unit_dir / "ssvnode.service").write_text(
        f"""
[Service]
WorkingDirectory={ssv}
ExecStart={ssv / "bin" / "ssvnode"} start-node --config={ssv / "config" / "config.yaml"}
""",
        encoding="utf-8",
    )
    (unit_dir / "ssvdkg.service").write_text(
        f"""
[Service]
WorkingDirectory={dkg}
ExecStart={dkg / "bin" / "ssv-dkg"} start-operator --configPath={dkg / "operator-config" / "operator.yaml"}
""",
        encoding="utf-8",
    )
    return unit_dir


def test_discover_units(tmp_path: Path):
    unit_dir = _write_legacy_stack(tmp_path)
    node, dkg = discover_units(str(unit_dir))
    assert node is not None and node.unit_name == "ssvnode.service"
    assert dkg is not None and dkg.unit_name == "ssvdkg.service"
    assert node.config_path.endswith("config.yaml")
    assert dkg.config_path.endswith("operator.yaml")


def test_discover_without_config_flags_uses_heuristics(tmp_path: Path):
    """Match home-dir layouts where ExecStart omits --config / uses a wrapper."""
    root = tmp_path / "home"
    ssv = root / "ssv"
    dkg = root / "ssv-dkg"
    (ssv / "bin").mkdir(parents=True)
    (ssv / "config").mkdir()
    (dkg / "bin").mkdir(parents=True)
    (dkg / "operator-config").mkdir(parents=True)
    (ssv / "bin" / "ssvnode").write_text("x", encoding="utf-8")
    (dkg / "bin" / "ssv-dkg").write_text("x", encoding="utf-8")
    (ssv / "config" / "config.yaml").write_text("ssv:\n  Network: mainnet\n", encoding="utf-8")
    (dkg / "operator-config" / "op.yaml").write_text("operatorID: 1\n", encoding="utf-8")
    wrapper = dkg / "run_ssv-dkg.sh"
    wrapper.write_text(
        "#!/bin/bash\nexec ./bin/ssv-dkg start-operator --configPath=./operator-config/op.yaml\n",
        encoding="utf-8",
    )
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    (unit_dir / "ssvnode.service").write_text(
        f"[Service]\nWorkingDirectory={ssv}\nExecStart={ssv / 'bin' / 'ssvnode'} start-node\n",
        encoding="utf-8",
    )
    (unit_dir / "ssvdkg.service").write_text(
        f"[Service]\nWorkingDirectory={dkg}\nExecStart={wrapper}\n",
        encoding="utf-8",
    )
    node, dkg_u = discover_units(str(unit_dir))
    assert node is not None
    assert node.config_path.endswith("config/config.yaml")
    assert dkg_u is not None
    assert dkg_u.config_path.endswith("op.yaml")
    assert dkg_u.binary_path.endswith("ssv-dkg")


@patch("deploy.ssv_migrate.get_dkg_release_info", return_value={"version": "v2.0.0"})
@patch("deploy.ssv_migrate.get_release_info", return_value={"version": "v2.5.1"})
@patch("deploy.ssv_migrate.probe_binary_version", return_value="v2.5.1")
def test_plan_multi_endpoints(mock_probe, mock_rel, mock_dkg, tmp_path: Path):
    unit_dir = _write_legacy_stack(tmp_path)
    plan = plan_ssv_migration(unit_dir=str(unit_dir))
    assert not plan.errors
    assert plan.beacon_node_addr.count("http://") == 3
    assert plan.eth1_ws_addr.count("ws://") == 3
    assert plan.eth1_http_addr.count("http://") == 3
    assert plan.operator_id == "99"
    assert plan.node_binary is not None
    assert plan.node_binary.action == "copy"
    assert plan.dkg_binary is not None
    summary = plan.summary()
    assert "sepolia" in summary
    # BN/EL appear before unit blocks so whiptail shows them without scrolling.
    assert summary.index("BeaconNode:") < summary.index("Node unit:")
    assert summary.index("ETH1 WS:") < summary.index("Node unit:")


@patch("deploy.ssv_migrate.get_dkg_release_info", return_value={"version": "v2.0.0"})
@patch("deploy.ssv_migrate.get_release_info", return_value={"version": "v2.5.1"})
@patch("deploy.ssv_migrate.probe_binary_version", return_value="v2.5.1")
def test_plan_cli_wrapper_ignores_debug_log(mock_probe, mock_rel, mock_dkg, tmp_path: Path):
    """run_ssv-dkg.sh + operator-config/debug.log must not be treated as YAML config."""
    root = tmp_path / "home"
    ssv = root / "ssv"
    dkg = root / "ssv-dkg"
    (ssv / "bin").mkdir(parents=True)
    (ssv / "config").mkdir()
    (ssv / "data" / "db").mkdir(parents=True)
    (dkg / "bin").mkdir(parents=True)
    (dkg / "operator-config" / "output").mkdir(parents=True)
    (dkg / "data").mkdir()
    (dkg / "ssl").mkdir()
    (ssv / "encrypted_private_key.json").write_text('{"key":1}', encoding="utf-8")
    (ssv / "password").write_text("secret-pass", encoding="utf-8")
    (ssv / "data" / "db" / "marker").write_text("db", encoding="utf-8")
    (ssv / "bin" / "ssvnode").write_text("#!/bin/true\n", encoding="utf-8")
    (ssv / "bin" / "ssvnode").chmod(0o755)
    (dkg / "bin" / "ssv-dkg").write_text("#!/bin/true\n", encoding="utf-8")
    (dkg / "bin" / "ssv-dkg").chmod(0o755)
    (dkg / "ssl" / "tls.crt").write_text("cert", encoding="utf-8")
    (dkg / "ssl" / "tls.key").write_text("key", encoding="utf-8")
    # Upstream DKG layout: log under operator-config/, no operator.yaml.
    (dkg / "operator-config" / "debug.log").write_text(
        '{"level":"info","msg":"started"}\n', encoding="utf-8"
    )
    (ssv / "config" / "config.yaml").write_text(
        f"""
db:
  Path: {ssv / "data" / "db"}
ssv:
  Network: mainnet
eth2:
  BeaconNodeAddr: http://100.1.1.1:5052;http://100.1.1.2:5052
eth1:
  ETH1Addr: ws://100.1.1.1:8546;ws://100.1.1.2:8546
KeyStore:
  PrivateKeyFile: {ssv / "encrypted_private_key.json"}
  PasswordFile: {ssv / "password"}
""",
        encoding="utf-8",
    )
    wrapper = dkg / "run_ssv-dkg.sh"
    wrapper.write_text(
        f"""#!/bin/bash
./bin/ssv-dkg start-operator \\
            --privKey ./data/encrypted_private_key.json \\
            --privKeyPassword ./data/password \\
            --operatorID 77 \\
            --port 3030 \\
            --logFilePath ./operator-config/debug.log \\
            --outputPath ./operator-config/output \\
            --serverTLSCertPath ./ssl/tls.crt \\
            --serverTLSKeyPath ./ssl/tls.key \\
            --ethEndpointURL http://100.118.113.11:8545
""",
        encoding="utf-8",
    )
    (dkg / "data" / "encrypted_private_key.json").write_text('{"key":1}', encoding="utf-8")
    (dkg / "data" / "password").write_text("secret-pass", encoding="utf-8")
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    (unit_dir / "ssvnode.service").write_text(
        f"[Service]\nWorkingDirectory={ssv}\n"
        f"ExecStart={ssv / 'bin' / 'ssvnode'} start-node "
        f"--config={ssv / 'config' / 'config.yaml'}\n",
        encoding="utf-8",
    )
    (unit_dir / "ssvdkg.service").write_text(
        f"[Service]\nWorkingDirectory={dkg}\nExecStart={wrapper}\n",
        encoding="utf-8",
    )

    plan = plan_ssv_migration(unit_dir=str(unit_dir))
    assert not plan.errors
    assert plan.operator_id == "77"
    assert plan.eth1_http_addr == "http://100.118.113.11:8545"
    assert "100.1.1.1:5052" in plan.beacon_node_addr
    assert plan.tls_cert_path.endswith("ssl/tls.crt")
    assert plan.dkg_unit is not None
    assert "debug.log" not in (plan.dkg_unit.config_path or "")
    assert "CLI flags" in (plan.dkg_unit.config_path or "")
    summary = plan.summary()
    assert "BeaconNode:" in summary
    assert "ETH1 HTTP:" in summary
    assert summary.index("BeaconNode:") < summary.index("DKG unit:")


@patch("deploy.ssv_migrate.get_dkg_release_info", return_value={"version": "v2.0.0"})
@patch("deploy.ssv_migrate.get_release_info", return_value={"version": "v2.5.1"})
@patch("deploy.ssv_migrate.probe_binary_version", return_value="v2.4.0")
def test_plan_rebuild_when_older(mock_probe, mock_rel, mock_dkg, tmp_path: Path):
    unit_dir = _write_legacy_stack(tmp_path)
    plan = plan_ssv_migration(unit_dir=str(unit_dir))
    assert plan.node_binary is not None
    assert plan.node_binary.action == "rebuild"
    plan2 = plan_ssv_migration(unit_dir=str(unit_dir), allow_older_binary=True)
    assert plan2.node_binary is not None
    assert plan2.node_binary.action == "copy"


@patch("deploy.ssv_migrate.subprocess.run")
def test_quarantine_renames_to_bak(mock_run, tmp_path: Path):
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    unit = unit_dir / "ssvnode.service"
    unit.write_text("[Service]\nExecStart=/bin/true\n", encoding="utf-8")

    def _run(cmd, **kwargs):
        # Emulate sudo mv for quarantine.
        if len(cmd) >= 4 and cmd[1] == "mv":
            Path(cmd[3]).write_text(Path(cmd[2]).read_text(encoding="utf-8"), encoding="utf-8")
            Path(cmd[2]).unlink()
        return MagicMock(returncode=0)

    mock_run.side_effect = _run
    bak = quarantine_unit(str(unit), dry_run=False)
    assert bak.endswith(".service.bak")
    assert not unit.exists()
    assert Path(bak).exists()


@patch("deploy.ssv_migrate.subprocess.run", return_value=MagicMock(returncode=0))
@patch("deploy.ssv_migrate.get_dkg_release_info", return_value={"version": "v2.0.0"})
@patch("deploy.ssv_migrate.get_release_info", return_value={"version": "v2.5.1"})
@patch("deploy.ssv_migrate.probe_binary_version", return_value="v2.5.1")
def test_dry_run_does_not_quarantine(
    mock_probe, mock_rel, mock_dkg, mock_run, tmp_path: Path
):
    unit_dir = _write_legacy_stack(tmp_path)
    run_ssv_migration(dry_run=True, unit_dir=str(unit_dir))
    # Dry-run quarantine returns early before systemctl.
    assert (unit_dir / "ssvnode.service").exists()
    assert (unit_dir / "ssvdkg.service").exists()
    # No sudo systemctl stop should have been issued for quarantine.
    for call in mock_run.call_args_list:
        cmd = call.args[0] if call.args else []
        assert "stop" not in cmd


def test_copy_tree_uses_cp_not_rsync(tmp_path: Path):
    from deploy.ssv_migrate import _copy_tree

    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    (src / "a").write_text("x", encoding="utf-8")
    calls: list = []

    def _run(cmd, **kwargs):
        calls.append(list(cmd))
        # Emulate sudo mkdir/cp/chown for the test harness.
        if cmd[:2] == ["sudo", "mkdir"]:
            Path(cmd[-1]).mkdir(parents=True, exist_ok=True)
        elif cmd[:2] == ["sudo", "cp"]:
            # sudo cp -a src/. dest/
            import shutil

            shutil.copytree(src, dest, dirs_exist_ok=True)
        return MagicMock(returncode=0)

    with patch("deploy.ssv_migrate.subprocess.run", side_effect=_run):
        _copy_tree(str(src), str(dest))
    assert any(c[:2] == ["sudo", "cp"] for c in calls)
    assert not any("rsync" in c for c in calls)
