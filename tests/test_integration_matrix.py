"""Tests for the Integration orchestrator matrix membership."""
from tests.integration.run_docker_tests import generate_tests


def test_nimbus_nethermind_hoodi_dropped_sepolia_kept():
    names = [t.log_name for t in generate_tests()]
    assert "Nimbus-Nethermind_HOODI" not in names
    assert "Nimbus-Nethermind_SEPOLIA" in names
    # Other HOODI combo cells and the HOODI VC-only custom case remain.
    assert "Lighthouse-Reth_HOODI" in names
    assert "Teku-Besu_HOODI" in names
    assert "Teku-VC-Only-HOODI" in names
