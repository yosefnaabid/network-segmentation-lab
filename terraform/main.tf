# Infraestructura del laboratorio de segmentacion: 5 VMs, 4 LXC.
# Los bridges NO se gestionan aqui: vmbr0 es intocable (acceso al hipervisor),
# vmbr2 lo crea scripts/bootstrap-pve.sh. El token de terraform@pve no tiene
# (ni debe tener) Sys.Modify para tocar la red del nodo.
#
# Version del provider FIJADA (regla del proyecto: no confiar en memoria;
# los atributos cambian entre versiones). Documentacion consultada: v0.111.x.

terraform {
  required_version = ">= 1.8"

  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "0.111.1"
    }
  }
}

provider "proxmox" {
  # endpoint, token e insecure llegan por PROXMOX_VE_* (~/.netseg.env).
  # Ninguna operacion de este plan requiere SSH al nodo (sin snippets ni
  # source_file): todo va por la API con el token acotado al pool.
}

locals {
  # VMs AlmaLinux: ballooning min 512 / max 1024 (spec §4). IPs estaticas por
  # cloud-init; gateway y DNS apuntan al futuro fw01 de su VLAN.
  vms_alma = {
    srv-dns = { vmid = 620, vlan = 20, ip = "10.10.20.10/24", gw = "10.10.20.1", segment = "SERVERS" }
    srv-app = { vmid = 621, vlan = 20, ip = "10.10.20.20/24", gw = "10.10.20.1", segment = "SERVERS" }
    dmz-web = { vmid = 630, vlan = 30, ip = "10.10.30.10/24", gw = "10.10.30.1", segment = "DMZ" }
    jump01  = { vmid = 650, vlan = 50, ip = "10.10.50.10/24", gw = "10.10.50.1", segment = "MGMT" }
  }

  # Clientes LXC. vlan = 0 significa "sin tag" (ext01 va al bridge WAN).
  # Sin DHCP en las VLANs internas hasta que fw01 este instalado: arrancan
  # igualmente y se les entra por pct exec (fuera de banda).
  lxc_clients = {
    pc01    = { vmid = 610, vlan = 10, template = var.lxc_alma_template, ostype = "centos", segment = "USERS" }
    iot01   = { vmid = 640, vlan = 40, template = var.lxc_alpine_template, ostype = "alpine", segment = "IOT" }
    guest01 = { vmid = 699, vlan = 99, template = var.lxc_alpine_template, ostype = "alpine", segment = "GUEST" }
    ext01   = { vmid = 601, vlan = 0, template = var.lxc_alpine_template, ostype = "alpine", segment = "WAN" }
  }
}

# ---------------------------------------------------------------------------
# fw01: OPNsense. ISO enganchado, instalacion manual (su instalador es de
# consola). agent desactivado hasta instalar os-qemu-guest-agent en fase 2:
# con agent=true el provider esperaria un agente que aun no existe.
# ---------------------------------------------------------------------------
resource "proxmox_virtual_environment_vm" "fw01" {
  name        = "fw01"
  description = "Firewall perimetral OPNsense: router, DHCP, DNS forwarder, WireGuard"
  node_name   = var.node_name
  vm_id       = 600
  pool_id     = var.pool_id
  tags        = ["netseg", "firewall"]

  # La consola de fw01 quedo con keymap espanol en la instalacion manual; sin
  # esto, VNC traduce con layout US y los simbolos bailan (incidencia #12).
  keyboard_layout = "es"

  operating_system {
    type = "other" # FreeBSD
  }

  cpu {
    cores = 2
    type  = "host"
  }

  # Ballooning desactivado: el soporte en FreeBSD es flojo y no
  # se arriesga en el firewall (spec §4). Sin "floating" no hay balloon.
  # Nota: el instalador del DVD exige >=3 GB; durante la instalacion manual se
  # sube temporalmente a 4096 y se revierte al terminar (incidencia #11).
  memory {
    dedicated = 2048
  }

  # Activado tras instalar el plugin os-qemu-guest-agent en la instalacion
  # manual: sin agente, el harness no llega a fw01 (spec §8).
  agent {
    enabled = true
  }

  disk {
    datastore_id = var.vm_storage
    interface    = "virtio0"
    size         = 16
    file_format  = "raw"
  }

  cdrom {
    file_id   = var.opnsense_iso
    interface = "ide3"
  }

  boot_order = ["virtio0", "ide3"]

  # Orden de las NICs (incidencia #7): la configuracion de fabrica del
  # ISO live de OPNsense asigna LAN = primera NIC, con una IP estatica del rango
  # domestico habitual y servidor DHCP activo. Si esa NIC cae en el bridge de la
  # red de casa, secuestra la puerta de enlace (ocurrio: ARP del router
  # envenenado e Internet caido en el equipo fisico). Por eso net0 es el trunk
  # aislado (la LAN de fabrica no puede hacer dano ahi) y la WAN (net1) nace
  # desconectada hasta que la instalacion manual asigne interfaces.
  network_device { # net0 = vtnet0: trunk 802.1Q (la LAN de fabrica cae aqui, aislada)
    bridge = var.lab_bridge
    model  = "virtio"
  }

  network_device { # net1 = vtnet1: WAN domestica (reconectada tras la instalacion manual)
    bridge       = var.wan_bridge
    model        = "virtio"
    disconnected = false
  }

  on_boot = true
  started = true
}

