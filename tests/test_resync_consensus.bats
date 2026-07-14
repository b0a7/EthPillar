#!/usr/bin/env bats
#
# tests/test_resync_consensus.bats
#
# Unit tests for pure helper functions in resync_consensus.sh:
#   normalize_client, normalize_network, default_checkpoint_url,
#   beacon_datadir_for_client, wipe_beacon_datadir
#
# Run: bats tests/test_resync_consensus.bats
#

setup() {
	# Scripts live at the repo root relative to this file
	cd "$BATS_TEST_DIRNAME/.."

	# Ensure ./env exists so functions.sh can source it
	if [ ! -f ./env ]; then
		touch ./env
		MOCKED_ENV=true
	fi

	export COMMAND_LOG
	COMMAND_LOG=$(mktemp)

	# Mock sudo so wipe_beacon_datadir never touches real paths
	sudo() {
		echo "sudo $*" >> "$COMMAND_LOG"
	}
	export -f sudo

	# Silence / no-op UI helpers that may be called by ohai/error styling
	clear() { return 0; }
	export -f clear

	# Source under test (resync_consensus.sh sources functions.sh itself
	# and does not auto-run main when sourced — see BASH_SOURCE guard).
	# shellcheck disable=SC1091
	source ./resync_consensus.sh

	# Reset globals that helpers may read
	CL=""
	NETWORK=""
	CHECKPOINT_SYNC_URL=""
	ASSUME_YES=0

	> "$COMMAND_LOG"
}

teardown() {
	rm -f "$COMMAND_LOG"
	if [ "${MOCKED_ENV:-false}" = "true" ]; then
		rm -f ./env
	fi
}

# ── normalize_client ───────────────────────────────────────────────────────────

@test "normalize_client maps canonical names to Title-case" {
	run normalize_client "nimbus"
	[ "$status" -eq 0 ]
	[ "$output" = "Nimbus" ]

	run normalize_client "lighthouse"
	[ "$output" = "Lighthouse" ]

	run normalize_client "teku"
	[ "$output" = "Teku" ]

	run normalize_client "prysm"
	[ "$output" = "Prysm" ]

	run normalize_client "lodestar"
	[ "$output" = "Lodestar" ]

	run normalize_client "grandine"
	[ "$output" = "Grandine" ]
}

@test "normalize_client is case-insensitive and strips whitespace" {
	run normalize_client "NiMbUs"
	[ "$output" = "Nimbus" ]

	run normalize_client "  LIGHTHOUSE  "
	[ "$output" = "Lighthouse" ]

	run normalize_client "Te Ku"
	# Spaces are stripped before matching, so "teku" still resolves
	[ "$output" = "Teku" ]
}

@test "normalize_client accepts common aliases" {
	run normalize_client "lh"
	[ "$output" = "Lighthouse" ]

	run normalize_client "LH"
	[ "$output" = "Lighthouse" ]

	run normalize_client "ls"
	[ "$output" = "Lodestar" ]
}

@test "normalize_client returns empty for unknown or empty input" {
	run normalize_client ""
	[ "$status" -eq 0 ]
	[ -z "$output" ]

	run normalize_client "besu"
	[ -z "$output" ]

	run normalize_client "not-a-client"
	[ -z "$output" ]
}

# ── normalize_network ──────────────────────────────────────────────────────────

@test "normalize_network maps known networks to Title-case" {
	run normalize_network "mainnet"
	[ "$status" -eq 0 ]
	[ "$output" = "Mainnet" ]

	run normalize_network "holesky"
	[ "$output" = "Holesky" ]

	run normalize_network "hoodi"
	[ "$output" = "Hoodi" ]

	run normalize_network "sepolia"
	[ "$output" = "Sepolia" ]

	run normalize_network "ephemery"
	[ "$output" = "Ephemery" ]
}

@test "normalize_network accepts aliases and mixed case" {
	run normalize_network "main"
	[ "$output" = "Mainnet" ]

	run normalize_network "ephemeral"
	[ "$output" = "Ephemery" ]

	run normalize_network "MAINNET"
	[ "$output" = "Mainnet" ]

	run normalize_network "  HoLeSkY  "
	[ "$output" = "Holesky" ]
}

@test "normalize_network returns empty for unknown or empty input" {
	run normalize_network ""
	[ "$status" -eq 0 ]
	[ -z "$output" ]

	run normalize_network "goerli"
	[ -z "$output" ]

	run normalize_network "devnet"
	[ -z "$output" ]
}

# ── default_checkpoint_url ─────────────────────────────────────────────────────

@test "default_checkpoint_url returns known endpoints per network" {
	run default_checkpoint_url "Mainnet"
	[ "$status" -eq 0 ]
	[ "$output" = "https://sync-mainnet.beaconcha.in" ]

	run default_checkpoint_url "Holesky"
	[ "$output" = "https://beaconstate-holesky.chainsafe.io" ]

	run default_checkpoint_url "Hoodi"
	[ "$output" = "https://beaconstate-hoodi.chainsafe.io" ]

	run default_checkpoint_url "Sepolia"
	[ "$output" = "https://checkpoint-sync.sepolia.ethpandaops.io" ]

	run default_checkpoint_url "Ephemery"
	[ "$output" = "https://checkpointz.bordel.wtf/" ]
}

@test "default_checkpoint_url accepts lowercase network names" {
	# Uses network_to_slug internally, so Title-case is not required
	run default_checkpoint_url "mainnet"
	[ "$output" = "https://sync-mainnet.beaconcha.in" ]

	run default_checkpoint_url "sepolia"
	[ "$output" = "https://checkpoint-sync.sepolia.ethpandaops.io" ]
}

