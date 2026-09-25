#!/usr/bin/env bats
#
# tests/test_history_expiry_suggestions.bats
#
# Unit tests for helpers/history_expiry_suggestions.sh detection,
# suggestion text, and ExecStart prune-flag merge. Mock Description /
# ExecStart strings only — never talks to a live Ethereum client.
#
# Run: bats tests/test_history_expiry_suggestions.bats
#

setup() {
  cd "$BATS_TEST_DIRNAME/.."
  # shellcheck disable=SC1091
  source ./helpers/history_expiry_suggestions.sh
}

@test "history_expiry_has_flag matches equals and space-separated values" {
  run history_expiry_has_flag "--history.chain=postmerge --http" "--history.chain" "postmerge"
  [ "$status" -eq 0 ]
  run history_expiry_has_flag "--history.chain postmerge --http" "--history.chain" "postmerge"
  [ "$status" -eq 0 ]
  run history_expiry_has_flag "--history.chain=all --http" "--history.chain" "postmerge"
  [ "$status" -ne 0 ]
}

@test "history_expiry_has_flag does not treat dots as regex" {
  run history_expiry_has_flag "--historyXchain=postmerge" "--history.chain" "postmerge"
  [ "$status" -ne 0 ]
}

@test "detects Geth from Description and ExecStart fallback" {
  run history_expiry_detect_client "Geth Execution Layer Client service for MAINNET" ""
  [ "$output" = "Geth" ]
  run history_expiry_detect_client "" "/usr/local/bin/geth --mainnet --http"
  [ "$output" = "Geth" ]
}

@test "detects Erigon-Caplin from Description first token" {
  run history_expiry_detect_client "Erigon-Caplin Integrated Execution-Consensus Client for MAINNET" "/usr/local/bin/erigon --caplin.enable-upnp"
  [ "$output" = "Erigon-Caplin" ]
}

@test "Geth without history.chain is missing and suggests postprague" {
  local execstart="/usr/local/bin/geth --mainnet --state.scheme=path --datadir=/var/lib/geth"
  run history_expiry_status "Geth" "$execstart"
  [ "$output" = "missing" ]
  run history_expiry_suggested_flags "Geth"
  [[ "$output" == *"--history.chain=postprague"* ]]
  run history_expiry_apply_extra "Geth"
  [[ "$output" == *"prune-history"* ]]
  [[ "$output" == *"postprague"* ]]
}

@test "Geth --history.chain=postmerge is missing not recommended" {
  run history_expiry_status "Geth" "/usr/local/bin/geth --history.chain=postmerge --state.scheme=path"
  [ "$output" = "missing" ]
  run history_expiry_checker_level "missing"
  [ "$output" = "WARN" ]
}

@test "Geth --history.chain postprague is recommended" {
  run history_expiry_status "Geth" "/usr/local/bin/geth --history.chain postprague"
  [ "$output" = "recommended" ]
}

@test "Geth --history.chain=recent is recommended" {
  run history_expiry_status "Geth" "/usr/local/bin/geth --history.chain=recent --history.blocks=200000"
  [ "$output" = "recommended" ]
}

@test "Geth archive gcmode is archive not FAIL" {
  run history_expiry_status "Geth" "/usr/local/bin/geth --gcmode=archive --syncmode full"
  [ "$output" = "archive" ]
  run history_expiry_checker_level "archive"
  [ "$output" = "INFO" ]
}

@test "Nethermind Hybrid without History.Pruning is missing" {
  local execstart
  execstart="/usr/local/bin/nethermind/nethermind --Pruning.Mode=Hybrid --Pruning.FullPruningTrigger=VolumeFreeSpace --Pruning.FullPruningThresholdMb=300000"
  run history_expiry_status "Nethermind" "$execstart"
  [ "$output" = "missing" ]
  run history_expiry_suggested_flags "Nethermind"
  [[ "$output" == *"--History.Pruning=Rolling --History.RetentionEpochs=33024"* ]]
  run history_expiry_notes "Nethermind" "missing"
  [[ "$output" == *"Flat"* ]]
  [[ "$output" == *"Patricia"* ]]
  [[ "$output" != *"82125"* ]]
}

