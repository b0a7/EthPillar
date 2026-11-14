#!/bin/bash

# Author: coincashew.eth | coincashew.com
# License: GNU GPL
# Source: https://github.com/coincashew
#
# Made for home and solo stakers 🏠🥩

# uninstall.sh is executed as its own process (runScript); resolve repo root.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="${BASE_DIR:-$SCRIPT_DIR}"

# Retire a systemd unit via the shared helper (stop+disable+*.service.bak+
# daemon-reload+verify absent). Prefer this over `rm` so units cannot
# accidentally restart via boot or Wants=.
retireSystemdUnit(){
	local unit="${1:?unit name required}"
	[[ -f "/etc/systemd/system/${unit}.service" ]] || return 0
	if ! PYTHONPATH="${BASE_DIR}" python3 -m deploy.systemd_units retire "${unit}.service"; then
		echo "ERROR: Failed to retire ${unit}.service (refusing to continue uninstall)" >&2
		exit 1
	fi
}

function promptYesNo(){
    if whiptail --title "Uninstall Staking Node" --defaultno --yesno "This will remove all data and files related to this staking node.\nAre you sure you want to remove all files?\n(consensus/execution/validator/charon/ssv/mevboost)" 9 78; then
  		uninstallCL
  		uninstallEL
  		uninstallCharon
  		uninstallWeb3Signer
  		uninstallSsv
  		uninstallVC
  		uninstallMevboost
		cleanupMisc
		uninstallPlugins
  		whiptail --title "Uninstall finished" --msgbox "You have uninstalled this staking node and all validator keys." 8 78
	else
		echo "Cancelled uninstall." && return 1
	fi
}

function uninstallPlugins(){
	if [[ -d /opt/ethpillar/plugin-csm ]]; then
		retireSystemdUnit csm_nimbusvalidator
		sudo userdel csm_nimbus_validator 2>/dev/null || true
		sudo rm -rf /opt/ethpillar/plugin-csm
	fi
	if [[ -d /opt/ethpillar/plugin-sentinel ]]; then
		sudo docker stop csm-sentinel
		sudo docker rm csm-sentinel
		sudo docker rmi csm-sentinel
		sudo docker volume rm csm-sentinel-persistent
		sudo rm -rf /opt/ethpillar/plugin-sentinel
	fi
	if [[ -d /opt/ethpillar/plugin-dora ]]; then
		retireSystemdUnit dora
		sudo userdel dora 2>/dev/null || true
		sudo rm -rf /opt/ethpillar/plugin-dora
	fi
	if [[ -d /opt/ethpillar/plugin-client-stats ]]; then
		retireSystemdUnit client-stats
		sudo userdel client-stats 2>/dev/null || true
		sudo rm -rf /opt/ethpillar/plugin-client-stats
	fi
	if [[ -d /opt/ethpillar/plugin-contributoor ]]; then
		retireSystemdUnit contributoor
		sudo userdel contributoor 2>/dev/null || true
		sudo rm -rf /opt/ethpillar/plugin-contributoor
	fi
	if [[ -d /opt/ethpillar/aztec ]]; then
	    cd /opt/ethpillar/aztec 2>/dev/null && docker compose down || true
	    sudo docker rm -f aztec-sequencer
	    TAG=$(grep "DOCKER_TAG" /opt/ethpillar/aztec/.env | sed "s/^DOCKER_TAG=\(.*\)/\1/")
	    sudo docker rmi -f aztecprotocol/aztec:"$TAG"
	    if [[ -f /opt/ethpillar/aztec/.cast_installed_by_plugin && -f /usr/local/bin/cast ]]; then
	      sudo rm /usr/local/bin/cast
	    fi
	    sudo rm -rf /opt/ethpillar/aztec
	fi
}

function cleanupMisc(){
	if [[ -f /etc/systemd/system/ethereum-metrics-exporter.service ]]; then
	   sudo rm /etc/apt/sources.list.d/grafana.list
	   retireSystemdUnit ethereum-metrics-exporter
	   sudo rm -f /usr/local/bin/ethereum-metrics-exporter
	   sudo systemctl disable grafana-server prometheus prometheus-node-exporter 2>/dev/null || true
	   sudo systemctl stop grafana-server prometheus prometheus-node-exporter 2>/dev/null || true
	   sudo apt remove -y grafana prometheus prometheus-node-exporter
	fi
	if [[ -f /usr/local/bin/eth-duties ]]; then sudo rm /usr/local/bin/eth-duties; fi
	if [[ -f /usr/local/bin/ethdo ]]; then sudo rm /usr/local/bin/ethdo; fi
	if [[ -f "$BASE_DIR/.env.overrides" ]]; then sudo rm -f "$BASE_DIR/.env.overrides"; fi
	if [[ -d /opt/ethpillar/testnet ]]; then sudo rm -rf /opt/ethpillar/testnet; fi
	if [[ -d /opt/ethpillar/patches ]]; then sudo rm -rf /opt/ethpillar/patches; fi
}

