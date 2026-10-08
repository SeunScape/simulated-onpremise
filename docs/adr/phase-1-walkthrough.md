# Phase 1 Walkthrough: As-Is Source Environment

Assumes Step 1 is done: Git and Terraform installed, project SSH key created (`~/.ssh/fineract_source`), repo created with `.gitignore`, `CLAUDE.md` and `.claude/settings.json` committed. Your DigitalOcean account must also be verified.

**How to use this with Claude Code:**
1. Save this file in your repo as `docs/phase-1-walkthrough.md`.
2. In a terminal, `cd` into the repo. For Steps 2–9, load your DigitalOcean token first (`read -s DIGITALOCEAN_TOKEN && export DIGITALOCEAN_TOKEN`), **then** start Claude Code from that same terminal so it inherits the token without ever seeing it.
3. Each step has a **Prompt for Claude Code**. Copy it in as written. One step per prompt, never the whole file.
4. Read every diff Claude Code proposes before accepting it. Bring "why" questions to your mentor, not Claude Code.
5. Run the **You verify** checks yourself, in your own terminal, not through Claude Code.
6. Only move on when **Done when** is true. Bring results from Steps 4, 6 and 8 back to your mentor for review.

**Session rule:** the Droplet costs money from `apply` until `destroy`. End every working session with Step 9 (teardown and cost record).

---

## Step 2: Terraform for the Droplet, SSH key and firewall

**Goal:** the source server defined in code. In this step you **write and plan only**. No `apply`: a Droplet without the Step 3 hardening must never go online.

**Create four files in `terraform/`.**

`versions.tf`: pins tool and provider versions.
```hcl
terraform {
  required_version = ">= 1.9"

  required_providers {
    digitalocean = {
      source  = "digitalocean/digitalocean"
      version = "~> 2.0"
    }
  }
}

provider "digitalocean" {}
```
The provider block is empty on purpose. It reads the token from the `DIGITALOCEAN_TOKEN` environment variable, so the token never appears in code.

`variables.tf`: the inputs.
```hcl
variable "region" {
  description = "DigitalOcean region; should be the closest to the chosen AWS region"
  type        = string
}

variable "droplet_size" {
  description = "Droplet size slug"
  type        = string
  default     = "s-4vcpu-8gb"
}

variable "operator_ip" {
  description = "Your public IPv4 address; the only source allowed to SSH in"
  type        = string
}

variable "ssh_public_key_path" {
  description = "Path to the project SSH public key"
  type        = string
  default     = "~/.ssh/fineract_source.pub"
}

variable "phase" {
  description = "Project phase, used for tagging"
  type        = string
  default     = "1"
}
```
`region` has no default deliberately: it depends on your AWS region ADR. Use a European region (`lon1` or `fra1`) for now and note in the ADR that it may change.

`main.tf`: the resources.
```hcl
locals {
  tags = ["project:fineract-migration", "env:source", "phase:${var.phase}"]
}

resource "digitalocean_ssh_key" "operator" {
  name       = "fineract-source-operator"
  public_key = file(pathexpand(var.ssh_public_key_path))
}

resource "digitalocean_droplet" "core_banking" {
  name       = "fineract-source-01"
  image      = "ubuntu-24-04-x64"
  region     = var.region
  size       = var.droplet_size
  ssh_keys   = [digitalocean_ssh_key.operator.fingerprint]
  monitoring = true
  tags       = local.tags
}

resource "digitalocean_firewall" "core_banking" {
  name        = "fineract-source-fw"
  droplet_ids = [digitalocean_droplet.core_banking.id]
  tags        = local.tags

  inbound_rule {
    protocol         = "tcp"
    port_range       = "22"
    source_addresses = ["${var.operator_ip}/32"]
  }

  outbound_rule {
    protocol              = "tcp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "udp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "icmp"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }
}
```

`outputs.tf`: what Terraform prints after deploying.
```hcl
output "droplet_ip" {
  value = digitalocean_droplet.core_banking.ipv4_address
}

output "ssh_command" {
  value = "ssh -i ~/.ssh/fineract_source <admin-user>@${digitalocean_droplet.core_banking.ipv4_address}"
}
```
Step 3 replaces `<admin-user>` once cloud-init creates that user.

**Know this:**
- **One inbound rule only:** SSH from your IP. Fineract and PostgreSQL ports aren't listed, so they're unreachable from the internet. Phase 2 adds VPN rules, restricted to AWS's tunnel addresses.
- **Outbound is wide open** so the server can fetch updates and Docker images. Record it as a known weakness for the Step 8 assessment; a real bank would restrict egress.
- **No `user_data` yet.** Step 3 adds it.
- **Dynamic IP:** if your ISP changes your public IP, SSH will stop working. Update `operator_ip` and re-apply the firewall.

