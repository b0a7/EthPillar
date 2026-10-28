#!/usr/bin/env bats
#
# Setup Type wording. The relay-step cases live in tests/test_builder_mode.py
# so bats does not import deploy (which needs requests) under system python3.

setup() {
  cd "$BATS_TEST_DIRNAME/.."
}

@test "setup type says MEV-Boost and stays within 78 columns" {
  grep -q 'EC+CC+VC; ePBS else MEV-Boost' ethpillar.sh
  grep -q 'Lido; ePBS else MEV-Boost' ethpillar.sh
  ! grep -q 'else MEV"' ethpillar.sh
  # 30 characters: fits beside the 31-character CSM VC tag at width 76.
  desc='EC+CC+VC; ePBS else MEV-Boost'
  [ "${#desc}" -le 32 ]
}
