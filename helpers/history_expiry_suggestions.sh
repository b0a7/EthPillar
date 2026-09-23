#!/usr/bin/env bash
#
# Author: coincashew.eth | coincashew.com
# License: GNU GPL
# Source: https://github.com/coincashew/ethpillar
# Description: Print suggested history-expiry / prune flags for ~2TB staking nodes.
#
# Made for home and solo stakers 🏠🥩
#
# Suggest-first / print-only. Does not rewrite systemd units.
# Research notes: docs/history-expiry-suggestions.md
#

# Allow sourcing from node-checker / bats without running main.
if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
  _HISTORY_EXPIRY_SELF="${BASH_SOURCE[0]}"
else
  _HISTORY_EXPIRY_SELF="$0"
fi

history_expiry_norm() {
  # Lowercase and collapse whitespace so `--flag value` and `--flag=value` match.
  printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]' | tr '\n' ' ' | sed 's/[[:space:]]\+/ /g'
}

history_expiry_has_flag() {
  # Usage: history_expiry_has_flag HAYSTACK FLAG [VALUE]
  # Glob matching (not regex) so dots in flag names are literal.
  local hay flag value
  hay=" $(history_expiry_norm "${1:-}") "
  flag=$(history_expiry_norm "${2:-}")
  value=$(history_expiry_norm "${3:-}")
  [[ -n "$flag" ]] || return 1
  if [[ -n "$value" ]]; then
    [[ "$hay" == *" ${flag}=${value} "* || "$hay" == *" ${flag} ${value} "* ]]
    return $?
  fi
  [[ "$hay" == *" ${flag} "* || "$hay" == *" ${flag}="* ]]
}

history_expiry_extract_description() {
  printf '%s\n' "${1:-}" | grep -m1 -E '^Description=' | sed 's/^Description=//'
}

history_expiry_extract_execstart() {
  # Join a systemd ExecStart= block (backslash continuations) into one line.
  local content="${1:-}" in_exec=0 line payload
  local -a parts=()
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ $in_exec -eq 0 ]]; then
      if [[ "$line" == ExecStart=* ]]; then
        in_exec=1
        payload="${line#ExecStart=}"
      else
        continue
      fi
    else
      payload="$line"
    fi
    if [[ "$payload" == *\\ ]]; then
      payload="${payload%\\}"
      parts+=("$payload")
      continue
    fi
    parts+=("$payload")
    break
  done <<< "$content"
  printf '%s' "${parts[*]}"
}

history_expiry_detect_client() {
  # Prefer Description first token (EthPillar style); fall back to ExecStart binary.
  local description="${1:-}"
  local execstart="${2:-}"
  local token
  token=$(printf '%s' "$description" | awk '{print $1}')
  case "$token" in
    Geth|Nethermind|Besu|Reth|Ethrex) printf '%s\n' "$token"; return 0 ;;
    Erigon-Caplin) printf '%s\n' "Erigon-Caplin"; return 0 ;;
    Erigon) printf '%s\n' "Erigon"; return 0 ;;
  esac
  local n
  n=$(history_expiry_norm "$execstart")
  if [[ "$n" == *nethermind* ]]; then echo "Nethermind"
  elif [[ "$n" == *besu* ]]; then echo "Besu"
  elif [[ "$n" == *reth* ]]; then echo "Reth"
  elif [[ "$n" == *ethrex* ]]; then echo "Ethrex"
  elif [[ "$n" == *erigon* ]]; then
    if [[ "$n" == *caplin* ]]; then echo "Erigon-Caplin"; else echo "Erigon"; fi
  elif [[ "$n" == *geth* ]]; then echo "Geth"
  else echo ""
  fi
}

