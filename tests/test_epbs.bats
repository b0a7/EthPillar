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
