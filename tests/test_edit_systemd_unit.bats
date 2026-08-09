#!/usr/bin/env bats
# Tests for editSystemdUnitAndMaybeRestart and related wiring.

setup() {
  cd "$BATS_TEST_DIRNAME/.."
  export COMMAND_LOG
  COMMAND_LOG=$(mktemp)
  export UNIT_FILE
  UNIT_FILE=$(mktemp)
  cat > "$UNIT_FILE" <<'EOF'
[Unit]
Description=Test Client service for MAINNET

[Service]
ExecStart=/usr/local/bin/test-client --foo=1

[Install]
WantedBy=multi-user.target
EOF

  export EDITOR=nano
  export EDIT_MUTATE=0
  export WHIPTAIL_EXIT_CODE=0

  # Load only the helper (sourcing functions.sh runs apt/pip bootstrap).
  eval "$(sed -n '/^editSystemdUnitAndMaybeRestart()/,/^}$/p' ./functions.sh)"

  sudo() {
    echo "sudo $*" >> "$COMMAND_LOG"
    local cmd="${1:-}"
    shift || true
    case "$cmd" in
      test|sha256sum)
        command "$cmd" "$@"
        ;;
      systemctl|service)
        return 0
        ;;
      *)
        # Editor invoked as: sudo nano /path/to/unit
        if [[ "${EDIT_MUTATE:-0}" == "1" && -n "${1:-}" && -f "${1}" ]]; then
          printf '\n# edited-by-test\n' >> "$1"
        fi
        return 0
        ;;
    esac
  }
  export -f sudo

  whiptail() {
    echo "whiptail $*" >> "$COMMAND_LOG"
    if [[ "$*" == *"--msgbox"* ]]; then
      return 0
    fi
    return "${WHIPTAIL_EXIT_CODE:-0}"
  }
  export -f whiptail

  > "$COMMAND_LOG"
}

teardown() {
  rm -f "$COMMAND_LOG" "$UNIT_FILE"
}

@test "editSystemdUnitAndMaybeRestart: unchanged file skips restart prompt and reload" {
  EDIT_MUTATE=0
  run editSystemdUnitAndMaybeRestart "$UNIT_FILE" "Do you want to restart execution client?" execution
  [ "$status" -eq 0 ]
  ! grep -q -- "--yesno" "$COMMAND_LOG"
  ! grep -q "daemon-reload" "$COMMAND_LOG"
  ! grep -q "service execution restart" "$COMMAND_LOG"
}

@test "editSystemdUnitAndMaybeRestart: changed file + Yes reloads and restarts" {
  EDIT_MUTATE=1
  WHIPTAIL_EXIT_CODE=0
  run editSystemdUnitAndMaybeRestart "$UNIT_FILE" "Do you want to restart consensus client?" consensus
  [ "$status" -eq 0 ]
  grep -q -- "--yesno" "$COMMAND_LOG"
  grep -q "systemctl daemon-reload" "$COMMAND_LOG"
  grep -q "service consensus restart" "$COMMAND_LOG"
  grep -q "edited-by-test" "$UNIT_FILE"
}

@test "editSystemdUnitAndMaybeRestart: changed file + No still daemon-reloads, does not restart" {
  EDIT_MUTATE=1
  WHIPTAIL_EXIT_CODE=1
  run editSystemdUnitAndMaybeRestart "$UNIT_FILE" "Do you want to restart validator?" validator
  [ "$status" -eq 0 ]
  grep -q -- "--yesno" "$COMMAND_LOG"
  grep -q "systemctl daemon-reload" "$COMMAND_LOG"
  ! grep -q "service validator restart" "$COMMAND_LOG"
}

@test "editSystemdUnitAndMaybeRestart: missing unit shows msgbox and fails" {
  run editSystemdUnitAndMaybeRestart "/nonexistent/no-such.service" "restart?" execution
  [ "$status" -eq 1 ]
  grep -q -- "--msgbox" "$COMMAND_LOG"
  ! grep -q -- "--yesno" "$COMMAND_LOG"
}

@test "ethpillar.sh wires edit helper for EC CC VC MEV" {
  local dump
  dump=$(grep -A2 'editSystemdUnitAndMaybeRestart' ethpillar.sh || true)
  [[ "$dump" == *execution.service* ]]
  [[ "$dump" == *consensus.service* ]]
  [[ "$dump" == *validator.service* ]]
  [[ "$dump" == *mevboost.service* ]]
}

@test "compareSystemdDefaults daemon-reloads when restart is declined after apply" {
  # Else branch after the restart yesno must still reload unit definitions.
  awk '
    /Do you want to daemon-reload and restart/ { in_block=1 }
    in_block && /^    else$/ { else_seen=1 }
    else_seen && /systemctl daemon-reload/ { found=1 }
    END { exit found ? 0 : 1 }
  ' functions.sh
}

@test "compareSystemdDefaults skips apply when list-changed is empty" {
  grep -q 'No changes were saved in the left pane' functions.sh
  grep -q 'list-changed' functions.sh
}