history_expiry_is_el_archive() {
  local client="${1:-}" execstart="${2:-}"
  case "$client" in
    Geth)
      history_expiry_has_flag "$execstart" "--gcmode" "archive" && return 0
      history_expiry_has_flag "$execstart" "--history.state" "0" && return 0
      return 1
      ;;
    Nethermind)
      history_expiry_has_flag "$execstart" "--Pruning.Mode" "None" && return 0
      history_expiry_has_flag "$execstart" "--Pruning.Mode" "Archive" && return 0
      return 1
      ;;
    Besu)
      history_expiry_has_flag "$execstart" "--data-storage-format" "FOREST" && return 0
      history_expiry_has_flag "$execstart" "--data-storage-format" "X_BONSAI_ARCHIVE" && return 0
      return 1
      ;;
    Reth)
      history_expiry_has_flag "$execstart" "--archive" && return 0
      # Reth default with no prune profile is archive.
      if history_expiry_has_flag "$execstart" "--full"; then return 1; fi
      if history_expiry_has_flag "$execstart" "--minimal"; then return 1; fi
      if history_expiry_has_flag "$execstart" "--prune.bodies.pre-merge"; then return 1; fi
      if history_expiry_has_flag "$execstart" "--prune.bodies.distance"; then return 1; fi
      if history_expiry_has_flag "$execstart" "--prune.mode"; then return 1; fi
      [[ -n "$execstart" ]]
      return $?
      ;;
    Erigon|Erigon-Caplin)
      history_expiry_has_flag "$execstart" "--prune.mode" "archive" && return 0
      return 1
      ;;
    *)
      return 1
      ;;
  esac
}

history_expiry_is_caplin_archive() {
  local execstart="${1:-}"
  history_expiry_has_flag "$execstart" "--caplin.states-archive" && return 0
  history_expiry_has_flag "$execstart" "--caplin.blocks-archive" && return 0
  history_expiry_has_flag "$execstart" "--caplin.blobs-archive" && return 0
  history_expiry_has_flag "$execstart" "--caplin.blobs-no-pruning" && return 0
  history_expiry_has_flag "$execstart" "--caplin.archive" && return 0
  return 1
}

history_expiry_has_recommended() {
  local client="${1:-}" execstart="${2:-}"
  case "$client" in
    Geth)
      history_expiry_has_flag "$execstart" "--history.chain" "postmerge" && return 0
      history_expiry_has_flag "$execstart" "--history.chain" "postprague" && return 0
      history_expiry_has_flag "$execstart" "--history.chain" "recent" && return 0
      return 1
      ;;
    Nethermind)
      history_expiry_has_flag "$execstart" "--History.Pruning" "Rolling" && return 0
      history_expiry_has_flag "$execstart" "--History.Pruning" "UseAncientBarriers" && return 0
      return 1
      ;;
    Besu)
      history_expiry_has_flag "$execstart" "--sync-mode" "SNAP" && return 0
      history_expiry_has_flag "$execstart" "--Xchain-pruning-enabled" && return 0
      return 1
      ;;
    Reth)
      history_expiry_has_flag "$execstart" "--full" && return 0
      history_expiry_has_flag "$execstart" "--minimal" && return 0
      history_expiry_has_flag "$execstart" "--prune.bodies.pre-merge" && return 0
      history_expiry_has_flag "$execstart" "--prune.bodies.distance" && return 0
      return 1
      ;;
    Erigon|Erigon-Caplin)
      history_expiry_has_flag "$execstart" "--prune.mode" "minimal" && return 0
      history_expiry_has_flag "$execstart" "--prune.mode" "full" && return 0
      history_expiry_has_flag "$execstart" "--prune.mode" "blocks" && return 0
      return 1
      ;;
    Ethrex)
      # No history-expiry flags yet; snap is the staking-oriented default.
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

history_expiry_status() {
  # Prints: recommended | missing | archive | caplin_archive | unsupported | unknown | no_el
  local client="${1:-}" execstart="${2:-}"
  if [[ -z "$client" && -z "$execstart" ]]; then
    echo "no_el"
    return 0
  fi
  if [[ -z "$client" ]]; then
    echo "unknown"
    return 0
  fi
  if [[ "$client" == "Ethrex" ]]; then
    echo "unsupported"
    return 0
  fi
  if history_expiry_is_el_archive "$client" "$execstart"; then
    echo "archive"
    return 0
  fi
  if [[ "$client" == "Erigon-Caplin" ]] && history_expiry_is_caplin_archive "$execstart"; then
    echo "caplin_archive"
    return 0
  fi
  if history_expiry_has_recommended "$client" "$execstart"; then
    echo "recommended"
    return 0
  fi
  echo "missing"
}

