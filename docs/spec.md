# network-segmentation-lab: laboratorio de segmentación de red y firewall perimetral

Especificación del proyecto, versión 2 (sustituye por completo a la primera). Este
documento define el alcance, y lo que no aparece aquí no forma parte del proyecto.

## 1. Tesis del proyecto

El tráfico entre segmentos solo pasa si el diseño lo permite de forma explícita, y eso
se verifica automáticamente con una suite de tests que genera la matriz de flujos
publicada en el README.

Todo lo que no sirva a esa tesis queda fuera. La detección de intrusiones, el SIEM y la
simulación de ataques son la fase 2, que es un proyecto distinto (ver §12).

## 2. Objetivos

1. Levantar desde cero, de forma reproducible, una red segmentada en 6 VLANs con
   firewall perimetral, DNS interno, DHCP por segmento y VPN de acceso remoto.
2. Declarar los flujos permitidos en un único fichero, derivar de él las reglas del
   firewall y los tests, y demostrar que lo que debe estar bloqueado lo está.
3. Documentar cada decisión de diseño junto con la configuración final.

Quedan fuera de los objetivos la alta disponibilidad del firewall, el routing dinámico,
IDS/SIEM y un proxy con inspección TLS. Se mencionan como trabajo futuro y nada más.

## 3. Plataforma y requisitos

- Hipervisor: Proxmox VE 9 (ya disponible). Bridge VLAN-aware `vmbr1` para la red
  interna del lab; `vmbr0` como WAN del firewall.
- SO de servidores: AlmaLinux 9, con SELinux en modo enforcing y firewalld activo.
- SO de clientes de prueba: AlmaLinux 9 y Alpine, en contenedores LXC.
- Herramientas: Terraform (provider `bpg/proxmox`), Ansible, Python 3.11+, Make.
- Firewall: OPNsense 25.x, instalado sobre UFS y no ZFS, porque el ARC de ZFS es
  agresivo con la memoria y en un hipervisor anidado no compensa.

### Restricción del entorno: Proxmox anidado

El Proxmox corre como VM dentro de VMware Workstation, con *Virtualize Intel VT-x/EPT*
activo. Está comprobado que 6 núcleos exponen VT-x y que `/dev/kvm` existe.

Por eso en ese PC no se puede activar Hyper-V, lo que descarta WSL2 como nodo de control:
Hyper-V le quitaría a VMware la virtualización anidada y el Proxmox se quedaría sin
`/dev/kvm`, incapaz de crear una sola VM. Ver ADR 0008.

El nodo de control es un contenedor LXC Ubuntu, `ctrl01`, en el propio Proxmox, con 2 GB
de RAM, red en `vmbr0` y fuera del pool `netseg`, para que la automatización nunca pueda
tocar la máquina desde la que se ejecuta. El repo y el toolchain (Terraform, Ansible,
ansible-lint, yamllint, ruff, make, git, Python 3.11+, Node) viven ahí. Las credenciales
van en variables de entorno, nunca en el repo.

### Acceso al hipervisor

Se crea en Proxmox un pool dedicado, `netseg`, y un token de API con rol limitado a ese
pool. Nunca `root@pam` con permisos globales: en ese mismo Proxmox hay otros proyectos
que no deben quedar dentro del radio de acción de la automatización.

(El pool se llama `netseg` y no como el repo porque los identificadores de pool
aparecen en rutas de permisos de la API y conviene que sean cortos.)

Para la ejecución de comandos en invitados (§8) hace falta acceso a `qm` y `pct`, y
ambos exigen root: Proxmox no ofrece forma nativa de delegarlos a un usuario normal
acotados por VMID. La solución es `scripts/netseg-exec.sh`, un wrapper que recibe VMID y
comando, valida contra `pvesh get /pools/netseg` que esa VMID pertenece al pool y
despacha a `qm guest exec` o `pct exec` según el tipo. Una línea de `sudoers` autoriza a
la cuenta dedicada únicamente ese wrapper.

Es la misma idea del proyecto, autorización explícita y acotada, aplicada al propio
tooling, y evita que un error de alcance de la automatización afecte a los otros labs
que viven en ese mismo hipervisor.

## 4. Topología

