# Web3Signer

Web3Signer is Consensys' remote signer. It is not a beacon-node proxy. The validator client still talks to the beacon node (or to Charon, in a later release). Only signature requests go to Web3Signer on `http://127.0.0.1:9000`. Validator keys and the slashing-protection database live in Web3Signer.

This version is for new installs. It does not move keys out of a validator client that already has them. It also does not install together with Obol Charon.

## Install

Web3Signer is listed after Obol Charon DV on every validator-client menu that offers Charon: Custom Setup, Solo/CSM/Failover, and Validator Client Only. Selecting it opens a second list for the validator client that will call the signer. That list does not include Charon or Web3Signer.

`--with_web3signer --vc <client>` installs the same pairing without the menus.

```bash
bash deploy/install-node.sh \
  --ec Geth --cc Teku --vc Teku --network HOODI \
  --with_web3signer --install_config "Custom Setup" \
  --skip_prompts true --fee_address 0xYourFeeRecipient
```

Do not pass `--with_web3signer` together with `--with_charon`.

What gets installed:

| Path | Purpose |
|------|---------|
| `/usr/local/bin/web3signer` | Web3Signer distribution (Java 25) |
| `/var/lib/web3signer/keystores` | EIP-2335 keystores and matching `.txt` password files |
| `/var/lib/web3signer/db.env` | PostgreSQL password for the slashing database (mode `600`) |
| `web3signer.service` | Listens on `127.0.0.1:9000` only |

Slashing protection uses a local PostgreSQL database named `web3signer`. Install loads the schema from the release's `migrations/postgresql` scripts; Web3Signer does not migrate on startup and refuses to start until `database_version` matches that release. The validator client unit is ordered `After=web3signer.service`.

How each client finds keys:

- **Teku:** public keys are read from `http://127.0.0.1:9000/api/v1/eth2/publicKeys`. The validator unit does not pass `--validator-keys`; that path is not created and Teku exits if it is missing.
- **Prysm:** the same public-key URL. Install still creates an empty wallet and `password.txt`, because the unit keeps `--wallet-dir` and `--wallet-password-file`. Prysm reads the public-key list at startup, so importing keys restarts the validator.
- **Nimbus:** `--web3-signer-url` (discovers keys itself).
- **Lodestar:** `--externalSigner.url` and `--externalSigner.fetch`.
- **Lighthouse:** `validator_definitions.yml` entries of `type: web3signer`. Import appends one entry per key and records that pubkey in `slashing_protection.sqlite`. Lighthouse does not create that file itself and exits if it is missing. Local slashing protection for those keys is disabled; Web3Signer records signatures.
- **Grandine (integrated):** `--web3signer-api-urls` on the consensus unit. No local keystore directory.

## Import keys

Use **Web3Signer → Generate / Import Validator Keys**. Keystores are copied into `/var/lib/web3signer/keystores` and are not imported into the validator client. The validator-client menu does not offer key import while Web3Signer is installed.

```bash
python3 -m deploy.web3signer import-keystores --directory /path/to/validator_keys --password-file /path/to/password.txt
```

If each `keystore*.json` already has a sibling `.txt` password file, `--password-file` is unnecessary.

## Manual check (not run in CI)

CI starts Web3Signer with an empty keystore directory and checks `/upcheck` and an empty public-key list. It does not attest.

After that smoke is green, use a throwaway Hoodi validator:

1. Install with `--with_web3signer --vc Teku`.
2. Import a new keystore (not a key that is active anywhere else).
3. Confirm the Teku log shows the external signer and a successful attestation.
4. Repeat once with Lighthouse. That path writes `validator_definitions.yml` instead of a URL flag.

## Updates

`ethpillar upgrade web3signer` or **Web3Signer → Update to latest release**.
