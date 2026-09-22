# Variables del lab. Las credenciales NO van aqui: el provider las lee de las
# variables de entorno PROXMOX_VE_ENDPOINT / PROXMOX_VE_API_TOKEN /
# PROXMOX_VE_INSECURE (fichero ~/.netseg.env en ctrl01).

variable "node_name" {
  description = "Nombre del nodo Proxmox"
  type        = string
  default     = "pve01"
}

variable "pool_id" {
  description = "Pool que acota TODO el radio de accion de esta automatizacion"
  type        = string
  default     = "netseg"
}

variable "lab_bridge" {
  description = "Bridge VLAN-aware interno del lab. vmbr2 y no vmbr1: vmbr1 ya pertenece a otro laboratorio en este hipervisor (ver docs/arquitectura.md)"
  type        = string
  default     = "vmbr2"
}

variable "wan_bridge" {
  description = "Bridge WAN (red domestica). Solo lo usan fw01 (WAN) y ext01"
  type        = string
  default     = "vmbr0"
}

variable "vm_storage" {
  description = "Datastore para discos de VMs, rootfs de LXC y unidades cloud-init"
  type        = string
  default     = "local-lvm"
}

variable "alma_image" {
  description = "Imagen cloud de AlmaLinux 9 preparada por scripts/fetch-images.sh: es la variante -netseg, con guest-exec habilitado en el agente (RHEL lo bloquea de fabrica y sin el no hay harness fuera de banda)"
  type        = string
  default     = "local:import/AlmaLinux-9-GenericCloud-netseg.x86_64.qcow2"
}

variable "opnsense_iso" {
  description = "ISO de OPNsense ya presente en el storage (scripts/fetch-images.sh)"
  type        = string
  default     = "local:iso/OPNsense-25.7-dvd-amd64.iso"
}

variable "lxc_alma_template" {
  description = "Plantilla LXC de AlmaLinux 9 (cliente pc01)"
  type        = string
  default     = "local:vztmpl/almalinux-9-default_20240911_amd64.tar.xz"
}

variable "lxc_alpine_template" {
  description = "Plantilla LXC de Alpine (iot01, guest01, ext01)"
  type        = string
  default     = "local:vztmpl/alpine-3.24-default_20260714_amd64.tar.xz"
}

variable "ci_user" {
  description = "Usuario que cloud-init crea en las VMs AlmaLinux (lo usara Ansible)"
  type        = string
  default     = "ansible"
}

variable "ci_ssh_key" {
  description = "Clave publica SSH inyectada en VMs (cloud-init) y LXC (root). Valor real en terraform.tfvars (fuera de git)"
  type        = string
  default     = ""
}
