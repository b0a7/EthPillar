#!/usr/bin/env bats
# Coverage for Charon Import .charon TUI + Charon-aware validator key menus.

setup() {
  # shellcheck disable=SC1091
  source "$BATS_TEST_DIRNAME/../functions.sh"
  # shellcheck disable=SC1091
  source "$BATS_TEST_DIRNAME/../manage_validator_keys.sh" helpers-only

  export COMMAND_LOG
  COMMAND_LOG=$(mktemp)
  export BASE_DIR="$BATS_TEST_DIRNAME/.."
  export CHARON_SERVICE_FILE
  CHARON_SERVICE_FILE=$(mktemp)
  export VALIDATOR_SERVICE_FILE
  VALIDATOR_SERVICE_FILE=$(mktemp)
  export CHARON_CLUSTER_DIR
  CHARON_CLUSTER_DIR=$(mktemp -d)
  export CHARON_VALIDATOR_KEYS_DIR="$CHARON_CLUSTER_DIR/validator_keys"
  export IMPORT_SRC
  IMPORT_SRC=$(mktemp -d)
  export WHIPTAIL_LOG
  WHIPTAIL_LOG=$(mktemp)
  export WHIPTAIL_INPUT="$IMPORT_SRC/.charon"
  # One answer per line in this file (0=yes, 1=no). Survives whiptail subshells.
  export WHIPTAIL_YESNO_FILE
  WHIPTAIL_YESNO_FILE=$(mktemp)
  printf '0\n0\n' > "$WHIPTAIL_YESNO_FILE"
  export COPY_CHARON_RC=0

  cat > "$VALIDATOR_SERVICE_FILE" <<EOF
[Unit]
Description=Lodestar Validator Client service for SEPOLIA
[Service]
ExecStart=/usr/local/bin/lodestar validator
EOF

  cat > "$CHARON_SERVICE_FILE" <<EOF
[Service]
ExecStart=/usr/local/bin/charon run --beacon-node-endpoints=http://127.0.0.1:5052
EOF

  mkdir -p "$IMPORT_SRC/.charon/validator_keys"
  echo '{}' > "$IMPORT_SRC/.charon/cluster-lock.json"
  echo '{}' > "$IMPORT_SRC/.charon/validator_keys/keystore-0.json"
  echo 'secret' > "$IMPORT_SRC/.charon/validator_keys/keystore-0.txt"

  sudo() {
    echo "sudo $*" >> "$COMMAND_LOG"
    local cmd="${1:-}"
    shift || true
    case "$cmd" in
      test|find)
        command "$cmd" "$@"
        ;;
      *)
        return 0
        ;;
    esac
  }
  export -f sudo

  whiptail() {
    echo "whiptail $*" >> "$WHIPTAIL_LOG"
    if [[ "$*" == *"--inputbox"* ]]; then
      # Real whiptail prints the value on stderr; callers use 3>&1 1>&2 2>&3.
      printf '%s\n' "${WHIPTAIL_INPUT}" >&2
      return 0
    fi
    if [[ "$*" == *"--yesno"* ]]; then
      local ans=0
      if [[ -s "${WHIPTAIL_YESNO_FILE}" ]]; then
        ans=$(head -n1 "${WHIPTAIL_YESNO_FILE}")
        sed -i '1d' "${WHIPTAIL_YESNO_FILE}"
      fi
      return "${ans:-0}"
    fi
    return 0
  }
  export -f whiptail

  ohai() { :; }
  export -f ohai

  getClientVC() { VC="Lodestar"; }
  export -f getClientVC
  getValidatorClient() { VALIDATOR_CLIENT="Lodestar"; }
  export -f getValidatorClient

  runCopyCharonCluster() {
    echo "runCopyCharonCluster $*" >> "$COMMAND_LOG"
    if [[ "${COPY_CHARON_RC}" -ne 0 ]]; then
      return "${COPY_CHARON_RC}"
    fi
    local src="$1" dest="$2"
    mkdir -p "$dest"
    cp -a "$src/." "$dest/"
    return 0
  }
  export -f runCopyCharonCluster

  runImportCharonKeySharesYes() {
    echo "runImportCharonKeySharesYes" >> "$COMMAND_LOG"
  }
  export -f runImportCharonKeySharesYes

  > "$COMMAND_LOG"
  > "$WHIPTAIL_LOG"
}

teardown() {
  rm -f "$COMMAND_LOG" "$WHIPTAIL_LOG" "$WHIPTAIL_YESNO_FILE" "$CHARON_SERVICE_FILE" "$VALIDATOR_SERVICE_FILE"
  rm -rf "$CHARON_CLUSTER_DIR" "$IMPORT_SRC"
}

# ── path helpers / charonKeysharesPresent ─────────────────────────────────────

@test "getCharonClusterDir respects CHARON_CLUSTER_DIR override" {
  run getCharonClusterDir
  [ "$status" -eq 0 ]
  [ "$output" = "$CHARON_CLUSTER_DIR" ]
}

@test "charonKeysharesPresent is false when keys dir missing" {
  rm -rf "$CHARON_VALIDATOR_KEYS_DIR"
  run charonKeysharesPresent
  [ "$status" -ne 0 ]
}

@test "charonKeysharesPresent is false when no keystore json" {
  mkdir -p "$CHARON_VALIDATOR_KEYS_DIR"
  run charonKeysharesPresent
  [ "$status" -ne 0 ]
}

