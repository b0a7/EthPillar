#!/usr/bin/env bats
#
# tests/test_grandine_menu.bats
#
# Integrated Grandine staking title and Validator menu wiring.
# Does not start a client or open whiptail.
#
# Run: bats tests/test_grandine_menu.bats
#

setup() {
	cd "$BATS_TEST_DIRNAME/.."
	export TEST_DIR
	TEST_DIR=$(mktemp -d)
	export EXEC_SERVICE_FILE="$TEST_DIR/execution.service"
	export CONSENSUS_SERVICE_FILE="$TEST_DIR/consensus.service"
	export VALIDATOR_SERVICE_FILE="$TEST_DIR/validator.service"
	export CSM_VALIDATOR_SERVICE_FILE="$TEST_DIR/csm_nimbusvalidator.service"
	export CHARON_SERVICE_FILE="$TEST_DIR/charon.service"
	export MEVBOOST_SERVICE_FILE="$TEST_DIR/mevboost.service"
	rm -f "$EXEC_SERVICE_FILE" "$CONSENSUS_SERVICE_FILE" "$VALIDATOR_SERVICE_FILE" \
		"$CSM_VALIDATOR_SERVICE_FILE" "$CHARON_SERVICE_FILE" "$MEVBOOST_SERVICE_FILE"

	# shellcheck disable=SC1091
	source ./ethpillar.sh
}

teardown() {
	rm -rf "$TEST_DIR"
}

write_execution() {
	cat > "$EXEC_SERVICE_FILE" <<'EOF'
[Unit]
Description=Geth Execution Client for MAINNET
[Service]
ExecStart=/usr/local/bin/geth
EOF
}

write_grandine_consensus() {
	local extra="${1:-}"
	cat > "$CONSENSUS_SERVICE_FILE" <<EOF
[Unit]
Description=Grandine Beacon Node Consensus Client service for MAINNET
[Service]
ExecStart=/usr/local/bin/grandine --network=mainnet ${extra}
EOF
}

write_lighthouse_validator() {
	cat > "$VALIDATOR_SERVICE_FILE" <<'EOF'
[Unit]
Description=Lighthouse Validator Client service for MAINNET
[Service]
ExecStart=/usr/local/bin/lighthouse validator_client
EOF
}

@test "validatorSubmenuTitle is Grandine (integrated) when keystore-dir is present" {
	write_grandine_consensus "--keystore-dir=/var/lib/grandine/validator_keys"
	rm -f "$VALIDATOR_SERVICE_FILE"
	run validatorSubmenuTitle
	[ "$status" -eq 0 ]
	[ "$output" = "Grandine (integrated)" ]
}

@test "validatorSubmenuTitle uses the separate validator client name" {
	write_grandine_consensus
	write_lighthouse_validator
	run validatorSubmenuTitle
	[ "$status" -eq 0 ]
	[ "$output" = "Lighthouse" ]
}

@test "mainMenuShowsValidator is true for integrated Grandine without validator.service" {
	write_grandine_consensus "--keystore-dir=/var/lib/grandine/validator_keys"
	rm -f "$VALIDATOR_SERVICE_FILE"
	run mainMenuShowsValidator
	[ "$status" -eq 0 ]
}

@test "mainMenuShowsValidator is false for a Grandine beacon node without keys" {
	write_grandine_consensus
	rm -f "$VALIDATOR_SERVICE_FILE"
	run mainMenuShowsValidator
	[ "$status" -eq 1 ]
}

@test "setNodeMode labels integrated Grandine as Solo Staking Node" {
	write_execution
	write_grandine_consensus "--keystore-dir=/var/lib/grandine/validator_keys"
	rm -f "$VALIDATOR_SERVICE_FILE" "$CSM_VALIDATOR_SERVICE_FILE"
	isIntegrated=false
	setNodeMode
	[ "$NODE_MODE" = "Solo Staking Node" ]
}

@test "setNodeMode labels a Grandine beacon without keys as Full Node" {
	write_execution
	write_grandine_consensus
	rm -f "$VALIDATOR_SERVICE_FILE" "$CSM_VALIDATOR_SERVICE_FILE" "$MEVBOOST_SERVICE_FILE"
	isIntegrated=false
	setNodeMode
	[ "$NODE_MODE" = "Full Node" ]
}

@test "ethpillar validator menu is wired to the integrated Grandine helpers" {
	grep -q 'mainMenuShowsValidator' ethpillar.sh
	grep -q 'validatorSubmenuTitle' ethpillar.sh
	grep -q '_vc_menu_title="$(validatorSubmenuTitle)"' ethpillar.sh
}
