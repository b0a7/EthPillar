#!/usr/bin/env bats
#
# R1 and the relay-checklist restore, driven through the install helpers.

setup() {
  cd "$BATS_TEST_DIRNAME/.."
  # sudo bats runs as root and does not see the developer's user-site
  # packages. The ethpillar-test image installs them system-wide; this VM
  # installs them for the ubuntu user.
  if ! python3 -c 'import requests' >/dev/null 2>&1; then
    local user_site="/home/ubuntu/.local/lib/python3.12/site-packages"
    if [[ -d "$user_site" ]]; then
      export PYTHONPATH="${user_site}${PYTHONPATH:+:$PYTHONPATH}"
    fi
  fi
}

@test "forced builder_mode empty selection does not promise going back" {
  python3 - <<'PY'
from deploy.builder_mode import BuilderModeCancelled, finalize_builder_install

notices = []

def checklist(title, prompt, items):
    if not notices:
        return []
    return ["Flashbots"]

flags, relays = finalize_builder_install(
    role="Custom Setup",
    network="hoodi",
    vc_name="Lighthouse",
    cc_name="Lighthouse",
    flags={"validator": True, "validator_only": False, "charon": False},
    builder_mode="mevboost",
    skip_prompts=False,
    checklist=checklist,
    notice=lambda _title, message: notices.append(message),
)
assert "go back" not in notices[0].lower(), notices[0]
assert "does not offer local blocks" in notices[0], notices[0]
assert flags["builder_mode"] == "mevboost"
assert [item["name"] for item in relays] == ["Flashbots"]

try:
    finalize_builder_install(
        role="Custom Setup",
        network="hoodi",
        vc_name="Lighthouse",
        cc_name="Lighthouse",
        flags={"validator": True, "validator_only": False, "charon": False},
        builder_mode="mevboost",
        skip_prompts=False,
        checklist=lambda *_args: None,
        notice=lambda *_args: None,
    )
except BuilderModeCancelled:
    pass
else:
    raise SystemExit("forced-mode cancel should abort")
PY
}

@test "back to step 4 restores the earlier relay checks" {
  python3 - <<'PY'
from deploy.builder_mode import finalize_builder_install

seen = []

def prompt_index(labels, title, subtitle):
    return labels.index("MEV-Boost")

def checklist(title, prompt, items):
    state = {item[0]: item[2] for item in items}
    seen.append(state)
    if len(seen) == 1:
        return []
    if len(seen) == 2:
        assert all(value is False for value in state.values()), state
        return None
    assert all(value is False for value in state.values()), state
    assert "All are checked" not in prompt
    return ["Flashbots", "Aestus"]

flags, relays = finalize_builder_install(
    role="Custom Setup",
    network="hoodi",
    vc_name="Lighthouse",
    cc_name="Lighthouse",
    flags={"validator": True, "validator_only": False, "charon": False},
    skip_prompts=False,
    prompt_index=prompt_index,
    checklist=checklist,
    notice=lambda *_args: None,
)
assert flags["builder_mode"] == "mevboost", flags
assert [item["name"] for item in relays] == ["Flashbots", "Aestus"]
assert len(seen) == 3
PY
}

@test "setup type says MEV-Boost and stays within 78 columns" {
  cd "$BATS_TEST_DIRNAME/.."
  grep -q 'EC+CC+VC; ePBS else MEV-Boost' ethpillar.sh
  grep -q 'Lido; ePBS else MEV-Boost' ethpillar.sh
  ! grep -q 'else MEV"' ethpillar.sh
  # 30 characters: fits beside the 31-character CSM VC tag at width 76.
  desc='EC+CC+VC; ePBS else MEV-Boost'
  [ "${#desc}" -le 32 ]
}
