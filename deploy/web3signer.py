"""Consensys Web3Signer remote signer: release lookup, systemd unit, and key import.

Web3Signer is not a beacon-node proxy. The validator client keeps its beacon
URL and sends signing requests to this service. Validator keys and the
slashing-protection database live here, not in the VC datadir.

Web3Signer 26.x is a Java 25 distribution. Slashing protection is on by
default and requires a JDBC URL; EthPillar uses a localhost PostgreSQL
database. The database password is stored in an EnvironmentFile and expanded
by systemd in ExecStart.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Sequence

from deploy.common import (
    BASE_DATA_DIR,
    DOWNLOAD_DIR,
    INSTALL_DIR,
    download_release_file,
    ensure_java_available,
    extract_and_install,
    setup_client_user_and_dir,
    write_service_file,
)
from deploy.service_generators import form_exec_start, generate_systemd_template

WEB3SIGNER_USER = "web3signer"
WEB3SIGNER_DATA_DIR = f"{BASE_DATA_DIR}/web3signer"
WEB3SIGNER_KEYSTORE_DIR = f"{WEB3SIGNER_DATA_DIR}/keystores"
WEB3SIGNER_DB_ENV = f"{WEB3SIGNER_DATA_DIR}/db.env"
WEB3SIGNER_SERVICE_PATH = "/etc/systemd/system/web3signer.service"
WEB3SIGNER_INSTALL_DIR = f"{INSTALL_DIR}/web3signer"
WEB3SIGNER_BIN = f"{WEB3SIGNER_INSTALL_DIR}/bin/web3signer"

DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 9000
DEFAULT_METRICS_PORT = 9001
DEFAULT_SIGNER_URL = f"http://{DEFAULT_HTTP_HOST}:{DEFAULT_HTTP_PORT}"
PUBLIC_KEYS_URL = f"{DEFAULT_SIGNER_URL}/api/v1/eth2/publicKeys"

SLASHING_DB_URL = "jdbc:postgresql://127.0.0.1:5432/web3signer"
SLASHING_DB_USER = "web3signer"
DB_PASSWORD_ENV = "WEB3SIGNER_DB_PASSWORD"

LIGHTHOUSE_DEFINITIONS = f"{BASE_DATA_DIR}/lighthouse_validator/validators/validator_definitions.yml"
LIGHTHOUSE_SLASHING_DB = (
    f"{BASE_DATA_DIR}/lighthouse_validator/validators/slashing_protection.sqlite"
)
VC_RUN_USER = "validator"

# Lighthouse opens this file with O_RDWR and does not create it. The table
# names match what the VC queries before it will enable a Web3Signer key.
_LIGHTHOUSE_SLASHING_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS validators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_key TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS signed_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    validator_id INTEGER NOT NULL,
    slot INTEGER NOT NULL,
    signing_root TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS signed_attestations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    validator_id INTEGER NOT NULL,
    source_epoch INTEGER NOT NULL,
    target_epoch INTEGER NOT NULL,
    signing_root TEXT NOT NULL
);
"""

# Web3Signer 26.x distribution ships for JDK 25 (same floor as current Teku).
JAVA_MAJOR = 25

GITHUB_REPO = "Consensys-Incorporated/web3signer"


def signer_flags_for_vc(vc_name: str, signer_url: str = DEFAULT_SIGNER_URL) -> str:
    """Return VC ExecStart flags that delegate signing to Web3Signer.

    Args:
        vc_name: EthPillar validator client name.
        signer_url: Web3Signer HTTP base URL.

    Returns:
        A single space-separated flag string, or ``""`` when *vc_name* has no
        remote-signer hook.
    """
    url = (signer_url or DEFAULT_SIGNER_URL).rstrip("/")
    public_keys = f"{url}/api/v1/eth2/publicKeys"
    flags = {
        "Teku": (
            f"--validators-external-signer-url={url} "
            f"--validators-external-signer-public-keys={public_keys}"
        ),
        "Prysm": (
            f"--validators-external-signer-url={url} "
            f"--validators-external-signer-public-keys={public_keys}"
        ),
        "Nimbus": f"--web3-signer-url={url}",
        "Lodestar": f"--externalSigner.url={url} --externalSigner.fetch=true",
        "Lighthouse": "--disable-slashing-protection-web3signer",
        "Grandine": f"--web3signer-api-urls={url}",
        "Grandine (integrated)": f"--web3signer-api-urls={url}",
    }
    return flags.get(vc_name or "", "")