```
                    Internet
                       │
                  [ vmbr0 ] WAN (DHCP) ── ext01 (cliente externo)
                       │
              ┌────────────────┐
              │    OPNsense    │  fw01
              │  router + fw   │
              └────────┬───────┘
                       │ trunk 802.1Q
                  [ vmbr1 ] VLAN-aware
     ┌──────┬──────────┼──────────┬──────────┬─────────┐
   VLAN10  VLAN20    VLAN30     VLAN40    VLAN50    VLAN99
   USERS   SERVERS    DMZ        IOT       MGMT     GUEST
```

### Plan de direccionamiento

| VLAN | Nombre  | Red             | Gateway      | DHCP        | Propósito                     |
|------|---------|-----------------|--------------|-------------|-------------------------------|
| 10   | USERS   | 10.10.10.0/24   | 10.10.10.1   | .100–.200   | Puestos de trabajo            |
| 20   | SERVERS | 10.10.20.0/24   | 10.10.20.1   | estático    | Servicios internos            |
| 30   | DMZ     | 10.10.30.0/24   | 10.10.30.1   | estático    | Expuesto a Internet           |
| 40   | IOT     | 10.10.40.0/24   | 10.10.40.1   | .50–.150    | Dispositivos no confiables    |
| 50   | MGMT    | 10.10.50.0/24   | 10.10.50.1   | estático    | Administración, jumphost      |
| 99   | GUEST   | 10.10.99.0/24   | 10.10.99.1   | .10–.250    | Invitados, solo Internet      |
| —    | VPN     | 10.10.60.0/24   | —            | WireGuard   | Acceso remoto (road warrior)  |

### Máquinas

| Host      | Tipo | VLAN | IP           | SO           | RAM    | Rol                                    |
|-----------|------|------|--------------|--------------|--------|----------------------------------------|
| `fw01`    | VM   | —    | trunk        | OPNsense 25  | 2 GB   | Firewall, router, DHCP, DNS, WireGuard |
| `srv-dns` | VM   | 20   | 10.10.20.10  | AlmaLinux 9  | 512 MB | BIND9 autoritativo de `lab.interno`    |
| `srv-app` | VM   | 20   | 10.10.20.20  | AlmaLinux 9  | 1 GB   | PostgreSQL + Samba (recurso compartido)|
| `dmz-web` | VM   | 30   | 10.10.30.10  | AlmaLinux 9  | 512 MB | Nginx, publicado por NAT               |
| `jump01`  | VM   | 50   | 10.10.50.10  | AlmaLinux 9  | 512 MB | Bastión SSH, único acceso a SERVERS    |
| `pc01`    | LXC  | 10   | DHCP         | AlmaLinux 9  | 256 MB | Cliente de pruebas                     |
| `iot01`   | LXC  | 40   | DHCP         | Alpine       | 256 MB | Dispositivo simulado no confiable      |
| `guest01` | LXC  | 99   | DHCP         | Alpine       | 256 MB | Cliente invitado                       |
| `ext01`   | LXC  | —    | vmbr0 DHCP   | Alpine       | 256 MB | Cliente **fuera** del perímetro        |

Los servidores son VMs porque SELinux enforcing es uno de los objetivos del lab, y un
contenedor LXC comparte el kernel del host Proxmox. Proxmox VE 9 corre sobre Debian 13 y
confina los contenedores con AppArmor: el LSM activo del host no es SELinux, así que
dentro del contenedor no hay política que aplicar. (El kernel de Debian sí trae SELinux
compilado; simplemente no es el módulo en uso.) Los hosts que deben correr política
SELinux tienen que ser VMs completas.
Los clientes van en LXC porque son solo generadores de paquetes: no necesitan SELinux,
arrancan en segundos y abaratan el ciclo `destroy` / `up`, que es lo que hace que se
ejecute a menudo.

`ext01` es obligatorio. Sin un cliente fuera del perímetro no se puede verificar de
forma automática ni el port forward 80/443 hacia `dmz-web` ni el criterio de la fase 6
(la VPN llega a MGMT y solo a MGMT). Va en `vmbr0` y no depende de la IP pública del ISP,
lo que además hace la suite determinista.

`jump01` se dimensiona a 512 MB en fase 1 y se sube a 4 GB cuando llegue Wazuh (§12).

### Presupuesto de memoria