# ---------------------------------------------------------------------------
# VMs AlmaLinux 9: disco importado de la imagen GenericCloud (via API, sin
# SSH), cloud-init con IP estatica, agente QEMU activo (viene en la imagen).
# ---------------------------------------------------------------------------
resource "proxmox_virtual_environment_vm" "alma" {
  for_each = local.vms_alma

  name        = each.key
  description = "Servidor AlmaLinux 9 del segmento ${each.value.segment}"
  node_name   = var.node_name
  vm_id       = each.value.vmid
  pool_id     = var.pool_id
  tags        = ["netseg", lower(each.value.segment)]

  operating_system {
    type = "l26"
  }

  cpu {
    cores = 1
    type  = "host" # AlmaLinux 9 exige x86-64-v2; "host" es lo mas robusto en anidado
  }

  memory {
    dedicated = 1024
    floating  = 512 # balloon: minimo 512, maximo 1024 (spec §4)
  }

  agent {
    enabled = true # qemu-guest-agent viene en la imagen GenericCloud; requisito del harness (§8)
  }

  disk {
    datastore_id = var.vm_storage
    interface    = "virtio0"
    import_from  = var.alma_image
    size         = 10 # tamano virtual de la imagen GenericCloud; sin esto el
    # provider aplica su default (8) e intenta ENCOGER el disco importado
  }

  initialization {
    datastore_id = var.vm_storage

    user_account {
      username = var.ci_user
      keys     = [var.ci_ssh_key]
    }

    ip_config {
      ipv4 {
        address = each.value.ip
        gateway = each.value.gw
      }
    }

    dns {
      domain  = "lab.interno"
      servers = [each.value.gw] # resolver: el forwarder del firewall (ADR 0003)
    }
  }

  network_device {
    bridge  = var.lab_bridge
    model   = "virtio"
    vlan_id = each.value.vlan
  }

  serial_device {} # consola serie: las imagenes cloud la esperan

  on_boot = true
  started = true
}

# ---------------------------------------------------------------------------
# Clientes LXC sin privilegios. Generadores de paquetes baratos (ADR 0007).
# ---------------------------------------------------------------------------
resource "proxmox_virtual_environment_container" "clients" {
  for_each = local.lxc_clients

  description = "Cliente de pruebas del segmento ${each.value.segment}"
  node_name   = var.node_name
  vm_id       = each.value.vmid
  pool_id     = var.pool_id
  tags        = ["netseg", lower(each.value.segment)]

  unprivileged = true

  operating_system {
    template_file_id = each.value.template
    type             = each.value.ostype
  }

  cpu {
    cores = 1
  }

  memory {
    dedicated = 256
    swap      = 0
  }

  disk {
    datastore_id = var.vm_storage
    size         = 4
  }

  network_interface {
    name    = "eth0"
    bridge  = each.value.vlan == 0 ? var.wan_bridge : var.lab_bridge
    vlan_id = each.value.vlan == 0 ? null : each.value.vlan
  }

  initialization {
    hostname = each.key

    ip_config {
      ipv4 {
        address = "dhcp"
      }
    }

    user_account {
      keys = [var.ci_ssh_key]
    }
  }

  started       = true
  start_on_boot = true
}

# ---------------------------------------------------------------------------
# Inventario para el harness de tests (tests/conftest.py lo lee con
# `terraform output -json machines`).
# ---------------------------------------------------------------------------
output "machines" {
  description = "Mapa nombre -> vmid/tipo/segmento/ip para el harness"
  value = merge(
    {
      fw01 = {
        vmid    = proxmox_virtual_environment_vm.fw01.vm_id
        type    = "qemu"
        segment = "FW"
        ip      = ""
      }
    },
    {
      for k, v in local.vms_alma : k => {
        vmid    = v.vmid
        type    = "qemu"
        segment = v.segment
        ip      = split("/", v.ip)[0]
      }
    },
    {
      for k, v in local.lxc_clients : k => {
        vmid    = v.vmid
        type    = "lxc"
        segment = v.segment
        ip      = ""
      }
    },
  )
}
