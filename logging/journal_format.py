#!/usr/bin/env python3
"""Format ``journalctl -o json`` records for EthPillar log viewers.

Each output line is::

    YYYY-MM-DD HH:MM:SS.mmm  execution [Reth]        <message>

The timestamp is journald receive time in the local timezone, millisecond
precision. The label is the systemd unit plus the installed client name from
the unit ``Description=`` (binary name is the fallback). One leading client
timestamp is removed from the message; a time mentioned later in the line
is left in place.

Reads JSON objects from stdin, one per line, and writes formatted lines to
stdout. Unbuffered when launched with ``python3 -u``.

Run this file as a script (``logging/journal_format.py``). Do not import it as
``logging.journal_format``: a ``logging`` package would shadow the stdlib.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from manage.service_parse import (  # noqa: E402
    SYSTEMD_DIR,
    known_client_name,
    parse_description_client,
    read_text_file,
)

# Long enough for ``consensus [Lodestar]`` (20). Longer labels such as
# ``consensus [Lighthouse]`` print in full and are not truncated.
LABEL_WIDTH = 20
TS_WIDTH = 23  # YYYY-MM-DD HH:MM:SS.mmm
# CSI / SGR sequences. Reth dims its timestamp with these, which hides the
# date from a start-of-line match. ccze recolors the plain text afterward.
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

_MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
_ISO_TS = (
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}[:\-]\d{2}[:\-]\d{2}"
    r"(?:\.\d+)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?"
)
_MONTH_TS = (
    rf"(?:(?:{_MONTHS})[-\s]+\d{{1,2}}|\d{{1,2}}[-\s]+(?:{_MONTHS}))"
    rf"[-\s]+\d{{2}}:\d{{2}}:\d{{2}}(?:\.\d+)?"
)
_BRACKET_TS = (
    r"\["
    r"(?:"
    r"\d{4}-\d{2}-\d{2}[ T|]\d{2}:\d{2}:\d{2}(?:\.\d+)?"
    r"|"
    r"\d{2}-\d{2}\|\d{2}:\d{2}:\d{2}(?:\.\d+)?"
    r")"
    r"\]"
)
_TS = rf"(?:{_BRACKET_TS}|{_ISO_TS}|{_MONTH_TS})"
_LEVEL = (
    r"CRITICAL|WARNING|DEBUG|TRACE|FATAL|ERROR|INFO|WARN|CRIT|"
    r"INF|WRN|DBG|NTC|NOT|ERR|TRC|FAT"
)
# Level may sit in front of the timestamp (Nimbus ``INF <date>``, Geth
# ``INFO [date]``). A bracketed level (``[INFO] <date>``) is accepted too.
_LEADING_TS = re.compile(
    rf"^(?:\[(?P<bracket_level>{_LEVEL})\]\s+"
    rf"|(?P<level>{_LEVEL})(?:\s+|(?=\[)))?"
    rf"(?P<ts>{_TS})"
    # Trailing separator: whitespace, one Nethermind/Besu pipe, and Lodestar's
    # empty ``[]`` scope that sits directly against the timestamp.
    rf"(?:\s*\|?\s*)(?:\[\]\s*)?",
    re.IGNORECASE,
)
_STRUCT_TIME = re.compile(
    r'(?:^|\s)(?:time="[^"]*"|ts=(?:"[^"]*"|\S+))(?=\s|$)'
)
_STRUCT_KEY = re.compile(r"(?:^|\s)(?:time|ts)=")


def format_realtime_timestamp(raw: object) -> str:
    """Format a journald microsecond timestamp as local ``YYYY-MM-DD HH:MM:SS.mmm``."""
    try:
        us = int(str(raw))
    except (TypeError, ValueError):
        return " " * TS_WIDTH
    if us < 0:
        return " " * TS_WIDTH
    seconds, rem = divmod(us, 1_000_000)
    dt = datetime.fromtimestamp(seconds)
    return f"{dt:%Y-%m-%d %H:%M:%S}.{rem // 1000:03d}"


def format_client_id(unit: str, client: str) -> str:
    """Return ``unit [Client]``, padded to ``LABEL_WIDTH`` when shorter.

    Unknown software (plugins, generic units) is the unit name alone.
    A label longer than ``LABEL_WIDTH`` is kept in full.
    """
    unit_name = unit[:-8] if unit.endswith(".service") else unit
    if unit_name and client:
        label = f"{unit_name} [{client}]"
    elif unit_name:
        label = unit_name
    elif client:
        label = client
    else:
        label = "unknown"
    if len(label) < LABEL_WIDTH:
        return label.ljust(LABEL_WIDTH)
    return label


def _strip_structured_time(line: str) -> str:
    """Drop one leading ``time=`` / ``ts=`` field from a key=value log header."""
    if _STRUCT_KEY.search(line) is None:
        return line
    if "level=" not in line and "msg=" not in line and _STRUCT_KEY.match(line) is None:
        return line
    msg_at = line.find("msg=")
    head, tail = (line, "") if msg_at < 0 else (line[:msg_at], line[msg_at:])
    new_head, count = _STRUCT_TIME.subn(" ", head, count=1)
    if count == 0:
        return line
    new_head = re.sub(r"[ \t]{2,}", " ", new_head).strip()
    if tail:
        return f"{new_head} {tail}" if new_head else tail
    return new_head


def _strip_leading_timestamp(line: str) -> str:
    """Remove one timestamp at the start of *line*, keeping a leading level word."""
    match = _LEADING_TS.match(line)
    if match is None:
        return line
    level = match.group("level") or match.group("bracket_level") or ""
    rest = line[match.end() :]
    if level:
        rest = rest.lstrip(" ")
        return f"{level} {rest}" if rest else level
    return rest


def _try_json_log(message: str) -> Optional[str]:
    """Render a JSON log object as ``level message``, dropping ``ts`` / ``time``."""
    stripped = message.strip()
    if not stripped.startswith("{"):
        return None
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    text = obj.get("msg")
    if text is None:
        text = obj.get("message")
    if not isinstance(text, str):
        return None
    level = obj.get("level", obj.get("severity", ""))
    if not isinstance(level, str):
        level = "" if level is None else str(level)
    level = level.strip()
    text = text.strip()
    if level and text:
        return f"{level} {text}"
    return level or text


def strip_client_timestamp(message: str) -> str:
    """Remove one client-supplied timestamp from the start of *message*.

    JSON log objects (``msg`` / ``message``) drop their ``ts`` / ``time`` field
    and keep the level and text. Other lines lose a single leading timestamp,
    including when a short level word sits in front of it. Text after that
    first token is unchanged, so a time mentioned in the message body stays.
    """
    plain = _ANSI_RE.sub("", message)
    rendered = _try_json_log(plain)
    if rendered is not None:
        return rendered
    normalized = plain.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if not lines:
        return ""
    first = _strip_structured_time(lines[0])
    lines[0] = _strip_leading_timestamp(first)
    return "\n".join(lines)


def message_text(raw: object) -> str:
    """Decode a journal ``MESSAGE`` field (string or raw byte array)."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        try:
            return bytes(int(b) & 0xFF for b in raw).decode("utf-8", errors="replace")
        except (TypeError, ValueError):
            return ""
    if raw is None:
        return ""
    return str(raw)