El hipervisor tiene 11 GB. Las cifras asignadas son techos, no reservas: en LXC el
límite no se reserva y un Alpine ocioso consume 10-15 MB reales, no 256 MB.

| | Asignado | Real en reposo |
|---|---|---|
| Proxmox | — | ~1 GB |
| `ctrl01` | 2 GB | 2 GB |
| `fw01` | 2 GB | 2 GB |
| 4 VMs AlmaLinux | 2 GB | ~1,5 GB |
| 4 LXC clientes | 1 GB | ~0,2 GB |
| **Total** | **7 GB** | **~6,7 GB** |

- Ballooning activado en las VMs AlmaLinux (mínimo 512, máximo 1024): reclaman lo que
  necesitan y devuelven lo que no usan. Las imágenes cloud ya traen `virtio_balloon`.
- Ballooning desactivado en `fw01`: el soporte en FreeBSD es flojo y no merece la pena
  arriesgarlo en el firewall.
- KSM activo (por defecto en Proxmox): con cuatro AlmaLinux idénticas deduplica bastante.
- Los labs de VirtualBox del resto del portfolio no pueden estar encendidos a la vez.

En la fase 2, con Wazuh, `jump01` sube a 4 GB y se añade `siem01`: son 6-7 GB más de los
que hay, así que habrá que ampliar RAM o mover el lab a otro hipervisor.

## 5. Matriz de flujos

Política por defecto en todas las interfaces: `deny all`, con logging.

| Origen ↓ / Destino → | USERS | SERVERS | DMZ | IOT | MGMT | GUEST | WAN |
|----------------------|-------|---------|-----|-----|------|-------|-----|
| **USERS**            | —     | ①       | 443 | ✗   | ✗    | ✗     | ✓   |
| **SERVERS**          | ✗     | —       | ✗   | ✗   | ✗    | ✗     | ②   |
| **DMZ**              | ✗     | ③       | —   | ✗   | ✗    | ✗     | ②   |
| **IOT**              | ✗     | ✗       | ✗   | —   | ✗    | ✗     | ④   |
| **MGMT**             | ✓     | ✓       | ✓   | ✓   | —    | ✓     | ✓   |
| **GUEST**            | ✗     | ✗       | ✗   | ✗   | ✗    | —     | ⑤   |
| **VPN**              | ✗     | ✗       | ✗   | ✗   | ✓    | ✗     | ✗   |

- ① Solo `srv-app:445/tcp` (recurso compartido) y `srv-dns:53`. Reglas host a host,
  nunca a la subred entera. Sin 3306, porque `srv-app` corre PostgreSQL y no MySQL.
- ② Solo 80/443 saliente y NTP.
- ③ Solo `dmz-web` → `srv-app:5432`. Regla host a host.
- ④ Solo 443 saliente y NTP. Sin DNS externo: obligado a usar el resolver interno.
- ⑤ Solo Internet, con aislamiento entre clientes del propio segmento.

Normas para escribir las reglas:
- Cada regla lleva una descripción que explica el porqué, no el qué.
  Mal: `permitir 445 de USERS a SERVERS`. Bien: `acceso a recursos compartidos del departamento`.
- Nada de `any→any` en ninguna dirección, tampoco en MGMT (MGMT es amplio, pero se
  define por servicios: 22, 443, 3389, 161).
- Toda denegación se registra, y todo permiso hacia SERVERS también.

## 6. `flows.yml`: solo se declara lo permitido

Es el cambio principal respecto a la versión 1 del spec: `flows.yml` declara únicamente
los `allow`. Las denegaciones no se escriben a mano; se derivan por producto cartesiano
(origen × host destino × puertos de sondeo) restando lo permitido.

Enumerar los `deny` a mano solo verifica los bloqueos que a uno se le ocurrió escribir.
Derivarlos verifica todos los que no están permitidos explícitamente, así que la matriz
es exhaustiva por construcción, y eso es el titular del README.

