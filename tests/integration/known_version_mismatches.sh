#!/bin/bash
# Known upstream packaging bugs where GitHub release tag != binary --version.
# Sourced by check_client_versions.sh (integration verify) and bats unit tests.
# Keep entries narrow (client + exact installed/expected pair) so the next
# fixed release fails the check again if something else is wrong.
known_upstream_version_mismatch() {
  local client="${1,,}"
  local installed="${2#v}"
  local expected="${3#v}"

  case "$client" in
    grandine)
      # Grandine 2.0.6 release asset still prints "Grandine 2.0.5".
      # https://github.com/grandinetech/grandine/issues/838
      if [[ "$expected" == "2.0.6" && "$installed" == "2.0.5" ]]; then
        return 0
      fi
      ;;
  esac
  return 1
}
