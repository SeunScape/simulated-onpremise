# Fineract notes (Phase 1, source environment)

## Pinned versions

Checked 2026-10-08 against the Docker Hub registry (`registry-1.docker.io`).

| Component | Image | Tag | Digest | Arch |
|---|---|---|---|---|
| Fineract (API) | `apache/fineract` | `1.12.1` | `sha256:a83cf1980609c984281a4741344bfe1c6df8a0c2dcc0ae6cee337cded1dc53fe` | multi-arch (amd64, arm64) |
| Mifos web UI | `openmf/web-app` | `1.14` | `sha256:18dae7cc7701669ccd983b8bd879bec2a68bb7166a4a4fc7e37661b4b7ef912b` | linux/amd64 |
| PostgreSQL | `postgres` | `16.15` | `sha256:ca0bd484cb98bf4b24eb1010e73fb3fcbd6714d240fbc1a10eea5b7dbecb641d` | multi-arch (index) |

### Why these versions

- **Fineract 1.12.1:** the latest Apache Fineract release is `1.15.0` (git tag, 2026-07-10), but `apache/fineract` on Docker Hub
  publishes only `1.10.1`, `1.11.0`, `1.12.0` and `1.12.1` as release tags. Neither `1.13.x`, `1.14.x` nor `1.15.x` exists.
  `1.12.1` is the newest released image.
- **web-app 1.14:** newest `openmf/web-app` release image (git tag `v1.0.0-fineract1.14`). The `1.14` tag is mutable
  (re-pushed 2026-10-08), hence the digest pin.
- **PostgreSQL 16.15:** newest 16.x patch release. The official Fineract compose file at `1.12.1` uses `postgres:16.1`.
  AWS DMS supports self-managed PostgreSQL 16.x as a source from DMS engine **3.5.3** (17.x and 18.x need 3.5.4).
  Source: https://docs.aws.amazon.com/dms/latest/userguide/CHAP_Introduction.Sources.html

### Known risk

- **UI/API version mismatch:** web-app `1.14` is built against Fineract 1.14. The API here is 1.12.1. Watch for login or
  screen errors and record them below.

## First boot

Measured 2026-10-08 on `s-4vcpu-8gb` (7.755 GiB visible to Docker), first deploy via `scripts/deploy-fineract.sh`.

- First-boot time: Fineract `Started ServerApplication in 140.732 seconds` (empty database, schema created on first start).
- Memory used (`docker stats --no-stream`, idle after first boot):

| Container | Memory |
|---|---|
| `fineract-fineract-1` | 930.1 MiB |
| `fineract-db-1` | 72.23 MiB |
| `mifos-web-app` | 4.59 MiB |

## What broke

- **cloud-init ignored all user-data** (2026-10-08): two em dashes in comments of `cloud-init/user-data.yaml.tftpl`
  made the Droplet's YAML parse fail (`unacceptable character #x0080`). No `opsadmin` user, no hardening, root key
  login still possible. Fixed by making the template plain ASCII.
- **Web UI login returned 404** (2026-10-08): `openmf/web-app` defaults to calling the API on its own origin
  (`http://localhost:4200/fineract-provider/...`). Fixed by setting `FINERACT_API_URL(S)=https://localhost:8443` and
  `FINERACT_PLATFORM_TENANT_IDENTIFIER=default` on the web-app service.

## Loan workflow (UI order of actions)

_TBD_

## Loaded data (provisional)

Loaded 2026-10-08 with `scripts/generate_data.py --clients 100 --seed 20261008 --as-of 2026-10-08`. The run stopped
while creating client 7 (error not captured); the partial load was kept as the working data set:

- 6 clients, 7 loans (2 closed, 5 active), 57 repayments, 71 loan transactions in total
- Backup: `backups/fineract-20261008-161056-fineract_default.dump` (not in git)
- Restore test 2026-10-08: fresh Droplet (`terraform apply -replace`), `scripts/deploy-fineract.sh`, then
  `scripts/restore-fineract.sh` with the dump above. Counts matched (6 clients, 7 loans, 71 loan transactions),
  loan statuses matched, Fineract restarted cleanly (`Started ServerApplication in 99.983 seconds`).
