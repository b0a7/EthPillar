"""Tests for manage.grafana ini/port helpers."""

from manage.grafana import (
    _grafana_ini_with_http_port,
    _parse_grafana_http_port_from_ini,
    read_grafana_http_port,
)


def test_grafana_ini_http_port_rewrite():
    content = "[server]\nhttp_port = 3000\ndomain = localhost\n"
    updated = _grafana_ini_with_http_port(content, 3701)
    assert "http_port = 3701" in updated
    assert "http_port = 3000" not in updated


def test_grafana_ini_http_port_rewrite_commented_default():
    content = "[server]\n;http_port = 3000\ndomain = localhost\n"
    updated = _grafana_ini_with_http_port(content, 3701)
    assert "http_port = 3701" in updated
    assert ";http_port = 3000" not in updated


def test_parse_grafana_http_port_from_ini():
    content = "[server]\n;http_port = 3000\nhttp_port = 3701\n"
    assert _parse_grafana_http_port_from_ini(content) == 3701
    assert read_grafana_http_port() == 3000  # no grafana.ini in test env
