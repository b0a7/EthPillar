### Do you like this software? Star the project and become a [⭐ Stargazer](https://github.com/mjkeating/EthPillar/stargazers)

# 🛡️ EthPillar
## Your Friendly Ethereum Node Installer & Manager

[![Github release](https://img.shields.io/github/v/release/mjkeating/EthPillar)](https://github.com/mjkeating/EthPillar/releases)
[![License](https://img.shields.io/github/license/mjkeating/EthPillar)](https://github.com/mjkeating/EthPillar/blob/main/LICENSE)
[![GitHub Repo stars](https://img.shields.io/github/stars/mjkeating/EthPillar?logo=github&color=yellow)](https://github.com/mjkeating/EthPillar/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/mjkeating/EthPillar?logo=github&color=blue)](https://github.com/mjkeating/EthPillar/network/members)
[![GitHub last commit](https://img.shields.io/github/last-commit/mjkeating/EthPillar?logo=git)](https://github.com/mjkeating/EthPillar/commits/main)
[![Discord](https://img.shields.io/badge/discord-join%20chat-5B5EA6)](https://discord.gg/VdQQ7Bc3hn)

---

## ⚠️ Important for Existing Installations (Switching to this fork)

This is the **actively maintained fork** of CoinCashew’s EthPillar [](https://github.com/coincashew/ethpillar).

If you installed EthPillar from https://github.com/coincashew/ethpillar (or before April 18, 2026) and have never switched to this fork, your installation is still pointing to the original (inactive) repo.

**Switch once** with these two commands:

```bash
cd ~/git/ethpillar
git remote set-url origin https://github.com/mjkeating/EthPillar.git
```

After switching, use **System Administration → Update EthPillar** inside the TUI to pull the latest changes.
New users can skip this step — the installer below already points to this fork.

---

## v5.4.11

Adds Phase 1 [Keymanager API](https://ethereum.github.io/keymanager-APIs/) support so you can manage local validator keystores from the TUI without hand-editing client tools, plus a fix so Ctrl-C while viewing logs returns to the menu.

---

## 🚀 What is EthPillar?

EthPillar is a free, open-source tool to set up and manage your Ethereum node with just a few commands. Whether you’re home solo staking, using Lido CSM, defending cypherpunk ethos with Aztec L2 sequencer node, or running your own RPC node, EthPillar makes everything easy—from installing clients to monitoring your system—all via a friendly text user interface (TUI).

**Highlights:**
- Supports ARM64 & AMD64 hardware
- Native [Lido CSM Integration](https://docs.lido.fi/run-on-lido/csm/node-setup/intermediate/ethpillar)
- Native [Aztec L2 Integration](https://docs.coincashew.com/ethpillar/aztec)
- Solo staking, full node, and testnet configurations
- Fast updates and troubleshooting
- Plugins for monitoring and performance

![EthPillar UI Preview](https://github.com/coincashew/coincashew/raw/master/.gitbook/assets/EthPillar.final.png)

---

## 🏁 Quickstart: One-line Ubuntu Install

Open a terminal and run:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/mjkeating/EthPillar/main/install.sh)"
```

---

## 🤔 Why use EthPillar?

- **Beginner Friendly**: No need to memorize complex commands
- **Fast Setup**: Deploy minority consensus/execution clients in minutes (Nimbus-Nethermind, Teku-Besu, Lodestar-Besu, Lighthouse-Reth, MEVboost included)
- **Easy Updates**: Find and install the latest releases quickly
- **Compatibility**: Works with Coincashew’s V2 staking setups

Already running a validator? EthPillar works with [Coincashew’s Staking Guide](https://docs.coincashew.com/guides/mainnet).

---

## 🌟 Features

- **Testnet Support**: Ephemery & Hoodi testnets for risk-free practice
- **Lido CSM Integration**: Stake with as little as 2.4 ETH via Lido CSM ([Learn more](https://csm.testnet.fi/?ref=ethpillar))
- **Plugins**: Aztec, Lido CSM, Node-checker, validator tools, monitoring, stats, and more
- **Grafana Dashboards**: Built-in Ethereum node monitoring
- **Troubleshooting Tools**: Built-in checks for common node issues with Node Checker
- **Flexible Deployment Configurations**: Solo staking node, Full Node, CSM, Validator-only, Failover, or Obol Charon DV setups
- **ePBS / Gloas migration**: Two-step MEV-Boost cutover under **MEV-Boost → ePBS migration** (Prysm TUI; [guide](docs/ePBS-migration.md))

---

## 🔀 ePBS migration (Gloas)

When Gloas lands, builder relays move from the MEV-Boost sidecar onto the validator client. EthPillar splits that into **prepare** (copy relays to the VC, keep MEV-Boost running) and **complete** (stop MEV-Boost and drop the BN sidecar URL after the fork).

See **[docs/ePBS-migration.md](docs/ePBS-migration.md)** for the TUI/CLI steps, what each client changes, and when it is safe to complete.

With **Obol Charon**, complete also removes `charon.service` `--builder-api` (the MEV-Boost builder proxy). Obol has not shipped a stable Charon ePBS release yet — watch [Charon releases](https://github.com/ObolNetwork/charon/releases).

---

## 👀 Screenshots

_Main Menu_
![Main Menu](https://docs.coincashew.com/img/preview02.png)

---

## 🎬 Demo

[![Watch the demo](https://img.youtube.com/vi/aZLPACj2oPI/maxresdefault.jpg)](https://www.youtube.com/watch?v=aZLPACj2oPI)

---

## 📝 Prerequisites

- Review [Staking for Beginners](https://www.reddit.com/r/ethstaker/wiki/staking_for_beginners/)
- [Learn staking basics & hardware requirements](https://docs.coincashew.com/guides/mainnet/step-1-prerequisites)
- Linux (Ubuntu recommended, tested on 24.04 LTS, also compatible with Armbian, Linux Mint, Debian)
- AMD64 or ARM64 hardware (16GB RAM recommended for ARM64 single-board computers)

---

## 🛠️ Installation

### Option 1: Automated One-Liner (Recommended)

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/mjkeating/EthPillar/main/install.sh)"
```

### Option 2: Manual Install

```bash
sudo apt-get update && sudo apt-get install git curl ccze bc tmux
mkdir -p ~/git/ethpillar
git clone https://github.com/mjkeating/EthPillar.git ~/git/ethpillar
sudo ln -s ~/git/ethpillar/ethpillar.sh /usr/local/bin/ethpillar
ethpillar
```

---

## 🏃 Next Steps

Congrats! You’ve installed EthPillar and are ready to set up your node.

Recommended next key steps:

- Configure network, port forwarding, and firewall (Security & Node Checks > UFW Firewall)
- Enable monitoring (Logging & Monitoring > Monitoring)
- Benchmark your node (Toolbox > Yet-Another-Bench-Script)
- Set up validator keys (Validator Client > Generate / Import Validator Keys)
- Finally, run the automated Node Checker to verify everything is up to spec (Security & Node Checks > Node Checker)

---

## 🪢 Obol Charon DV

Charon is Obol’s DVT **middleware**. It is not a validator client: it sits between your beacon node and a normal VC (Lighthouse, Nimbus, Teku, Lodestar, or Prysm). EthPillar installs `charon.service` and points the VC at `http://127.0.0.1:3600`.

You need an existing `.charon` folder from [charon-distributed-validator-node](https://github.com/ObolNetwork/charon-distributed-validator-node) or an Obol DKG / Launchpad ceremony.

### Install

1. In EthPillar, choose **Custom Setup** (or Solo / CSM / VC-only).
2. When asked for a validator client, select **Obol Charon DV**, then select the **signer** VC (Lodestar matches CDVN’s default).
3. Non-interactive: `--with_charon --vc Lodestar` (do not pass `--vc "Obol Charon DV"`).

Artifacts:

| Path | Owner | Purpose |
|------|--------|---------|
| `/var/lib/charon/.charon/` | `charon` | Cluster lock, ENR private key, deposit data, key-share backup |
| `/var/lib/<vc>_validator/` | `validator` | Operational keystores after import |

### Migrate from CDVN

One-shot migrate of a [charon-distributed-validator-node](https://github.com/ObolNetwork/charon-distributed-validator-node) stack into EthPillar (clients + Charon + datadirs):

```bash
# After install.sh / from a clone
ethpillar --migrate_cdvn
ethpillar --migrate_cdvn --migrate_cdvn_path=/path/to/charon-distributed-validator-node
```

**Stop Docker Compose first** — migrate aborts if CDVN Compose still has running services (slashing / port conflict risk). Never run CDVN Charon/VC and EthPillar Charon/VC at the same time.

What it does:

1. Ensures the `ethpillar` symlink + Python deps exist
2. Builds a plan from CDVN `.env` (`NETWORK`, `EL`, `CL`, `VC`, `MEV`, builder flags, BN endpoints) and on-disk `.charon` / `./data/*`
3. Shows the plan and asks you to confirm; you can decline individual datadir **moves**
4. Runs non-interactive deploy for the detected role, then moves confirmed Docker datadirs into `/var/lib/…`
5. Overlays `.charon` + maps `CHARON_*` → `charon.service`, installs **fresh** EthPillar monitoring (not the CDVN Grafana volume), offers key import / start

**Role detection (v1):**

| CDVN stack | EthPillar |
|---|---|
| `EL=el-none` + `CL=cl-none` + VC + Charon (external BN) | **Validator Client Only** + `--with_charon` + rewritten `CHARON_BEACON_NODE_ENDPOINTS` |
| Local EL + local CL + VC + Charon | **Custom Setup** + `--ec` / `--cc` / `--vc` / `--with_charon` |
| `MEV=mev-mevboost` + local CL | also `--with_mevboost` |
| `MEV=mev-none` / external MEV + `BUILDER_API_ENABLED` | no `mevboost.service`; still Charon `--builder-api` + VC builder flags |
| Unknown `EL=` / `CL=` profile token | hard fail (rename to a supported stock profile) |
| Local CL with `EL=el-none` (or EL without CL) | unsupported in v1 |

Supported stock profiles today: `el-nethermind`, `el-reth`; `cl-lighthouse` / `teku` / `lodestar` / `prysm` / `nimbus` / `grandine`; `vc-lodestar` / `teku` / `prysm` / `nimbus`.

Datadir moves (after confirm): e.g. `./data/nethermind` → `/var/lib/nethermind`, `./data/lodestar` (VC) → `/var/lib/lodestar_validator`, `.charon` → `/var/lib/charon/.charon`. Destinations that already have data are skipped (clear manually if you want the CDVN DB).

TUI: **Obol Charon DV → Migrate from CDVN (full stack)** runs the same flow.

Dry-run / inspect plan (tests / advanced):

```bash
PYTHONPATH=. python3 -m deploy.cdvn_migrate plan --path ~/charon-distributed-validator-node
PYTHONPATH=. python3 -m deploy.cdvn_migrate run --path ~/charon-distributed-validator-node --dry-run
```

Lower-level Charon-only helpers (`.charon` copy / `.env` import) remain available via `python3 -m deploy.charon …`.

Docker beacon hostnames (`lighthouse`, `host.docker.internal`, …) are rewritten to `127.0.0.1`. Behind Charon, Lighthouse/Nimbus/Prysm VCs get `--distributed`; Lodestar VC gets `--distributed --slotSkip false`; Teku gets `--Xobol-dvt-integration-enabled=true` and `--Xvalidator-client-beacon-api-executor-threads=50`. Grandine (integrated) is not supported behind Charon.

### Beacon node notes (Obol)

On a full EthPillar stack these are applied automatically:

- **Nimbus BN** → Charon gets `--feature-set-enable=json_requests`
- **Teku BN** → consensus unit gets `--validators-graffiti-client-append-format=DISABLED`
- **Lodestar BN + Lighthouse/Nimbus/Prysm VC** → install/migrate warns (Charon v1.11+ 🟠 matrix; client-side bug — use Lodestar/Teku VC or another BN until patched)
- **Lodestar VC behind Charon** → `--distributed --slotSkip false` (Obol/CDVN DVT tuning)

For **VC-only + Charon**, configure the remote BN yourself (or add the Charon feature flag locally if the upstream BN is Nimbus).

### Monitoring

If EthPillar **Monitoring** (Grafana/Prometheus) is installed, Charon is wired in automatically:

- Prometheus scrapes `localhost:3620`
- Grafana provisions Obol’s **Charon Overview** dashboard (`/d/charon_overview/`)
- Prometheus datasource UID is set to `prometheus` so the CDVN dashboard binds correctly

Order does not matter: installing Charon after monitoring, or monitoring after Charon, both call the same provisioner (`ethereum-metrics-exporter.sh -c` / `manage.charon_monitoring`).

See `deploy/DEPLOY_FLOW.md` for orchestrator wiring.

---

## ❓ FAQ

- Visit the [FAQs](https://docs.coincashew.com/ethpillar/faq)
  
---

## 📞 Support & Community

- Join [Discord](https://discord.gg/Kjrnkv8dgs)
- Open issues or pull requests on [GitHub](https://github.com/mjkeating/EthPillar)

---

## ❤️ Donate

Support public goods! Find us on [Giveth || Gitcoin Grants](https://giveth.io/project/ethpillar-streamlining-ethereum-staking-for-everyone) or donate to [0xCF83d0c22dd54475cC0C52721B0ef07d9756E8C0](https://etherscan.io/address/0xCF83d0c22dd54475cC0C52721B0ef07d9756E8C0) (coincashew.eth)

---

## 🔄 Update EthPillar

**TUI Update:**  
System Administration > Update EthPillar, then restart.

**Manual Update:**
```bash
cd ~/git/ethpillar
git pull
```

---

## 🌠 Contribute

- Star the project on [GitHub](https://github.com/coincashew/EthPillar)
- Share your experience on X or Reddit
- Give feedback ([GitHub Issues](https://github.com/coincashew/EthPillar/issues))
- Submit PRs to improve EthPillar!

---

## 🙌 Credits

Thanks to [accidental-green](https://github.com/accidental-green/validator-install) for inspiring this tooling!

---

## ⭐ Stargazers over time

[![Stargazers over time](https://starchart.cc/coincashew/EthPillar.svg?variant=adaptive)](https://starchart.cc/coincashew/EthPillar)