```yaml
segments:
  USERS:   { vlan: 10, cidr: 10.10.10.0/24 }
  SERVERS: { vlan: 20, cidr: 10.10.20.0/24 }
  DMZ:     { vlan: 30, cidr: 10.10.30.0/24 }
  IOT:     { vlan: 40, cidr: 10.10.40.0/24 }
  MGMT:    { vlan: 50, cidr: 10.10.50.0/24 }
  GUEST:   { vlan: 99, cidr: 10.10.99.0/24 }
  VPN:     { cidr: 10.10.60.0/24 }

hosts:
  srv-dns: { segment: SERVERS, ip: 10.10.20.10 }
  srv-app: { segment: SERVERS, ip: 10.10.20.20 }
  dmz-web: { segment: DMZ,     ip: 10.10.30.10 }
  jump01:  { segment: MGMT,    ip: 10.10.50.10 }

# Puertos con los que se sondea cada par origen/destino.
probes:
  ports: [22/tcp, 53/udp, 80/tcp, 443/tcp, 445/tcp, 3389/tcp, 5432/tcp, 161/udp]
  icmp: true

# Solo allow. Todo lo demás se espera bloqueado.
allow:
  - id: users-to-fileshare
    src: USERS
    dst: srv-app
    ports: [445/tcp]
    reason: "Acceso a recursos compartidos del departamento"

  - id: users-to-internal-dns
    src: USERS
    dst: srv-dns
    ports: [53/udp, 53/tcp]
    reason: "Resolución de nombres internos sin salir a DNS público"

  - id: dmz-to-database
    src: dmz-web
    dst: srv-app
    ports: [5432/tcp]
    reason: "La web publicada consulta su base de datos, host a host"
```

Si las reglas del firewall y `flows.yml` divergen, es un bug: `make check` compara las
reglas vivas contra el fichero y reporta la deriva.

### Coste de los tests derivados

Salen del orden de 300 comprobaciones. Como la política es `drop` silencioso, cada
denegación cuesta un timeout entero. Por tanto:

- Timeout de 1 s (es una LAN, sobra).
- Cada origen ejecuta su batería completa en un solo comando: son ~9 invocaciones,
  no 300.
- Paralelizar por host origen.
- Usar conexión TCP normal (`nc -z`, `nmap -sT`, o sockets de Python), nunca escaneo
  SYN: los LXC sin privilegios no tienen sockets raw y `-sS` falla.

## 7. Servicios a configurar

En `fw01`:
- Interfaces VLAN, NAT de salida, port forward 80/443 → `dmz-web`.
- DHCP por segmento, con reservas para los hosts fijos.
- Unbound como forwarder: los clientes solo resuelven contra el firewall o contra
  `srv-dns`; el DNS externo directo está bloqueado.
- WireGuard road-warrior, con la red VPN llegando solo a MGMT.
- Exportación de flujos (Netflow) hacia `jump01`, como enganche para la fase 2.
- Sincronización horaria: el firewall es el servidor NTP del lab.

`srv-dns` lleva BIND9 autoritativo de `lab.interno`, con zona inversa.
Paquete `bind`, servicio `named`, config en `/etc/named.conf`, zonas en `/var/named/`.

`srv-app` lleva PostgreSQL (requiere `postgresql-setup --initdb`, que Debian hacía solo)
y un recurso compartido Samba que da sentido al flujo ①.

`dmz-web` lleva Nginx desde AppStream.

`jump01` es el único host con SSH abierto desde USERS y VPN: acceso con claves y sin
contraseñas, `sshd` con `AllowGroups` y registro de sesiones.

Todos los AlmaLinux llevan SELinux enforcing y firewalld activo con los servicios mínimos.
Esto implica trabajo real de política: `semanage port -a` para servicios en puertos no
estándar, `setsebool` donde haga falta, `restorecon` de contextos, y `ausearch` +
`audit2allow` para diagnosticar denegaciones. No se desactiva SELinux para que algo
funcione. Cada denegación diagnosticada es material para la sección «Lo que no funcionó
a la primera».

### Doble capa de filtrado

Con firewalld en los hosts hay dos capas. La matriz verifica la política efectiva, la que
resulta de combinar las dos, y el reporte de tests debe consultar el log de denegación de
OPNsense para que cada fallo sea atribuible a una capa concreta (perímetro o host),
porque sin eso no se puede diagnosticar. Ver ADR 0006.

## 8. Ejecución de comandos en los invitados

Los tests se ejecutan fuera de banda: el runner habla con el hipervisor, no con la red
que está probando. Si el runner entrase por SSH, se acoplaría al diseño bajo prueba.

`conftest.py` implementa un wrapper que elige el mecanismo según el tipo de máquina:

