#!/usr/bin/env bats

setup() {
  cd "$BATS_TEST_DIRNAME/.."
}

@test "MEV menu lists ePBS migration only for a supported VC" {
  grep -q 'ePBS migration' ethpillar.sh
  grep -q 'submenuEPBS' ethpillar.sh
  grep -A40 '^submenuMEV-Boost()' ethpillar.sh | grep -q 'epbsTuiSupported'
}

@test "functions.sh defines ePBS CLI wrappers" {
  grep -q '^runEpbsCli()' functions.sh
  grep -q '^runEpbsMigrationStep()' functions.sh
  grep -q '^submenuEPBS()' functions.sh
  grep -q '^submenuEPBSImport()' functions.sh
  grep -q '^epbsTuiSupported()' functions.sh
  grep -q '^epbsImportMenuSupported()' functions.sh
  grep -q '^charonEpbsSupported()' functions.sh
  grep -q '^runEpbsExport()' functions.sh
  grep -q 'Before Gloas Fork' functions.sh
  grep -q 'After Gloas Fork' functions.sh
  grep -q 'ethpillar.epbs-migration' functions.sh
  grep -q 'remote-vc-prepared' functions.sh
}

@test "README links ePBS migration guide" {
  grep -q 'docs/ePBS-migration.md' README.md
  test -f docs/ePBS-migration.md
  grep -q 'prepare' docs/ePBS-migration.md
  grep -q 'complete' docs/ePBS-migration.md
  grep -q 'export' docs/ePBS-migration.md
  grep -q 'import' docs/ePBS-migration.md
  grep -q 'enable --now mevboost' docs/ePBS-migration.md
  grep -q 'Split hosts' docs/ePBS-migration.md
  grep -q 'Solo node' docs/ePBS-migration.md
  grep -q 'Obol Charon DV' docs/ePBS-migration.md
  grep -q 'Lighthouse v8.2.0' docs/ePBS-migration.md
  grep -q 'suggested-fee-recipient' docs/ePBS-migration.md
  grep -q 'Teku 26.6.0' docs/ePBS-migration.md
  grep -q '11099' docs/ePBS-migration.md
  grep -q 'Nimbus v26.8.0' docs/ePBS-migration.md
  grep -q 'payload-builder=true' docs/ePBS-migration.md
  grep -q 'Import refused' manage/epbs.py
  grep -q 'charon_epbs_supported' manage/epbs.py
}

@test "integration matrix attaches live-binary ePBS once per supported VC" {
  # Prysm has no combo; ePBS piggybacks on the existing Custom Setup deploy.
  grep -q "Prysm-Reth-Custom-Setup-SEPOLIA" tests/integration/run_docker_tests.py
  grep 'Prysm-Reth-Custom-Setup-SEPOLIA' tests/integration/run_docker_tests.py | grep -q -- '--test-epbs'
  # Other VCs attach to Solo Staking combo rows (VC+MEV already deployed).
  grep -q 'EPBS_SOLO_COMBOS' tests/integration/run_docker_tests.py
  grep -A8 'EPBS_SOLO_COMBOS' tests/integration/run_docker_tests.py | grep -q 'Lighthouse-Reth'
  grep -A8 'EPBS_SOLO_COMBOS' tests/integration/run_docker_tests.py | grep -q 'Lodestar-Besu'
  grep -A8 'EPBS_SOLO_COMBOS' tests/integration/run_docker_tests.py | grep -q 'Teku-Besu'
  grep -A8 'EPBS_SOLO_COMBOS' tests/integration/run_docker_tests.py | grep -q 'Nimbus-Nethermind'
  grep -q '_combo_gets_epbs' tests/integration/run_docker_tests.py
  grep -q -- '--test-epbs' tests/integration/run_inside_docker.py
  grep -q -- '--force-validator' tests/integration/run_inside_docker.py
  test -f tests/integration/test_epbs.sh
  grep -q -- '--force-validator' tests/integration/test_epbs.sh
  grep -q 'enable_lodestar_empty_wallet' tests/integration/test_epbs.sh
  grep -q 'enable_teku_empty_wallet' tests/integration/test_epbs.sh
  grep -q 'enable_nimbus_empty_wallet' tests/integration/test_epbs.sh
}

@test "integration matrix has no dedicated ePBS-Migration duplicate deploys" {
  run grep -E 'ePBS-Migration' tests/integration/run_docker_tests.py
  [ "$status" -ne 0 ]
}
