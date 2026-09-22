# Runbook: arrancar, parar, recuperar y rotar credenciales

## Dónde se opera

Todo se hace desde ctrl01 (`~/network-segmentation-lab`), nunca desde Windows ni como
root@pam contra la API. Se entra siempre con `ProxyJump pve01`: el acceso directo desde
el anfitrión es poco fiable por la red anidada de VMware, y la IP de ctrl01 es DHCP y
cambia. Para descubrir la IP actual:

```bash
ssh root@<IP-DEL-HIPERVISOR> 'pct exec 900 -- ip -4 -o addr show eth0'
```

Las credenciales están en `~/.netseg.env` (600) en ctrl01, generado por
`scripts/bootstrap-pve.sh`, y nunca en el repo.

## Arrancar / parar el lab

```bash
make up        # terraform init + apply (lee ~/.netseg.env)
make destroy   # destruye SOLO los recursos del pool netseg
```

`fw01` arranca con el ISO de OPNsense enganchado; hasta que se instale a mano (fase 2)
se queda en el instalador. Los LXC de cliente en VLANs internas no tendrán IP hasta que
el firewall dé DHCP: es lo esperado, y `pct exec` funciona igualmente (fuera de banda).

## Instalar OPNsense y reconectar la WAN (fase 2, manual)

**La WAN de fw01 (`net1`, vmbr0) está `disconnected` y así tiene que seguir hasta el
paso 4.** El live de fábrica de OPNsense pone LAN=<PUERTA-DE-ENLACE-DOMESTICA>+DHCP en la
primera NIC y, conectado a la red de casa, secuestra el gateway real (incidencia #7). El
orden seguro es:

0. El instalador del DVD exige ≥3 GB de RAM: subir temporalmente `memory` de fw01
   a 4096 en `main.tf` (apply dirigido) y revertir a 2048 al terminar (incidencia #11).
1. Consola de fw01 (GUI de Proxmox o `qm terminal 600` como root). Instalar OPNsense
   en disco (usuario `installer`/`opnsense`) sobre UFS, no ZFS (spec §3). En este lab
   se dejaron las credenciales de fábrica, `root`/`opnsense`, por ser un laboratorio
   (está entre las limitaciones del README); cámbialas si el lab deja de ser desechable.
2. En la primera configuración de consola, asignar interfaces: WAN = vtnet1 (DHCP) y
   LAN = vtnet0 (el trunk; las VLANs 10-99 se crean encima en fase 3), y cambiar la IP
   de LAN a una del plan (p. ej. 10.10.50.1/24 en la VLAN 50).
3. Verificar desde consola que la LAN ya no es <PUERTA-DE-ENLACE-DOMESTICA> y que el DHCP
   de fábrica está reasignado o apagado.
4. Solo entonces, reconectar la WAN con Terraform, nunca a mano: en
   `terraform/main.tf`, poner `disconnected = false` en la `net1` de fw01 y
   `terraform apply`. Vigilar el ping a Internet del PC físico durante el cambio.
5. La LAN de fw01 debe salir de `<RED-DOMESTICA>/24` (colisiona con la WAN doméstica):
   opción 2 del menú → LAN → IP `10.10.0.1/24`, sin DHCP. Con las dos interfaces en
   la misma /24, la GUI es inalcanzable aunque `pf` esté parado (incidencia #13).
6. Agente para el harness: `pkg install -y os-qemu-guest-agent`,
   `sysrc qemu_guest_agent_enable=YES`, `service qemu-guest-agent start`. En
   Terraform, fw01 lleva `agent = true` y `keyboard_layout = "es"`.

### Pilotar la consola de fw01 sin ratón

`scripts/console-type.sh <VMID> "texto" [--ret]` teclea en la consola vía la API de
Proxmox (útil cuando la GUI aún no es accesible). Traduce el keymap español de la
consola. Para "ver" la pantalla: `echo "screendump /tmp/x.ppm" | qm monitor 600` y
convertir con `scripts` auxiliares. La cuenta `netseg` no puede hacer esto (no tiene
`sendkey`): es una operación de root de rescate, documentada aquí como tal.

## Ejecutar comandos dentro de los invitados

```bash
ssh netseg@<IP-DEL-HIPERVISOR> sudo -n /usr/local/sbin/netseg-exec.sh <VMID> -- <comando>
```

El wrapper rechaza (rc=3) cualquier VMID fuera del pool `netseg`. Para VMs usa
`qm guest exec` (JSON; requiere qemu-guest-agent activo), para LXC `pct exec` (salida
cruda).

**En las VMs, prefija siempre `/usr/local/sbin/netseg-agent-runner`** (el harness lo
hace solo): sin él, los hijos de guest-exec quedan confinados por SELinux en
`virt_qemu_ga_t`, sin red y sin ver binarios como `hostname` (incidencia #9).

## Recuperación del hipervisor

Hay un snapshot de VMware Workstation llamado `pre-bootstrap` de la VM
`infra-lab-pve01`, tomado antes de cualquier cambio de red del bootstrap. Restaurarlo
revierte todo pve01, incluido lo que hayan hecho otros proyectos después:

```powershell
& "C:\Program Files\VMware\VMware Workstation\vmrun.exe" -T ws revertToSnapshot `
  "C:\Users\Yosef\Documents\Virtual Machines\infra-lab-pve01\infra-lab-pve01.vmx" pre-bootstrap
```

Úsalo como último recurso; para deshacer el lab basta `make destroy` (y, si hiciera
falta, borrar pool/rol/usuario con `pveum` como está en `scripts/bootstrap-pve.sh`).

## Rotar el token de API

```bash
# en pve01, como root:
pveum user token remove terraform@pve netseg
rm -f /root/.netseg.env
/root/netseg-bootstrap/bootstrap-pve.sh   # regenera token + env y lo re-push a ctrl01
```

El secreto no aparece por pantalla en ningún paso: va directo al fichero (600).

## Verificaciones rápidas de salud

```bash
ssh root@<IP-DEL-HIPERVISOR> 'ip -br addr show vmbr0; ip -br link show vmbr2'   # red del host
ssh root@<IP-DEL-HIPERVISOR> 'pvesh get /pools/netseg'                          # miembros del pool
make test-unit                                                           # harness sin lab
```
