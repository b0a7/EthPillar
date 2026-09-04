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

That item appears when:

- **Solo co-located** — MEV + local VC, no Charon, and the VC fully supports migration (**Prysm**, **Lodestar** v1.47.0+)
- **Split LXC (MEV/CC host)** — `mevboost.service` present and **no** local `validator.service` (always shown; export / remote complete)

Lighthouse, Teku, Nimbus, and Grandine do not get the solo menu; the CLI still works for those clients.

**Obol Charon DVT (co-located with MEV):** the MEV menu entry is **hidden** while Charon is installed — even if the signer VC is Prysm or Lodestar. Builder/MEV traffic goes through Charon, and Obol has not shipped stable Gloas/ePBS support yet. The CLI still refuses to write VC relay lists behind Charon on a co-located prepare (that would bypass the middleware).

| Menu item | When to use it |
|-----------|----------------|
| Before Gloas Fork — Apply Relays to VC | Before the Gloas fork (co-located) |
| Before Gloas Fork — Export migration file | Before the Gloas fork (MEV host, remote VC) |
| After Gloas Fork — Complete ePBS migration | After the Gloas fork |
| Show current ePBS status | Anytime (read-only) |

### Split LXC / remote VC

When the beacon node + MEV-Boost live on one host and the validator (or Charon + VC) on another:

1. **MEV/CC host** — **MEV-Boost → ePBS migration → Export migration file**. Writes `~/hostname-YYYYMMDD-HHMMSS.ethpillar.epbs-migration` (relays + min-bid). Copy that file to the VC host.
2. **VC host** — **Validator → ePBS migration (import)** (or **Charon → ePBS migration** once `charonEpbsSupported` is true). Import applies prepare from the file. Until Obol ships Charon ePBS, DV setups with a supported signer VC use the Validator menu.
3. **After Gloas** — Complete on the **MEV/CC host** (stops MEV, strips BN sidecar; confirm the other host already imported). Complete on the **VC/Charon host** strips Charon `--builder-api` when present; solo VC is a no-op locally.

EC placement does not matter for this flow.

### What you see

**Before Gloas Fork** and **After Gloas Fork** use the same four screens. Nothing is written until you say yes on the second screen.

1. **Preview (dry-run).** A scrollable textbox titled with the menu item. It lists your client, what *would* change, warnings, and which services would need a restart. The last line is **`Dry-run (no files written).`** Press OK. Disk is unchanged.
2. **Confirm.** Yes/no. Before-fork: *Write these VC changes now?* (MEV-Boost stays running). After-fork: *Stop MEV-Boost and remove BN sidecar flags now?* (cutting over early can miss proposals). **No** or Esc returns to the ePBS menu with no changes.
3. **Applied.** If you confirmed, EthPillar copies the old files next to the originals, writes the new config, then shows a second textbox titled **`… — applied`**.
4. **Restart?** Only if something actually changed. Example: *Restart now so the new flags take effect?* **No** leaves the new config on disk; it takes effect the next time you restart that client from the usual menus. After Complete, the applied textbox also shows how to roll back (restore `*.bak.epbs.*` and `systemctl enable --now mevboost`).

**Show current ePBS status** is one textbox: which clients you have, whether relays are already on the validator, and whether the beacon node still points at local MEV-Boost. No confirm, no writes.

### What each step does (in plain language)

**Before the Gloas fork**

| Your validator | What EthPillar does |
|----------------|---------------------|
| **Prysm** (v7.1.7+) | Writes your MEV-Boost relays into Prysm’s proposer settings and turns builder mode on. Restarts the validator if you agree. **Does not** stop MEV-Boost. |
| **Lodestar** (v1.47.0+) | Writes `--builder.urls` and `--builder.minBid` on the validator. Older Lodestar builds skip this so the client can still start. **Does not** stop MEV-Boost. |
| **Lighthouse, Teku, Nimbus, Grandine** | Not offered in the TUI yet. Prepare is a no-op; Complete is refused unless you use the CLI `--force`. |

After this step, the beacon node still uses local MEV-Boost. Pre-fork blocks keep working as they do today.

