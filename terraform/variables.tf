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