function uninstallCL(){
	if [[ -f /etc/systemd/system/consensus.service ]]; then
		retireSystemdUnit consensus

		#Lighthouse
		sudo rm -rf /usr/local/bin/lighthouse
		sudo rm -rf /var/lib/lighthouse

		#Lodestar
		sudo rm -rf /usr/local/bin/lodestar
		sudo rm -rf /var/lib/lodestar

		#Teku
		sudo rm -rf /usr/local/bin/teku
		sudo rm -rf /var/lib/teku

		#Nimbus
		sudo rm -rf /usr/local/bin/nimbus_beacon_node
		sudo rm -rf /var/lib/nimbus

		#Prysm from Binaries
		sudo rm -rf /usr/local/bin/prysm-beacon-chain
		#Prysm from Build from Source
		sudo rm -rf /usr/local/bin/prysm
		sudo rm -rf /var/lib/prysm

		#Grandine
		sudo rm -rf /usr/local/bin/grandine
		sudo rm -rf /var/lib/grandine

		sudo userdel consensus 2>/dev/null || true
	fi
}

function uninstallEL(){
	if [[ -f /etc/systemd/system/execution.service ]]; then
		retireSystemdUnit execution

		#Nethermind
		sudo rm -rf /usr/local/bin/nethermind
		sudo rm -rf /var/lib/nethermind

		#Besu
		sudo rm -rf /usr/local/bin/besu
		sudo rm -rf /var/lib/besu

		#Geth
		sudo rm -rf /usr/local/bin/geth
		sudo rm -rf /var/lib/geth

		#Erigon
		sudo rm -rf /usr/local/bin/erigon
		sudo rm -rf /var/lib/erigon

		#Reth
		sudo rm -rf /usr/local/bin/reth
		sudo rm -rf /var/lib/reth

		#Ethrex
		sudo rm -rf /usr/local/bin/ethrex
		sudo rm -rf /var/lib/ethrex

		sudo userdel execution 2>/dev/null || true
	fi
}

function uninstallWeb3Signer(){
	if [[ -f /etc/systemd/system/web3signer.service ]]; then
		retireSystemdUnit web3signer
		sudo rm -rf /usr/local/bin/web3signer
		sudo rm -rf /var/lib/web3signer
		sudo userdel web3signer 2>/dev/null || true
	fi
}

function uninstallCharon(){
	if [[ -f /etc/systemd/system/charon.service ]]; then
		retireSystemdUnit charon
		sudo rm -f /usr/local/bin/charon
		sudo rm -rf /var/lib/charon
		sudo userdel charon 2>/dev/null || true
	fi
}

function uninstallSsv(){
	if [[ -f /etc/systemd/system/ssv-dkg.service ]]; then
		retireSystemdUnit ssv-dkg
	fi
	if [[ -f /etc/systemd/system/ssv-node.service ]]; then
		retireSystemdUnit ssv-node
	fi
	if [[ -d /var/lib/ssv ]]; then
		sudo rm -rf /var/lib/ssv
	fi
	sudo rm -f /usr/local/bin/ssvnode /usr/local/bin/ssv-dkg
	sudo userdel ssv 2>/dev/null || true
}

function uninstallVC(){
	if [[ -f /etc/systemd/system/validator.service ]]; then
		retireSystemdUnit validator

		#Lighthouse
		sudo rm -rf /var/lib/lighthouse/validators
		sudo rm -rf /var/lib/lighthouse_validator

		#Lodestar
		sudo rm -rf /var/lib/lodestar/validators
		sudo rm -rf /var/lib/lodestar_validator

		#Teku, if running Standalone Teku Validator
		sudo rm -rf /var/lib/teku_validator

		#Nimbus, if running standalone Nimbus Validator
		sudo rm -rf /var/lib/nimbus_validator
		sudo rm -rf /usr/local/bin/nimbus_validator_client

		#Prysm from Binaries
		sudo rm -rf /usr/local/bin/prysm-validator
		sudo rm -rf /var/lib/prysm_validator
		sudo rm -rf /var/lib/prysm/validators

		sudo userdel validator 2>/dev/null || true
	fi
}

function uninstallMevboost(){
	if [[ -f /etc/systemd/system/mevboost.service ]]; then
		retireSystemdUnit mevboost
		sudo rm /usr/local/bin/mev-boost
		sudo userdel mevboost 2>/dev/null || true
	fi
}

promptYesNo