@test "default_checkpoint_url returns empty for unknown network" {
	run default_checkpoint_url "goerli"
	[ "$status" -eq 0 ]
	[ -z "$output" ]

	run default_checkpoint_url ""
	[ -z "$output" ]
}

# ── beacon_datadir_for_client ──────────────────────────────────────────────────

@test "beacon_datadir_for_client returns fixed paths for most clients" {
	CL="Lighthouse"
	run beacon_datadir_for_client
	[ "$status" -eq 0 ]
	[ "$output" = "/var/lib/lighthouse/beacon" ]

	CL="Lodestar"
	run beacon_datadir_for_client
	[ "$output" = "/var/lib/lodestar/chain-db" ]

	CL="Teku"
	run beacon_datadir_for_client
	[ "$output" = "/var/lib/teku/beacon" ]

	CL="Nimbus"
	run beacon_datadir_for_client
	[ "$output" = "/var/lib/nimbus/db" ]

	CL="Prysm"
	run beacon_datadir_for_client
	[ "$output" = "/var/lib/prysm/beacon/beaconchaindata" ]
}

@test "beacon_datadir_for_client Grandine path includes network slug" {
	CL="Grandine"
	NETWORK="Mainnet"
	run beacon_datadir_for_client
	[ "$status" -eq 0 ]
	[ "$output" = "/var/lib/grandine/mainnet/beacon" ]

	NETWORK="Hoodi"
	run beacon_datadir_for_client
	[ "$output" = "/var/lib/grandine/hoodi/beacon" ]

	NETWORK="Holesky"
	run beacon_datadir_for_client
	[ "$output" = "/var/lib/grandine/holesky/beacon" ]
}

@test "beacon_datadir_for_client Grandine defaults network slug to mainnet when empty" {
	CL="Grandine"
	NETWORK=""
	run beacon_datadir_for_client
	[ "$status" -eq 0 ]
	[ "$output" = "/var/lib/grandine/mainnet/beacon" ]
}

@test "beacon_datadir_for_client errors on unsupported client" {
	CL="UnknownClient"
	run beacon_datadir_for_client
	[ "$status" -ne 0 ]
	[[ "$output" == *"No beacon data path mapping"* ]]
}

# ── wipe_beacon_datadir (happy path) ───────────────────────────────────────────

@test "wipe_beacon_datadir removes a safe /var/lib/ path via sudo" {
	run wipe_beacon_datadir "/var/lib/nimbus/db"
	[ "$status" -eq 0 ]

	run cat "$COMMAND_LOG"
	[[ "$output" == *"sudo rm -rf /var/lib/nimbus/db"* ]]
}

@test "wipe_beacon_datadir allows nested EthPillar-style paths" {
	run wipe_beacon_datadir "/var/lib/prysm/beacon/beaconchaindata"
	[ "$status" -eq 0 ]

	run cat "$COMMAND_LOG"
	[[ "$output" == *"sudo rm -rf /var/lib/prysm/beacon/beaconchaindata"* ]]

	> "$COMMAND_LOG"
	run wipe_beacon_datadir "/var/lib/grandine/mainnet/beacon"
	[ "$status" -eq 0 ]

	run cat "$COMMAND_LOG"
	[[ "$output" == *"sudo rm -rf /var/lib/grandine/mainnet/beacon"* ]]
}

# ── wipe_beacon_datadir (safety) ───────────────────────────────────────────────
# Must refuse destructive paths so a bug cannot rm -rf the system.

@test "wipe_beacon_datadir refuses empty path" {
	run wipe_beacon_datadir ""
	[ "$status" -ne 0 ]
	[[ "$output" == *"Refusing to delete unsafe path"* ]]

	# Must not have invoked sudo rm
	run cat "$COMMAND_LOG"
	[[ "$output" != *"rm -rf"* ]]
}

@test "wipe_beacon_datadir refuses root filesystem /" {
	run wipe_beacon_datadir "/"
	[ "$status" -ne 0 ]
	[[ "$output" == *"Refusing to delete unsafe path"* ]]

	run cat "$COMMAND_LOG"
	[[ "$output" != *"rm -rf"* ]]
}

@test "wipe_beacon_datadir refuses paths outside /var/lib/" {
	run wipe_beacon_datadir "/tmp/beacon-db"
	[ "$status" -ne 0 ]
	[[ "$output" == *"Refusing to delete unsafe path"* ]]

	run wipe_beacon_datadir "/home/user/nimbus/db"
	[ "$status" -ne 0 ]
	[[ "$output" == *"Refusing to delete unsafe path"* ]]

	run wipe_beacon_datadir "/opt/ethpillar/data"
	[ "$status" -ne 0 ]
	[[ "$output" == *"Refusing to delete unsafe path"* ]]

	run cat "$COMMAND_LOG"
	[[ "$output" != *"rm -rf"* ]]
}

@test "wipe_beacon_datadir refuses /var/lib without a subdirectory" {
	# Exact /var/lib (no trailing segment) must not match /var/lib/*
	run wipe_beacon_datadir "/var/lib"
	[ "$status" -ne 0 ]
	[[ "$output" == *"Refusing to delete unsafe path"* ]]

	run cat "$COMMAND_LOG"
	[[ "$output" != *"rm -rf"* ]]
}

@test "wipe_beacon_datadir refuses path that only prefixes /var/lib" {
	# e.g. /var/liberty would be catastrophic if a loose prefix check were used
	run wipe_beacon_datadir "/var/liberty/beacon"
	[ "$status" -ne 0 ]
	[[ "$output" == *"Refusing to delete unsafe path"* ]]

	run cat "$COMMAND_LOG"
	[[ "$output" != *"rm -rf"* ]]
}
