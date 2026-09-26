# EthPillar logical-inconsistencies audit

**Base:** `b0a7/EthPillar` `main` @ `de78215` (Merge #70 Nimbus checkpoint-sync).  
**Deduped against:** open PRs on [mjkeating/EthPillar](https://github.com/mjkeating/EthPillar) #71–#78 (titles/diffs via `gh`).  
**Scope:** bash TUI/helpers, systemd generators, menus, EL/CL/VC/Charon/DV, UFW, node-checker on this base, install/upgrade/resync/migrate.  
**Out of scope:** style-only, feature requests, re-litigating open PR review threads.

The motivating Charon `try-restart` / `&&`/`||` bug is **not** re-flagged (`ensureCharonBeforeValidator`; covered by #73).

---

## Summary counts

| Bucket | Count |
|--------|------:|
| Already covered by open PRs (do not re-fix) | 8 PR areas |
| New High | 7 |
| New Medium | 13 |
| New Low (omitted unless they encode a wrong claim) | 0 reported |

---

## Already covered by open PRs

Do **not** open a second fix for these. They are on `mjkeating/EthPillar` and are present on this `main`.

| PR | Title | What it already covers |
|----|--------|------------------------|
| **#73** | Start a stopped Charon before starting the validator | `functions.sh` `ensureCharonBeforeValidator`: `isCharonEnabled && try-restart … \|\| start … \|\| true` — bash `&&`/`\|\|` binds the `start` fallback to “Charon not installed”, and `try-restart` is a no-op (exit 0) on an inactive unit. **Treat this call site as covered.** |
| **#74** | Fee recipient prompt: include Prysm BN, skip on execution switches | `deploy/deploy-node.py` `_cc_needs_fee` omits Prysm (empty `--suggested-fee-recipient=` on BN-only / Prysm switch). EL switch still prompts because `--cc` is passed for context only; `--auto` can then exit after the old EL datadir was already wiped. |
| **#75** | Remove stale duplicate Prysm entry from `FUSAKA_MIN_VERSIONS` | `client_requirements.py` has `'prysm': 'v6.1.0'` then `'prysm': 'v7.0.0'`; last key wins. Dead first line only. |
| **#76** | CDVN migration: fail closed on unlistable destinations | `deploy/cdvn_migrate.py` `_dest_has_data`: `list_dir_basenames()` returns `[]` when `sudo find` fails, so an occupied dest looks empty and the move proceeds. `main()` does not catch `CalledProcessError`. |
| **#77** | Charon: add `TimeoutStartSec=infinity` even without `TimeoutStopSec` | `deploy/charon.py` `patch_beacon_endpoints` inserts the start-timeout only after a `TimeoutStopSec=` line. Hand-edited units get the BN-wait `ExecStartPre` and systemd’s default 90s kill. |
| **#78** | Three small shell bugs | (1) `plugins/node-checker/run.sh` calls `print_check_result` before it is defined. (2) `helpers/install_docker.sh` `id -d` (invalid; should be `id -u`). (3) `resync_consensus.sh --help` prints a fixed `sed -n '2,60p'` range and truncates the header. |
| **#72** | Correct inaccurate/stale comments | Comment/docstring-only. Deliberately left the Nimbus Ephemery `--network` path mismatch for a code PR (see New / M-1). |
| **#71** | Node checker – QUIC UDP inbound + UFW P2P | Rewrites node-checker networking (listen vs inbound TCP vs QUIC) and UFW EL/CL P2P (unit/env ports, Charon row only if installed). **Do not re-flag hardcoded 9000/30303/QUIC UFW on `main`.** #71 still hardcodes LAN CL REST **5052** (see New / M-7). Merge-conflict risk with #78 on `plugins/node-checker/run.sh`. |

---

## New findings

### High

#### H-1. TUI “Start/Stop/Restart all” uses the opposite order of the CLI

**Where:** `ethpillar.sh` `testAndServiceCommand` / `_SERVICES` (~144–151, 243–253). Contrast `cli.sh` `CLI_CLIENT_START_ORDER` / `CLI_CLIENT_STOP_ORDER` (~31–35, 273–281).

```144:151:ethpillar.sh
_SERVICES=("execution" "consensus" "validator" "mevboost" "charon" "csm_nimbusvalidator" "dora")
...
function testAndServiceCommand() {
  for _service in "${_SERVICES[@]}"; do
    test -f /etc/systemd/system/"${_service}".service && sudo service "${_service}" "$1"
  done
}
```

**Why inconsistent:** CLI comments and `startConsensusStackAfterUpdate` require `execution → consensus → mevboost → charon → validator` on start, reverse on stop. The TUI walks one array for all three actions, so **Charon starts after the validator** (VC talks to a dead `:3600`) and **stop kills execution while BN/VC/Charon may still be running**. Same class as #73 (dependency order), different surface.

**Fix direction:** Reuse the CLI order arrays (forward on start/restart, reverse on stop).

---

#### H-2. Validator submenu start/restart ignores Charon (and integrated Grandine)

**Where:** `ethpillar.sh` `submenuValidator` cases 2–5 (~600–613). Logs (case 1) already branch on `getValidatorMode`.

```600:607:ethpillar.sh
      2)
        sudo service validator start
        ;;
      ...
      4)
        sudo service validator restart
```

**Why inconsistent:** Key import / `ensureCharonBeforeValidator` / `startValidatorStackAfterUpdate` require Charon up before the VC. This menu starts/restarts **only** `validator.service`. Integrated Grandine (`keystore-dir` on `consensus.service`) is handled in the log branch but start/stop/edit still target `validator`. A DVT operator using the Validator menu can bring the signer up against a stopped Charon.

**Fix direction:** Route through `startValidatorService` / `stopValidatorService` and, when `isCharonEnabled`, start/restart Charon first (same as `startValidatorStackAfterUpdate`).

---

#### H-3. `loadKeys` runs the BN binary for Lighthouse / Lodestar / Nimbus import

**Where:** `manage_validator_keys.sh` `loadKeys` (~429–479). Contrast `update_validator.sh` (~112–148) and `functions.sh` `getPubKeys` / `getClVcCurrentVersion` (role `vc`), which read **`validator.service`**.

```431:431:manage_validator_keys.sh
        LH_BIN=$(get_systemd_exec_path "/etc/systemd/system/consensus.service" "/usr/local/bin/lighthouse")
```

Same pattern for Lodestar (~445) and Nimbus (~479). Prysm correctly uses `validator.service` (~488).

**Why inconsistent:** Same-client stacks accidentally work (one binary). Mixed stacks — including **Charon signer ≠ CC** — run e.g. Teku’s `ExecStart` as `lighthouse account validator import`. VC-only nodes fall back to the hardcoded default only because `consensus.service` is missing.

**Fix direction:** Resolve the import binary from `validator.service` (Nimbus deposits import may still need `nimbus_beacon_node`, but from the VC install path / default, not the BN unit).

---

#### H-4. Prysm pubkey listing uses a wallet path install/import never write

**Where:** `functions.sh` `getPubKeys` Prysm branch (~1715–1718) vs `deploy/prysm.py` (~128–129), `manage_validator_keys.sh` `loadKeys` (~491), `deploy/keymanager.py` `PRYSM_DEFAULT_WALLET_DIR`.

```1718:1718:functions.sh
            TEMP=$(sudo -u validator "$PRYSM_VC" accounts list --wallet-dir=/var/lib/prysm/validators 2>/dev/null | grep -Eo '0x[a-fA-F0-9]{96}' || true)
```

Deploy/import/keymanager all use `/var/lib/prysm_validator/validator_keys`. Listing uses `/var/lib/prysm/validators` (BN-era leftover; `uninstall.sh` still deletes both).

**Why inconsistent:** After a successful EthPillar Prysm import, **View pubkeys / indices / eth-duties** see an empty wallet. Charon DV skips this path (`list_distributed_validator_pubkeys`), so the miss is solo/Prysm VC.

**Fix direction:** Use `/var/lib/prysm_validator/validator_keys` (or scrape `--wallet-dir=` from `validator.service`).

---

#### H-5. Consensus switch on an existing Charon node drops companion BN flags

**Where:** `deploy/deploy-node.py` switch flags (~94–114) set `flags["charon"]` only from `--with_charon`, not from an installed `charon.service`. `switch_client.sh` does not pass `--with_charon`. `deploy/orchestrator.py` gates Teku graffiti and Charon `json_requests` on `flags.get('charon')` / `charon_enabled and not flags.get('switch_client')` (~298–303, 339–354).

**Why inconsistent:** Fresh Charon+Nimbus install sets `--feature-set-enable=json_requests` (Obol: Charon needs JSON beacon APIs). Fresh Charon+Teku sets `--validators-graffiti-client-append-format=DISABLED`. A later **Switch consensus client** rebuilds the BN **without** those flags and only patches Charon’s `--beacon-node-endpoints`. Switching **to Nimbus** can leave Charon unable to talk to the new BN; switching **to Teku** can append graffiti Charon already owns.

**Fix direction:** On consensus switch, detect installed Charon (or pass `--with_charon`) and apply the same Teku/Nimbus companion flags as install; when patching endpoints, set/clear `json_requests` to match the new upstream.

---

#### H-6. Node-checker “Validator client” version is the beacon node’s version

**Where:** `plugins/node-checker/run.sh` `check_client_version` (~644–648), used by `check_validator_version` (~625–631). **Beyond #71** (P2P/UFW only; this block is unchanged).

```644:648:plugins/node-checker/run.sh
  if [[ "$name" =~ "Consensus" || "$name" =~ "Validator" ]]; then
    version=$(curl -s -X GET "${API_BN_ENDPOINT}/eth/v1/node/version" \
      -H "accept: application/json" \
      | jq -r '.data.version' \
```

**Why inconsistent:** The check is labeled `Validator client ($VAL)` and compared to **that VC’s** GitHub latest, but the payload is **CL REST `/eth/v1/node/version`**. Mixed CL/VC (or Charon signer ≠ CC) yields a false PASS/WARN. Same-client stacks look fine by accident. TUI version helpers already distinguish `getClVcCurrentVersion … vc`.

**Fix direction:** For the validator row, query the VC binary (same as `getClVcCurrentVersion` role `vc`), not the BN REST API.

---

#### H-7. `switch_client.sh` `try-restart`s Charon after a BN switch (not #73)

**Where:** `switch_client.sh` (~185–194). #73 only changes `functions.sh` `ensureCharonBeforeValidator`.

```190:194:switch_client.sh
            if isCharonEnabled; then
                sudo systemctl daemon-reload
                sudo systemctl try-restart charon
            fi
            startValidatorService
```

**Why inconsistent:** After `patchValidatorBeaconEndpoint`, a **running** Charon needs a real restart to load the new BN URL; an **inactive** Charon (`try-restart` success-on-inactive) stays down and `startValidatorService` still starts the VC. `stopValidatorService` (used earlier, ~85) never stops Charon, so this usually works until Charon has crashed or was stopped. Same systemd idiom as the #73 bug, different call site; here `restart` (not mere `start`) is what the patch requires.

**Fix direction:** After daemon-reload, `systemctl restart charon` when Charon is installed (or `try-restart \|\| start`), then start the VC.

---

### Medium

#### M-1. Nimbus Ephemery `--network` path: deploy vs resync (left open by #72)

**Where:** `deploy/nimbus.py` `_nimbus_network_flag` (~14–16) and `build_checkpoint_sync_exec_start_pre` (docstring: “Mirrors `resync_nimbus()`”). `resync_consensus.sh` `EPHEMERY_NETWORK_PATH` / `resync_nimbus` (~82, 423–424).

- Install / `ExecStartPre`: `--network=/opt/ethpillar/testnet/config.yaml`
- Manual resync: `--network=/opt/ethpillar/testnet` (directory)

**Why inconsistent:** Two helpers claimed to be mirrors pass different Nimbus `--network` values. Nimbus accepts a network name **or** a config directory/file; both *may* work, but this cannot be assumed without an Ephemery node (#72). If one form is rejected, resync or first-start checkpoint-sync fails while the other path works.

**Fix direction:** One constant, used by both `nimbus.py` and `resync_nimbus()`.

---

#### M-2. Ephemery genesis is downloaded; `network_override` is never passed

**Where:** `deploy/orchestrator.py` (~251–252) always calls `setup_ephemery_network()`. Every EL/CL generator accepts `network_override`; **no `install_*` call from the orchestrator passes it.** Only Nimbus hardcodes a custom path (`_nimbus_network_flag`). `tests/test_service_generators.py` `test_bn_ephemery` (~654–661) shows the intended Lodestar override (`--paramsFile=…/config.yaml`, `--genesisStateFile=…/genesis.ssz`, bootnodes) that production never applies.

**Why inconsistent:** `/opt/ethpillar/testnet` is fetched for every Ephemery install, then unused except Nimbus. Non-Nimbus clients get `--network=ephemery` (or equivalent) whether or not that name exists in that release.

**Fix direction:** Centralize per-client Ephemery overrides (as the Lodestar test already sketches) and pass them from `run_install`.

---

#### M-3. `getBeaconNodeEndpoint` scrapes flags `exposeRpcCL` knows are wrong for most CLs

**Where:** `functions.sh` `getBeaconNodeEndpoint` (~1077–1084) only scrapes `--http-port=` / `--rest-port=` / `--rest-api-port=` / `--rest.port=` and **`--http-address=`**. `exposeRpcCL` (~2357–2364) already maps the real bind flags: Nimbus `--rest-address`, Lodestar `--rest.address`, Prysm `--http-host`, Teku `--rest-api-interface`, Lighthouse/Grandine `--http-address`.

**Why inconsistent:** After “Expose CL RPC”, or any non-`--http-address` bind, `patchValidatorBeaconEndpoint` / TUI slot / ethdo can keep `CL_IP_ADDRESS` (default `127.0.0.1`) while the unit listens elsewhere. `ethpillar.sh` `initializeRpcEndpoints` never reads the unit at all. Port scrape is mostly OK (includes `--http-port`); **IP scrape is Lighthouse/Grandine-only**.

**Fix direction:** Share `exposeRpcCL`’s per-client flag map for scrape + patch; optionally call `getBeaconNodeEndpoint` from `initializeRpcEndpoints`.

---

#### M-4. Keystore password gate is 12 characters; the error says 8

**Where:** `manage_validator_keys.sh` `_setKeystorePassword` (~313–323).

```313:323:manage_validator_keys.sh
        if [[ ${#_KEYSTOREPASSWORD} -ge 12 ]]; then
            ...
        else
            whiptail --msgbox "The keystore password must be at least 8 characters long." 8 78
```

**Why inconsistent:** Prompt says 12; rejection says 8. A user who picks 8–11 chars is blocked with the wrong rule. Copy-paste residue (staking-deposit-cli’s historical 8-char minimum).

**Fix direction:** Make the message and the test the same (and match deposit-cli if that is the real constraint).

---

#### M-5. `setNodeMode` / main-menu VC entry miss integrated Grandine and CSM plugin

**Where:** `ethpillar.sh` `setNodeMode` (~1882–1911), `buildMenu` (~161–165). `getValidatorMode` already returns `integrated_grandine` when `consensus.service` has `keystore-dir`.

**Why inconsistent:** Staking mode requires `validator.service`. Integrated Grandine (keys on the BN, no `validator.service`) is labeled **Full Node** and the 🚀 Validator menu is **never added**, even though `submenuValidator` has an `integrated_grandine` log title. CSM plugin (`csm_nimbusvalidator.service`) only sets `PLUGIN_MODE`; fee-recipient grep never looks at that unit, so EL+CL+CSM shows **Full Node**.

**Fix direction:** Treat `getValidatorMode != none` as staking; show Validator (or Consensus-hosted key mgmt) for integrated Grandine; include the CSM unit in Lido/CSM detection.

---

#### M-6. `getClientVC` ≠ `getValidatorClient` / `getValidatorMode`

**Where:** `manage_validator_keys.sh` `getClientVC` (~625–633) vs `functions.sh` `getValidatorClient` / `getValidatorMode` (~970–998) vs `getClient` (~855–878).

**Why inconsistent:** `getClient` / `getClientVC` set `VC` from `validator.service` or “Description contains Grandine” (even a Grandine **BN-only** full node). `getValidatorMode` requires `keystore-dir`. Status, key import, and menus can disagree on whether a VC exists and what it is.

**Fix direction:** One helper: `getValidatorClient` + `getValidatorMode`; delete or wrap `getClientVC`.

---

#### M-7. UFW “CC RPC” still hardcodes port 5052 (beyond #71)

**Where:** `ethpillar.sh` `submenuUFW` option 7 (~1285). #71 keeps the same hardcoded `5052` in the rewritten `cc_rpc` action.

**Why inconsistent:** `CL_REST_PORT` / unit flags can be non-5052. The LAN allow rule opens the wrong port (fail-open to a closed port, or a different service). EL 8545 / Grafana 3000 have the same hardcoded-default shape; 5052 is the one that already diverges from `getBeaconNodeEndpoint`.

**Fix direction:** Allow `CL_REST_PORT` (or the scraped REST port), not a literal 5052. Same for 8545/3000 if those become overridable in the unit.

---

#### M-8. `config_compare apply` writes units and never `daemon-reload`s

**Where:** `manage/config_compare.py` `apply_changes` (~755–778). Contrast `deploy/common.finish_install`, `charon.import_cdvn_env_to_service(apply=True)` (~828), `keymanager._restart_systemd_unit`.

**Why inconsistent:** Left-pane apply updates files under `/etc/systemd/system` while systemd keeps the old `ExecStart` until a manual reload/restart. Operators can think the compare UI “applied” when the running process is unchanged.

**Fix direction:** `systemctl daemon-reload` after the apply loop; optionally prompt to restart changed units (same as the TUI edit-unit helper).

---

#### M-9. Charon P2P env name: install vs CDVN import

**Where:** `env` / `.env.overrides.example` / `deploy/orchestrator.py` (~341) use **`CHARON_P2P_PORT`**. `deploy/charon.py` `plan_cdvn_env_import` (~633–642) reads **`CHARON_PORT_P2P_TCP`** / `CHARON_P2P_TCP_ADDRESS` only.

**Why inconsistent:** Copying a CDVN `.env` key into EthPillar overrides does nothing on `install_charon()`. The reverse (EthPillar name in a CDVN `.env`) does nothing on import. Integration tests already alias both names (`tests/integration/port_bindings.py`).

**Fix direction:** Accept both keys in orchestrator and import (document one canonical name).

---

#### M-10. CDVN migrate can rewrite Charon binds after the VC was pointed at `:3600`

**Where:** `deploy/cdvn_migrate.py` `run_migration` (~1267–1280): `run_deploy()` then `import_cdvn_env_to_service(apply=True)`. Import maps `CHARON_VALIDATOR_API_ADDRESS` (and P2P/metrics). Orchestrator already wrote `validator.service` to `charon_validator_api_url(CHARON_VALIDATOR_API_PORT)` (default 3600).

**Why inconsistent:** Non-default CDVN validator-API ports survive on Charon but not on the VC. Default 3600 hides this. `preserve_beacon_endpoints` only keeps Charon’s **upstream BN** URL, not the VC→Charon URL.

**Fix direction:** After import, scrape `--validator-api-address` and `vc_service.patch_beacon_endpoint` (or regenerate the VC unit).

---

#### M-11. `list_dir_basenames` / `_dir_nonempty` still fail-open (residual of #76)

**Where:** `deploy/charon.py` `list_dir_basenames` (~52–65) returns `[]` when `sudo find` fails. `deploy/cdvn_migrate.py` `_dir_nonempty` (~698–699) uses that. #76 only changes `_dest_has_data`.

**Why inconsistent:** An unlistable **source** looks empty → datadir move skipped (`source empty or missing`). Occupied dest is now fail-closed (#76); source listing is still fail-open. Same helper, opposite safety.

**Fix direction:** Make `list_dir_basenames` raise / return a distinct error on sudo failure; treat unlistable sources as “do not skip silently”.

---

#### M-12. Lighthouse pubkey-list fallback datadir ≠ import fallback

**Where:** `manage_validator_keys.sh` `loadKeys` (~430): fallback `--datadir=/var/lib/lighthouse/validators`. `functions.sh` `getPubKeys` (~1656–1660): fallback `--datadir=/var/lib/lighthouse`.

**Why inconsistent:** EthPillar install uses `/var/lib/lighthouse_validator` (both paths agree). Legacy installs without that dir: import treats `…/validators` as the datadir (Lighthouse then looks for `…/validators/validators`); listing uses the parent. Silent empty list or failed import.

**Fix direction:** Use `/var/lib/lighthouse_validator` then `/var/lib/lighthouse` as `--datadir` in both helpers.

---

#### M-13. `resync_execution.sh` sources `functions.sh` via `pwd`

**Where:** `resync_execution.sh` (~9–10). Contrast `resync_consensus.sh` / `update_execution.sh` (`SCRIPT_DIR` / script location).

```9:10:resync_execution.sh
BASE_DIR=$(pwd)
source $BASE_DIR/functions.sh
```

**Why inconsistent:** Invoking the script from any cwd other than the repo (or `/usr/local/bin` context) fails to source helpers. Same class of “looks careful, breaks when not run from the TUI’s cwd”.

**Fix direction:** Resolve `BASE_DIR` from `BASH_SOURCE` like the other update/resync scripts.

---

## Suggested follow-up order

Small, independent PRs. Do not duplicate #72–#78.

1. **H-4 + H-3 + M-4 + M-12** — Validator key paths/binaries/password (Prysm list wallet, `loadKeys` from `validator.service`, aligned Lighthouse fallback, 12-vs-8 message). Highest “looks fine, keys/indices missing” risk.
2. **H-1 + H-2 + H-7** — Start-order / ensure-Charon (TUI all-clients, Validator submenu, `switch_client.sh` `restart` not `try-restart`). Same systemd idiom as #73, remaining call sites.
3. **H-5 + M-10 + M-9** — Charon DV companions (switch-time `json_requests`/Teku graffiti, CDVN VC API align, P2P env names).
4. **H-6 + M-3 + M-7** — Endpoint/version truth (node-checker VC version, scrape flags vs `exposeRpcCL`, UFW 5052). Coordinate with #71/#78 on `run.sh`.
5. **M-1 + M-2** — Ephemery (unify Nimbus `--network` path; pass `network_override` from orchestrator). Needs an Ephemery node to confirm Nimbus file vs directory.

Honorable next: **M-5/M-6** (Grandine/CSM menu + one VC helper), **M-8** (compare apply + daemon-reload), **M-11** (`list_dir_basenames` fail-closed), **M-13** (`resync_execution.sh` `BASE_DIR`).

---

## Notes for reviewers

- **Not flagged:** `reloadEnvOverridesAndMaybeRestart` using `try-restart` — that *is* the right idiom when the user may have left a unit stopped. `stopValidatorStackForUpdate` bouncing Charon during a VC binary swap is explicit in the comment (cluster blip, not a silent no-op).
- **#71 vs this tree:** Hardcoded 9000/30303/QUIC UFW and node-checker P2P checks on `main` are #71’s job. H-6, M-3, and M-7 are the leftovers.
- **#73 vs H-7:** #73’s preferred patch on that function is `restart` when Charon is installed (not `start`-only). H-7 needs `restart` after a unit patch so a healthy Charon also picks up the new BN URL.
- Report-only; no code fixes in this artifact.
