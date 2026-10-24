#!/bin/bash
# Known upstream packaging bugs where GitHub release tag != binary --version.
# Sourced by check_client_versions.sh (integration verify) and bats unit tests.
# Entries live in known_failures.yaml (kind: version_mismatch); this wrapper
# keeps the bash call site stable.
known_upstream_version_mismatch() {
  local client="${1,,}"
  local installed="${2#v}"
  local expected="${3#v}"
  local root
  root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  PYTHONPATH="${root}/tests/integration:${PYTHONPATH:-}" python3 -c '
from known_failures import known_upstream_version_mismatch
import sys
sys.exit(0 if known_upstream_version_mismatch(sys.argv[1], sys.argv[2], sys.argv[3]) else 1)
' "$client" "$installed" "$expected"
}
