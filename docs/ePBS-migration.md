# ePBS / Gloas MEV migration

Gloas (the consensus-layer half of [Glamsterdam](https://docs.ethstaker.org/upgrades/glamsterdam-features/)) moves builder relay configuration **off MEV-Boost and onto the validator client**. Until that fork, proposals still go through the local MEV-Boost sidecar on `127.0.0.1:18550`.

EthPillar follows EthStaker’s two-step cutover so you do not drop MEV too early:

1. **Before the fork (prepare)** — copy relays onto the VC. Keep MEV-Boost running and keep the beacon node’s sidecar URL.
2. **After the fork (complete)** — stop MEV-Boost and remove the BN flags that pointed at `http://127.0.0.1:18550`.

Do **not** run complete until Gloas is live on your network. Completing early means the BN no longer talks to MEV-Boost, and most VCs cannot yet fetch relays themselves.

## How to run it

In the TUI:

**MEV-Boost → 9 ePBS migration** (shown only when a validator client is installed — separate `validator.service` or Grandine integrated)

| Menu item | Command | When |
|-----------|---------|------|
| Before Merge — Apply Relays to VC | `prepare` | Before Gloas |
| After Merge — Complete ePBS migration | `complete` | After Gloas |
| Show current ePBS status | `status` | Anytime |

Each apply step dry-runs first, asks for confirmation, writes backups, then offers to restart the units that changed.

From the CLI (EthPillar install directory, production venv):

```bash
PYTHONPATH="${PWD}" python3 -m manage.epbs status
PYTHONPATH="${PWD}" python3 -m manage.epbs prepare          # dry-run
PYTHONPATH="${PWD}" python3 -m manage.epbs prepare --apply
PYTHONPATH="${PWD}" python3 -m manage.epbs complete         # dry-run
PYTHONPATH="${PWD}" python3 -m manage.epbs complete --apply
```

Add `--json` for machine-readable output. Default is dry-run until you pass `--apply`.

## What each step changes

Relays and `-min-bid` are read from `mevboost.service`. Sidecar URLs are recognized when the value contains `127.0.0.1:18550`, `localhost:18550`, or `[::1]:18550`. Non-sidecar builder URLs on the BN are left alone.

Changed unit files and Prysm settings are copied to `*.bak.epbs.<timestamp>` before overwrite. `mevboost.service` is stopped and disabled on complete; the unit file is kept.

### Prepare (before Gloas)

| Client | What happens |
|--------|----------------|
| **Prysm** (v7.1.7+) | Writes `/var/lib/prysm_validator/proposer-settings.json` (schema v2) with `default_config.builder.enabled`, `relays`, and `max_execution_payment: "0"`. Copies `--suggested-fee-recipient` into `fee_recipient` if missing. Adds VC `--enable-builder` and `--proposer-settings-file`. Restarts `validator`. **Does not** stop MEV-Boost. |
| **Lodestar** | Adds prerelease VC flags `--builder`, `--builder.urls=<comma URLs>`, and `--builder.minBid` if set. These flags come from [ChainSafe/lodestar#9832](https://github.com/ChainSafe/lodestar/pull/9832) and are **not in a tagged Lodestar release** as of August 2026. The VC may refuse unknown flags. |
| **Lighthouse, Teku, Nimbus, Grandine** | No released VC relay-list flag. Prepare is a documented no-op (units are not changed). |

After prepare, the BN still has its MEV-Boost sidecar flag (for example Prysm `--http-mev-relay=http://127.0.0.1:18550`). Pre-fork blocks keep using the sidecar.

### Complete (after Gloas)

Runs for every client that has a consensus unit:

1. Stop and disable `mevboost.service` (unit file stays on disk).
2. Strip BN sidecar builder flags whose value is the local MEV-Boost URL:

   | Beacon node | Flag removed when it points at local MEV-Boost |
   |-------------|--------------------------------------------------|
   | Prysm | `--http-mev-relay` |
   | Lighthouse | `--builder` |
   | Teku | `--builder-endpoint` |
   | Lodestar | `--builder.urls` (and boolean `--builder` if no URL remains) |
   | Nimbus | `--payload-builder-url` |
   | Grandine | `--builder-url` / `--builder-api-url` |
   | Erigon-Caplin | `--caplin.mev-relay-url` |

3. Leave VC builder-enable flags and any relay list from prepare in place. Complete does not rewrite the Prysm proposer-settings file.

If you complete without a VC relay list (placeholder clients, or prepare never applied), the node uses **local EL + P2P builder bids only** — no off-protocol relays.

Restart `consensus` after complete so the BN drops the sidecar URL. Prysm/Lodestar VC flags do not change on this step.

## Client support

| Validator | Support | Notes |
|-----------|---------|--------|
| Prysm v7.1.7+ | **full** | Relays live in proposer-settings (`BuilderConfig.Relays`). BN still uses `--http-mev-relay` until complete. |
| Lodestar | **prerelease** | VC `--builder.urls` / `--builder.minBid` from an open PR; tagged builds may reject them. |
| Lighthouse | **placeholder** | VC is `--builder-proposals` only; one BN `--builder` URL. |
| Teku | **placeholder** | Staked Builder REST client ([Consensys/teku#11026](https://github.com/Consensys/teku/issues/11026)) not wired. Relays stay on BN `--builder-endpoint`. |
| Nimbus | **placeholder** | VC `--payload-builder=true`; URL on BN. |
| Grandine | **placeholder** | Integrated client; single `--builder-url`. |

## Checking the result

TUI **Show current ePBS status** (or `python -m manage.epbs status`) reports:

- detected VC / BN and support level
- whether MEV-Boost is installed and how many relays it has
- whether the VC already has a relay list
- whether BN sidecar flags are still present

For Prysm you can also inspect the live process after a validator restart:

```bash
pid=$(sudo systemctl show -p MainPID --value validator)
tr '\0' ' ' < /proc/${pid}/cmdline
# expect --enable-builder and --proposer-settings-file=...
sudo cat /var/lib/prysm_validator/proposer-settings.json
```

## Safety

- Run **prepare** while MEV-Boost is healthy and the BN sidecar URL is still present.
- Run **complete** only after Gloas on that network.
- Review the dry-run textbox (or CLI output) before confirming `--apply`.
- Backups are `*.bak.epbs.<timestamp>` next to the original unit or settings file.
- Completing on a placeholder client is still valid if you only want to drop the sidecar and rely on local + P2P bids.
