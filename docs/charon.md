# Obol Charon DV

Charon is Obol’s DVT **middleware**. It is not a validator client: it sits between your beacon node and a normal VC (Lighthouse, Nimbus, Teku, Lodestar, or Prysm). EthPillar installs `charon.service` and points the VC at `http://127.0.0.1:3600`.

You need an existing `.charon` folder from [charon-distributed-validator-node](https://github.com/ObolNetwork/charon-distributed-validator-node) or an Obol DKG / Launchpad ceremony.

---

## Install

1. In EthPillar, choose **Custom Setup** (or Solo / CSM / VC-only).
2. When asked for a validator client, select **Obol Charon DV**, then select the **signer** VC (Lodestar matches CDVN’s default).
3. Non-interactive: `--with_charon --vc Lodestar` (do not pass `--vc "Obol Charon DV"`).

Artifacts:

| Path | Owner | Purpose |
|------|--------|---------|
| `/var/lib/charon/.charon/` | `charon` | Cluster lock, ENR private key, deposit data, key-share backup |
| `/var/lib/<vc>_validator/` | `validator` | Operational keystores after import |

---

## Migrate from CDVN

One-shot migrate of a [charon-distributed-validator-node](https://github.com/ObolNetwork/charon-distributed-validator-node) stack into EthPillar (clients + Charon + datadirs):

```bash
# After install.sh / from a clone
ethpillar --migrate_cdvn
ethpillar --migrate_cdvn --migrate_cdvn_path=/path/to/charon-distributed-validator-node
```

**Stop Docker Compose first** — migrate aborts if CDVN Compose still has running services, or if Docker status cannot be verified (slashing / port conflict risk). Never run CDVN Charon/VC and EthPillar Charon/VC at the same time.

What it does:

1. Ensures the `ethpillar` symlink + Python deps exist
2. Builds a plan from CDVN `.env` (`NETWORK`, `EL`, `CL`, `VC`, `MEV`, builder flags, BN endpoints) and on-disk `.charon` / `./data/*`
3. Shows the plan and asks you to confirm; you can decline individual datadir **moves**
4. Runs non-interactive deploy for the detected role, then moves confirmed Docker datadirs into `/var/lib/…`
5. Overlays `.charon` + maps `CHARON_*` → `charon.service`, syncs DKG key shares into the signer VC, enables the installed systemd units, installs **fresh** EthPillar monitoring (not the CDVN Grafana volume), then starts Charon/VC

**Role detection (v1):**

| CDVN stack | EthPillar |
|---|---|
| `EL=el-none` + `CL=cl-none` + VC + Charon (external BN) | **Validator Client Only** + `--with_charon` + rewritten `CHARON_BEACON_NODE_ENDPOINTS` |
| Local EL + local CL + VC + Charon | **Custom Setup** + `--ec` / `--cc` / `--vc` / `--with_charon` |
| `MEV=mev-mevboost` + local CL | also `--with_mevboost` |
| `MEV=mev-none` / external MEV + `BUILDER_API_ENABLED` | no `mevboost.service`; still Charon `--builder-api` + VC builder flags |
| Unknown `EL=` / `CL=` profile token | treated as external (no local EL/CL) when unmapped |
| Local CL with `EL=el-none` (or EL without CL) | unsupported in v1 |

Supported stock profiles today: `el-nethermind`, `el-reth`; `cl-lighthouse` / `teku` / `lodestar` / `prysm` / `nimbus` / `grandine`; `vc-lodestar` / `lighthouse` / `teku` / `prysm` / `nimbus`.

Datadir moves (after confirm): e.g. `./data/nethermind` → `/var/lib/nethermind`, `./data/lodestar` (VC) → `/var/lib/lodestar_validator`, `.charon` is **copied** to `/var/lib/charon/.charon` (CDVN checkout is preserved). Destinations that already have data are skipped (clear manually if you want the CDVN DB).

Run from CLI only: `ethpillar --migrate_cdvn` (interactive whiptail prompts for path/plan confirmation).

Dry-run / inspect plan (tests / advanced):

```bash
PYTHONPATH=. python3 -m deploy.cdvn_migrate plan --path ~/charon-distributed-validator-node
PYTHONPATH=. python3 -m deploy.cdvn_migrate run --path ~/charon-distributed-validator-node --dry-run
```

Lower-level Charon-only helpers (`.charon` copy / `.env` import) remain available via `python3 -m deploy.charon …`.

Docker beacon hostnames (`lighthouse`, `host.docker.internal`, …) are rewritten to `127.0.0.1`. Behind Charon, Lighthouse/Nimbus/Prysm/Lodestar VCs get `--distributed`; Teku gets `--Xobol-dvt-integration-enabled=true` and `--Xvalidator-client-beacon-api-executor-threads=50`. Grandine (integrated) is not supported behind Charon.

Key shares import from `.charon/validator_keys` using DKG `keystore-*.txt` passphrases (no password prompt). The same importer is used by **Validator → Import Obol Charon key shares**.

---

## Beacon node notes (Obol)

On a full EthPillar stack these are applied automatically:

- **Nimbus BN** → Charon gets `--feature-set-enable=json_requests`
- **Teku BN** → consensus unit gets `--validators-graffiti-client-append-format=DISABLED`
- **Lodestar BN + Lighthouse/Nimbus/Prysm VC** → install/migrate warns (Charon v1.11+ 🟠 matrix; client-side bug — use Lodestar/Teku VC or another BN until patched)
- **Lodestar VC behind Charon** → `--distributed` (Lodestar disables slot skip automatically when distributed)

For **VC-only + Charon**, configure the remote BN yourself (or add the Charon feature flag locally if the upstream BN is Nimbus).

---

## Monitoring

If EthPillar **Monitoring** (Grafana/Prometheus) is installed, Charon is wired in automatically:

- Prometheus scrapes Charon metrics (default `localhost:3620`; uses `--monitoring-address` from `charon.service` when set)
- Grafana provisions Obol’s **Charon Overview** dashboard (`/d/charon_overview/`)
- Prometheus datasource UID is set to `prometheus` so the CDVN dashboard binds correctly

Order does not matter: installing Charon after monitoring, or monitoring after Charon, both call the same provisioner (`ethereum-metrics-exporter.sh -c` / `manage.charon_monitoring`).

See `deploy/DEPLOY_FLOW.md` for orchestrator wiring. For ePBS / Gloas cutover with Charon, see [ePBS-migration.md](ePBS-migration.md).