- VMs: SSH al host Proxmox + `qm guest exec <vmid> -- <cmd>`.
  Requiere `qemu-guest-agent` instalado y activo en el invitado, y `agent=1` en la
  configuración de la VM (se define en Terraform).
- LXC: SSH al host Proxmox + `pct exec <vmid> -- <cmd>`.
  No necesita agente.

No existe endpoint de exec para LXC en la API de Proxmox: por eso el camino es SSH al host.

## 9. Estructura del repositorio

```
network-segmentation-lab/
├── README.md                  # portada del proyecto (§11)
├── CONVENTIONS.md             # convenciones permanentes del proyecto
├── Makefile                   # up, provision, test, report, check, chaos, destroy
├── docs/
│   ├── spec.md                # este documento
│   ├── arquitectura.md
│   ├── matriz-flujos.md       # GENERADO por los tests, no editar a mano
│   ├── runbook.md             # arrancar, parar, recuperar, rotar credenciales
│   └── adr/
│       ├── 0001-opnsense-vs-vyos.md
│       ├── 0002-deny-por-defecto.md
│       ├── 0003-dns-interno-obligatorio.md
│       ├── 0004-bastion-unico-acceso-servers.md
│       ├── 0005-almalinux-vs-debian.md
│       ├── 0006-firewalld-y-politica-combinada.md
│       ├── 0007-clientes-lxc-y-ejecucion-fuera-de-banda.md
│       └── 0008-nodo-de-control-lxc-vs-wsl2.md
├── terraform/
│   ├── main.tf                # VMs, LXC y bridges en Proxmox
│   ├── variables.tf
│   └── terraform.tfvars.example
├── ansible/
│   ├── inventory/lab.yml      # preparado para agentes de la fase 2
│   ├── group_vars/
│   ├── roles/
│   │   ├── common/            # NTP, resolver, hardening base, usuarios, SELinux, firewalld
│   │   ├── named/
│   │   ├── nginx_dmz/
│   │   ├── postgres/
│   │   ├── samba/
│   │   └── bastion/
│   └── site.yml
├── opnsense/
│   ├── config.xml.j2          # plantilla derivada de un export REAL
│   ├── flows.yml              # ← FUENTE DE VERDAD de la matriz
│   └── apply.py               # renderiza e importa vía API
├── scripts/
│   ├── sanitize.py            # limpia secretos del config.xml antes de commitear
│   └── netseg-exec.sh         # wrapper qm/pct acotado al pool, se instala en el host
└── tests/
    ├── conftest.py            # wrapper de exec dual (qm/pct) + fixtures
    ├── test_connectivity.py   # matriz derivada de flows.yml
    ├── test_services.py       # DNS, DHCP, NAT, VPN funcionan
    └── report.py              # genera docs/matriz-flujos.md
```

### `config.xml` de OPNsense: partir de un export real

El esquema de OPNsense no se plantilla de memoria. El procedimiento es:

1. Instalar OPNsense a mano (su instalador es de consola, no automatizable).
2. Crear una sola regla por GUI.
3. Exportar el `config.xml` real.
4. Usar ese export como base de la plantilla.

Si el plugin `os-firewall` está disponible, preferir su API para gestionar solo el
ruleset de automatización, en vez de importar el fichero completo: un import mal formado
deja el firewall inaccesible.

## 10. Fases de implementación

Cada fase termina en commit y en un lab que funciona. No se empieza una sin cerrar la anterior.

| # | Entregable | Criterio de aceptación |
|---|-----------|------------------------|
| 1 | Terraform levanta las 5 VMs, los 4 LXC y los bridges VLAN | `terraform apply` desde cero deja todo arrancado, `ext01` incluido |
| 2 | OPNsense con interfaces, NAT y DHCP + harness de tests operativo | `pc01` obtiene IP y sale a Internet, y el wrapper `qm`/`pct` ejecuta comandos en las 9 máquinas |
| 3 | `flows.yml` + generación e importación de reglas | Las reglas del firewall se corresponden 1:1 con `flows.yml`; `make check` no reporta deriva |
| 4 | Ansible provisiona los servicios con SELinux enforcing | `ansible-playbook site.yml` es idempotente: segunda pasada, 0 changed. Ningún host en permissive |
| 5 | Suite completa de conectividad | Tests pasan y generan `docs/matriz-flujos.md` con los bloqueos derivados |
| 6 | WireGuard + acceso remoto | Desde `ext01`, la VPN conecta y alcanza MGMT y solo MGMT, verificado por test |
| 7 | `make chaos`, documentación, diagrama, ADRs, capturas | Un tercero levanta el lab siguiendo solo el README |