@test "Nethermind History.Pruning=Rolling is recommended" {
  run history_expiry_status "Nethermind" "--Pruning.Mode=Hybrid --History.Pruning=Rolling"
  [ "$output" = "recommended" ]
}

@test "Nethermind History.Pruning=UseAncientBarriers is recommended" {
  run history_expiry_status "Nethermind" "--History.Pruning=UseAncientBarriers"
  [ "$output" = "recommended" ]
}

@test "missing flags map to WARN not FAIL" {
  run history_expiry_checker_level "missing"
  [ "$output" = "WARN" ]
}

@test "Nethermind Pruning.Mode=None is archive" {
  run history_expiry_status "Nethermind" "--Pruning.Mode=None --Sync.FastSync=false"
  [ "$output" = "archive" ]
}

@test "Besu SNAP is recommended" {
  run history_expiry_status "Besu" "/usr/local/bin/besu/bin/besu --sync-mode=SNAP --data-storage-format=BONSAI"
  [ "$output" = "recommended" ]
}

@test "Besu Forest is archive" {
  run history_expiry_status "Besu" "--sync-mode=FULL --data-storage-format=FOREST"
  [ "$output" = "archive" ]
}

@test "Reth --full is recommended" {
  run history_expiry_status "Reth" "/usr/local/bin/reth node --full --chain mainnet"
  [ "$output" = "recommended" ]
}

@test "Reth with no prune profile is archive" {
  run history_expiry_status "Reth" "/usr/local/bin/reth node --chain mainnet --http"
  [ "$output" = "archive" ]
}

@test "Erigon prune.mode=minimal is recommended" {
  run history_expiry_status "Erigon" "/usr/local/bin/erigon --prune.mode=minimal --externalcl"
  [ "$output" = "recommended" ]
}

@test "Erigon prune.mode=archive is archive" {
  run history_expiry_status "Erigon" "--prune.mode=archive --prune.distance=0"
  [ "$output" = "archive" ]
}

@test "Erigon-Caplin archive flags are INFO caplin_archive" {
  local execstart
  execstart="/usr/local/bin/erigon --prune.mode=minimal --caplin.states-archive=true --caplin.blocks-archive=true"
  run history_expiry_status "Erigon-Caplin" "$execstart"
  [ "$output" = "caplin_archive" ]
  run history_expiry_checker_level "caplin_archive"
  [ "$output" = "INFO" ]
}

@test "Ethrex is unsupported INFO not WARN" {
  run history_expiry_status "Ethrex" "/usr/local/bin/ethrex --syncmode snap"
  [ "$output" = "unsupported" ]
  run history_expiry_checker_level "unsupported"
  [ "$output" = "INFO" ]
}

@test "checker never maps statuses to FAIL" {
  local status
  for status in recommended missing archive caplin_archive unsupported unknown no_el; do
    run history_expiry_checker_level "$status"
    [[ "$output" != "FAIL" ]]
  done
}

@test "evaluate_unit_text parses multiline ExecStart and prints Geth suggestion" {
  local unit
  unit=$(cat <<'EOF'
[Unit]
Description=Geth Execution Layer Client service for MAINNET

[Service]
ExecStart=/usr/local/bin/geth \
    --mainnet \
    --state.scheme=path \
    --datadir=/var/lib/geth
EOF
)
  run history_expiry_evaluate_unit_text "$unit" ""
  [ "$status" -eq 0 ]
  [[ "$output" == $'missing\n'* ]]
  [[ "$output" == *"Detected: Geth"* ]]
  [[ "$output" == *"missing recommended expiry flags"* ]]
  [[ "$output" == *"--history.chain=postprague"* ]]
  [[ "$output" == *"Recommended flags:"* ]]
  [[ "$output" == *"How to apply:"* ]]
  [[ "$output" == *"Execution Client → Suggest pruning parameters"* ]]
  [[ "$output" != *"ExecStart (collapsed)"* ]]
}

