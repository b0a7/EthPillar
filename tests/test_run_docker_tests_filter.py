"""Unit tests for integration matrix --filter selection."""
from pathlib import Path

from tests.integration.run_docker_tests import filter_tasks, generate_tests

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_sepolia_nimbus_filter_is_initial_install_only():
    tasks = filter_tasks(generate_tests(), ["Nimbus-Nethermind_SEPOLIA"])
    assert [t.log_name for t in tasks] == ["Nimbus-Nethermind_SEPOLIA"]
    cmd = tasks[0].cmd
    assert "--combo \"Nimbus-Nethermind\"" in cmd
    assert "--network SEPOLIA" in cmd
    assert "Full Node Only" in cmd
    assert "--test-updates" not in cmd
    assert "--test-switching" not in cmd
    assert "--test-epbs" not in cmd


def test_nimbus_nethermind_filter_excludes_upgrade_and_switch():
    names = [t.log_name for t in filter_tasks(generate_tests(), ["Nimbus-Nethermind"])]
    assert names == ["Nimbus-Nethermind_HOODI", "Nimbus-Nethermind_SEPOLIA"]


def test_filter_is_case_insensitive_and_matches_label():
    tasks = filter_tasks(generate_tests(), ["upgrade-nethermind-nimbus"])
    assert [t.label for t in tasks] == ["Upgrade-Nethermind-Nimbus"]


def test_empty_needles_return_full_matrix():
    all_tasks = generate_tests()
    assert len(filter_tasks(all_tasks, [])) == len(all_tasks)
    assert len(filter_tasks(all_tasks, ["", "  "])) == len(all_tasks)


def test_unmatched_filter_returns_empty():
    assert filter_tasks(generate_tests(), ["no-such-client-combo"]) == []


def test_ci_integration_defaults_nimbus_sepolia_filter():
    text = (_REPO_ROOT / ".github/workflows/ci-integration.yml").read_text(encoding="utf-8")
    assert "default: Nimbus-Nethermind_SEPOLIA" in text
    assert "filter: ${{ inputs.filter }}" in text


def test_reusable_integration_workflow_forwards_filter():
    text = (_REPO_ROOT / ".github/workflows/integration-test.yml").read_text(encoding="utf-8")
    assert "INTEGRATION_FILTER:" in text
    assert "--filter" in text
    assert 'default: ""' in text