def generate_web3signer_service(
    eth_network: str,
    *,
    http_host: str = DEFAULT_HTTP_HOST,
    http_port: int = DEFAULT_HTTP_PORT,
    metrics_port: int = DEFAULT_METRICS_PORT,
    keystore_dir: str = WEB3SIGNER_KEYSTORE_DIR,
    data_dir: str = WEB3SIGNER_DATA_DIR,
) -> str:
    """Generate web3signer.service content.

    The slashing-database password is not embedded. Systemd reads
    ``WEB3SIGNER_DB_PASSWORD`` from ``db.env`` and expands it in ExecStart.

    Args:
        eth_network: Network name passed to ``eth2 --network``.
        http_host: HTTP bind address. Must stay on localhost.
        http_port: HTTP listen port.
        metrics_port: Prometheus listen port.
        keystore_dir: Directory of EIP-2335 keystores and matching ``.txt`` passwords.
        data_dir: Web3Signer data directory.

    Returns:
        Service file content as a string.
    """
    binary = WEB3SIGNER_BIN
    args = [
        binary,
        f"--http-listen-host={http_host}",
        f"--http-listen-port={http_port}",
        f"--data-path={data_dir}",
        "--metrics-enabled=true",
        f"--metrics-host={http_host}",
        f"--metrics-port={metrics_port}",
        "eth2",
        f"--network={eth_network}",
        "--slashing-protection-enabled=true",
        f"--slashing-protection-db-url={SLASHING_DB_URL}",
        f"--slashing-protection-db-username={SLASHING_DB_USER}",
        f"--slashing-protection-db-password=${{{DB_PASSWORD_ENV}}}",
        f"--keystores-path={keystore_dir}",
        f"--keystores-passwords-path={keystore_dir}",
    ]
    return generate_systemd_template(
        description=f"Web3Signer remote signer for {eth_network.upper()}",
        user=WEB3SIGNER_USER,
        exec_start=form_exec_start(args),
        working_dir=data_dir,
        timeout_stop_sec=120,
        environment_file=WEB3SIGNER_DB_ENV,
        unit_after=["postgresql.service"],
    )


def get_release_info(version_tag: str, arch_amd64: bool) -> dict:
    """Get Web3Signer release version, download URL, and filename.

    The published tarball is architecture-neutral (a Java distribution).

    Args:
        version_tag: ``LATEST`` or a specific version tag.
        arch_amd64: Ignored; kept so the client-module contract stays uniform.

    Returns:
        A dictionary with keys ``version``, ``download_urls``, and ``filenames``.
    """
    del arch_amd64  # Java tarball is not arch-specific.
    from deploy.common import get_github_release, pick_github_release_asset, release_info_from_github

    data = get_github_release(GITHUB_REPO, version_tag)
    filename, download_url = pick_github_release_asset(
        data.get("assets", []),
        None,
        name_contains=("web3signer",),
        prefer_extensions=(".tar.gz", ".zip"),
        client_label="Web3Signer",
    )
    return release_info_from_github(data, [download_url], [filename])


def _sudo_write(path: str, content: str, owner: str, mode: str = "600") -> None:
    """Write *content* to *path* via sudo and set owner and mode."""
    subprocess.run(
        ["sudo", "tee", path],
        input=content,
        text=True,
        stdout=subprocess.DEVNULL,
        check=True,
    )
    subprocess.run(["sudo", "chown", f"{owner}:{owner}", path], check=True)
    subprocess.run(["sudo", "chmod", mode, path], check=True)


