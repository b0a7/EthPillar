"""Tests for manage.charon_monitoring Prometheus scrape + dashboard helpers."""

from pathlib import Path
from unittest.mock import patch

from manage.charon_monitoring import (
    CDVN_GRAFANA_DASHBOARDS,
    CHARON_SCRAPE_JOB,
    charon_scrape_job,
    ensure_charon_scrape,
    has_charon_scrape,
    provision_cdvn_grafana_dashboards,
    provision_charon_monitoring,
    provision_charon_overview_dashboard,
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


def test_has_charon_scrape_detects_job():
    assert has_charon_scrape(_BASE_YML) is False
    assert has_charon_scrape(_BASE_YML + CHARON_SCRAPE_JOB) is True
    assert has_charon_scrape('job_name: "charon"') is True


def test_ensure_charon_scrape_appends_once(tmp_path: Path):
    yml = tmp_path / "prometheus.yml"
    yml.write_text(_BASE_YML, encoding="utf-8")

    assert ensure_charon_scrape(yml, metrics_port=3620) is True
    text = yml.read_text(encoding="utf-8")
    assert "job_name: 'charon'" in text
    assert "localhost:3620" in text

    assert ensure_charon_scrape(yml, metrics_port=3620) is False
    assert yml.read_text(encoding="utf-8").count("job_name: 'charon'") == 1


def test_ensure_charon_scrape_custom_port(tmp_path: Path):
    yml = tmp_path / "prometheus.yml"
    yml.write_text(_BASE_YML, encoding="utf-8")
    assert ensure_charon_scrape(yml, metrics_port=3700) is True
    text = yml.read_text(encoding="utf-8")
    assert "localhost:3700" in text
    assert charon_scrape_job(3700) in text
    assert ensure_charon_scrape(yml, metrics_port=3800) is True
    assert "localhost:3800" in yml.read_text(encoding="utf-8")


def test_ensure_charon_scrape_missing_file(tmp_path: Path):
    assert ensure_charon_scrape(tmp_path / "missing.yml") is False


def test_provision_dashboard_writes_file(tmp_path: Path):
    dash_dir = tmp_path / "dashboards"
    payload = b'{"uid":"charon_overview","title":"Charon Overview"}'

    def _fake_download(dest: Path, url: str = "") -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)

    with patch(
        "manage.charon_monitoring.download_charon_overview_dashboard",
        side_effect=_fake_download,
    ):
        assert provision_charon_overview_dashboard(dash_dir) is True
        assert (dash_dir / "charon_overview_dashboard.json").read_bytes() == payload


def test_provision_cdvn_dashboard_bundle(tmp_path: Path):
    dash_dir = tmp_path / "dashboards"
    seen: list[str] = []

    def _fake_download(dest: Path, url: str) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        seen.append(dest.name)
        dest.write_bytes(b'{"uid":"x"}')

    with patch("manage.charon_monitoring.download_charon_dashboard", side_effect=_fake_download):
        count = provision_cdvn_grafana_dashboards(dash_dir)
    assert count == 4
    assert seen == list(CDVN_GRAFANA_DASHBOARDS)


def test_provision_charon_monitoring_noop_without_stack(tmp_path: Path):
    results = provision_charon_monitoring(
        prometheus_yml=tmp_path / "nope.yml",
        grafana_dashboards=tmp_path / "nope" / "dashboards",
        datasources_yml=tmp_path / "nope" / "datasources.yml",
        restart=False,
    )
    assert results == {"scrape": False, "dashboard": False, "datasource": False}


def test_provision_charon_monitoring_scrape_only(tmp_path: Path):
    yml = tmp_path / "prometheus.yml"
    yml.write_text(_BASE_YML, encoding="utf-8")
    results = provision_charon_monitoring(
        prometheus_yml=yml,
        grafana_dashboards=tmp_path / "missing" / "dashboards",
        datasources_yml=tmp_path / "missing" / "datasources.yml",
        restart=False,
    )
    assert results["scrape"] is True
    assert results["dashboard"] is False
    assert has_charon_scrape(yml.read_text(encoding="utf-8"))