class ClientNames:
    """Cache of systemd unit name → canonical client label.

    Reads ``Description=`` from ``ETHPILLAR_SYSTEMD_DIR`` (default
    ``/etc/systemd/system``). A missing unit or an unrecognized description
    caches "" so the caller can fall back to the process name.
    """

    def __init__(self, systemd_dir: Optional[str] = None) -> None:
        self.systemd_dir = systemd_dir or os.environ.get("ETHPILLAR_SYSTEMD_DIR", SYSTEMD_DIR)
        self._cache: Dict[str, str] = {}

    def lookup(self, unit: str) -> str:
        """Return the known client for *unit*, or "" when the unit does not name one."""
        key = unit[:-8] if unit.endswith(".service") else unit
        if not key:
            return ""
        if key in self._cache:
            return self._cache[key]
        client = self._read(key)
        self._cache[key] = client
        return client

    def _read(self, unit: str) -> str:
        path = os.path.join(self.systemd_dir, f"{unit}.service")
        text = read_text_file(path)
        if not text:
            return ""
        for line in text.splitlines():
            if line.startswith("Description="):
                return known_client_name(parse_description_client(line.split("=", 1)[1]))
        return ""


def _unit_name(entry: dict) -> str:
    raw = entry.get("_SYSTEMD_UNIT") or entry.get("UNIT") or ""
    if not isinstance(raw, str):
        return ""
    return raw[:-8] if raw.endswith(".service") else raw


def _syslog_id(entry: dict) -> str:
    raw = entry.get("SYSLOG_IDENTIFIER") or entry.get("_COMM") or ""
    return raw if isinstance(raw, str) else ""


def format_entry_lines(entry: dict, names: ClientNames) -> List[str]:
    """Format one journald JSON object as one or more display lines.

    Continuation lines of a multi-line message are indented under the body
    so the timestamp column stays aligned.
    """
    raw_ts = entry.get("__REALTIME_TIMESTAMP")
    if raw_ts is None:
        raw_ts = entry.get("_SOURCE_REALTIME_TIMESTAMP")
    ts = format_realtime_timestamp(raw_ts)
    unit = _unit_name(entry)
    client = names.lookup(unit) if unit else ""
    if not client:
        client = known_client_name(_syslog_id(entry))
    label = format_client_id(unit, client)
    body = strip_client_timestamp(message_text(entry.get("MESSAGE")))
    lines = body.split("\n") if body else [""]
    prefix = f"{ts}  {label} "
    formatted = [prefix + lines[0]]
    if len(lines) > 1:
        indent = " " * len(prefix)
        formatted.extend(indent + line for line in lines[1:])
    return formatted


def main() -> int:
    """Read journal JSON lines on stdin and write formatted lines on stdout."""
    names = ClientNames()
    for raw_line in sys.stdin:
        line = raw_line.strip("\r\n")
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            print(line, flush=True)
            continue
        if not isinstance(entry, dict):
            print(line, flush=True)
            continue
        for formatted in format_entry_lines(entry, names):
            print(formatted, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