@test "charonKeysharesPresent is true when keystore-*.json exists" {
  mkdir -p "$CHARON_VALIDATOR_KEYS_DIR"
  echo '{}' > "$CHARON_VALIDATOR_KEYS_DIR/keystore-0.json"
  run charonKeysharesPresent
  [ "$status" -eq 0 ]
}

# ── validator key menu (Charon vs solo) ───────────────────────────────────────

@test "buildValidatorKeyMenuOptions shows Charon import when Charon enabled" {
  buildValidatorKeyMenuOptions
  local joined="${VALIDATOR_KEY_MENU_OPTIONS[*]}"
  [[ "$joined" == *"${OBOL_IMPORT_KEY_SHARES}"* ]]
  [[ "$joined" != *"Generate new validator keys"* ]]
  [[ "$joined" != *"Import validator keys from offline"* ]]
}

@test "buildValidatorKeyMenuOptions shows solo Generate/Import when Charon absent" {
  rm -f "$CHARON_SERVICE_FILE"
  export CHARON_SERVICE_FILE="/nonexistent/charon.service"
  buildValidatorKeyMenuOptions
  local joined="${VALIDATOR_KEY_MENU_OPTIONS[*]}"
  [[ "$joined" == *"Generate new validator keys"* ]]
  [[ "$joined" == *"Import validator keys from offline"* ]]
  [[ "$joined" != *"${OBOL_IMPORT_KEY_SHARES}"* ]]
}

# ── importCharonClusterFolder ─────────────────────────────────────────────────

@test "importCharonClusterFolder copies cluster and offers keyshare import" {
  printf '0\n0\n' > "$WHIPTAIL_YESNO_FILE"  # confirm copy, yes import keys
  run importCharonClusterFolder
  [ "$status" -eq 0 ]
  [ -f "$CHARON_CLUSTER_DIR/cluster-lock.json" ]
  [ -f "$CHARON_CLUSTER_DIR/validator_keys/keystore-0.json" ]
  grep -q "runCopyCharonCluster" "$COMMAND_LOG"
  grep -q "runImportCharonKeySharesYes" "$COMMAND_LOG"
  grep -q "Also import them into" "$WHIPTAIL_LOG"
}

@test "importCharonClusterFolder accepts parent path containing .charon" {
  WHIPTAIL_INPUT="$IMPORT_SRC"  # parent of .charon
  printf '0\n1\n' > "$WHIPTAIL_YESNO_FILE"  # confirm copy, decline key import
  run importCharonClusterFolder
  [ "$status" -eq 0 ]
  [ -f "$CHARON_CLUSTER_DIR/cluster-lock.json" ]
  ! grep -q "runImportCharonKeySharesYes" "$COMMAND_LOG"
  grep -q "Start Charon" "$WHIPTAIL_LOG"
}

@test "importCharonClusterFolder skips keyshare prompt without validator.service" {
  rm -f "$VALIDATOR_SERVICE_FILE"
  export VALIDATOR_SERVICE_FILE="/nonexistent/validator.service"
  printf '0\n' > "$WHIPTAIL_YESNO_FILE"
  run importCharonClusterFolder
  [ "$status" -eq 0 ]
  [ -f "$CHARON_CLUSTER_DIR/cluster-lock.json" ]
  ! grep -q "Also import them into" "$WHIPTAIL_LOG"
  ! grep -q "runImportCharonKeySharesYes" "$COMMAND_LOG"
}

@test "importCharonClusterFolder fails when cluster-lock.json missing" {
  rm -f "$IMPORT_SRC/.charon/cluster-lock.json"
  run importCharonClusterFolder
  [ "$status" -ne 0 ]
  grep -q "Missing cluster-lock.json" "$WHIPTAIL_LOG"
  ! grep -q "runCopyCharonCluster" "$COMMAND_LOG"
}

@test "importCharonClusterFolder fails when copy_charon returns non-zero" {
  COPY_CHARON_RC=1
  printf '0\n' > "$WHIPTAIL_YESNO_FILE"
  run importCharonClusterFolder
  [ "$status" -ne 0 ]
  grep -q "Failed to copy" "$WHIPTAIL_LOG"
  ! grep -q "runImportCharonKeySharesYes" "$COMMAND_LOG"
}

# ── ethpillar.sh wiring ───────────────────────────────────────────────────────

@test "ethpillar Charon submenu includes Import .charon cluster folder" {
  grep -q 'Import .charon cluster folder' "$BATS_TEST_DIRNAME/../ethpillar.sh"
  grep -q 'importCharonClusterFolder' "$BATS_TEST_DIRNAME/../ethpillar.sh"
}

@test "ethpillar validator menu uses charon-import when Charon enabled" {
  grep -q 'manage_validator_keys.sh charon-import' "$BATS_TEST_DIRNAME/../ethpillar.sh"
  grep -q 'OBOL_IMPORT_KEY_SHARES' "$BATS_TEST_DIRNAME/../ethpillar.sh"
}

@test "manage_validator_keys supports charon-import-yes mode" {
  grep -q 'charon-import-yes' "$BATS_TEST_DIRNAME/../manage_validator_keys.sh"
  grep -q 'skip_confirm' "$BATS_TEST_DIRNAME/../manage_validator_keys.sh"
}