history_expiry_status_plain() {
  # Operator-facing status words (not the machine token).
  case "${1:-}" in
    recommended) echo "OK — recommended flags present" ;;
    missing) echo "missing recommended expiry flags" ;;
    archive) echo "archive / full-history (suggestions are opt-in)" ;;
    caplin_archive) echo "Caplin archive flags present (not a failure)" ;;
    unsupported) echo "no history-expiry CLI yet" ;;
    no_el) echo "no execution client detected" ;;
    *) echo "could not classify" ;;
  esac
}

history_expiry_suggested_flags() {
  local client="${1:-}"
  case "$client" in
    Geth)
      echo "--history.chain=postmerge"
      ;;
    Nethermind)
      echo "--History.Pruning=Rolling --History.RetentionEpochs=33024"
      ;;
    Besu)
      echo "--sync-mode=SNAP --data-storage-format=BONSAI"
      ;;
    Reth)
      echo "--full"
      ;;
    Erigon|Erigon-Caplin)
      echo "--prune.mode=minimal"
      ;;
    Ethrex)
      echo "(none — keep --syncmode snap)"
      ;;
    *)
      echo "(unknown client)"
      ;;
  esac
}

history_expiry_suggested_why() {
  local client="${1:-}"
  case "$client" in
    Geth) echo "Drops pre-merge PoW history (~300-500 GB) on a ~2TB staking disk." ;;
    Nethermind) echo "Rolling history window (~5 months / 33024 epochs). Hybrid is state prune only." ;;
    Besu) echo "SNAP + BONSAI already skips pre-merge bodies on Mainnet checkpoint sync." ;;
    Reth) echo "Staking full-node profile: pre-merge body prune, ~10k-block state/receipt window." ;;
    Erigon|Erigon-Caplin) echo "Leanest built-in mode (~100k blocks / ~14 days). Usual 2TB staking choice." ;;
    Ethrex) echo "Ethrex has no history-expiry CLI yet." ;;
    *) echo "No suggestion table for this client." ;;
  esac
}

history_expiry_optional_flags() {
  # Flags only. Empty when there is no tighter-than-recommended option.
  local client="${1:-}"
  case "$client" in
    Geth)
      echo "--history.chain=postprague"
      echo "--history.chain=recent --history.blocks=<N>"
      ;;
    Besu)
      echo "--Xchain-pruning-enabled=ALL --Xchain-pruning-blocks-retained=1056768"
      ;;
    Reth)
      echo "--prune.bodies.distance 1056768 --prune.receipts.distance 1056768"
      echo "--minimal"
      ;;
    *)
      echo ""
      ;;
  esac
}

history_expiry_optional_why() {
  local client="${1:-}"
  case "$client" in
    Geth) echo "postprague on newer binaries; recent is rolling (N > 100000) and still settling." ;;
    Besu) echo "~5 months rolling. Experimental; skip if you need local receipts/logs." ;;
    Reth) echo "~5 months rolling, or aggressive --minimal. Both drop receipts some protocols need." ;;
    *) echo "" ;;
  esac
}

history_expiry_apply_extra() {
  # Extra apply line only when an offline prune/resync step is required.
  local client="${1:-}"
  case "$client" in
    Geth) echo "Offline first: geth prune-history --datadir <datadir> --history.chain postmerge" ;;
    Besu) echo "Existing full-history DB: besu --data-path=<path> storage prune-pre-merge-blocks" ;;
    *) echo "" ;;
  esac
}

