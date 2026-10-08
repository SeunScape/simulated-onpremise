# CLAUDE.md — fineract-source

## Project context
This repo builds the **"on-premises" source environment** for a portfolio cloud migration project.
A fictional Nigerian microfinance bank runs Apache Fineract (core banking) with the Mifos web UI
and PostgreSQL on a single server. That server is simulated by a DigitalOcean Droplet.
Later phases migrate it to AWS (RDS for PostgreSQL via AWS DMS over a Site-to-Site VPN).
That target lives in a separate repo; do not add AWS resources here.

This environment deliberately represents the bank's **current state**, including its weaknesses
(single server, manual deployments, no tested DR). Do not "fix" those weaknesses here unless asked;
they are documented in the Well-Architected assessment.

## Stack
- DigitalOcean Droplet, Ubuntu 24.04 LTS, x86, 8 GB RAM during build sessions
- Terraform (version pinned in `terraform/versions.tf`), DigitalOcean provider
- cloud-init for bootstrap and hardening
- Fineract deployed with the official Apache Fineract Docker Compose files
- Terraform state is LOCAL for this repo (disposable environment); see `docs/adr/`

## Repo layout
- `terraform/` — Droplet, cloud firewall, SSH key
- `cloud-init/` — hardening and Docker bootstrap
- `scripts/` — fictional data generator, baseline capture, live-write script
- `docs/adr/` — Architecture Decision Records (the human writes these; you may review)

## Hard rules
1. **Never** run `terraform apply`, `terraform destroy`, or any command that creates, changes or
   deletes real infrastructure without explicit approval in the current message.
   Always show the `terraform plan` output and summarise it first.
2. **Never** write secrets, tokens, passwords or private keys into any file. The DigitalOcean token
   comes from the `DIGITALOCEAN_TOKEN` environment variable. Never print it.
3. **Never** commit `*.tfstate`, `*.tfvars`, `.terraform/`, or `.env`. Check `.gitignore` before commits.
4. **Fineract and PostgreSQL must never be exposed publicly.** The cloud firewall allows inbound SSH
   only from the operator's IP. Docker must not publish database or app ports on public interfaces.
5. Hardening is non-negotiable: SSH keys only, no root login, no password auth, unattended security
   updates enabled.
6. **Only fictional data.** The data generator must never use or resemble real personal data
   (no real BVNs, phone numbers or account numbers; use clearly fake formats).

## Cost rules
- The Droplet is deleted (not stopped) when not in use; stopped Droplets are still billed.
- Watch for lingering billable items after teardown: snapshots, volumes, reserved IPs.
- Tag every resource: `project:fineract-migration`, `env:source`, `phase:<n>`.

## Wording rule
Describe the design as "using controls expected of CBN-regulated institutions".
Never write "CBN compliant" anywhere.

## How to work with me
- Implementation only. Do not explain concepts, choices or commands; explanations happen elsewhere.
- Keep replies terse: what you changed (file paths), what you ran, and the result. No summaries of the code.
- If a step is ambiguous or conflicts with these rules, ask one short question instead of guessing.
- Make small, reviewable changes. One concern per change.
- When something fails, show the actual error and a one-line diagnosis before changing code.
- Pin versions (Terraform, providers, Docker images). No `latest` tags.