**Set your variable values** in `terraform/terraform.tfvars` (already excluded by `.gitignore`):
```hcl
region      = "lon1"
operator_ip = "YOUR.IP.FROM.CURL"
```
Get your IP with `curl -4 ifconfig.me`.

**Create the DigitalOcean API token.** You don't need a Droplet first: the token belongs to your account, and Terraform uses it to create the Droplet. In the console under API, create a token with **custom scopes** limited to droplets, SSH keys, firewalls and tags, plus read access to regions, sizes and images. Set an expiry of 30 or 90 days. If a plan later fails with a permissions error, add only the scope it names. That's least privilege in practice; note it in an ADR.

Load it without it landing in your shell history:
```bash
read -s DIGITALOCEAN_TOKEN && export DIGITALOCEAN_TOKEN
```
Paste the token at the blank prompt and press Enter. Never paste it into Claude Code.

**Initialise, check and plan:**
```bash
cd terraform
terraform init
terraform fmt
terraform validate
terraform plan
```

**You verify:**
- `validate` reports success
- `plan` shows **3 to add**: the SSH key, the Droplet and the firewall
- `git status` shows no `.tfvars`, `.tfstate` or `.terraform/` files staged

**Prompt for Claude Code:** "Do Step 2. Create the four files exactly as specified, then run init, fmt and validate. Do not run plan or apply; I will."

**Done when:** `plan` shows 3 to add, and your commit contains the four `.tf` files plus `.terraform.lock.hcl` (it records the exact provider version so rebuilds are reproducible).

---

## Step 3: cloud-init (hardening and Docker)

**Goal:** the Droplet hardens itself on first boot, so it is never online in an unhardened state.

**Create** `cloud-init/user-data.yaml.tftpl` (a Terraform template) that:

1. Creates an admin user (e.g. `opsadmin`) in the `sudo` and `docker` groups, with your public key injected by Terraform. Sudo without a password is acceptable for a key-only user, but record it as an accepted risk in the assessment.
2. Sets `disable_root: true` and `ssh_pwauth: false`.
3. Writes `/etc/ssh/sshd_config.d/99-hardening.conf`:
   ```
   PermitRootLogin no
   PasswordAuthentication no
   KbdInteractiveAuthentication no
   PubkeyAuthentication yes
   AllowUsers opsadmin
   MaxAuthTries 3
   X11Forwarding no
   ```
   then restarts the `ssh` service.
4. Installs and enables `unattended-upgrades` (write `/etc/apt/apt.conf.d/20auto-upgrades` with both periodic settings set to `"1"`).
5. Installs `fail2ban`.
6. Installs Docker Engine and the Compose plugin from Docker's official apt repository (not Ubuntu's `docker.io` package).
7. Sets the timezone to `Africa/Lagos` (the bank operates in Lagos time; it matters for logs and cutover timing).

**Wire it into Terraform** in `main.tf` on the Droplet:
```hcl
user_data = templatefile("${path.module}/../cloud-init/user-data.yaml.tftpl", {
  admin_user     = "opsadmin"
  ssh_public_key = trimspace(file(pathexpand(var.ssh_public_key_path)))
})
```
Update the `ssh_command` output to use `opsadmin`.

**Know this:**
- Changing `user_data` forces Terraform to **replace** the Droplet. That's expected for a disposable server, but read plans carefully.
- **Docker bypasses host firewalls like `ufw`.** Published container ports are opened directly in iptables. That's why the DigitalOcean cloud firewall is the real perimeter, and why Step 5 binds every port to `127.0.0.1`. This is a common real-world mistake and a strong interview point.

**Prompt for Claude Code:** "Do Step 3 from docs/phase-1-walkthrough.md."

**Done when:** `terraform validate` passes and `terraform plan` still shows 3 to add, with `user_data` set.

---

## Step 4: Deploy and verify hardening

**Prompt for Claude Code:** "Do Step 4 from docs/phase-1-walkthrough.md. Run terraform plan and summarise what it will create. Wait for my approval before apply. After apply, wait for cloud-init to finish and show me its status. Then stop: I'll run the verification table myself."

**Run** (you approve the apply):
```bash
terraform plan
terraform apply
```

**Wait for cloud-init to finish** before testing anything:
```bash
ssh -i ~/.ssh/fineract_source opsadmin@<ip> 'cloud-init status --wait'
```
Expect `status: done`. If it says `error`, check `/var/log/cloud-init-output.log`.

**You verify** (save every command and its output to `docs/evidence/phase-1-hardening.md`; this is your audit evidence):