@test "evaluate_unit_text labels Caplin archive as INFO opt-in" {
  local unit
  unit=$(cat <<'EOF'
[Unit]
Description=Erigon-Caplin Integrated Execution-Consensus Client for MAINNET

[Service]
ExecStart=/usr/local/bin/erigon --prune.mode=minimal --caplin.states-archive=true
EOF
)
  run history_expiry_evaluate_unit_text "$unit" ""
  [ "$status" -eq 0 ]
  [[ "$output" == $'caplin_archive\n'* ]]
  [[ "$output" == *"not a failure"* ]]
  [[ "$output" == *"--prune.mode=minimal"* ]]
}

@test "CLI --all prints per-client table without needing a unit" {
  run bash ./helpers/history_expiry_suggestions.sh --all --no-pause
  [ "$status" -eq 0 ]
  [[ "$output" == *"Geth"* ]]
  [[ "$output" == *"Nethermind"* ]]
  [[ "$output" == *"Besu"* ]]
  [[ "$output" == *"Reth"* ]]
  [[ "$output" == *"Erigon"* ]]
  [[ "$output" == *"Ethrex"* ]]
  [[ "$output" == *"--history.chain=postprague"* ]]
  [[ "$output" == *"--History.Pruning=Rolling --History.RetentionEpochs=33024"* ]]
  [[ "$output" == *"Suggestions only"* ]]
  [[ "$output" == *"Recommended flags:"* ]]
  [[ "$output" != *"82125"* ]]
}

@test "CLI --unit uses a mock execution.service path" {
  local unit
  unit=$(mktemp)
  cat > "$unit" <<'EOF'
[Unit]
Description=Reth Execution Layer Client service for MAINNET
[Service]
ExecStart=/usr/local/bin/reth node --full --chain mainnet
EOF
  run bash ./helpers/history_expiry_suggestions.sh --unit "$unit" --no-pause
  [ "$status" -eq 0 ]
  [[ "$output" == *"Detected: Reth"* ]]
  [[ "$output" == *"OK — recommended flags present"* ]]
  [[ "$output" != *"ExecStart (collapsed)"* ]]
  rm -f "$unit"
}

@test "CLI --verbose includes collapsed ExecStart" {
  local unit
  unit=$(mktemp)
  cat > "$unit" <<'EOF'
[Unit]
Description=Nethermind Execution Layer Client service for MAINNET
[Service]
ExecStart=/usr/local/bin/nethermind/nethermind --Pruning.Mode=Hybrid
EOF
  run bash ./helpers/history_expiry_suggestions.sh --unit "$unit" --verbose --no-pause
  [ "$status" -eq 0 ]
  [[ "$output" == *"ExecStart (collapsed)"* ]]
  [[ "$output" == *"--Pruning.Mode=Hybrid"* ]]
  rm -f "$unit"
}

@test "evaluate_unit_text Nethermind missing flags is scan-friendly" {
  local unit
  unit=$(cat <<'EOF'
[Unit]
Description=Nethermind Execution Layer Client service for MAINNET

[Service]
ExecStart=/usr/local/bin/nethermind/nethermind \
    --Pruning.Mode=Hybrid \
    --Pruning.FullPruningTrigger=VolumeFreeSpace \
    --Pruning.FullPruningThresholdMb=300000
EOF
)
  run history_expiry_evaluate_unit_text "$unit" ""
  [ "$status" -eq 0 ]
  [[ "$output" == $'missing\n'* ]]
  [[ "$output" == *"Detected: Nethermind — missing recommended expiry flags"* ]]
  [[ "$output" == *"Recommended flags:"* ]]
  [[ "$output" == *"--History.Pruning=Rolling --History.RetentionEpochs=33024"* ]]
  [[ "$output" == *"How to apply:"* ]]
  [[ "$output" == *"Execution Client → Suggest pruning parameters"* ]]
  [[ "$output" == *"Notes:"* ]]
  [[ "$output" == *"Flat"* ]]
  [[ "$output" != *"ExecStart (collapsed)"* ]]
  [[ "$output" != *"82125"* ]]
}

