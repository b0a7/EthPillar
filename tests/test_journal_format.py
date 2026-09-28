"""Tests for the journald log line formatter."""

import json
from datetime import datetime

from manage.journal_format import (
    LABEL_WIDTH,
    ClientNames,
    format_client_id,
    format_entry_lines,
    format_realtime_timestamp,
    strip_client_timestamp,
)


def test_format_realtime_timestamp_is_local_with_milliseconds():
    us = 1_695_840_781_105_000
    text = format_realtime_timestamp(str(us))
    seconds, _rem = divmod(us, 1_000_000)
    assert text == datetime.fromtimestamp(seconds).strftime("%Y-%m-%d %H:%M:%S") + ".105"
    assert format_realtime_timestamp(None) == " " * 23
    assert format_realtime_timestamp("nope") == " " * 23


def test_format_client_id_pads_short_labels_and_keeps_long_ones():
    short = format_client_id("execution", "Reth")
    assert short == "execution [Reth]".ljust(LABEL_WIDTH)
    assert len(short) == LABEL_WIDTH
    long = format_client_id("csm_nimbusvalidator", "Nimbus")
    assert long == "csm_nimbusvalidator [Nimbus]"
    assert len(long) > LABEL_WIDTH
    assert format_client_id("grafana-server", "") == "grafana-server".ljust(LABEL_WIDTH)


def test_strip_client_timestamp_shapes():
    cases = [
        (
            "INFO [09-27|21:53:01.105] Imported new potential chain segment",
            "INFO Imported new potential chain segment",
        ),
        (
            "INFO[09-27|21:53:01.105] Imported",
            "INFO Imported",
        ),
        (
            "Sep 27 21:53:01.102 INFO  Synced",
            "INFO  Synced",
        ),
        (
            "Sep-27 21:53:01.123 [CHAIN] info: Synced",
            "[CHAIN] info: Synced",
        ),
        (
            "2026-09-27 21:53:02.011|Info|BlockTree|Imported",
            "Info|BlockTree|Imported",
        ),
        (
            "2026-09-27 14-30-45.123|Info|BlockTree|Imported",
            "Info|BlockTree|Imported",
        ),
        (
            "2026-09-27 21:53:01.123+00:00 | main | INFO  | AbstractBlockProcessor | Imported",
            "main | INFO  | AbstractBlockProcessor | Imported",
        ),
        (
            "2026-09-27T21:53:01.123456Z  INFO Received headers",
            "INFO Received headers",
        ),
        (
            "INF 2026-09-27 21:53:01.123+00:00 Slot start",
            "INF Slot start",
        ),
        (
            "[2026-09-27 21:53:01]  INFO blockchain: Synced",
            "INFO blockchain: Synced",
        ),
        (
            'time="2026-09-27 21:53:01" level=info msg="Synced new block"',
            'level=info msg="Synced new block"',
        ),
        (
            'level=info ts=2026-09-27T21:53:01.123Z caller=server/service.go:123 msg="listening"',
            'level=info caller=server/service.go:123 msg="listening"',
        ),
        (
            '{"level":"info","ts":"2026-09-27T21:53:01.123Z","logger":"app","msg":"started charon"}',
            "info started charon",
        ),
        (
            "Imported block at 2026-09-27 21:53:01 peer=abc",
            "Imported block at 2026-09-27 21:53:01 peer=abc",
        ),
        (
            "Fork on 2026-09-27 was scheduled",
            "Fork on 2026-09-27 was scheduled",
        ),
        (
            "plain line with no timestamp",
            "plain line with no timestamp",
        ),
    ]
    for raw, expected in cases:
        assert strip_client_timestamp(raw) == expected, raw


def test_strip_client_timestamp_keeps_time_inside_msg_value():
    raw = 'level=info msg="scheduled at time=\\"2026-09-27 21:53:01\\""'
    assert "2026-09-27" in strip_client_timestamp(raw)


def test_format_entry_uses_description_client_and_strips_geth_stamp(tmp_path):
    (tmp_path / "execution.service").write_text(
        "Description=Reth Execution Layer Client service for MAINNET\n",
        encoding="utf-8",
    )
    names = ClientNames(str(tmp_path))
    entry = {
        "__REALTIME_TIMESTAMP": "1695840781105000",
        "_SYSTEMD_UNIT": "execution.service",
        "SYSLOG_IDENTIFIER": "reth",
        "MESSAGE": "INFO [09-27|21:53:01.105] Imported block",
    }
    line = format_entry_lines(entry, names)[0]
    ts = format_realtime_timestamp(entry["__REALTIME_TIMESTAMP"])
    assert line == f"{ts}  {'execution [Reth]'.ljust(LABEL_WIDTH)}  INFO Imported block"
    assert "[09-27|" not in line


