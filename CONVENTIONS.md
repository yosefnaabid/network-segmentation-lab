# Convenciones de network-segmentation-lab

## Qué es este repo

Laboratorio de segmentación de red con firewall perimetral OPNsense sobre Proxmox VE 9.
Entre segmentos solo pasa el tráfico que el diseño permite de forma explícita, y una
suite de tests lo verifica automáticamente.

`docs/spec.md` define el alcance del proyecto: lo que no está ahí queda fuera.

## Reglas

- `opnsense/flows.yml` es la única fuente de verdad de la matriz de flujos. Las reglas del
  firewall y los tests se derivan de ahí, y las reglas no se editan nunca a mano.
- `flows.yml` declara solo los `allow`. Las denegaciones se derivan por producto
  cartesiano restando lo permitido, así que el fichero no lleva entradas `deny`.
- Todo cambio de infraestructura pasa por Terraform o Ansible. Nada por SSH manual.
- Ansible tiene que ser idempotente, y eso se comprueba ejecutando el playbook dos veces:
  la segunda pasada debe terminar con `changed=0`.
- En Ansible se evitan `command` y `shell`. Si no hay más remedio, se usan con
  `creates`/`changed_when` para que sean idempotentes y se explica por qué no había módulo.
- SELinux se queda en enforcing. No se pasa a permissive ni se desactiva para que algo
  funcione: si un servicio no arranca, se diagnostica con `ausearch` y `audit2allow` y se
  resuelve con `semanage`, `setsebool` o `restorecon`.
- Los secretos no se escriben nunca en ficheros versionados.
- Los commits van en español, en imperativo, uno por unidad lógica de trabajo. El mensaje
  dice qué cambia y por qué, nunca solo el área tocada. No se repite el mismo mensaje en
  commits distintos: si dos commits merecen el mismo texto, o son el mismo commit o el
  texto no dice nada.
- La documentación y los comentarios van en español.

## Forma de trabajar

- Cuando algo falla, se diagnostica y se anota en `docs/incidencias.md`. Ese diagnóstico
  escrito es el material de la sección «Lo que no funcionó a la primera» del README.
- Las decisiones de diseño que el spec no resuelve se anotan en `docs/arquitectura.md`
  con su porqué.
- Los tests se escriben antes que las reglas del firewall.
- Nada se da por cumplido sin la salida del comando que lo demuestra.

## Entorno

- Proxmox VE 9, con un pool dedicado `netseg` y un token de API con rol limitado a ese
  pool. Nunca se usa `root@pam` ni se tocan recursos fuera del pool, porque en ese
  hipervisor hay otros proyectos.
- Las credenciales van en variables de entorno (`~/.netseg.env`), nunca en el repo.
- El nodo de control es el contenedor LXC Ubuntu `ctrl01`, dentro del propio Proxmox, en
  `vmbr0` y fuera del pool `netseg`. El repo vive en `~/network-segmentation-lab`.
  WSL2 no se puede usar: el Proxmox corre anidado en VMware Workstation y activar
  Hyper-V le quitaría la virtualización anidada, dejándolo sin `/dev/kvm`. Ver ADR 0008.
- Para `qm`/`pct` se usa `scripts/netseg-exec.sh`, el wrapper que valida la VMID contra
  el pool. Nunca `qm` o `pct` directos con root.
- Servidores: AlmaLinux 9 en VMs (SELinux enforcing + firewalld).
- Clientes de prueba: LXC (AlmaLinux 9 y Alpine). No corren SELinux: comparten el kernel
  del host Proxmox, que confina contenedores con AppArmor. El LSM activo del host no es
  SELinux, así que dentro del contenedor no hay política que aplicar.
- Firewall: OPNsense 25.x en VM.

## Detalles fáciles de olvidar

- AlmaLinux, no Debian: paquete `bind` y servicio `named`, no `bind9`. Config en
  `/etc/named.conf`, zonas en `/var/named/`. PostgreSQL necesita `postgresql-setup --initdb`.
  Gestor de paquetes `dnf`. Firewalld, no iptables suelto.
- La ejecución en los invitados va fuera de banda: SSH al host Proxmox y luego
  `qm guest exec` para VMs (requiere `qemu-guest-agent` y `agent=1`) o `pct exec` para
  LXC. No hay endpoint de exec para LXC en la API de Proxmox. Los tests nunca entran por
  SSH a los invitados, porque eso acoplaría el runner a la red que está bajo prueba.
- En los LXC sin privilegios no hay sockets raw, así que los escaneos SYN (`nmap -sS`)
  fallan. Se usa conexión TCP normal (`nc -z`, `nmap -sT`, sockets de Python).
- Timeout de 1 s en las sondas y batería agrupada por host origen: son ~9 invocaciones,
  no 300. Se paraleliza por origen.
- El esquema del `config.xml` de OPNsense no se escribe de memoria: se parte siempre de un
  export real del firewall. Si el plugin `os-firewall` está disponible, es mejor usar su
  API que importar el fichero completo.
- La versión del provider `bpg/proxmox` va fijada en `required_providers`, y conviene
  consultar su documentación actual en vez de fiarse de la memoria: los nombres de
  recursos y atributos cambian entre versiones.
- Hay dos capas de filtrado (OPNsense y firewalld en cada host). Cuando un test falla, el
  reporte debe indicar cuál de las dos bloqueó, consultando el log de OPNsense.

## Partes manuales

Esto se hace a mano y no se automatiza:

- El instalador de OPNsense (es de consola, sin cloud-init).
- Las descargas del portal de Broadcom (requieren login).
- La captura de la VPN desde una red externa real.