@test "suggestPruningParameters reuses compare tmeld apply path" {
  grep -q 'prepare-prune-suggest' functions.sh
  grep -q 'finishTmeldSystemdApply' functions.sh
  awk '
    /^suggestPruningParameters\(\)/ { in_fn=1 }
    in_fn && /finishTmeldSystemdApply/ { found=1 }
    in_fn && /^}/ { exit found ? 0 : 1 }
  ' functions.sh
  awk '
    /^compareSystemdDefaults\(\)/ { in_fn=1 }
    in_fn && /finishTmeldSystemdApply/ { found=1 }
    in_fn && /^}/ { exit found ? 0 : 1 }
  ' functions.sh
}

@test "prune-suggest help opens the unit diff not the folder list" {
  awk '
    /^suggestPruningParameters\(\)/ { in_fn=1 }
    in_fn && /folder view/ { bad=1 }
    in_fn && /^}/ { exit bad ? 1 : 0 }
  ' functions.sh
  grep -q 'tmeld_pane_paths' manage/config_compare.py
  grep -q 'content diff' manage/config_compare.py
}

@test "history expiry is under Execution Client, not Toolbox" {
  awk '
    /^submenuExecution\(\)/ { in_el=1; in_tools=0 }
    /^submenuTools\(\)/ { in_tools=1; in_el=0 }
    /^}/ { if (in_el || in_tools) { in_el=0; in_tools=0 } }
    in_el && /suggestPruningParameters/ { el=1 }
    in_tools && /suggestPruningParameters/ { tools=1 }
    END { exit (el && !tools) ? 0 : 1 }
  ' ethpillar.sh
  grep -q 'Suggest pruning parameters' functions.sh
  grep -q 'buildExecutionSuboptions' ethpillar.sh
}

