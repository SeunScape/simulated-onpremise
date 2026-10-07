# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this repo is

Infrastructure-as-code for deploying [Apache Fineract](https://fineract.apache.org/).
This is **not** the Fineract application source — it provisions and configures the
environment Fineract runs in.

## Layout

| Directory    | Purpose                                                        |
|--------------|----------------------------------------------------------------|
| `terraform/` | Terraform configuration for cloud infrastructure.              |
| `cloud-init/`| cloud-init configs for provisioning VM instances on first boot.|
| `scripts/`   | Helper/automation scripts for deployment and operations.       |
| `docs/`      | Project documentation.                                         |

## Conventions

- Do not commit Terraform state or secrets. `.gitignore` already excludes
  `.terraform/`, `*.tfstate`, `*.tfstate.*`, `*.tfvars`, `crash.log`, and `.env`.
- Keep sensitive values (credentials, tokens) out of tracked files; use
  `*.tfvars` or `.env` locally.

## Common commands

```bash
# Terraform (run from terraform/)
terraform init       # initialize providers/backend
terraform plan       # preview changes
terraform apply      # apply changes
terraform fmt        # format .tf files
terraform validate   # validate configuration
```
