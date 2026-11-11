#!/bin/bash

# Author: coincashew.eth | coincashew.com
# License: GNU GPL
# Source: https://github.com/coincashew/ethpillar
# Description: Update SSV operator node (build from source) and ssv-dkg binary

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/functions.sh" ]]; then
    BASE_DIR="$SCRIPT_DIR"
else
    BASE_DIR="$HOME/git/ethpillar"
fi

# shellcheck disable=SC1091
source "$BASE_DIR"/functions.sh

function promptViewLogs(){
    if whiptail --title "Update complete" --yesno "Would you like to view ssv-node logs?" 8 78; then
		view_journal_logs -fu ssv-node
    fi
}

function updateClient(){
	local _target_tag="${1:-LATEST}"
	local RELEASE_DATA TAG

	info "ℹ️  Resolving SSV node release ($_target_tag)…"
	RELEASE_DATA=$(PYTHONPATH="${BASE_DIR}" python3 -m deploy.common release_info "ssv" "$_target_tag")
	TAG=$(echo "$RELEASE_DATA" | jq -r .version)

	info "ℹ️  Building ssvnode from source ($TAG)…"
	isSsvEnabled && sudo systemctl stop ssv-node
	PYTHONPATH="${BASE_DIR}" python3 -c \
		"from deploy.ssv import build_ssvnode_from_source; build_ssvnode_from_source('${TAG}')"
	isSsvEnabled && sudo systemctl start ssv-node

	if isSsvDkgEnabled; then
		info "ℹ️  Updating ssv-dkg…"
		RELEASE_DATA=$(PYTHONPATH="${BASE_DIR}" python3 - <<'PY'
import json
from deploy.ssv import get_dkg_release_info
from deploy.common import get_machine_architecture
print(json.dumps(get_dkg_release_info("LATEST", get_machine_architecture() == "amd64")))
PY
)
		cd "$HOME" || true
		FILENAME=$(echo "$RELEASE_DATA" | jq -r '.filenames[0]')
		download_release_asset 0 "$FILENAME" "ssv-dkg"
		sudo systemctl stop ssv-dkg 2>/dev/null || true
		EXEC_PATH=$(get_systemd_exec_path "/etc/systemd/system/ssv-dkg.service" "/usr/local/bin/ssv-dkg")
		PYTHONPATH="${BASE_DIR}" python3 -m deploy.common extract_and_install \
			"$FILENAME" "ssv-dkg" "$EXEC_PATH" "binary" 0 --binary-name "ssv-dkg"
		sudo systemctl start ssv-dkg 2>/dev/null || true
	fi
}

if [[ "${1:-}" == "--auto" ]]; then
    updateClient LATEST
else
    updateClient LATEST
    promptViewLogs
fi