| Control | Test | Expected |
|---|---|---|
| Key login works | `ssh -i ~/.ssh/fineract_source opsadmin@<ip>` | Logged in |
| Root login blocked | `ssh -i ~/.ssh/fineract_source root@<ip>` | Rejected |
| Password login blocked | `ssh -o PubkeyAuthentication=no opsadmin@<ip>` | `Permission denied (publickey)` |
| Effective SSH config | `sudo sshd -T \| grep -E 'permitrootlogin\|passwordauthentication'` | both `no` |
| Auto updates on | `cat /etc/apt/apt.conf.d/20auto-upgrades` and `systemctl is-enabled unattended-upgrades` | both `"1"`; `enabled` |
| Only SSH exposed | From your laptop: `nmap -Pn <ip>` | Only `22/tcp open` |
| Firewall restricts by IP | From your phone's hotspot (different IP): `ssh ...` | Times out |
| Docker installed | `docker version` and `docker compose version` | Both report versions |

**Done when:** every row matches and the evidence file is committed.

---

## Step 5: Deploy Fineract and the Mifos web UI

**Goal:** core banking running on the Droplet, reachable only through an SSH tunnel.

**Prompt for Claude Code:** "Do Step 5 from docs/phase-1-walkthrough.md. Find the latest stable Fineract release, the PostgreSQL compose file at that tag, and current pinned tags for openmf/web-app, and tell me what you chose. Create deploy/docker-compose.yml, deploy/.env.example and scripts/deploy-fineract.sh. Bind every port to 127.0.0.1. List each change you made from the official compose file, one line each, then wait for my approval before running the deploy script."

1. **Find the official compose setup.** Have Claude Code inspect the `apache/fineract` repository **at a pinned release tag** (check GitHub releases for the latest stable) and identify the PostgreSQL compose file and the services it defines. For the UI, use the `openmf/web-app` image, also pinned. No `latest` tags.
2. **Create `deploy/` in your repo** containing:
   - `docker-compose.yml` adapted from the official file
   - `.env.example` listing every variable with placeholder values (the real `.env` lives only on the server and is gitignored)
3. **Required changes from the official file:**
   - Bind **every** published port to localhost, e.g. `"127.0.0.1:8443:8443"`. Never `"8443:8443"`.
   - Replace default database passwords with generated ones in the server's `.env`.
   - Pin the PostgreSQL image to a specific major.minor version and record it. DMS compatibility in Phase 3 depends on it.
   - Use a named volume for PostgreSQL data.
4. **Create `scripts/deploy-fineract.sh`** that copies `deploy/` to the server and runs `docker compose up -d`. The Droplet is destroyed every session, so deployment must be one command.
5. **Access through a tunnel** from your laptop:
   ```bash
   ssh -i ~/.ssh/fineract_source -L 8443:localhost:8443 -L 4200:localhost:<ui-port> opsadmin@<ip>
   ```
   Then open the UI in your browser on `localhost`.
6. **Change the default Fineract admin password immediately** after first login. Record that defaults existed, since that's an assessment finding.

7. **Accept the self-signed certificate first.** With the tunnel open, visit `https://localhost:8443/fineract-provider/actuator/health` in your browser and accept the warning (if that path 404s, open any Fineract API URL instead). Otherwise the web app's login fails silently.
8. **Walk through one loan by hand** in the UI: create an office, a loan officer, a loan product, and a client; then create, approve and disburse a loan, and post a repayment. Each click becomes an API call in your Step 6 generator.
9. **Record what you learned** in `docs/fineract-notes.md`: pinned versions (Fineract, PostgreSQL, web-app), first-boot time, memory used (`docker stats`), anything that broke, and the order of actions to create a loan.

**Expect a slow first boot.** Fineract creates hundreds of tables on first start, which can take several minutes. Watch `docker compose logs -f` rather than assuming it's broken.

**Do not** enable PostgreSQL logical replication now. The as-is bank wouldn't have it. Enabling it is a Phase 3 migration-prep task, and requiring a database restart to do so is part of your migration story.

**You verify:**
- `docker compose ps`: all services running or healthy
- `sudo ss -tlnp`: application and database ports listen on `127.0.0.1` only, never `0.0.0.0`
- `nmap -Pn <ip>` from your laptop: still only port 22
- You can log into the Mifos UI through the tunnel

**Done when:** you're logged into the UI via the tunnel, you've walked one loan through by hand, `docs/fineract-notes.md` is committed, and the port checks prove nothing else is exposed.

---

## Step 6: Fictional data generator

**Goal:** a realistic microfinance loan book, loaded through the Fineract API.

**Prompt for Claude Code (design first):** "Read Step 6 of docs/phase-1-walkthrough.md and the loan workflow in docs/fineract-notes.md. Before writing code, propose the design of scripts/generate_data.py: the sequence of API calls, the data model, how the seed and --continuous mode work, and how fake identifiers are formatted. Don't write code until I approve the design."

