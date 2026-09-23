#!/bin/bash
# Verify functions.sh can parse installed client versions and that each matches
# release_info LATEST — the same comparison update_*.sh uses before showing
# "You are already on the latest version".
# Integration tests snapshot LATEST before deploy (latest_snapshot.py) and
# compare against that install-time snapshot so a mid-test release cannot fail verify.
# Upgrade-case deploy may force a seed (RC or previous stable) via
# latest_override.py; when that file is present, expect the forced seed tag
# instead of LATEST (override is cleared before ethpillar upgrade, so
# post-upgrade checks still use official LATEST). Forced-seed matching
# tolerates a missing prerelease suffix on the binary (base semver ± commit).
set -euo pipefail

cd /ethpillar
# shellcheck source=../../functions.sh
source ./functions.sh
# shellcheck source=known_version_mismatches.sh
source "$(dirname "${BASH_SOURCE[0]}")/known_version_mismatches.sh"

fail=0

# Same comparison as update_*.sh promptYesNo (semver + optional commit prefix).
installed_matches_latest_tag() {
  version_matches_latest "$1" "$2" "${INSTALLED_COMMIT:-}" "${3:-}"
}

# Forced-seed compare: base version ± commit; tolerate missing -rc.N on --version.
installed_matches_forced_seed() {
  local installed="$1"
  local expected="$2"
  local tag_commit="${3:-}"
  PYTHONPATH="/ethpillar/tests/integration:/ethpillar" python3 -c '
from find_client_rc import matches_forced_seed
import sys
ok = matches_forced_seed(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
sys.exit(0 if ok else 1)
' "$installed" "$expected" "${INSTALLED_COMMIT:-}" "$tag_commit"
}

OVERRIDE_DEFAULT="/tmp/ethpillar-integration-latest-override.json"

get_forced_rc_tag() {
  local client="$1"
  local override="${ETHPILLAR_INTEGRATION_LATEST_OVERRIDE:-$OVERRIDE_DEFAULT}"
  local key="${client,,}"
  local tag
  if [[ -n "$override" && -f "$override" ]]; then
    tag=$(jq -r --arg k "$key" '.clients[$k] // empty' "$override")
    if [[ -n "$tag" && "$tag" != "null" ]]; then
      echo "$tag"
      return 0
    fi
  fi
  return 1
}

get_latest_release_tag() {
  local client="$1"
  local data tag
  data=$(PYTHONPATH="/ethpillar" python3 -m deploy.common release_info "$client" "LATEST")
  tag=$(echo "$data" | jq -r .version)
  TAG_COMMIT=$(echo "$data" | jq -r '.commit // empty')
  if [[ -z "$tag" || "$tag" == "null" ]]; then
    return 1
  fi
  echo "$tag"
}

# Integration tests snapshot LATEST before deploy so a release mid-test cannot fail verify.
# Upgrade-case seed override (when present) wins so post-deploy does not require official LATEST.
get_expected_release_tag() {
  local client="$1"
  local snapshot="${ETHPILLAR_INTEGRATION_LATEST_SNAPSHOT:-}"
  local key="${client,,}"
  local tag

  TAG_COMMIT=""
  if tag=$(get_forced_rc_tag "$client"); then
    TAG_COMMIT=$(PYTHONPATH="/ethpillar" python3 -m deploy.common release_info "$client" "$tag" 2>/dev/null | jq -r '.commit // empty' || true)
    echo "$tag"
    return 0
  fi
  if [[ -n "$snapshot" && -f "$snapshot" ]]; then
    tag=$(jq -r --arg k "$key" '.[$k] // empty' "$snapshot")
    if [[ -n "$tag" && "$tag" != "null" ]]; then
      # Prefer live commit for the snapshotted tag when resolvable; otherwise semver-only.
      TAG_COMMIT=$(PYTHONPATH="/ethpillar" python3 -m deploy.common release_info "$client" "$tag" 2>/dev/null | jq -r '.commit // empty' || true)
      echo "$tag"
      return 0
    fi
  fi
  get_latest_release_tag "$client"
}

assert_matches_latest() {
  local label="$1"
  local release_client="$2"
  local installed="$3"
  local expected
  local expected_label="LATEST"

  if get_forced_rc_tag "$release_client" >/dev/null; then
    expected_label="forced seed"
  elif [[ -n "${ETHPILLAR_INTEGRATION_LATEST_SNAPSHOT:-}" && -f "${ETHPILLAR_INTEGRATION_LATEST_SNAPSHOT}" ]]; then
    expected_label="install-time LATEST"
  fi

  if ! expected=$(get_expected_release_tag "$release_client"); then
    echo "❌ ${label}: could not resolve ${expected_label} release tag"
    fail=1
    return 0
  fi
  if [[ "$expected_label" == "forced seed" ]] && installed_matches_forced_seed "$installed" "$expected" "${TAG_COMMIT:-}"; then
    echo "✅ ${label} matches forced seed (${installed#v} vs ${expected#v}) — deploy used seed; upgrade should move to official LATEST"
    return 0
  fi
  if installed_matches_latest_tag "$installed" "$expected" "${TAG_COMMIT:-}"; then
    echo "✅ ${label} matches ${expected_label} (${installed#v}) — update menu would show already on latest"
    return 0
  fi
  if known_upstream_version_mismatch "$release_client" "$installed" "$expected"; then
    echo "⚠️  ${label} mismatch accepted: installed ${installed#v} vs ${expected_label} ${expected#v} (known upstream version-report bug)"
    return 0
  fi
  echo "❌ ${label} mismatch: installed ${installed#v}${INSTALLED_COMMIT:+ (${INSTALLED_COMMIT:0:7})}, ${expected_label} ${expected#v}${TAG_COMMIT:+ (${TAG_COMMIT:0:7})}"
  fail=1
}

check_el_version() {
  [[ -f /etc/systemd/system/execution.service ]] || return 0
  local el
  el=$(grep Description= /etc/systemd/system/execution.service | awk -F= '{print $2}' | awk '{print $1}')
  # Erigon+Caplin integrated unit uses the erigon binary.
  [[ "$el" == "Erigon-Caplin" ]] && el=Erigon
  getExecutionCurrentVersion "$el"
  if [[ -z "$VERSION" || "$VERSION" == Unable* ]]; then
    echo "❌ EL version parse failed for ${el}: ${VERSION:-empty}"
    fail=1
    return 0
  fi
  echo "✅ EL ${el} version: ${VERSION}"
  assert_matches_latest "EL ${el}" "$el" "$VERSION"
}

check_cl_version() {
  [[ -f /etc/systemd/system/consensus.service ]] || return 0
  local cl
  cl=$(grep Description= /etc/systemd/system/consensus.service | awk -F= '{print $2}' | awk '{print $1}')
  if [[ "$cl" == "Caplin" ]]; then
    echo "ℹ️  Skipping Caplin version (integrated in Erigon)"
    return 0
  fi
  getClVcCurrentVersion "$cl" cl
  if [[ -z "$VERSION" || "$VERSION" == "NotInstalled" ]]; then
    echo "❌ CL version parse failed for ${cl}: ${VERSION:-empty}"
    fail=1
    return 0
  fi
  echo "✅ CL ${cl} version: ${VERSION}"
  assert_matches_latest "CL ${cl}" "$cl" "$VERSION"
}

check_vc_version() {
  [[ -f /etc/systemd/system/validator.service ]] || return 0
  local vc
  vc=$(grep Description= /etc/systemd/system/validator.service | awk -F= '{print $2}' | awk '{print $1}')
  getClVcCurrentVersion "$vc" vc
  if [[ -z "$VERSION" || "$VERSION" == "NotInstalled" ]]; then
    echo "❌ VC version parse failed for ${vc}: ${VERSION:-empty}"
    fail=1
    return 0
  fi
  echo "✅ VC ${vc} version: ${VERSION}"
  assert_matches_latest "VC ${vc}" "$vc" "$VERSION"
}

check_mevboost_version() {
  [[ -f /etc/systemd/system/mevboost.service ]] || return 0
  local installed version
  installed=$(mev-boost --version 2>&1 || true)
  if [[ -z "$installed" ]]; then
    echo "❌ MEV-Boost version parse failed: empty output"
    fail=1
    return 0
  fi
  version=$(echo "$installed" | sed 's/.*v\?\([0-9]\+\.[0-9]\+\(\.[0-9]\+\)\?\).*/\1/')
  if [[ -z "$version" || "$version" == "$installed" ]]; then
    echo "❌ MEV-Boost version parse failed: ${installed}"
    fail=1
    return 0
  fi
  echo "✅ MEV-Boost version: ${version}"
  assert_matches_latest "MEV-Boost" "mevboost" "$version"
}

check_charon_version() {
  [[ -f /etc/systemd/system/charon.service ]] || return 0
  local version
  INSTALLED_COMMIT=""
  if ! getCharonCurrentVersion; then
    echo "❌ Charon version parse failed: ${VERSION:-empty}"
    fail=1
    return 0
  fi
  version=$VERSION
  echo "✅ Charon version: ${version}"
  assert_matches_latest "Charon" "charon" "$version"
}

echo "🔢 Verifying installed client versions (parse + LATEST match)..."
check_el_version
check_cl_version
check_vc_version
check_mevboost_version
check_charon_version

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi
