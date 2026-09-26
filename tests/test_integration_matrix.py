"""Tests for the Integration orchestrator matrix membership."""
from tests.integration.run_docker_tests import (
    combos,
    generate_tests,
    sepolia_only_combos,
)


def test_nimbus_nethermind_is_sepolia_only_not_a_hoodi_combo():
    """HOODI Nimbus is omitted from the combo source, not filtered after the fact."""
    assert "Nimbus-Nethermind" not in combos
    assert "Nimbus-Nethermind" in sepolia_only_combos
    names = [t.log_name for t in generate_tests()]
    assert "Nimbus-Nethermind_HOODI" not in names
    assert "Nimbus-Nethermind_SEPOLIA" in names
    # Other HOODI combo cells and the HOODI VC-only custom case remain.
    assert "Lighthouse-Reth_HOODI" in names
    assert "Teku-Besu_HOODI" in names
    assert "Teku-VC-Only-HOODI" in names