El harness va en la fase 2 y no en la 5: con `conftest.py` y el wrapper de exec
funcionando antes de escribir la primera regla, la fase 3 se puede hacer test-first. Si
los tests llegan después, se ajustan a lo que hay en vez de a lo que debería haber.

Estimación: 5-6 semanas a ritmo de tardes. La fase 3 sola se come una entera.

### `make chaos`

Inserta una regla `any→any` y ejecuta la suite, que debe ponerse en rojo. Así se
demuestra que los tests detectan una política rota. La captura de la suite fallando por
ese cambio es la que vende el proyecto.

## 11. Documentación (esto es lo que lee el reclutador)

El README abre con el problema y no con la lista de tecnologías:

> Una red plana significa que un portátil comprometido en recepción tiene la misma
> ruta hacia la base de datos que el administrador. Este laboratorio implementa
> segmentación con denegación por defecto y verifica automáticamente que cada
> flujo permitido existe por una razón escrita.

Debe incluir:
- Diagrama de topología (Mermaid o draw.io exportado a SVG).
- La matriz de flujos renderizada, con la nota de que está generada por los tests.
- Salida real de la suite: X permitidos verificados, Y bloqueos verificados.
- Capturas: dashboard de OPNsense, log de una denegación, cliente VPN conectado,
  salida de los tests en verde, y `make chaos` en rojo.
- La sección «Lo que no funcionó a la primera», con qué se rompió, cómo se
  diagnosticó y cómo se arregló. Es la parte que más se lee, y las denegaciones de
  SELinux dan material de sobra.
- Limitaciones: es un lab, sin HA, sin IDS todavía y con credenciales de laboratorio.
  Si el ISP da CGNAT y no se puede probar la VPN desde una red externa real, se escribe
  aquí en vez de montar un apaño.

Los ADR son de medio folio: contexto, decisión, alternativas descartadas y
consecuencias. Son la señal más clara de que hay criterio detrás, y lo que un
entrevistador usará para preguntar.

## 12. Seguridad del propio repositorio

- Nada de secretos en git. `terraform.tfvars` y `group_vars/vault.yml` fuera, con
  `.example` versionado.
- Secretos de Ansible con `ansible-vault` o SOPS.
- El `config.xml` exportado se sanea antes de commitear (hashes de contraseñas,
  claves de WireGuard, claves de API). `scripts/sanitize.py` llamado desde un pre-commit hook.
- CI en GitHub Actions: `terraform validate`, `ansible-lint`, `yamllint`, `ruff` y
  validación de esquema de `flows.yml`. La CI no toca el lab, solo valida el código.

## 13. Enganches para la fase 2 (Wazuh)

Nada de esto se implementa ahora, pero el diseño lo deja preparado:

- El inventario de Ansible ya agrupa los hosts de forma que añadir un rol `wazuh_agent`
  sea una línea.
- `jump01` sube a 4 GB de RAM y disco de sobra: será el colector.
- Netflow del firewall ya sale hacia `jump01`.
- La VLAN MGMT tiene sitio reservado para `siem01` en 10.10.50.20.
- Los logs de denegación del firewall se emiten ya en formato syslog remoto.

## 14. Cómo saber que está terminado

- [ ] `make destroy && make up && make provision && make test` funciona de cero.
- [ ] `docs/matriz-flujos.md` se genera solo y coincide con `flows.yml`.
- [ ] Segunda pasada de Ansible: 0 changed.
- [ ] Todos los AlmaLinux en SELinux enforcing. Ninguno en permissive.
- [ ] Un cliente en GUEST no alcanza nada interno, verificado por test.
- [ ] La VPN llega a MGMT y a nada más, verificado desde `ext01`.
- [ ] `make chaos` pone la suite en rojo.
- [ ] `make check` no reporta deriva entre reglas vivas y `flows.yml`.
- [ ] El README lo entiende alguien que no conoce el proyecto, en cinco minutos.
- [ ] No hay ni un secreto en el historial de git.