history_expiry_notes() {
  # One bullet per line, no leading dash.
  local client="${1:-}"
  local status="${2:-}"
  case "$client" in
    Geth)
      echo "Rocket Pool / SSV / StakeWise: short windows can break local eth_getLogs; use an external RPC."
      echo "Pruning is destructive."
      echo "--state.scheme=path is state layout, not block-history expiry."
      ;;
    Nethermind)
      echo "Rocket Pool / SSV / StakeWise: a ~5 month window can break local eth_getLogs; use an external RPC or --History.Pruning=UseAncientBarriers."
      echo "Pruning is destructive."
      echo "Nethermind 2.0: fresh DBs use Flat (default); existing Patricia DBs keep Patricia. Hybrid full-prune knobs apply to Patricia only. Patricia → Flat needs a resync; units are not rewritten."
      ;;
    Besu)
      echo "Rocket Pool / SSV / StakeWise: rolling prune can break local eth_getLogs; use an external RPC."
      echo "Pruning is destructive. Online --history-expiry-prune is deprecated in Besu 26.1.0."
      ;;
    Reth)
      echo "Rocket Pool / SSV / StakeWise: rolling/--minimal drop receipts; keep --full or use an external RPC."
      echo "Pruning is destructive. Reth with no prune profile is archive."
      ;;
    Erigon|Erigon-Caplin)
      echo "Rocket Pool / SSV / StakeWise: minimal already keeps ~14 days of history; use an external RPC if you need longer local logs."
      echo "Pruning is destructive."
      if [[ "$status" == "caplin_archive" ]]; then
        echo "Caplin *-archive flags keep extra consensus history on purpose. Not a failure."
      fi
      ;;
    Ethrex)
      echo "Nothing to add for rolling expiry yet. Monitor disk."
      ;;
    *)
      echo "Suggestions only — systemd units are not modified."
      ;;
  esac
}

history_expiry_cl_note() {
  local cl="${1:-}"
  case "$cl" in
    Lighthouse)
      echo "Lighthouse already prunes blobs/payloads by default. Avoid --prune-blobs=false and --supernode on ~2TB staking disks."
      ;;
    Teku)
      echo "Teku --data-storage-mode=minimal (default) is the staking setting; archive reconstructs historic states."
      ;;
    Prysm)
      echo "Prysm --beacon-db-pruning is opt-in for operators who do not need historic beacon data."
      ;;
    Nimbus)
      echo "Nimbus staking nodes should keep the pruned/default storage profile, not archive."
      ;;
    Lodestar)
      echo "Lodestar staking nodes should keep the pruned/default profile; archive is for historic queries."
      ;;
    Grandine)
      echo "Grandine aggressive-pruned is optional; default pruned is enough for staking."
      ;;
    *)
      echo "Beacon archive / supernode / no-blob-prune flags are optional extras, not required to validate."
      ;;
  esac
}

history_expiry_print_title() {
  echo "History expiry suggestions (~2TB staking)"
  echo "Suggestions only — not applied."
}

history_expiry_print_recommended_block() {
  local client="${1:-}"
  echo "Recommended flags:"
  history_expiry_suggested_flags "$client"
  echo "Why: $(history_expiry_suggested_why "$client")"
}

history_expiry_print_apply() {
  local client="${1:-}"
  local extra
  echo "How to apply:"
  echo "Execution Client → Edit configuration"
  extra=$(history_expiry_apply_extra "$client")
  if [[ -n "$extra" ]]; then
    echo "$extra"
  fi
}

history_expiry_print_optional() {
  local client="${1:-}"
  local flags why
  flags=$(history_expiry_optional_flags "$client")
  [[ -n "$flags" ]] || return 0
  echo "Optional further savings:"
  printf '%s\n' "$flags"
  why=$(history_expiry_optional_why "$client")
  if [[ -n "$why" ]]; then
    echo "$why"
  fi
}

history_expiry_print_notes() {
  local client="${1:-}"
  local status="${2:-}"
  local notes
  notes=$(history_expiry_notes "$client" "$status")
  [[ -n "$notes" ]] || return 0
  echo "Notes:"
  while IFS= read -r line; do
    [[ -n "$line" ]] && echo "- $line"
  done <<< "$notes"
}

