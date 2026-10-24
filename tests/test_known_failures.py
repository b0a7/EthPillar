"""Unit tests for the known-failures registry and classification helpers."""

from __future__ import annotations

import json
from pathlib import Path

from tests.integration.known_failures import (
    NOTICE_PREFIX,
    classify_failed_cell,
    known_upstream_version_mismatch,
    load_registry,
    match_log_known_failure,
    parse_notice_lines,
    version_mismatch_is_known,
    write_ci_notices_artifact,
    CiNotice,
)


def test_registry_has_exactly_two_initial_entries():
    entries = load_registry()
    ids = sorted(e.id for e in entries)
    assert ids == [
        "grandine-2.0.6-reports-2.0.5",
        "nimbus-hoodi-genesis-buffer-limit",
    ]


def test_nimbus_hoodi_log_signature_matches():
    # Harness now puts the journal ERR on the ❌ summary line itself.
    log = (
        "❌ systemctl start consensus failed: "
        "Job for consensus.service failed because the control process exited "
        "with error code. | "
        'ERR Failed to obtain genesis state err="Could not read response headers: '
        'Buffer limit reached"\n'
    )
    entry = match_log_known_failure("Nimbus-Nethermind_HOODI", log)
    assert entry is not None
    assert entry.id == "nimbus-hoodi-genesis-buffer-limit"
    status, matched = classify_failed_cell("Nimbus-Nethermind_HOODI", log)
    assert status == "KNOWN_FAIL"
    assert matched is not None and matched.id == entry.id


def test_nimbus_hoodi_matches_from_failure_summary_alone(tmp_path: Path):
    """Known-failure matching must work from the ❌ summary, not only raw journal."""
    from tests.integration.run_docker_tests import extract_failure_reason
    from tests.integration.service_start import format_systemctl_start_failure

    journal = (
        'ERR Failed to obtain genesis state err="Could not read response headers: '
        'Buffer limit reached"\n'
    )
    summary = format_systemctl_start_failure(
        "consensus",
        stderr="Job for consensus.service failed.\n",
        journal_text=journal,
    )
    assert "Buffer limit reached" in summary
    log_path = tmp_path / "cell.log"
    log_path.write_text(f"Starting test...\n  ❌ {summary}\n", encoding="utf-8")
    assert "Buffer limit reached" in extract_failure_reason(str(log_path))
    status, matched = classify_failed_cell(
        "Nimbus-Nethermind_HOODI", log_path.read_text(encoding="utf-8")
    )
    assert status == "KNOWN_FAIL"
    assert matched is not None


def test_nimbus_hoodi_other_failure_stays_red():
    log = "❌ systemctl start consensus failed:\nERR something completely different\n"
    status, matched = classify_failed_cell("Nimbus-Nethermind_HOODI", log)
    assert status == "FAIL"
    assert matched is None


def test_unrelated_cell_not_classified_as_nimbus_known():
    log = "Could not read response headers: Buffer limit reached"
    status, matched = classify_failed_cell("Teku-Besu_SEPOLIA", log)
    assert status == "FAIL"
    assert matched is None


def test_grandine_version_mismatch_pair():
    assert known_upstream_version_mismatch("Grandine", "2.0.5", "2.0.6")
    assert known_upstream_version_mismatch("grandine", "v2.0.5", "v2.0.6")
    assert not known_upstream_version_mismatch("Grandine", "2.0.5", "2.0.7")
    assert not known_upstream_version_mismatch("Lighthouse", "2.0.5", "2.0.6")
    entry = version_mismatch_is_known("grandine", "2.0.5", "2.0.6")
    assert entry is not None
    assert "838" in entry.issue


def test_parse_notice_lines_and_artifact(tmp_path: Path):
    blob = "\n".join(
        [
            "noise",
            f'{NOTICE_PREFIX} {{"kind": "soft-skip", "status": "skipped", '
            f'"client": "reth", "reason": "no sane upgrade seed"}}',
            "more noise",
        ]
    )
    notices = parse_notice_lines(blob)
    assert len(notices) == 1
    assert notices[0].kind == "soft-skip"
    assert notices[0].client == "reth"
    out = write_ci_notices_artifact(
        [CiNotice(kind="soft-skip", status="skipped", client="reth", reason="x")],
        tmp_path / "ci-notices.json",
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["notices"][0]["client"] == "reth"
