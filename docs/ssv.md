# SSV Operator (EthPillar)

SSV is a **validator-client alternative**, not Charon-style beacon middleware.
Selecting **SSV Operator** in the install VC menu installs `ssv-node.service`
(and `ssv-dkg.service`) instead of `validator.service`. The node talks to your
local execution WebSocket and beacon REST API directly.

## Layout

| Path | Role |
|------|------|
| `/var/lib/ssv/` | Shared datadir (`ssv` user, mode `700`) |
| `/var/lib/ssv/encrypted_private_key.json` | Operator RSA keystore |
| `/var/lib/ssv/password` | Keystore password |
| `/var/lib/ssv/config.yaml` | Node config |
| `/var/lib/ssv/db/` | Persistent DB (**slashing-critical** — back up) |
| `/var/lib/ssv/operator.yaml` | DKG config (same key + password) |
| `/var/lib/ssv/output/` | DKG ceremony artifacts |
| `/usr/local/bin/ssvnode` | Node binary (built from source) |
| `/usr/local/bin/ssv-dkg` | DKG binary (GitHub release zip) |

Upstream does not publish `ssvnode` Linux binaries on GitHub. EthPillar clones
the release tag and runs `CGO_ENABLED=1 make build` (needs Go 1.26+ and
`build-essential`). That path works on amd64 and arm64 systemd hosts without
Docker. DKG still uses the published zip assets.

## Install

1. Install EL + CL as usual (EL must expose WebSocket, typically `:8546`).
2. On the validator-mode prompt, choose **SSV Operator** (or pass `--with_ssv`).
3. Not available as the Charon/Web3Signer *second* signer prompt.
4. Mutually exclusive with Charon and Web3Signer.
5. **SSV-only host** (remote BN/EL): use **Validator Client Only** → **SSV Operator**, or migrate an existing systemd operator (below).

## Migrate from existing systemd SSV

For a VM that already runs `ssvnode.service` / `ssvdkg.service` (or name variants
like `ssv-node`, `ssv_node`, `ssv-dkg`), EthPillar can take over management without
reinstalling EL/CL:

```bash
ethpillar --migrate_ssv
# or:
PYTHONPATH=. python3 -m deploy.ssv_migrate plan
PYTHONPATH=. python3 -m deploy.ssv_migrate run          # enable only
PYTHONPATH=. python3 -m deploy.ssv_migrate run --start  # enable + start
```

What it does:

1. Discovers legacy node/DKG units under `/etc/systemd/system/`
2. Reads `ExecStart` / `WorkingDirectory` to find config + binaries (also parses
   `run_ssv-dkg.sh` CLI flags when there is no YAML `operator-config`)
3. Preserves **full** `BeaconNodeAddr` / `ETH1Addr` (including multiple EL/CL pairs) and ports in `/var/lib/ssv/config.yaml` — not `.env.overrides`
4. Stops, disables, and renames legacy units to `*.service.bak` (systemd will not load them)
5. Copies keystore + `db/` (+ DKG output; TLS cert/key → `/var/lib/ssv/ssl/` when the wrapper uses `--serverTLSCertPath` / `--serverTLSKeyPath`)
6. Reuses legacy binaries when they match GitHub LATEST; otherwise rebuilds `ssvnode` / downloads `ssv-dkg` (`--rebuild-ssvnode`, `--allow-older-binary`)

DKG and the node **share** keystore/password/db under `/var/lib/ssv/`. DKG still gets its own `operator.yaml` (operator ID, ETH1 HTTP, TLS paths).

**Rollback:** stop EthPillar `ssv-node` / `ssv-dkg`, rename `*.service.bak` → `*.service`, `daemon-reload`, start the legacy units.

## Secrets

On first start without existing files, the node can generate
`encrypted_private_key.json` + `password`. Import existing keys via
**EthPillar → SSV Operator → Import operator keystore**.

**Back up both files on a separate device.** Losing either file means losing
the operator with no recovery path. The same files are required by `ssv-dkg`.

## Ports / firewall

| Port | Proto | Purpose | UFW |
|------|-------|---------|-----|
| 13001 | TCP | P2P | Open |
| 12001 | UDP | Discovery | Open |
| 3030 | TCP | DKG | Open when DKG enabled |
| 15000 | TCP | Prometheus metrics | Localhost only |
| 16000 | TCP | Health/API | Localhost only |

EthPillar → Security → UFW → **SSV: Allow P2P…** (or enable-defaults) opens
P2P + DKG. Override ports with `SSV_*` in `env` / `.env.overrides`.

## Monitoring

When Prometheus/Grafana are installed, EthPillar provisions:

- Scrape job `ssv` → `127.0.0.1:15000/metrics` with a static `pod` label
- **SSV Operational** Grafana dashboard (from `ssvlabs/ssv-stack`)

## DKG

After registering the operator at [app.ssv.network](https://app.ssv.network/),
set `operatorID` in `/var/lib/ssv/operator.yaml` (or `SSV_OPERATOR_ID` before
install) and start `ssv-dkg`.

## Next steps after install

1. Back up keystore + password + `db/`
2. Register operator (web app)
3. Open firewall ports
4. `sudo systemctl start ssv-node`
5. Optional: `sudo systemctl start ssv-dkg`

See also: [SSV node setup](https://docs.ssv.network/operators/operator-node/node-setup/),
[DKG](https://docs.ssv.network/operators/operator-node/setup-sidecars/enabling-dkg/start-dkg-node/),
[Monitoring](https://docs.ssv.network/operators/operator-node/monitoring/).