def test_format_entry_charon_description_and_json_message(tmp_path):
    (tmp_path / "charon.service").write_text(
        "Description=Obol Charon DVT middleware for MAINNET\n",
        encoding="utf-8",
    )
    names = ClientNames(str(tmp_path))
    entry = {
        "__REALTIME_TIMESTAMP": "1695840782200000",
        "_SYSTEMD_UNIT": "charon.service",
        "SYSLOG_IDENTIFIER": "charon",
        "MESSAGE": '{"level":"info","ts":"2026-09-27T21:53:02.200Z","msg":"started"}',
    }
    line = format_entry_lines(entry, names)[0]
    assert "charon [Charon]" in line
    assert line.endswith("info started")
    assert "2026-09-27T21:53:02" not in line


def test_format_entry_syslog_fallback_when_unit_file_missing(tmp_path):
    names = ClientNames(str(tmp_path))
    entry = {
        "__REALTIME_TIMESTAMP": "1695840781105000",
        "_SYSTEMD_UNIT": "consensus.service",
        "SYSLOG_IDENTIFIER": "nimbus_beacon_node",
        "MESSAGE": "INF 2026-09-27 21:53:01.440+00:00 Slot event processed",
    }
    line = format_entry_lines(entry, names)[0]
    assert "consensus [Nimbus]" in line
    assert line.endswith("INF Slot event processed")


def test_format_entry_unknown_unit_has_no_client_brackets(tmp_path):
    (tmp_path / "grafana-server.service").write_text(
        "Description=Grafana instance\n",
        encoding="utf-8",
    )
    names = ClientNames(str(tmp_path))
    entry = {
        "__REALTIME_TIMESTAMP": "1695840781105000",
        "_SYSTEMD_UNIT": "grafana-server.service",
        "SYSLOG_IDENTIFIER": "grafana",
        "MESSAGE": "logger=server t=2026-09-27T21:53:01Z level=info msg=HTTP Server Listen",
    }
    line = format_entry_lines(entry, names)[0]
    assert "grafana-server" in line
    assert "[" not in line.split("  ", 2)[1]


def test_format_entry_indents_continuation_lines(tmp_path):
    names = ClientNames(str(tmp_path))
    entry = {
        "__REALTIME_TIMESTAMP": "1695840781105000",
        "_SYSTEMD_UNIT": "execution.service",
        "SYSLOG_IDENTIFIER": "reth",
        "MESSAGE": "INFO [09-27|21:53:01.105] boom\nstack frame",
    }
    lines = format_entry_lines(entry, names)
    assert len(lines) == 2
    assert lines[0].endswith("INFO boom")
    prefix_len = len(lines[0]) - len("INFO boom")
    assert lines[1] == (" " * prefix_len) + "stack frame"


def test_format_entry_decodes_byte_array_message(tmp_path):
    names = ClientNames(str(tmp_path))
    text = "plain line"
    entry = {
        "__REALTIME_TIMESTAMP": "0",
        "_SYSTEMD_UNIT": "execution.service",
        "MESSAGE": list(text.encode("utf-8")),
    }
    assert format_entry_lines(entry, names)[0].endswith(text)


def test_main_reads_json_lines(tmp_path, capsys):
    from manage.journal_format import main

    (tmp_path / "execution.service").write_text(
        "Description=Geth Execution Layer Client service for MAINNET\n",
        encoding="utf-8",
    )
    entry = {
        "__REALTIME_TIMESTAMP": "1695840781105000",
        "_SYSTEMD_UNIT": "execution.service",
        "SYSLOG_IDENTIFIER": "geth",
        "MESSAGE": "INFO [09-27|21:53:01.105] Imported",
    }
    import os

    os.environ["ETHPILLAR_SYSTEMD_DIR"] = str(tmp_path)
    try:
        import io
        from unittest.mock import patch

        with patch("sys.stdin", io.StringIO(json.dumps(entry) + "\nnot json\n")):
            assert main() == 0
    finally:
        os.environ.pop("ETHPILLAR_SYSTEMD_DIR", None)
    out = capsys.readouterr().out
    assert "execution [Geth]" in out
    assert "INFO Imported" in out
    assert "not json" in out
