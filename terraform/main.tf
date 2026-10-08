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

  # The Droplet hardens itself on first boot via cloud-init, so it is never
  # online in an unhardened state. Changing user_data forces a replace.
  user_data = templatefile("${path.module}/../cloud-init/user-data.yaml.tftpl", {
    admin_user     = "opsadmin"
    ssh_public_key = trimspace(file(pathexpand(var.ssh_public_key_path)))
  })
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
