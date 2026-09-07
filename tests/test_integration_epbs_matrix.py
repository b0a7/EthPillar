"""Contract: one live-binary ePBS path per supported VC, no duplicate deploys."""

from __future__ import annotations

from tests.integration.run_docker_tests import generate_tests

SUPPORTED_EPBS_VCS = frozenset({"Prysm", "Lodestar", "Lighthouse", "Teku", "Nimbus"})


def _vc_for_epbs_task(label: str, cmd: str) -> str:
    """Map an attached ePBS matrix row to the VC it exercises."""
    if "Prysm" in label or "--vc Prysm" in cmd or "--cc Prysm" in cmd:
        return "Prysm"
    if "Lodestar" in label or "Lodestar-Besu" in cmd:
        return "Lodestar"
    if "Lighthouse" in label or "Lighthouse-Reth" in cmd:
        return "Lighthouse"
    if "Teku" in label or "Teku-Besu" in cmd:
        return "Teku"
    if "Nimbus" in label or "Nimbus-Nethermind" in cmd:
        return "Nimbus"
    raise AssertionError(f"cannot infer ePBS VC from label={label!r} cmd={cmd!r}")


def test_one_live_binary_epbs_path_per_supported_vc() -> None:
    """ePBS piggybacks on existing VC+MEV deploys; no dedicated duplicate cases."""
    tasks = generate_tests()
    epbs_tasks = [t for t in tasks if "--test-epbs" in t.cmd]

    assert {t.label for t in epbs_tasks} == {
        "Prysm-Reth-Custom-Setup-SEPOLIA",
        "Lighthouse-Reth",
        "Lodestar-Besu",
        "Teku-Besu",
        "Nimbus-Nethermind",
    }
    assert { _vc_for_epbs_task(t.label, t.cmd) for t in epbs_tasks } == SUPPORTED_EPBS_VCS
    assert all("ePBS-Migration" not in t.label for t in tasks)

    for task in tasks:
        if "Full Node Only" in task.cmd or "--charon" in task.cmd:
            assert "--test-epbs" not in task.cmd, task.label
        if task.label == "Caplin-Erigon":
            assert "--test-epbs" not in task.cmd


def test_combo_epbs_only_on_solo_staking_mev_rows() -> None:
    """Full Node Only combo rows stay deploy-only; Solo Staking hosts ePBS."""
    tasks = generate_tests()
    combo_epbs = [
        t for t in tasks
        if t.label in {"Lighthouse-Reth", "Lodestar-Besu", "Teku-Besu", "Nimbus-Nethermind"}
    ]
    solo = [t for t in combo_epbs if "Solo Staking" in t.cmd]
    full = [t for t in combo_epbs if "Full Node Only" in t.cmd]
    assert len(solo) == 4
    assert len(full) == 4
    assert all("--test-epbs" in t.cmd for t in solo)
    assert all("--test-epbs" not in t.cmd for t in full)
    assert all("-ePBS" in t.log_name for t in solo)