def ensure_slashing_database(env_path: str = WEB3SIGNER_DB_ENV) -> None:
    """Install local PostgreSQL and ensure the Web3Signer role and database exist.

    The password is created once and stored in *env_path* (mode ``600``).
    Later installs reuse that password and ALTER the role to match.
    """
    subprocess.run(["sudo", "apt-get", "update"], check=True)
    subprocess.run(["sudo", "apt-get", "install", "-y", "postgresql"], check=True)
    subprocess.run(["sudo", "systemctl", "enable", "--now", "postgresql"], check=True)
    for _ in range(30):
        ready = subprocess.run(
            ["sudo", "-u", "postgres", "pg_isready", "-q"],
            check=False,
        )
        if ready.returncode == 0:
            break
        subprocess.run(["sleep", "1"], check=False)
    else:
        raise RuntimeError("PostgreSQL did not become ready")

    password = ""
    if os.path.isfile(env_path):
        try:
            with open(env_path, encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith(f"{DB_PASSWORD_ENV}="):
                        password = line.split("=", 1)[1].strip()
                        break
        except OSError:
            password = ""
    if not password:
        result = subprocess.run(
            ["sudo", "cat", env_path],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            for line in (result.stdout or "").splitlines():
                if line.startswith(f"{DB_PASSWORD_ENV}="):
                    password = line.split("=", 1)[1].strip()
                    break
    if not password:
        password = secrets.token_hex(24)
        parent = os.path.dirname(env_path)
        subprocess.run(["sudo", "mkdir", "-p", parent], check=True)
        _sudo_write(env_path, f"{DB_PASSWORD_ENV}={password}\n", WEB3SIGNER_USER, "600")

    # Dollar-quoting avoids escaping a hex password. Hex has no quotes.
    sql = f"""
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{SLASHING_DB_USER}') THEN
    CREATE ROLE {SLASHING_DB_USER} LOGIN PASSWORD '{password}';
  ELSE
    ALTER ROLE {SLASHING_DB_USER} WITH LOGIN PASSWORD '{password}';
  END IF;
END
$$;
"""
    subprocess.run(["sudo", "-u", "postgres", "psql", "-v", "ON_ERROR_STOP=1", "-c", sql], check=True)
    exists = subprocess.run(
        [
            "sudo", "-u", "postgres", "psql", "-tAc",
            "SELECT 1 FROM pg_database WHERE datname = 'web3signer'",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    if (exists.stdout or "").strip() != "1":
        subprocess.run(
            ["sudo", "-u", "postgres", "createdb", "-O", SLASHING_DB_USER, "web3signer"],
            check=True,
        )


_MIGRATION_VERSION_RE = re.compile(r"^V(\d+)", re.IGNORECASE)


def migrations_to_apply(filenames: Sequence[str], current_version: Optional[int]) -> List[str]:
    """Return slashing-schema SQL files that still need to be applied.

    Files are ordered by the Flyway ``V<number>`` prefix. A missing
    ``database_version`` row (``current_version is None``) applies every file.

    Args:
        filenames: Basenames from ``migrations/postgresql``.
        current_version: Version stored in ``database_version``, if the table exists.

    Returns:
        Basenames to apply, lowest version first.
    """
    ranked = []
    for name in filenames:
        if not name.endswith(".sql"):
            continue
        match = _MIGRATION_VERSION_RE.match(name)
        if not match:
            continue
        version = int(match.group(1))
        if current_version is not None and version <= current_version:
            continue
        ranked.append((version, name))
    ranked.sort()
    return [name for _, name in ranked]


def _db_password(env_path: str = WEB3SIGNER_DB_ENV) -> str:
    """Return the slashing-database password stored in ``db.env``."""
    text = _read_sudo_text(env_path)
    for line in text.splitlines():
        if line.startswith(f"{DB_PASSWORD_ENV}="):
            password = line.split("=", 1)[1].strip()
            if password:
                return password
    raise FileNotFoundError(f"No {DB_PASSWORD_ENV} in {env_path}")


def _migrations_dir(install_dir: str = WEB3SIGNER_INSTALL_DIR) -> str:
    """Return the directory of Web3Signer PostgreSQL migration scripts."""
    candidates = (
        os.path.join(install_dir, "migrations", "postgresql"),
        os.path.join(install_dir, "migrations", "postgresql", "postgresql"),
    )
    for path in candidates:
        if os.path.isdir(path):
            return path
    raise FileNotFoundError(
        f"Web3Signer PostgreSQL migrations not found under {install_dir}/migrations"
    )


def _psql(password: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run psql against the local web3signer database as its owner role."""
    env = os.environ.copy()
    env["PGPASSWORD"] = password
    return subprocess.run(
        [
            "psql",
            "-h",
            "127.0.0.1",
            "-U",
            SLASHING_DB_USER,
            "-d",
            "web3signer",
            "-v",
            "ON_ERROR_STOP=1",
            *args,
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _current_schema_version(password: str) -> Optional[int]:
    """Return ``database_version.version``, or None when the table is absent."""
    result = _psql(
        password,
        "-tAc",
        "SELECT version FROM database_version WHERE id = 1",
    )
    if result.returncode != 0:
        return None
    text = (result.stdout or "").strip()
    if not text:
        return None
    return int(text)


def apply_slashing_schema(install_dir: str = WEB3SIGNER_INSTALL_DIR) -> List[str]:
    """Load Web3Signer's PostgreSQL slashing-protection schema.

    Web3Signer refuses to start until ``database_version`` matches the version
    shipped with that release. The SQL files live in the distribution and are
    applied in version order. Files at or below the current version are skipped.

    Args:
        install_dir: Unpacked Web3Signer directory.

    Returns:
        Basenames of the migration files that were applied.
    """
    password = _db_password()
    directory = _migrations_dir(install_dir)
    pending = migrations_to_apply(os.listdir(directory), _current_schema_version(password))
    applied: List[str] = []
    for name in pending:
        path = os.path.join(directory, name)
        print(f">> Applying Web3Signer slashing schema {name}")
        result = _psql(password, "-f", path)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"Failed to apply {name}: {detail}")
        applied.append(name)
    return applied


def install_web3signer(eth_network: str) -> tuple[str, str]:
    """Install the Web3Signer distribution, slashing database, and systemd unit.

    Args:
        eth_network: Network name (for example ``hoodi``).

    Returns:
        ``(version, service_file_path)``
    """
    setup_client_user_and_dir(WEB3SIGNER_USER, "web3signer")
    subprocess.run(["sudo", "mkdir", "-p", WEB3SIGNER_KEYSTORE_DIR], check=True)
    subprocess.run(
        ["sudo", "chown", "-R", f"{WEB3SIGNER_USER}:{WEB3SIGNER_USER}", WEB3SIGNER_DATA_DIR],
        check=True,
    )
    subprocess.run(["sudo", "chmod", "700", WEB3SIGNER_DATA_DIR], check=True)
    subprocess.run(["sudo", "chmod", "700", WEB3SIGNER_KEYSTORE_DIR], check=True)

    if not ensure_java_available(JAVA_MAJOR):
        print(
            f"JDK {JAVA_MAJOR} is required by Web3Signer but could not be installed. "
            "Aborting Web3Signer install."
        )
        sys.exit(1)

    ensure_slashing_database()

    info = get_release_info("LATEST", True)
    version = info["version"]
    download_path = download_release_file(info, DOWNLOAD_DIR, "web3signer")
    extract_and_install(download_path, "web3signer", WEB3SIGNER_INSTALL_DIR, "directory", 1)
    # Web3Signer does not migrate on startup. An empty database fails with
    # "Failed to read database version, expected 12".
    apply_slashing_schema()

    service_content = generate_web3signer_service(eth_network)
    write_service_file(service_content, WEB3SIGNER_SERVICE_PATH, "web3signer_temp.service")
    return version, WEB3SIGNER_SERVICE_PATH


def merge_validator_definitions(
    existing: str,
    pubkeys: Sequence[str],
    signer_url: str = DEFAULT_SIGNER_URL,
) -> str:
    """Return Lighthouse ``validator_definitions.yml`` with Web3Signer entries added.

    Existing entries for the same pubkey are left in place. A file that is
    empty or only ``[]`` is replaced. Other entries are preserved.

    Args:
        existing: Current file text.
        pubkeys: Validator public keys (with or without ``0x``).
        signer_url: Web3Signer base URL written into each new entry.

    Returns:
        YAML document text ending in a newline.
    """
    url = (signer_url or DEFAULT_SIGNER_URL).rstrip("/")
    wanted: List[str] = []
    for raw in pubkeys:
        key = (raw or "").strip().lower()
        if not key:
            continue
        if not key.startswith("0x"):
            key = "0x" + key
        if key not in wanted:
            wanted.append(key)

    body = (existing or "").strip()
    if body in ("", "[]"):
        kept = ""
    else:
        kept = existing
        if kept and not kept.endswith("\n"):
            kept += "\n"

    present = kept.lower()
    chunks: List[str] = []
    for key in wanted:
        if key in present:
            continue
        chunks.append(
            "- enabled: true\n"
            f"  voting_public_key: \"{key}\"\n"
            "  type: web3signer\n"
            f"  url: \"{url}\"\n"
        )
    if not chunks:
        if kept:
            return kept
        return "[]\n"
    return kept + "".join(chunks)


def _keystore_pubkey(path: str) -> str:
    """Return the ``pubkey`` field from an EIP-2335 keystore."""
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    pubkey = str(payload.get("pubkey") or "").strip()
    if not pubkey:
        raise ValueError(f"missing pubkey in {path}")
    if not pubkey.startswith("0x"):
        pubkey = "0x" + pubkey
    return pubkey


def _read_sudo_text(path: str) -> str:
    """Read a file that may be owned by another user."""
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as handle:
                return handle.read()
        except OSError:
            pass
    result = subprocess.run(["sudo", "cat", path], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return ""
    return result.stdout or ""


def import_keystores(
    src_dir: str,
    *,
    password_file: str = "",
    keystore_dir: str = WEB3SIGNER_KEYSTORE_DIR,
    definitions_path: str = LIGHTHOUSE_DEFINITIONS,
    restart: bool = True,
) -> Dict[str, object]:
    """Copy EIP-2335 keystores into Web3Signer and refresh Lighthouse definitions.

    Password files already beside each keystore are copied. Otherwise
    *password_file* is written as the matching ``.txt`` for every keystore.
    Keystores are not imported into the validator client.

    Args:
        src_dir: Directory containing ``keystore*.json`` files.
        password_file: Single password used when a keystore has no sibling ``.txt``.
        keystore_dir: Destination keystore directory.
        definitions_path: Lighthouse validator definitions file.
        restart: Restart web3signer and the VC (or Grandine consensus) after import.

    Returns:
        Mapping with ``count`` and ``pubkeys``.
    """
    src = os.path.abspath(os.path.expanduser(src_dir))
    if not os.path.isdir(src):
        raise FileNotFoundError(f"keystore directory not found: {src}")

    names = sorted(
        name for name in os.listdir(src)
        if name.startswith("keystore") and name.endswith(".json")
    )
    if not names:
        raise ValueError(f"no keystore JSON files in {src}")

    password = ""
    if password_file:
        with open(password_file, encoding="utf-8") as handle:
            password = handle.read().strip("\n")

    subprocess.run(["sudo", "mkdir", "-p", keystore_dir], check=True)
    pubkeys: List[str] = []
    for name in names:
        src_json = os.path.join(src, name)
        dest_json = os.path.join(keystore_dir, name)
        subprocess.run(["sudo", "cp", "-a", src_json, dest_json], check=True)
        txt_name = name[:-5] + ".txt" if name.endswith(".json") else name + ".txt"
        src_txt = os.path.join(src, txt_name)
        dest_txt = os.path.join(keystore_dir, txt_name)
        if os.path.isfile(src_txt):
            subprocess.run(["sudo", "cp", "-a", src_txt, dest_txt], check=True)
        elif password:
            _sudo_write(dest_txt, password + "\n", WEB3SIGNER_USER, "600")
        else:
            raise ValueError(
                f"{name} has no password file and no --password-file was given"
            )
        pubkeys.append(_keystore_pubkey(src_json))

    subprocess.run(
        ["sudo", "chown", "-R", f"{WEB3SIGNER_USER}:{WEB3SIGNER_USER}", keystore_dir],
        check=True,
    )
    subprocess.run(["sudo", "chmod", "-R", "700", keystore_dir], check=True)

    if os.path.isfile("/etc/systemd/system/validator.service"):
        content = _read_sudo_text("/etc/systemd/system/validator.service")
        if "lighthouse" in content.lower():
            definitions_dir = os.path.dirname(definitions_path)
            subprocess.run(["sudo", "mkdir", "-p", definitions_dir], check=True)
            merged = merge_validator_definitions(_read_sudo_text(definitions_path), pubkeys)
            _sudo_write(definitions_path, merged, VC_RUN_USER, "600")
            subprocess.run(
                ["sudo", "chown", f"{VC_RUN_USER}:{VC_RUN_USER}", definitions_dir],
                check=True,
            )
            register_lighthouse_slashing_pubkeys(pubkeys)

    if restart:
        restart_after_import()

    return {"count": len(names), "pubkeys": pubkeys}


def restart_after_import() -> None:
    """Reload keys into Web3Signer and restart the validator that calls it."""
    subprocess.run(["sudo", "systemctl", "restart", "web3signer"], check=False)
    if os.path.isfile("/etc/systemd/system/validator.service"):
        subprocess.run(["sudo", "systemctl", "restart", "validator"], check=False)
        return
    consensus = "/etc/systemd/system/consensus.service"
    if os.path.isfile(consensus) and "web3signer-api-urls" in _read_sudo_text(consensus):
        subprocess.run(["sudo", "systemctl", "restart", "consensus"], check=False)


def initialize_lighthouse_slashing_db(db_path: str) -> None:
    """Create or repair an empty Lighthouse slashing-protection database.

    The file must already contain a ``validators`` table before the VC will
    enable Web3Signer keys. Lighthouse does not create the file itself.

    Args:
        db_path: Path to ``slashing_protection.sqlite``. The parent directory
            must be writable by the caller.
    """
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(_LIGHTHOUSE_SLASHING_SCHEMA)
        row = connection.execute("SELECT COUNT(*) FROM schema_version").fetchone()
        if row is None or int(row[0]) == 0:
            connection.execute("INSERT INTO schema_version (version) VALUES (0)")
        connection.commit()
    finally:
        connection.close()


def add_lighthouse_slashing_pubkeys(db_path: str, pubkeys: Sequence[str]) -> None:
    """Insert *pubkeys* into an existing Lighthouse slashing database.

    Args:
        db_path: Path to ``slashing_protection.sqlite``.
        pubkeys: Validator public keys (with or without ``0x``).
    """
    initialize_lighthouse_slashing_db(db_path)
    connection = sqlite3.connect(db_path)
    try:
        for raw in pubkeys:
            key = (raw or "").strip().lower()
            if not key:
                continue
            if not key.startswith("0x"):
                key = "0x" + key
            connection.execute(
                "INSERT OR IGNORE INTO validators (public_key) VALUES (?)",
                (key,),
            )
        connection.commit()
    finally:
        connection.close()


def _sudo_sqlite_update(db_path: str, pubkeys: Sequence[str]) -> None:
    """Create *db_path* if needed and insert *pubkeys*, using sudo for ownership."""
    parent = os.path.dirname(db_path)
    subprocess.run(["sudo", "mkdir", "-p", parent], check=True)
    subprocess.run(["sudo", "chown", f"{VC_RUN_USER}:{VC_RUN_USER}", parent], check=True)
    subprocess.run(["sudo", "chmod", "700", parent], check=True)
    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, "slashing_protection.sqlite")
        exists = subprocess.run(["sudo", "test", "-s", db_path], check=False)
        if exists.returncode == 0:
            subprocess.run(["sudo", "cp", db_path, local], check=True)
            subprocess.run(["sudo", "chown", f"{os.getuid()}:{os.getuid()}", local], check=True)
            subprocess.run(["sudo", "chmod", "600", local], check=True)
        add_lighthouse_slashing_pubkeys(local, pubkeys)
        subprocess.run(["sudo", "cp", local, db_path], check=True)
    subprocess.run(["sudo", "chown", f"{VC_RUN_USER}:{VC_RUN_USER}", db_path], check=True)
    subprocess.run(["sudo", "chmod", "600", db_path], check=True)


def register_lighthouse_slashing_pubkeys(
    pubkeys: Sequence[str],
    db_path: str = LIGHTHOUSE_SLASHING_DB,
) -> None:
    """Record Web3Signer pubkeys in Lighthouse's slashing database.

    Args:
        pubkeys: Validator public keys from the imported keystores.
        db_path: Slashing database path. Defaults to the EthPillar VC datadir.
    """
    _sudo_sqlite_update(db_path, pubkeys)


def ensure_lighthouse_slashing_db(db_path: str = LIGHTHOUSE_SLASHING_DB) -> None:
    """Create an empty Lighthouse slashing database when it is missing."""
    exists = subprocess.run(["sudo", "test", "-s", db_path], check=False)
    if exists.returncode == 0:
        return
    _sudo_sqlite_update(db_path, [])


def ensure_lighthouse_definitions_file(
    definitions_path: str = LIGHTHOUSE_DEFINITIONS,
) -> None:
    """Create an empty Lighthouse definitions file when Web3Signer is installed.

    Key import appends ``type: web3signer`` entries later. An empty list lets
    the VC start before any keys exist. The slashing-protection database is
    created alongside it; Lighthouse exits if that file is missing.
    """
    if not _read_sudo_text(definitions_path).strip():
        definitions_dir = os.path.dirname(definitions_path)
        subprocess.run(["sudo", "mkdir", "-p", definitions_dir], check=True)
        subprocess.run(
            ["sudo", "chown", f"{VC_RUN_USER}:{VC_RUN_USER}", definitions_dir],
            check=True,
        )
        subprocess.run(["sudo", "chmod", "700", definitions_dir], check=True)
        _sudo_write(definitions_path, "[]\n", VC_RUN_USER, "600")
    ensure_lighthouse_slashing_db()


def _cmd_import(args: argparse.Namespace) -> int:
    try:
        result = import_keystores(
            args.directory,
            password_file=args.password_file or "",
            restart=not args.no_restart,
        )
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"imported={result['count']}")
    for pubkey in result["pubkeys"]:
        print(pubkey)
    return 0


def _cmd_apply_migrations(_args: argparse.Namespace) -> int:
    try:
        applied = apply_slashing_schema()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if applied:
        print(f"applied={','.join(applied)}")
    else:
        print("applied=none")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point for keystore import and schema migrations."""
    parser = argparse.ArgumentParser(description="EthPillar Web3Signer helpers")
    sub = parser.add_subparsers(dest="command", required=True)
    migrate_parser = sub.add_parser(
        "apply-migrations",
        help="Load the Web3Signer slashing-protection schema into PostgreSQL",
    )
    migrate_parser.set_defaults(func=_cmd_apply_migrations)
    import_parser = sub.add_parser(
        "import-keystores",
        help="Copy EIP-2335 keystores into the Web3Signer keystore directory",
    )
    import_parser.add_argument("--directory", required=True, help="Directory of keystore JSON files")
    import_parser.add_argument(
        "--password-file",
        default="",
        help="Password file used when a keystore has no sibling .txt",
    )
    import_parser.add_argument(
        "--no-restart",
        action="store_true",
        help="Do not restart web3signer or the validator after import",
    )
    import_parser.set_defaults(func=_cmd_import)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
