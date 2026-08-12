"""Tests for deploy/charon.py beacon-endpoint patching."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deploy.charon import (
    generate_charon_service,
    patch_beacon_endpoints,
    scrape_beacon_endpoints,
)

CHARON_UNIT = generate_charon_service("mainnet", "http://127.0.0.1:5052", builder_api=True)


def test_scrape_beacon_endpoints():
    assert scrape_beacon_endpoints(CHARON_UNIT) == "http://127.0.0.1:5052"


def test_patch_beacon_endpoints_updates_url(tmp_path):
    service_path = tmp_path / "charon.service"
    service_path.write_text(CHARON_UNIT, encoding="utf-8")
    assert patch_beacon_endpoints(str(service_path), "http://192.168.1.20:5052")
    updated = service_path.read_text(encoding="utf-8")
    assert "--beacon-node-endpoints=http://192.168.1.20:5052" in updated
    assert "http://127.0.0.1:5052" not in updated
    assert "--builder-api" in updated


def test_patch_beacon_endpoints_no_change(tmp_path):
    service_path = tmp_path / "charon.service"
    service_path.write_text(CHARON_UNIT, encoding="utf-8")
    assert patch_beacon_endpoints(str(service_path), "http://127.0.0.1:5052") is False


def test_patch_beacon_endpoints_missing_file():
    with pytest.raises(FileNotFoundError):
        patch_beacon_endpoints("/tmp/does-not-exist-charon.service", "http://127.0.0.1:5052")
