#!/usr/bin/env bash
# Integration test: systemd config compare without running Ethereum clients.
#
# Writes EthPillar-generated units into /etc/systemd/system/, then exercises
# manage.config_compare prepare / list-changed / apply (+ .bak) inside the
# ethpillar-test container. Does not download client binaries or start nodes.
set -euo pipefail

cd /ethpillar
# shellcheck source=../../functions.sh
source functions.sh
ensure_python_deps

py="${ETHPILLAR_PYTHON:-python3}"
SYSTEMD_DIR=/etc/systemd/system

ohai "Installing generator fixtures into ${SYSTEMD_DIR}"

PYTHONPATH=/ethpillar "$py" <<'PY'
import os
from pathlib import Path

import config
import deploy.geth as geth
import deploy.lighthouse as lighthouse
import deploy.mevboost as mevboost
from deploy.common import write_service_file

os.makedirs("/secrets", exist_ok=True)
Path("/secrets/jwtsecret").write_text("0" * 64 + "\n", encoding="utf-8")

network = "sepolia"
jwt = "/secrets/jwtsecret"
sync_url = config.sepolia_sync_urls[0][1]
relays = config.sepolia_relay_options

el = geth.generate_geth_service(network, "30303", "8545", "50", jwt)
cl = lighthouse.generate_lighthouse_bn_service(
    network, sync_url, jwt, "5052", "9000", "9001", "100",
    mev_parameters="--builder http://127.0.0.1:18550",
)
vc = lighthouse.generate_lighthouse_vc_service(
    network,
    "EthPillarTest",
    "--beacon-nodes=http://127.0.0.1:5052",
    fee_parameters="--suggested-fee-recipient=0x1111111111111111111111111111111111111111",
    mev_parameters="--builder-proposals",
)
mev = mevboost.generate_mevboost_service(network, "0.006", relays)

write_service_file(el, "/etc/systemd/system/execution.service", "execution_temp.service")
write_service_file(cl, "/etc/systemd/system/consensus.service", "consensus_temp.service")
write_service_file(vc, "/etc/systemd/system/validator.service", "validator_temp.service")
write_service_file(mev, "/etc/systemd/system/mevboost.service", "mevboost_temp.service")
print("Wrote execution/consensus/validator/mevboost units")
PY

export FEE_RECIPIENT_ADDRESS=0x1111111111111111111111111111111111111111
export GRAFFITI=EthPillarTest
export JWTSECRET_PATH=/secrets/jwtsecret
export MEV_MIN_BID=0.006
export EL_P2P_PORT=30303
export EL_RPC_PORT=8545
export EL_MAX_PEER_COUNT=50
export CL_P2P_PORT=9000
export CL_P2P_PORT_2=9001
export CL_REST_PORT=5052
export CL_MAX_PEER_COUNT=100

workdir=$(mktemp -d /tmp/ethpillar-compare-test-XXXXXX)
ohai "1) prepare against matching units (expect exit 2 / no diff)"
set +e
PYTHONPATH=/ethpillar "$py" -m manage.config_compare prepare --workdir "$workdir"
rc=$?
set -e
if [[ $rc -ne 2 ]]; then
  echo "❌ Expected EXIT_NO_DIFF (2) for matching units, got $rc"
  echo "--- workdir contents ---"
  find "$workdir" -type f -print -exec echo '--- {} ---' \; -exec cat {} \;
  exit 1
fi
echo "✅ Matching units → no diff"

ohai "2) introduce drift on consensus.service, expect a diff"
PYTHONPATH=/ethpillar "$py" <<'PY'
from pathlib import Path
from manage.service_parse import parse_exec_start, rebuild_service_content

path = Path("/etc/systemd/system/consensus.service")
content = path.read_text(encoding="utf-8")
start, end, args = parse_exec_start(content)
if "--stale-custom-flag=true" not in args:
    args = list(args) + ["--stale-custom-flag=true"]
new = rebuild_service_content(content, start, end, args)
path.write_text(new, encoding="utf-8")
print("Injected --stale-custom-flag=true into consensus.service")
PY

rm -rf "$workdir"
workdir=$(mktemp -d /tmp/ethpillar-compare-test-XXXXXX)
set +e
PYTHONPATH=/ethpillar "$py" -m manage.config_compare prepare --workdir "$workdir"
rc=$?
set -e
if [[ $rc -ne 0 ]]; then
  echo "❌ Expected prepare success (0) when units differ, got $rc"
  exit 1
fi
if [[ ! -f "$workdir/installed/consensus.service" || ! -f "$workdir/default/consensus.service" ]]; then
  echo "❌ Missing canonicalized compare files for consensus"
  exit 1
fi
if ! grep -q 'stale-custom-flag' "$workdir/installed/consensus.service"; then
  echo "❌ Installed side missing stale flag"
  exit 1
fi
if grep -q 'stale-custom-flag' "$workdir/default/consensus.service"; then
  echo "❌ Default side should not include stale flag"
  exit 1
fi
echo "✅ Drift detected; default lacks stale flag"

ohai "3) simulate tmeld merge: copy default → installed left pane, then apply"
cp "$workdir/default/consensus.service" "$workdir/installed/consensus.service"
changed=$(PYTHONPATH=/ethpillar "$py" -m manage.config_compare list-changed --workdir "$workdir")
if [[ "$changed" != *consensus* ]]; then
  echo "❌ list-changed did not report consensus (got: '$changed')"
  exit 1
fi

PYTHONPATH=/ethpillar "$py" -m manage.config_compare apply --workdir "$workdir"
if [[ ! -f "${SYSTEMD_DIR}/consensus.service.bak" ]]; then
  echo "❌ Expected consensus.service.bak after apply"
  exit 1
fi
if grep -q 'stale-custom-flag' "${SYSTEMD_DIR}/consensus.service"; then
  echo "❌ Applied unit still has stale flag"
  exit 1
fi
echo "✅ Apply wrote cleaned unit + .bak"

ohai "4) daemon-reload accepts applied unit"
if command -v systemctl >/dev/null 2>&1 && systemctl daemon-reload 2>/dev/null; then
  echo "✅ daemon-reload OK"
else
  echo "⚠️  daemon-reload skipped/unavailable in this container (unit files still validated above)"
fi

ohai "5) tmeld importable in venv"
PYTHONPATH=/ethpillar "$py" -c "import tmeld; print('tmeld OK', getattr(tmeld, '__version__', 'unknown'))"

ohai "6) re-prepare after apply → no diff"
rm -rf "$workdir"
workdir=$(mktemp -d /tmp/ethpillar-compare-test-XXXXXX)
set +e
PYTHONPATH=/ethpillar "$py" -m manage.config_compare prepare --workdir "$workdir"
rc=$?
set -e
if [[ $rc -ne 2 ]]; then
  echo "❌ Expected no diff after apply, got $rc"
  find "$workdir" -type f | head
  exit 1
fi
echo "✅ Post-apply units match defaults again"

rm -rf "$workdir"
ohai "All config_compare integration checks passed"
