#!/usr/bin/env bats

setup() {
  # shellcheck disable=SC1091
  source "$BATS_TEST_DIRNAME/integration/known_version_mismatches.sh"
}

@test "known_upstream_version_mismatch accepts Grandine 2.0.6 reporting 2.0.5" {
  run known_upstream_version_mismatch Grandine 2.0.5 2.0.6
  [ "$status" -eq 0 ]

  run known_upstream_version_mismatch grandine v2.0.5 v2.0.6
  [ "$status" -eq 0 ]
}

@test "known_upstream_version_mismatch rejects other Grandine pairs" {
  run known_upstream_version_mismatch Grandine 2.0.5 2.0.7
  [ "$status" -ne 0 ]

  run known_upstream_version_mismatch Grandine 2.0.6 2.0.6
  [ "$status" -ne 0 ]

  run known_upstream_version_mismatch Grandine 2.0.4 2.0.6
  [ "$status" -ne 0 ]
}

@test "known_upstream_version_mismatch rejects other clients" {
  run known_upstream_version_mismatch Lighthouse 2.0.5 2.0.6
  [ "$status" -ne 0 ]
}
