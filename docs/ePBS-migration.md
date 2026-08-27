# ePBS / Gloas MEV migration

Gloas (the consensus-layer half of [Glamsterdam](https://docs.ethstaker.org/upgrades/glamsterdam-features/)) moves builder relay configuration **off MEV-Boost and onto the validator client**. Until that fork, proposals still go through the local MEV-Boost sidecar.

EthPillar follows EthStaker’s two-step cutover so you do not drop MEV too early.

---

## For node operators

This section is the TUI only. You do not need to run Python yourself.

### The two steps

1. **Before the Gloas fork** — copy your relays onto the validator client. Keep MEV-Boost running. The beacon node still talks to local MEV-Boost.
2. **After the Gloas fork** — stop MEV-Boost and tell the beacon node to stop using the local sidecar.

Do **not** run the after-fork step until Gloas is live on your network. Doing it early means the beacon node no longer talks to MEV-Boost, and most validator clients cannot fetch relays themselves yet.

### Open the menu

**MEV-Boost → 9 ePBS migration**

That item appears only when a validator client is installed (a separate validator service, or Grandine with the validator built into the consensus client).

| Menu item | When to use it |
|-----------|----------------|
| Before Gloas Fork — Apply Relays to VC | Before the Gloas fork |
| After Gloas Fork — Complete ePBS migration | After the Gloas fork |
| Show current ePBS status | Anytime (read-only) |

### What you see

**Before Gloas Fork** and **After Gloas Fork** use the same four screens. Nothing is written until you say yes on the second screen.

1. **Preview (dry-run).** A scrollable textbox titled with the menu item. It lists your client, what *would* change, warnings, and which services would need a restart. The last line is **`Dry-run (no files written).`** Press OK. Disk is unchanged.
2. **Confirm.** Yes/no. Before-fork: *Write these VC changes now?* (MEV-Boost stays running). After-fork: *Stop MEV-Boost and remove BN sidecar flags now?* (cutting over early can miss proposals). **No** or Esc returns to the ePBS menu with no changes.
3. **Applied.** If you confirmed, EthPillar copies the old files next to the originals, writes the new config, then shows a second textbox titled **`… — applied`**.
4. **Restart?** Only if something actually changed. Example: *Restart now so the new flags take effect?* **No** leaves the new config on disk; it takes effect the next time you restart that client from the usual menus.

**Show current ePBS status** is one textbox: which clients you have, whether relays are already on the validator, and whether the beacon node still points at local MEV-Boost. No confirm, no writes.

### What each step does (in plain language)

**Before the Gloas fork**

| Your validator | What EthPillar does |
|----------------|---------------------|
| **Prysm** (v7.1.7+) | Writes your MEV-Boost relays into Prysm’s proposer settings and turns builder mode on. Restarts the validator if you agree. **Does not** stop MEV-Boost. |
| **Lodestar** | Tries to put relay URLs on the validator. This needs a Lodestar build that is not in a normal release yet; the client may refuse to start. |
| **Lighthouse, Teku, Nimbus, Grandine** | Nothing is changed. Those clients cannot take a relay list on the validator yet. You can still run the after-fork step later to drop MEV-Boost and use local + P2P bids only. |

After this step, the beacon node still uses local MEV-Boost. Pre-fork blocks keep working as they do today.

**After the Gloas fork**

- Stops and disables MEV-Boost (the service file stays on disk).
- Removes the beacon-node setting that pointed at local MEV-Boost (`127.0.0.1:18550`). Other builder URLs are left alone.
- Leaves any validator relay config from the first step in place.

If the validator never got a relay list (placeholder clients, or you skipped the first step), the node uses **local execution client + P2P builder bids only** — no off-protocol relays.

### Safety

- Run the before-fork step while MEV-Boost is healthy.
- Run the after-fork step only after Gloas on that network.
- Read the preview before you confirm.
- Old files are copied beside the originals before overwrite (`*.bak.epbs.` plus a timestamp).

### Checking from the TUI

Use **Show current ePBS status**. It reports:

- validator and beacon-node clients
- whether your client fully supports VC relays yet
- whether MEV-Boost is installed and how many relays it has
- whether the validator already has a relay list
- whether the beacon node still has the local MEV-Boost URL

---

## For automation and developers

The TUI calls `python -m manage.epbs`. Scripts and tests can do the same. Default is dry-run; pass `--apply` to write.

```bash
# From the EthPillar install directory
PYTHONPATH="${PWD}" python3 -m manage.epbs status
PYTHONPATH="${PWD}" python3 -m manage.epbs prepare          # dry-run
PYTHONPATH="${PWD}" python3 -m manage.epbs prepare --apply
PYTHONPATH="${PWD}" python3 -m manage.epbs complete         # dry-run
PYTHONPATH="${PWD}" python3 -m manage.epbs complete --apply
```

`--json` prints a machine-readable plan (the TUI uses this after apply). `--systemd-dir` and `--prysm-settings` override paths for tests.

Implementation: `manage/epbs.py`. TUI wrappers: `runEpbsCli` / `runEpbsMigrationStep` / `submenuEPBS` in `functions.sh`.

### What each command changes

Relays and `-min-bid` are read from `mevboost.service`. Sidecar URLs are those containing `127.0.0.1:18550`, `localhost:18550`, or `[::1]:18550`. Non-sidecar builder URLs on the BN are kept.

Changed units and Prysm settings are copied to `*.bak.epbs.<timestamp>` before overwrite. `complete` stops and disables `mevboost.service`; the unit file is kept.

#### `prepare`

| Client | Behavior |
|--------|----------|
| **Prysm** (v7.1.7+) | Writes `/var/lib/prysm_validator/proposer-settings.json` (schema v2) with `default_config.builder.enabled`, `relays`, and `max_execution_payment: "0"`. Copies `--suggested-fee-recipient` into `fee_recipient` if missing. Upserts VC `--enable-builder` and `--proposer-settings-file`. Restarts `validator` if the TUI operator agrees. Does not stop MEV-Boost. |
| **Lodestar** | Adds prerelease VC flags `--builder`, `--builder.urls=<comma URLs>`, and `--builder.minBid` if set ([ChainSafe/lodestar#9832](https://github.com/ChainSafe/lodestar/pull/9832); not in a tagged release as of August 2026). |
| **Lighthouse, Teku, Nimbus, Grandine** | Documented no-op; units are not mutated. |

BN sidecar flags stay until `complete`.

#### `complete`

1. Stop and disable `mevboost.service`.
2. Strip BN sidecar builder flags:

   | Beacon node | Flag removed when it points at local MEV-Boost |
   |-------------|--------------------------------------------------|
   | Prysm | `--http-mev-relay` |
   | Lighthouse | `--builder` |
   | Teku | `--builder-endpoint` |
   | Lodestar | `--builder.urls` (and boolean `--builder` if no URL remains) |
   | Nimbus | `--payload-builder-url` |
   | Grandine | `--builder-url` / `--builder-api-url` |
   | Erigon-Caplin | `--caplin.mev-relay-url` |

3. Do not rewrite VC relay config from `prepare`.

Restart `consensus` after apply so the BN drops the sidecar URL. Prysm/Lodestar VC flags do not change on this step.

### Client support levels

| Validator | Support | Notes |
|-----------|---------|--------|
| Prysm v7.1.7+ | **full** | Relays in proposer-settings (`BuilderConfig.Relays`). BN `--http-mev-relay` until complete. |
| Lodestar | **prerelease** | VC `--builder.urls` / `--builder.minBid` from an open PR; tagged builds may reject them. |
| Lighthouse | **placeholder** | VC `--builder-proposals` only; one BN `--builder` URL. |
| Teku | **placeholder** | Staked Builder REST client ([Consensys/teku#11026](https://github.com/Consensys/teku/issues/11026)) not wired. Relays stay on BN `--builder-endpoint`. |
| Nimbus | **placeholder** | VC `--payload-builder=true`; URL on BN. |
| Grandine | **placeholder** | Integrated client; single `--builder-url`. |

### Inspecting a running Prysm VC

```bash
pid=$(sudo systemctl show -p MainPID --value validator)
tr '\0' ' ' < /proc/${pid}/cmdline
# expect --enable-builder and --proposer-settings-file=...
sudo cat /var/lib/prysm_validator/proposer-settings.json
```
