# network-segmentation-lab

En una red plana, un portátil comprometido en recepción tiene la misma ruta hacia la base
de datos que el administrador. Este laboratorio implementa segmentación con denegación por
defecto y verifica automáticamente que cada flujo permitido existe por una razón escrita.

Seis VLANs sobre Proxmox VE 9, firewall perimetral OPNsense y una matriz de flujos
declarativa (`opnsense/flows.yml`, solo con entradas `allow`). De ella se derivan las
reglas del firewall y una suite de tests que comprueba que lo que debe estar bloqueado lo
está. Las denegaciones esperadas se calculan por producto cartesiano en lugar de
enumerarse a mano.

Estado: bloque 1 completado (infraestructura + harness). La especificación
completa está en [docs/spec.md](docs/spec.md); las decisiones de diseño, en
[docs/adr/](docs/adr/).

## Fases

| # | Entregable | Estado |
|---|------------|--------|
| 1 | Terraform levanta 5 VMs, 4 LXC y los bridges VLAN | completada: ciclo `destroy`/`up` desde cero verificado |
| 2 | OPNsense (instalación manual) + harness de exec operativo | completada: OPNsense instalado; harness verificado en las 9 máquinas (fw01 incluido) |
| 3 | flows.yml → reglas importadas, `make check` sin deriva | flows.yml + esquema + validador listos; reglas pendientes |
| 4 | Ansible provisiona con SELinux enforcing (idempotente) | roles escritos, ansible-lint «production»; sin ejecutar |
| 5 | Suite completa y `docs/matriz-flujos.md` generado | derivación, sondas y reporte implementados con tests unitarios |
| 6 | WireGuard: la VPN llega a MGMT y solo a MGMT | pendiente |
| 7 | chaos, documentación completa, capturas | pendiente |

## Uso rápido (desde ctrl01)

```bash
make up         # terraform apply del lab completo
make test-unit  # tests que no necesitan lab (los mismos de la CI)
make test       # suite completa contra el lab
make report     # regenera docs/matriz-flujos.md
make check      # valida flows.yml (fase 3: + deriva contra reglas vivas)
make destroy    # tira el lab (solo recursos del pool netseg)
```

Activar el hook de secretos una vez por clon: `git config core.hooksPath hooks`.

## Cómo está montado

El hipervisor es un Proxmox VE 9 anidado en VMware Workstation (11 GB de RAM). El lab
vive en el pool `netseg` con un token de API de privilegios mínimos, y el nodo de control
es un LXC (`ctrl01`) fuera de ese pool. Ver [ADR 0008](docs/adr/0008-nodo-de-control-lxc-vs-wsl2.md).

Los tests se ejecutan fuera de banda: el runner habla con el hipervisor (`qm guest exec` /
`pct exec`) a través de un wrapper acotado al pool, sin entrar por SSH a la red bajo
prueba. Ver [ADR 0007](docs/adr/0007-clientes-lxc-y-ejecucion-fuera-de-banda.md).

Hay dos capas de filtrado, OPNsense en el perímetro y firewalld en cada host, y el
reporte atribuye cada bloqueo a su capa. Ver [ADR 0006](docs/adr/0006-firewalld-y-politica-combinada.md).

## Lo que no funcionó a la primera

En [docs/incidencias.md](docs/incidencias.md) está qué se rompió, cómo se diagnosticó y
cómo se arregló, desde un `vmbr1` ocupado por otro laboratorio hasta la ausencia de
`sudo` en la instalación mínima de PVE.

## Limitaciones

Es un laboratorio: no hay alta disponibilidad del firewall ni IDS/SIEM (eso es la fase 2,
un proyecto aparte), las credenciales son de laboratorio y la prueba de VPN desde una red
externa real depende de que el ISP no imponga CGNAT.