**After the Gloas fork** (Prysm or Lodestar, after the first step succeeded)

- Stops and disables MEV-Boost (the service file stays on disk).
- Removes the beacon-node setting that pointed at local MEV-Boost (`127.0.0.1:18550`). Other builder URLs are left alone.
- With **Obol Charon** installed, also removes `charon.service` `--builder-api` — see [Obol Charon DV](#obol-charon-dv) below and [docs/charon.md](charon.md) for install/migrate.
- Leaves any validator relay config from the first step in place.

Restart order after complete when Charon is present: **consensus → charon → validator**.

If you skipped the first step, **Complete is refused** so you do not drop MEV-Boost with no VC relay replacement.

If you ran Complete too early: restore `consensus.service` from the newest `consensus.service.bak.epbs.*`, then `sudo systemctl enable --now mevboost` and restart consensus (`sudo systemctl daemon-reload && sudo systemctl restart consensus`).

### Obol Charon DV

Charon sits between your validator client and beacon node and proxies builder/MEV traffic. On the pre-Gloas path that means `charon.service` runs with **`--builder-api`** (MEV-Boost builder proxy).

Open the cutover from **MEV-Boost → ePBS migration** when Charon is **not** installed. While Charon is present the TUI entry is hidden (signer VC support does not apply). CLI prepare still skips VC relay writes so relays are not written behind Charon by mistake.

| Step | Charon behavior |
|------|-----------------|
| **Before Gloas (prepare)** | Keeps `--builder-api`; **does not** write Prysm/Lodestar VC relay lists (those would bypass Charon). MEV-Boost sidecar path unchanged |
| **After Gloas (complete)** | Removes `--builder-api` from `charon.service`, strips the BN sidecar URL, disables MEV-Boost. Allowed while `--builder-api` is still present (no VC relay list required) |

After **complete**, restart in order: **consensus → charon → validator**.

**Upstream:** Obol has not shipped a stable Charon ePBS/Gloas release yet. EthPillar strips the MEV-Boost proxy flag on complete so your node matches the post-fork sidecar-off layout; confirm Charon versions against [Charon releases](https://github.com/ObolNetwork/charon/releases) before relying on Gloas block production through Charon.

### Safety

- Run the before-fork step while MEV-Boost is healthy.
- Run the after-fork step only after Gloas on that network.
- Read the preview before you confirm.
- Old files are copied beside the originals before overwrite (`*.bak.epbs.` plus a timestamp).
- Too-early Complete: restore the BN unit from that backup and `sudo systemctl enable --now mevboost`.

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
# Only if you really want local EL + P2P bids with no VC relays:
PYTHONPATH="${PWD}" python3 -m manage.epbs complete --apply --force
# Split LXC: export on MEV host, import on VC host
PYTHONPATH="${PWD}" python3 -m manage.epbs export -o ~/bn.ethpillar.epbs-migration
PYTHONPATH="${PWD}" python3 -m manage.epbs import ~/bn.ethpillar.epbs-migration
PYTHONPATH="${PWD}" python3 -m manage.epbs import ~/bn.ethpillar.epbs-migration --apply
# MEV/CC host after the VC host imported:
PYTHONPATH="${PWD}" python3 -m manage.epbs complete --apply --remote-vc-prepared
```

`--json` prints a machine-readable plan (the TUI uses this after apply). `--systemd-dir` and `--prysm-settings` override paths for tests. `--force` allows `complete` when the VC has no relay list. `--remote-vc-prepared` allows `complete` on a MEV/CC host when the VC on another host already imported.

Changed units and Prysm settings are copied to `*.bak.epbs.<timestamp>` before overwrite. `complete` stops and disables `mevboost.service`; the unit file is kept. If you completed too early: restore the newest `consensus.service.bak.epbs.*` over `consensus.service`, then `sudo systemctl enable --now mevboost && sudo systemctl daemon-reload && sudo systemctl restart consensus`.

Implementation: `manage/epbs.py`. TUI wrappers: `runEpbsCli` / `runEpbsMigrationStep` / `submenuEPBS` / `submenuEPBSImport` in `functions.sh`. Menu visibility: `epbsTuiSupported`, `epbsImportUnderValidator`, `epbsImportUnderCharon` (`charonEpbsSupported` stub until Obol ships).

### What each command changes

Relays and `-min-bid` are read from `mevboost.service` (or from a migration file for `import`). Sidecar URLs are those containing `127.0.0.1:18550`, `localhost:18550`, or `[::1]:18550`. Non-sidecar builder URLs on the BN are kept.

#### `export` / `import`

| Command | Host | Behavior |
|---------|------|----------|
| **export** | MEV | Writes `.ethpillar.epbs-migration` JSON (format version 1: relays, min-bid, network, hostname, timestamp). Always writes. |
| **import** | VC / Charon+VC | Loads that file and applies the same VC relay writes as solo `prepare`. Does not require local MEV. Unlike co-located prepare, **does** write VC relays even when Charon is present. |

#### `prepare`

| Client | Behavior |
|--------|----------|
| **Obol Charon** (any signer VC, co-located) | Keeps `--builder-api`; **skips** VC relay writes (CLI). TUI entry hidden until Obol ships Gloas/ePBS support. |
| **Prysm** (v7.1.7+, no Charon) | Writes `/var/lib/prysm_validator/proposer-settings.json` (schema v2) with `default_config.builder.enabled`, `relays`, and `max_execution_payment: "0"` (Gloas execution-payment cap; `0` is the public-bid / proto default and does not disable builder payments). Copies `--suggested-fee-recipient` into `fee_recipient` if missing. Upserts VC `--enable-builder` and `--proposer-settings-file`. Restarts `validator` if the TUI operator agrees. Does not stop MEV-Boost. |
| **Lodestar** (v1.47.0+, no Charon) | Adds VC flags `--builder`, `--builder.urls=<comma URLs>`, and `--builder.minBid` (MEV-Boost ETH min-bid converted to integer Gwei), **only when** `lodestar validator --help` lists `--builder.urls`. Older builds are skipped so the VC can still start. |
| **Lighthouse, Teku, Nimbus, Grandine** | Documented no-op; units are not mutated. |

BN sidecar flags stay until `complete`.

#### `complete`

Refused unless the VC already has a relay list (successful solo `prepare` / `import`), Charon still has `--builder-api`, you pass `--remote-vc-prepared` (split MEV host), or you pass `--force` (local EL + P2P bids only).

On a **VC/Charon-only** host (no consensus/MEV): strips Charon `--builder-api` when present; solo VC reports nothing local to complete.

On a **MEV/CC** host:

1. Stop and disable `mevboost.service`.
2. Strip BN sidecar builder flags (skipped with a warning if no consensus unit):

   | Beacon node | Flag removed when it points at local MEV-Boost |
   |-------------|--------------------------------------------------|
   | Prysm | `--http-mev-relay` |
   | Lighthouse | `--builder` |
   | Teku | `--builder-endpoint` |
   | Lodestar | `--builder.urls` (and boolean `--builder` if no URL remains) |
   | Nimbus | `--payload-builder-url` |
   | Grandine | `--builder-url` / `--builder-api-url` |
   | Erigon-Caplin | `--caplin.mev-relay-url` |
   | Obol Charon | `--builder-api` (MEV-Boost proxy; no stable upstream ePBS release yet) |

3. Do not rewrite VC relay config from `prepare` / `import`.

Restart `consensus` after apply so the BN drops the sidecar URL. When Charon is installed, also restart `charon` (and `validator` if its flags changed on prepare/import). Prysm/Lodestar VC flags do not change on this step alone.

### Client support levels

The MEV-Boost TUI shows ePBS migration for **full** support (solo) or always on a MEV host with no local VC.

| Validator | Support | Notes |
|-----------|---------|--------|
| Prysm v7.1.7+ | **full** | TUI + CLI. Relays in proposer-settings (`BuilderConfig.Relays`). BN `--http-mev-relay` until complete. |
| Lodestar v1.47.0+ | **full** | TUI + CLI. VC `--builder.urls` / `--builder.minBid` written only if `--help` lists them. |
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
