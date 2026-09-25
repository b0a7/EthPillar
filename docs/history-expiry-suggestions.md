# History expiry suggestions (suitable for ~2TB disks)

Helper for home stakers on ~2TB NVMe: these flags are the usual choice so
the EL stays comfortable on that size of disk — not a claim that the modes
use ~2TB.

`helpers/history_expiry_suggestions.sh` is the source of truth for suggested
and optional flags plus status. Execution Client → **Suggest pruning
parameters** opens tmeld directly on the `execution.service` pair (not the
folder list): left is the exact installed unit (what gets applied), right is
that same unit with selected prune flags merged into **ExecStart only** (not
a full EthPillar regen). After you quit, the compare-style apply path runs
(list-changed → confirm → `.bak` → apply → daemon-reload / restart). Node
Checker stays print/status-based. The menu row is hidden for unsupported ELs
(Ethrex).

Suggestions target **staking / full nodes**, not intentional archive, Caplin
archive, or operators who need local `eth_getLogs` / receipts (Rocket Pool,
SSV, StakeWise, indexers). A short rolling window (~5 months) can break
those protocols unless they use an external RPC.

Research snapshot: **2026-09-23** upstream docs + [eth-docker](https://github.com/ethstaker/eth-docker)
`EL_NODE_TYPE` entrypoints (including merged [#2763](https://github.com/ethstaker/eth-docker/pull/2763)
rolling 33024 epochs and [#2819](https://github.com/ethstaker/eth-docker/pull/2819)
Nethermind 2.0 FlatDB default). Commands change; prefer each client’s current
docs before applying.

## EthPillar defaults today (main)

| Client | EthPillar default | History expiry? |
|--------|-------------------|-----------------|
| Geth | `--state.scheme=path`, no `--history.chain` (Geth default `all`) | No block-history expiry |
| Nethermind | Hybrid pruning + `FullPruningTrigger=VolumeFreeSpace` / `ThresholdMb=300000` | State prune only (Patricia). No `History.Pruning` |
| Besu | `--sync-mode=SNAP` + `BONSAI` | SNAP skips pre-merge bodies on Mainnet checkpoint |
| Reth | `--full` | Full-node prune profile (incl. pre-merge bodies) |
| Erigon / Caplin | `--prune.mode=minimal` | ~100k-block window |
| Ethrex | `--syncmode snap` | No history-expiry CLI yet |

## Per-client options (2026-09)

### Geth
- **Full history:** default `--history.chain=all`.
- **Pre-merge expiry:** `--history.chain=postmerge`. Offline first:
  `geth prune-history --datadir <dir> --history.chain postmerge`.
- **Pre-Prague:** `--history.chain=postprague` (newer binaries; eth-docker
  `pre-prague-expiry`).
- **Rolling:** `--history.chain=recent --history.blocks=N` (N > 100000) is
  not in a tagged Geth release yet. When it ships, restore Further as
  experimental `--history.chain=recent --history.blocks=1056768` (same
  ~5-month window as Besu/Reth Further).
- **Archive:** `--gcmode=archive` (hash scheme) or `--history.state=0` (path).
- **Suitable for a ~2TB drive:** Recommended `--history.chain=postprague`.
  No Further picker until rolling history is released.
  `--history.chain=postmerge` alone is no longer the recommendation
  (pre-Prague history is getting tight).

### Nethermind (2.0 is LATEST, released ~2026-09-22)
- **State prune:** `Pruning.Mode=Hybrid` with `VolumeFreeSpace` /
  `StateDbSize` / `Manual`. EthPillar already sets Hybrid + 300 GB free-space
  trigger. This is **not** block-history expiry. Those full-prune knobs apply
  to **Patricia** DBs; on Flat they are accepted but do not prune.
- **History prune:** `History.Pruning=Disabled` (default),
  `UseAncientBarriers` (pre-merge on Mainnet/Sepolia), or `Rolling` (moving
  window). eth-docker rolling expiry uses
  `--History.Pruning=Rolling --History.RetentionEpochs=33024` (~5 months).
- **2.0 Flat vs Patricia:** FlatDB is the default for a **fresh** (or
  resynced) database. An existing **Patricia** DB keeps Patricia on upgrade.
  Patricia → Flat needs a resync / migration; EthPillar does not rewrite
  units. Patricia drop is TBD upstream.
- **Full history:** `--Sync.AncientBodiesBarrier=0 --Sync.AncientReceiptsBarrier=0`.
- **Suitable for a ~2TB drive:** keep Hybrid, add
  `--History.Pruning=Rolling --History.RetentionEpochs=33024`.
  Use `UseAncientBarriers` instead if Rocket Pool / SSV / StakeWise need
  local logs.

### Besu
- **SNAP + Bonsai:** default full-node path; Mainnet checkpoint SNAP does not
  download pre-merge bodies/receipts. ~1.14 TB observed for 26.5.0 snap.
- **Offline pre-merge prune:** `besu --data-path=<path> storage prune-pre-merge-blocks`.
- **Online** `--history-expiry-prune` is **deprecated in 26.1.0**.
- **Rolling / aggressive:** `--Xchain-pruning-enabled=ALL` with
  `--Xchain-pruning-blocks-retained=1056768` (~5 months) or `113056`
  (aggressive). Experimental in eth-docker.
- **Archive:** Forest + FULL, or `X_BONSAI_ARCHIVE`.
- **Suitable for a ~2TB drive:** keep SNAP + BONSAI; add rolling only if disk
  is still tight.

### Reth
- **Archive:** default when no `--full` / `--minimal` / `--prune.*`.
- **Full:** `--full` — ~10,064-block state/receipts window, pre-merge body
  prune. EthPillar default.
- **Rolling:** `--prune.bodies.distance 1056768 --prune.receipts.distance 1056768`
  (~5 months; eth-docker `rolling-expiry`).
- **Aggressive:** `--minimal`.
- **Suitable for a ~2TB drive:** keep `--full`. Use rolling/`--minimal` only
  for extra savings; both drop receipts that some protocols need.

### Erigon / Caplin
- **minimal:** last ~100k blocks (~14 days). EthPillar default; usual choice
  for a ~2TB drive.
- **full:** ~262,144-block EIP-8252 window (v3.6+), unless
  `--prune.distance.blocks=keep-post-merge` (or the numeric sentinel) keeps
  all post-merge blocks.
- **Rolling (eth-docker):** `--prune.mode=full --persist.receipts=false
  --prune.distance=1056768 --prune.distance.blocks=1056768`.
- **archive:** `--prune.mode=archive`.
- **Caplin:** `--caplin.states-archive` / `--caplin.blocks-archive` /
  `--caplin.blobs-archive` keep extra consensus history **on purpose**. Helper
  reports INFO, never FAIL.

### Ethrex
No history-expiry flags (eth-docker `prune-history` is a no-op). Keep snap.
Helper reports INFO.

### Consensus layer
CL is usually not the growth driver on a ~2TB drive. Staking defaults:
- **Lighthouse:** blob/payload prune on by default; avoid `--prune-blobs=false`
  and `--supernode`.
- **Teku:** `--data-storage-mode=minimal` (default).
- **Prysm:** `--beacon-db-pruning` is opt-in.

## How to use

- Execution Client → **Suggest pruning parameters**:
  1. Detects the installed EL from `/etc/systemd/system/execution.service`.
  2. The menu row is omitted for unsupported ELs (Ethrex: no history-expiry
     CLI). Short-circuits (msgbox, no tmeld) when there is no EL or
     recommended flags are already present.
  3. Archive / Caplin archive: warns, then opens tmeld only if you confirm.
  4. Besu / Reth: pick **Recommended** (suitable for a ~2TB drive) vs
     **Further savings**. Geth / Nethermind / Erigon skip the picker and
     use recommended. Geth Recommended is `--history.chain=postprague`.
  5. Pre-tmeld warnings cover destructive prune, RP/SSV `eth_getLogs`, and
     Geth/Besu offline prune commands as **notes only** (never auto-run).
  6. tmeld opens on the unit file pair (content diff), not the folder list.
  7. Consensus layer is a one-line note, not a second pane.
- Security & Node Checks → **Node Checker** (WARN if flags missing or disk
  ≥90%; INFO for archive/Caplin; never FAIL). Print/status only.
- CLI: `helpers/history_expiry_suggestions.sh` (`--all`, `--unit FILE`,
  `--checker`) still prints suggestions without rewriting units.

## Sources (2026-09-23)

- [Geth command-line options](https://geth.ethereum.org/docs/fundamentals/command-line-options)
- [Nethermind history pruning](https://docs.nethermind.io/fundamentals/history-pruning/)
- [Besu pre-merge history expiry](https://besu.hyperledger.org/public-networks/how-to/pre-merge-history-expiry)
- [Reth pruning & node modes](https://reth.rs/run/storage/pruning/)
- [Erigon pruning modes](https://docs.erigon.tech/fundamentals/pruning-modes)
- [Partial history expiry (EF, 2025-07-08)](https://blog.ethereum.org/2025/07/08/partial-history-exp)
- eth-docker `EL_NODE_TYPE` (`pre-merge-expiry`, `rolling-expiry`, …) and
  client `docker-entrypoint.sh` files
- eth-docker [#2763](https://github.com/ethstaker/eth-docker/pull/2763)
  (`--History.Pruning=Rolling --History.RetentionEpochs=33024`)
- eth-docker [#2819](https://github.com/ethstaker/eth-docker/pull/2819)
  (Nethermind 2.0 FlatDB default; Patricia existing DBs keep their layout)
