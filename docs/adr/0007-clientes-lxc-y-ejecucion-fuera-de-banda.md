# ADR 0007: Clientes en LXC y ejecución de tests fuera de banda

- Estado: aceptada
- Fecha: 2026-07-25

## Contexto

Los clientes de prueba (`pc01`, `iot01`, `guest01`, `ext01`) son generadores de paquetes:
no necesitan SELinux ni un kernel propio, pero sí arrancar en segundos para que el ciclo
`destroy`/`up` sea barato y se ejecute a menudo. Por otro lado, si el runner de tests
entrara por SSH a los invitados, dependería de la red que está bajo prueba: un bloqueo
correcto podría dejar al runner fuera, y un permiso de gestión abierto "para los tests"
falsearía la matriz.

## Decisión

Clientes como contenedores LXC (AlmaLinux 9 para `pc01`, Alpine para el resto) y
servidores como VMs (SELinux enforcing exige kernel propio: Proxmox confina LXC con
AppArmor, no SELinux). Los tests se ejecutan fuera de banda: el runner (ctrl01) habla
con el hipervisor por SSH y entra a los invitados con `qm guest exec` (VMs, requiere
`qemu-guest-agent` y `agent=1`) o `pct exec` (LXC), siempre a través del wrapper
`scripts/netseg-exec.sh`, que valida la VMID contra el pool `netseg`.

## Alternativas descartadas

- Clientes como VMs: coherencia total con los servidores a cambio de RAM y tiempo de
  arranque; en un hipervisor de 11 GB anidado no cabe, y un cliente no aporta nada como VM.
- Runner dentro de la red (SSH a los invitados): acopla el harness al diseño bajo
  prueba y obliga a abrir flujos de gestión que la matriz debería negar.
- API de Proxmox para exec en LXC: no existe endpoint de exec para LXC en la API;
  el camino uniforme es SSH al host + `qm`/`pct`.

## Consecuencias

- En LXC sin privilegios no hay sockets raw: las sondas usan conexión TCP normal
  (`nc -z`, sockets de Python), nunca escaneo SYN.
- Las VMs necesitan `agent=1` en Terraform y el agente activo; es un requisito de
  aceptación de la fase 1/2 del harness.
- El wrapper con sudoers en el host es la frontera de seguridad: la cuenta de
  automatización solo puede ejecutar dentro del pool `netseg` (ver spec §3).