**Then:** "Implement the approved design. Test it with --clients 5 through the SSH tunnel and show me the output."

**Key decision: use the Fineract REST API, not direct SQL inserts.** The API enforces business rules, generates proper repayment schedules, and keeps referential integrity intact. Direct SQL would produce data Fineract itself might not accept, and your Phase 3 integrity validation would be testing garbage. Write a short ADR on this.

**Create `scripts/generate_data.py`** (Python, `requests`, `Faker`) that, in order:
1. Creates organisational setup: offices (branches), staff (loan officers), a loan product or two (e.g. a group-lending-style microloan and an SME loan)
2. Creates clients
3. Creates loans for a subset of clients: submit, approve, disburse
4. Posts repayments, including some late and partial ones, so the book looks real
5. Uses a **fixed random seed** so runs are reproducible
6. Takes a `--clients N` argument. Start small (e.g. 50) to test, then load the provisional volume from your client brief, labelled as provisional
7. Has a `--continuous` mode that keeps creating loans and repayments at a steady rate. This is your Phase 3 live-write workload, so build it now

**Fictional-data rules:** no real BVNs, account numbers or phone numbers. Use obviously fake formats (e.g. phone numbers in a reserved test range or prefixed with `000`). Note this in the README.

**Run it from your laptop through the SSH tunnel.** The self-signed certificate means disabling TLS verification for `localhost` only. Document why that's acceptable here and not acceptable in production.

**Back up after loading** so rebuilds don't require re-running the generator:
```bash
ssh ... 'docker compose exec -T <db-service> pg_dump -Fc -U <user> <db>' > backups/fineract-<date>.dump
```
Add `backups/` to `.gitignore`. Also add a restore script, since you'll need it after every rebuild.

**Done when:** the UI shows clients with active loans and repayment history, and you have a dump file that restores successfully on a fresh Droplet.

---

## Step 7: Capture the baseline

**Goal:** a recorded "before" snapshot. Phase 3 validation compares against it.

**Prompt for Claude Code:** "Do Step 7 from docs/phase-1-walkthrough.md. Write scripts/capture-baseline.sh using exact count(*) row counts, run it against the Droplet, and save the output to docs/baseline/."

**Create `scripts/capture-baseline.sh`** that records, with a timestamp, into `docs/baseline/`:
- PostgreSQL version, `wal_level`, database size
- **Exact** row counts for every table (`SELECT count(*)`, not `pg_stat` estimates, which are approximate and useless for validation)
- List of foreign key constraints
- Container image versions
- Server resources: `nproc`, `free -h`, `df -h`
- Backup state: which is "manual dump to operator laptop, no schedule, no tested restore". Write that honestly; it's a finding

**Done when:** a baseline file is committed and you can explain what each section will be compared against in Phase 3.

---

## Step 8: Well-Architected assessment

**Condition from the plan:** the client brief must be drafted before you write this. Findings are judged against the bank's needs.

**You write this, not Claude Code.** Use Claude Code afterwards to challenge it.

**Prompt for Claude Code (only after your draft exists):** "Act as a sceptical senior reviewer. Read docs/assessment.md and the evidence in docs/evidence/ and docs/baseline/. For each finding, tell me whether the evidence supports it and whether the severity is too high or too low. List weaknesses in this environment that I missed. Don't rewrite my document."

For each of the six pillars (Operational Excellence, Security, Reliability, Performance Efficiency, Cost Optimisation, Sustainability), record each finding as:

| Finding | Evidence | Business risk to the bank | Severity | Recommendation |
|---|---|---|---|---|

Some findings you've already created on purpose: the single server, open outbound traffic, no scheduled or tested backups, manual deployment, and default credentials at install. Find at least as many more yourself. Look at monitoring, logging, audit trail, patching of containers (unattended-upgrades doesn't update Docker images), secrets handling, and what happens to the bank if this one Droplet's region has an outage.

Link every finding to evidence from Steps 4–7. A finding without evidence is an opinion.

**Done when:** every pillar has findings with evidence, and you've had Claude Code argue against your severity ratings.

---

## Step 9: End of every session (teardown and cost record)

**Prompt for Claude Code:** "Do Step 9 from docs/phase-1-walkthrough.md. Take a fresh pg_dump to backups/, then run terraform plan -destroy and wait for my approval before destroying. Afterwards, draft today's line for docs/cost-record.md using the hours the Droplet ran."

After it finishes, check the DigitalOcean console yourself for leftovers. Don't rely on Claude Code's word for it.

1. Take a fresh `pg_dump` if data changed.
2. `terraform destroy` (you approve).
3. Check the DigitalOcean console for leftovers: snapshots, volumes, reserved IPs.
4. Add a line to `docs/cost-record.md`: date, phase, resources, hours running, cost.