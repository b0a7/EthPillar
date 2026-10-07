import json
from unittest.mock import patch

from tests.integration.latest_snapshot import clear_snapshot, write_snapshot


def test_write_and_clear_snapshot(tmp_path):
    snapshot_path = tmp_path / "latest.json"

    def fake_release_info(client, tag="LATEST", network=None):
        return {"version": "v8.1.3" if client == "Lighthouse" else "v2.3.0"}

    with patch("tests.integration.latest_snapshot._release_info_module") as module_factory:
        module_factory.return_value = fake_release_info
        written = write_snapshot(str(snapshot_path))

    assert written["lighthouse"] == "v8.1.3"
    assert written["reth"] == "v2.3.0"
    on_disk = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert on_disk["lighthouse"] == "v8.1.3"

    clear_snapshot(str(snapshot_path))
    assert not snapshot_path.exists()


def test_write_snapshot_passes_network_for_remap(tmp_path):
    snapshot_path = tmp_path / "latest.json"
    seen = {}

    def fake_release_info(client, tag="LATEST", network=None):
        seen[client.lower()] = network
        if client.lower() == "lighthouse" and (network or "").lower() == "sepolia":
            return {"version": "v8.3.0-rc.0"}
        if client.lower() == "lighthouse":
            return {"version": "v8.2.3"}
        return {"version": "v1.0.0"}

    with patch("tests.integration.latest_snapshot._release_info_module") as module_factory:
        module_factory.return_value = fake_release_info
        written = write_snapshot(str(snapshot_path), network="SEPOLIA")

    assert seen["lighthouse"] == "SEPOLIA"
    assert written["lighthouse"] == "v8.3.0-rc.0"
