output "droplet_ip" {
  value = digitalocean_droplet.core_banking.ipv4_address
}

output "ssh_command" {
  value = "ssh -i ~/.ssh/fineract_source opsadmin@${digitalocean_droplet.core_banking.ipv4_address}"
}
