"""Tests for manage.client_monitoring scrape jobs and dashboard rewrites."""

from pathlib import Path

from manage.client_monitoring import (
    DashboardSpec,
    detect_clients,
    ensure_client_scrapes,
    metrics_port_from_unit,
    rewrite_dashboard,
    upsert_client_scrapes,
)

_BASE_YML = """\
rule_files:
  - alert.rules.yml

global:
  scrape_interval:     15s

scrape_configs:
   - job_name: 'ethereum-metrics-exporter'
     static_configs:
       - targets: ['localhost:9099']
   - job_name: 'node_exporter'
     static_configs:
       - targets: ['localhost:9100']
"""

_GETH_UNIT = """\
[Service]
ExecStart=/usr/local/bin/geth --mainnet --metrics --http --pprof
"""

_LIGHTHOUSE_BN = """\
[Service]
ExecStart=/usr/local/bin/lighthouse bn --metrics --metrics-address=127.0.0.1 --metrics-port=8008
"""

_LIGHTHOUSE_VC = """\
[Service]
ExecStart=/usr/local/bin/lighthouse vc --metrics --metrics-port=8009
"""

_RETH_UNIT = """\
[Service]
ExecStart=/usr/local/bin/reth node --metrics 127.0.0.1:6060
"""

_ERIGON_UNIT = """\
[Service]
ExecStart=/usr/local/bin/erigon --metrics --private.api.addr=127.0.0.1:9091
"""

_NETHERMIND_UNIT = """\
[Service]
ExecStart=/usr/local/bin/nethermind/nethermind --Metrics.Enabled true --Metrics.ExposePort 6060
"""


def test_detect_geth_lighthouse_ports():
    found = detect_clients(_GETH_UNIT, _LIGHTHOUSE_BN, _LIGHTHOUSE_VC)
    jobs = [(client.job_name, port, client.metrics_path, client.instance) for client, port in found]
    assert jobs == [
        ("geth", 6060, "/debug/metrics/prometheus", "execution"),
        ("lighthouse", 8008, "/metrics", "consensus"),
        ("lighthouse_validator", 8009, "/metrics", "validator"),
    ]


def test_reth_and_nethermind_ports_do_not_cross_match():
    reth = detect_clients(_RETH_UNIT, None, None)
    assert reth[0][0].job_name == "reth"
    assert reth[0][1] == 6060
    nethermind = detect_clients(_NETHERMIND_UNIT, None, None)
    assert nethermind[0][0].job_name == "nethermind"
    assert nethermind[0][0].metrics_path == "/metrics"
    assert metrics_port_from_unit(_ERIGON_UNIT, 6060) == 6060


def test_upsert_appends_once_and_replaces_managed_block():
    clients = detect_clients(_GETH_UNIT, _LIGHTHOUSE_BN, None)
    updated, changed = upsert_client_scrapes(_BASE_YML, clients)
    assert changed is True
    assert "job_name: 'geth'" in updated
    assert "metrics_path: /debug/metrics/prometheus" in updated
    assert "instance: execution" in updated
    assert "job_name: 'lighthouse'" in updated
    assert "instance: consensus" in updated
    assert updated.count("job_name: 'geth'") == 1

    again, changed_again = upsert_client_scrapes(updated, clients)
    assert changed_again is False
    assert again.count("BEGIN ethpillar client scrape jobs") == 1

    geth_only = detect_clients(_GETH_UNIT, None, None)
    replaced, changed_replaced = upsert_client_scrapes(updated, geth_only)
    assert changed_replaced is True
    assert "job_name: 'lighthouse'" not in replaced
    assert "job_name: 'ethereum-metrics-exporter'" in replaced


def test_upsert_keeps_hand_edited_job_outside_block():
    custom = _BASE_YML + "   - job_name: 'geth'\n     static_configs:\n       - targets: ['localhost:6061']\n"
    clients = detect_clients(_GETH_UNIT, None, None)
    updated, _changed = upsert_client_scrapes(custom, clients)
    assert updated.count("job_name: 'geth'") == 1
    assert "localhost:6061" in updated
    assert "BEGIN ethpillar client scrape jobs" in updated


def test_ensure_client_scrapes_writes_file(tmp_path: Path):
    yml = tmp_path / "prometheus.yml"
    yml.write_text(_BASE_YML, encoding="utf-8")
    clients = detect_clients(_GETH_UNIT, None, None)
    assert ensure_client_scrapes(yml, clients) is True
    assert ensure_client_scrapes(yml, clients) is False
    assert "localhost:6060" in yml.read_text(encoding="utf-8")


def test_rewrite_dashboard_datasource_and_lodestar():
    spec = DashboardSpec(
        filename="lodestar_summary.json",
        title="Lodestar Dashboard",
        lodestar_instances=True,
        instance_label="execution",
    )
    payload = {
        "title": "old",
        "templating": {"list": [{}, {}, {}, {"query": "job"}, {"query": "job"}]},
        "panels": [{"datasource": "${DS_PROMETHEUS}", "expr": "${VAR_INSTANCE_LABEL}"}],
    }
    rewritten = rewrite_dashboard(payload, spec)
    assert rewritten["title"] == "Lodestar Dashboard"
    assert rewritten["panels"][0]["datasource"] == "Prometheus"
    assert rewritten["panels"][0]["expr"] == "execution"
    assert rewritten["templating"]["list"][3]["query"] == "consensus"
    assert rewritten["templating"]["list"][4]["query"] == "validator"