history_expiry_print_client_block() {
  local client="${1:-}"
  echo
  echo "=== ${client} ==="
  history_expiry_print_recommended_block "$client"
  echo
  history_expiry_print_optional "$client"
  echo
  history_expiry_print_notes "$client"
}

history_expiry_print_all() {
  history_expiry_print_title
  echo
  echo "How to apply: Execution Client → Edit configuration"
  echo "Archive / Caplin archive / full-history RPC nodes should ignore these."
  local c
  for c in Geth Nethermind Besu Reth Erigon Ethrex; do
    history_expiry_print_client_block "$c"
  done
  echo
  echo "=== Consensus layer ==="
  echo "- Lighthouse: default blob/payload prune is enough; skip --supernode / --prune-blobs=false."
  echo "- Teku: --data-storage-mode=minimal (default)."
  echo "- Prysm: --beacon-db-pruning if you do not need historic CL data."
}

history_expiry_read_unit() {
  local path="${1:-}"
  if [[ -z "$path" || ! -e "$path" ]]; then
    return 1
  fi
  if [[ -r "$path" ]]; then
    cat "$path"
    return 0
  fi
  if command -v sudo >/dev/null 2>&1; then
    sudo -n cat "$path" 2>/dev/null && return 0
  fi
  return 1
}

history_expiry_evaluate_unit_text() {
  # First line is the machine status token (node-checker / bats).
  # Remaining lines are the operator-facing report. ExecStart is omitted
  # unless HISTORY_EXPIRY_VERBOSE=1.
  local unit_text="${1:-}"
  local cl_text="${2:-}"
  local verbose="${3:-${HISTORY_EXPIRY_VERBOSE:-0}}"
  local description execstart client cl status extra
  description=$(history_expiry_extract_description "$unit_text")
  execstart=$(history_expiry_extract_execstart "$unit_text")
  client=$(history_expiry_detect_client "$description" "$execstart")
  cl=$(history_expiry_detect_client "$(history_expiry_extract_description "$cl_text")" "")
  if [[ -z "$client" ]]; then
    echo "no_el"
    echo "Detected: (none) — no execution client detected"
    return 0
  fi
  status=$(history_expiry_status "$client" "$execstart")
  echo "$status"
  echo "Detected: ${client} — $(history_expiry_status_plain "$status")"
  if [[ "$verbose" == "1" && -n "$execstart" ]]; then
    echo "ExecStart (collapsed): ${execstart}"
  fi
  echo
  history_expiry_print_recommended_block "$client"
  echo
  history_expiry_print_apply "$client"
  case "$status" in
    archive|caplin_archive|unsupported)
      ;;
    *)
      extra=$(history_expiry_optional_flags "$client")
      if [[ -n "$extra" ]]; then
        echo
        history_expiry_print_optional "$client"
      fi
      ;;
  esac
  echo
  history_expiry_print_notes "$client" "$status"
  if [[ -n "$cl" ]]; then
    echo
    echo "CL: $(history_expiry_cl_note "$cl")"
  fi
}

history_expiry_print_checker_detail() {
  # Compact structured follow-up for node-checker (after the one-line summary).
  local client="${1:-}"
  local status="${2:-}"
  echo "Recommended flags: $(history_expiry_suggested_flags "$client")"
  echo "How to apply: Execution Client → Edit configuration"
  case "$status" in
    archive)
      echo "Notes: archive / full-history — suggestions are opt-in, not a failure."
      ;;
    caplin_archive)
      echo "Notes: Caplin archive flags present — not a failure."
      ;;
    missing)
      echo "Why: $(history_expiry_suggested_why "$client")"
      ;;
  esac
}

