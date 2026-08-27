#!/usr/bin/env bats

setup() {
  cd "$BATS_TEST_DIRNAME/.."
}

@test "MEV menu lists ePBS migration" {
  grep -q 'ePBS migration' ethpillar.sh
  grep -q 'submenuEPBS' ethpillar.sh
}

@test "functions.sh defines ePBS CLI wrappers" {
  grep -q '^runEpbsCli()' functions.sh
  grep -q '^runEpbsMigrationStep()' functions.sh
  grep -q '^submenuEPBS()' functions.sh
  grep -q 'Before Merge' functions.sh
  grep -q 'After Merge' functions.sh
}

@test "README links ePBS migration guide" {
  grep -q 'docs/ePBS-migration.md' README.md
  test -f docs/ePBS-migration.md
  grep -q 'prepare' docs/ePBS-migration.md
  grep -q 'complete' docs/ePBS-migration.md
}

@test "integration matrix has Prysm ePBS migration case" {
  grep -q 'Prysm-Reth-ePBS-Migration-SEPOLIA' tests/integration/run_docker_tests.py
  grep -q -- '--test-epbs' tests/integration/run_docker_tests.py
  grep -q -- '--test-epbs' tests/integration/run_inside_docker.py
  grep -q -- '--force-validator' tests/integration/run_inside_docker.py
  test -f tests/integration/test_epbs.sh
  grep -q -- '--force-validator' tests/integration/test_epbs.sh
}
