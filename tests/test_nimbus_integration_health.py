"""Unit tests for Nimbus first-start checkpoint-sync integration asserts."""
from tests.integration.nimbus_checkpoint_asserts import (
    journal_indicates_start_timeout,
    nimbus_checkpoint_db_ready,
    systemd_result_is_start_timeout,
    unit_uses_nimbus_checkpoint_sync,
)


def test_unit_uses_nimbus_checkpoint_sync_requires_both_markers():
    assert unit_uses_nimbus_checkpoint_sync(
        "ExecStart=/usr/local/bin/nimbus_beacon_node --network=sepolia\n"
        "ExecStartPre=/bin/bash -c 'nimbus_beacon_node trustedNodeSync ...'"
    )
    assert not unit_uses_nimbus_checkpoint_sync(
        "ExecStart=/usr/local/bin/nimbus_beacon_node --network=sepolia"
    )
    assert not unit_uses_nimbus_checkpoint_sync(
        "ExecStart=/usr/local/bin/lighthouse beacon trustedNodeSync"
    )


def test_systemd_result_is_start_timeout():
    assert systemd_result_is_start_timeout("timeout")
    assert systemd_result_is_start_timeout("timeout-abort")
    assert systemd_result_is_start_timeout("Timeout")
    assert not systemd_result_is_start_timeout("success")
    assert not systemd_result_is_start_timeout("exit-code")


def test_journal_indicates_start_timeout():
    assert journal_indicates_start_timeout(
        "Job for consensus.service failed because a timeout was exceeded."
    )
    assert journal_indicates_start_timeout("consensus.service: Failed with result 'timeout'.")
    assert journal_indicates_start_timeout("start-pre operation timed out")
    assert not journal_indicates_start_timeout("Nimbus beacon node started")
    assert not journal_indicates_start_timeout("peer request timed out; retrying")


def test_nimbus_checkpoint_db_ready(tmp_path):
    db = tmp_path / "nimbus" / "db"
    assert not nimbus_checkpoint_db_ready(str(db))
    db.mkdir(parents=True)
    assert nimbus_checkpoint_db_ready(str(db))