history_expiry_checker_summary() {
  # One-line summary for node-checker (status already known).
  local status="${1:-}" client="${2:-}"
  case "$status" in
    recommended)
      echo "${client} already has recommended expiry/prune flags ($(history_expiry_suggested_flags "$client"))."
      ;;
    missing)
      echo "${client} lacks recommended ~2TB staking expiry/prune flags. Suggested: $(history_expiry_suggested_flags "$client")"
      ;;
    archive)
      echo "${client} looks like an intentional archive / full-history node; history-expiry suggestions are opt-in."
      ;;
    caplin_archive)
      echo "Caplin archive flags present; extra consensus history may be intentional. Not a failure."
      ;;
    unsupported)
      echo "${client} has no history-expiry flags yet; nothing to add for rolling expiry."
      ;;
    no_el)
      echo "No execution client unit found."
      ;;
    *)
      echo "Could not classify execution-client history expiry."
      ;;
  esac
}

history_expiry_checker_level() {
  # Map status → node-checker print_check_result level. Never FAIL.
  case "${1:-}" in
    recommended) echo "PASS" ;;
    missing) echo "WARN" ;;
    archive|caplin_archive|unsupported) echo "INFO" ;;
    *) echo "INFO" ;;
  esac
}

history_expiry_main() {
  local print_all=0 checker=0 verbose=0 unit="${EXEC_SERVICE_FILE:-/etc/systemd/system/execution.service}"
  local cl_unit="${CONSENSUS_SERVICE_FILE:-/etc/systemd/system/consensus.service}"
  local pause=1
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --all) print_all=1; shift ;;
      --checker) checker=1; pause=0; shift ;;
      --verbose) verbose=1; shift ;;
      --no-pause) pause=0; shift ;;
      --unit) unit="${2:-}"; shift 2 ;;
      --cl-unit) cl_unit="${2:-}"; shift 2 ;;
      -h|--help)
        cat <<'EOF'
Usage: history_expiry_suggestions.sh [--all] [--checker] [--verbose] [--unit FILE] [--cl-unit FILE]

Print suggested rolling-history / prune flags for ~2TB staking full nodes.
Does not modify systemd units.

  --all        Print the per-client suggestion table (ignore installed unit)
  --checker    Compact output for node-checker (no pause)
  --verbose    Include collapsed ExecStart in interactive / unit output
  --unit FILE  Read this execution.service instead of /etc/systemd/system/execution.service
EOF
        return 0
        ;;
      *)
        echo "Unknown option: $1" >&2
        return 1
        ;;
    esac
  done

  if [[ $print_all -eq 1 ]]; then
    history_expiry_print_all
    if [[ $pause -eq 1 ]]; then
      echo
      echo "Press ENTER to return to menu"
      read -r
    fi
    return 0
  fi

  local unit_text="" cl_text=""
  unit_text=$(history_expiry_read_unit "$unit" 2>/dev/null || true)
  cl_text=$(history_expiry_read_unit "$cl_unit" 2>/dev/null || true)

  if [[ -z "$unit_text" ]]; then
    echo "No execution.service found at ${unit}."
    echo "Printing the full per-client suggestion table instead."
    echo
    history_expiry_print_all
    if [[ $pause -eq 1 ]]; then
      echo
      echo "Press ENTER to return to menu"
      read -r
    fi
    return 0
  fi

  if [[ $checker -eq 1 ]]; then
    local description execstart client status
    description=$(history_expiry_extract_description "$unit_text")
    execstart=$(history_expiry_extract_execstart "$unit_text")
    client=$(history_expiry_detect_client "$description" "$execstart")
    status=$(history_expiry_status "$client" "$execstart")
    echo "$status"
    echo "$client"
    history_expiry_checker_summary "$status" "$client"
    history_expiry_print_checker_detail "$client" "$status"
    return 0
  fi

  history_expiry_print_title
  echo
  HISTORY_EXPIRY_VERBOSE="$verbose" history_expiry_evaluate_unit_text "$unit_text" "$cl_text" "$verbose" | tail -n +2
  echo
  echo "Full table: $0 --all"
  echo "Docs: docs/history-expiry-suggestions.md"
  if [[ $pause -eq 1 ]]; then
    echo
    echo "Press ENTER to return to menu"
    read -r
  fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  history_expiry_main "$@"
fi
