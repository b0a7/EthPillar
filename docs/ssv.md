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