@test "Execution Client menu tags are sequential with Suggest pruning as 6" {
  grep -q 'buildExecutionSuboptions' ethpillar.sh
  awk '
    /^submenuExecution\(\)/ { in_fn=1 }
    in_fn && /suggestPruningParameters/ { found=1 }
    in_fn && /^}/ { exit found ? 0 : 1 }
  ' ethpillar.sh
  # shellcheck disable=SC1091
  source ./functions.sh
  buildExecutionSuboptions "Geth"
  [[ "$EXEC_MENU_SUGGEST" == "6" ]]
  [[ "$EXEC_MENU_BACK" == "11" ]]
  [[ "${SUBOPTIONS[*]}" == *"Suggest pruning parameters"* ]]
  local prev="" tag
  local i
  for ((i = 0; i < ${#SUBOPTIONS[@]}; i += 2)); do
    tag="${SUBOPTIONS[$i]}"
    [[ "$tag" == "-" ]] && continue
    if [[ -n "$prev" ]]; then
      [[ "$tag" -eq $((prev + 1)) ]]
    fi
    prev="$tag"
  done
}

@test "Ethrex Execution menu omits Suggest pruning and stays sequential" {
  run history_expiry_prune_suggest_menu_visible "Ethrex"
  [ "$status" -ne 0 ]
  run history_expiry_prune_suggest_menu_visible "Geth"
  [ "$status" -eq 0 ]
  run history_expiry_prune_suggest_menu_visible "Nethermind"
  [ "$status" -eq 0 ]
  # shellcheck disable=SC1091
  source ./functions.sh
  buildExecutionSuboptions "Ethrex"
  [[ -z "$EXEC_MENU_SUGGEST" ]]
  [[ "$EXEC_MENU_UPDATE" == "6" ]]
  [[ "$EXEC_MENU_BACK" == "10" ]]
  [[ "${SUBOPTIONS[*]}" != *"Suggest pruning parameters"* ]]
  local prev="" tag
  local i
  for ((i = 0; i < ${#SUBOPTIONS[@]}; i += 2)); do
    tag="${SUBOPTIONS[$i]}"
    [[ "$tag" == "-" ]] && continue
    if [[ -n "$prev" ]]; then
      [[ "$tag" -eq $((prev + 1)) ]]
    fi
    prev="$tag"
  done
}

@test "docs and node-checker do not point Suggest pruning parameters at Toolbox" {
  run grep -n -i 'toolbox' docs/history-expiry-suggestions.md
  [ "$status" -ne 0 ]
  run grep -n 'Toolbox → History expiry' plugins/node-checker/run.sh
  [ "$status" -ne 0 ]
  grep -q 'Execution Client → Suggest pruning parameters' plugins/node-checker/run.sh
  grep -q 'Suggest pruning parameters' docs/history-expiry-suggestions.md
}

@test "further savings exist for Besu Reth and not Geth Nethermind Erigon" {
  run history_expiry_has_further_savings "Geth"
  [ "$status" -ne 0 ]
  run history_expiry_has_further_savings "Besu"
  [ "$status" -eq 0 ]
  run history_expiry_has_further_savings "Reth"
  [ "$status" -eq 0 ]
  run history_expiry_has_further_savings "Nethermind"
  [ "$status" -ne 0 ]
  run history_expiry_has_further_savings "Erigon"
  [ "$status" -ne 0 ]
  run history_expiry_flags_for_level "Geth" "recommended"
  [ "$output" = "--history.chain=postprague" ]
  run history_expiry_flags_for_level "Geth" "further"
  [ "$output" = "--history.chain=postprague" ]
  run history_expiry_flags_for_level "Nethermind" "further"
  [[ "$output" == *"--History.Pruning=Rolling"* ]]
}

@test "Suggest pruning radiolist names Recommended as suitable for a ~2TB drive" {
  grep -F -q 'Recommended (suitable for a ~2TB drive)' functions.sh
  grep -F -q 'Recommended is the usual choice for home staking on ~2TB disks' functions.sh
  ! grep -F -q 'Recommended (~2TB staking)' functions.sh
  ! grep -F -q 'Recommended is the ~2TB staking default' functions.sh
  grep -F -q 'Suitable for a ~2TB drive:** `--history.chain=postprague`' docs/history-expiry-suggestions.md
  ! grep -F -q '300-500' helpers/history_expiry_suggestions.sh
  ! grep -F -q '300–500' docs/history-expiry-suggestions.md
}

@test "merge adds recommended Geth flags and leaves unrelated flags" {
  run history_expiry_merge_execstart \
    "/usr/local/bin/geth --mainnet --state.scheme=path --http --datadir=/var/lib/geth" \
    "--history.chain=postmerge"
  [ "$status" -eq 0 ]
  [[ "$output" == *"--state.scheme=path"* ]]
  [[ "$output" == *"--http"* ]]
  [[ "$output" == *"--datadir=/var/lib/geth"* ]]
  [[ "$output" == *"--history.chain=postmerge"* ]]
  [[ "$output" == *"--mainnet"* ]]
}

@test "merge replaces conflicting Geth --history.chain peer" {
  run history_expiry_merge_execstart \
    "/usr/local/bin/geth --history.chain=all --http --maxpeers=50" \
    "--history.chain=postmerge"
  [ "$status" -eq 0 ]
  [[ "$output" == *"--history.chain=postmerge"* ]]
  [[ "$output" != *"--history.chain=all"* ]]
  [[ "$output" == *"--http"* ]]
  [[ "$output" == *"--maxpeers=50"* ]]
}

@test "merge replaces space-separated Geth --history.chain peer" {
  run history_expiry_merge_execstart \
    "/usr/local/bin/geth --history.chain all --http" \
    "--history.chain=postmerge"
  [ "$status" -eq 0 ]
  [[ "$output" == *"--history.chain=postmerge"* ]]
  [[ "$output" != *" all "* ]]
  [[ "$output" == *"--http"* ]]
}

@test "merge replaces Nethermind History.Pruning and keeps Hybrid" {
  run history_expiry_merge_execstart \
    "/usr/local/bin/nethermind --Pruning.Mode=Hybrid --History.Pruning=Disabled --JsonRpc.Port=8545" \
    "--History.Pruning=Rolling --History.RetentionEpochs=33024"
  [ "$status" -eq 0 ]
  [[ "$output" == *"--Pruning.Mode=Hybrid"* ]]
  [[ "$output" == *"--History.Pruning=Rolling"* ]]
  [[ "$output" == *"--History.RetentionEpochs=33024"* ]]
  [[ "$output" != *"--History.Pruning=Disabled"* ]]
  [[ "$output" == *"--JsonRpc.Port=8545"* ]]
}

@test "merge replaces Erigon --prune.mode and leaves unrelated flags" {
  run history_expiry_merge_execstart \
    "/usr/local/bin/erigon --datadir=/var/lib/erigon --prune.mode=archive --http.port=8545" \
    "--prune.mode=minimal"
  [ "$status" -eq 0 ]
  [[ "$output" == *"--prune.mode=minimal"* ]]
  [[ "$output" != *"--prune.mode=archive"* ]]
  [[ "$output" == *"--datadir=/var/lib/erigon"* ]]
  [[ "$output" == *"--http.port=8545"* ]]
}

@test "merge is a no-op when recommended flags are already present" {
  run history_expiry_merge_execstart \
    "/usr/local/bin/geth --history.chain=postprague --http" \
    "--history.chain=postprague"
  [ "$status" -eq 0 ]
  [ "$output" = "/usr/local/bin/geth --history.chain=postprague --http" ]
}

@test "merge unit text only changes ExecStart" {
  local unit merged
  unit=$(cat <<'EOF'
[Unit]
Description=Geth Execution Layer Client service for MAINNET

[Service]
User=execution
ExecStart=/usr/local/bin/geth \
    --mainnet \
    --state.scheme=path \
    --datadir=/var/lib/geth
LimitNOFILE=65535
EOF
)
  merged=$(history_expiry_merge_unit_text "$unit" "--history.chain=postmerge")
  [[ "$merged" == *"Description=Geth Execution Layer Client service for MAINNET"* ]]
  [[ "$merged" == *"User=execution"* ]]
  [[ "$merged" == *"LimitNOFILE=65535"* ]]
  [[ "$merged" == *"--history.chain=postmerge"* ]]
  [[ "$merged" == *"--state.scheme=path"* ]]
  [[ "$merged" == *"--datadir=/var/lib/geth"* ]]
  [[ "$merged" != *"--history.chain=all"* ]]
}

@test "pre-tmeld warnings mention offline prune as notes only" {
  run history_expiry_pre_tmeld_warnings "Geth" "missing" "Lighthouse"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Pruning is destructive"* ]]
  [[ "$output" == *"eth_getLogs"* ]]
  [[ "$output" == *"do not run automatically"* ]]
  [[ "$output" == *"prune-history"* ]]
  [[ "$output" == *"postprague"* ]]
  [[ "$output" == *"CL: Lighthouse"* ]]
  run history_expiry_pre_tmeld_warnings "Geth" "missing" "Lighthouse" "--history.chain=recent --history.blocks=200000"
  [[ "$output" == *"history.chain recent"* ]]
}
