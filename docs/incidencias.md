# Incidencias: lo que no funcionó a la primera

Registro cronológico. Cada entrada cuenta qué falló, cómo se diagnosticó y cómo se
resolvió, y de aquí sale la sección «Lo que no funcionó a la primera» del README.

## 1. vmbr1 ya existía y pertenece a otro laboratorio

El spec (§3) pide crear `vmbr1` VLAN-aware para la red interna, pero en pve01 ya existía
un `vmbr1` con IP `10.10.10.1/24` en el host, NAT de salida (`MASQUERADE`) y el
comentario «gestionado por Ansible». Además, las 4 VMs preexistentes (dns01, web01, fs01,
debian13-cloudinit) cuelgan de él sin tag.

Lo vi con `cat /etc/network/interfaces` y `qm config 111/112/113/9000` antes de tocar
nada. El `10.10.10.1/24` del host colisiona de lleno con la VLAN 10 del lab (USERS,
`10.10.10.0/24`, gateway el firewall).

`vmbr1` no se toca, porque es de otro proyecto. Para el lab se creó `vmbr2`, VLAN-aware y
sin puerto físico. Es una desviación del spec, y todo el código usa la variable
`lab_bridge = "vmbr2"`.

## 2. Heredocs grandes por SSH desde la máquina Windows se truncan

El primer intento de escribir `bootstrap-pve.sh` (~250 líneas) con un heredoc a través de
`ssh` murió con `unexpected EOF while looking for matching quote`.

Un heredoc pequeño de prueba (`cat <<'X' | od -c`) llega limpio (LF), así que no era
CRLF en el transporte: el comando largo se trunca/mangla en la capa de la shell de
Windows.

Ahora se usa un pipeline de staging: el fichero se escribe en local, se copia con `scp`
(byte a byte), en destino se pasa `sed -i 's/\r$//'` y se valida con `bash -n` antes de
ejecutar.

## 3. Los ficheros creados en Windows llegan con CRLF

Los scripts escritos en el lado Windows del pipeline anterior llegaron con `\r` al final
de cada línea (263 CR en `bootstrap-pve.sh`). Lo confirmaron `grep -c $'\r'` en origen y
`file` en destino («with CRLF»).

Desde entonces se pasa `sed -i 's/\r$//'` sistemáticamente tras cada `scp`, se verifica
con `file`/`bash -n`, y `.gitattributes` lleva `* text=auto eol=lf` para que el repo
quede normalizado a LF pase lo que pase. Ningún fichero del repo se edita desde Windows
sin este pipeline.

## 4. La instalación mínima de Proxmox VE 9 no trae `sudo`

El paso 7 del bootstrap (la línea de sudoers del wrapper) rompió con
`visudo: command not found` (RC=127). `command -v visudo` salía vacío: PVE 9 se
administra como root y el paquete `sudo` no viene preinstalado.

El bootstrap instala ahora `sudo` (de forma idempotente: solo si falta `visudo`) antes
de escribir `/etc/sudoers.d/netseg-exec`, que se valida con `visudo -cf` antes de
instalarse.

## 5. Dos versiones de spec.md y de las convenciones en Downloads

En Downloads había `spec.md`/`CONVENTIONS.md` y `spec (1).md`/`CONVENTIONS (1).md`, con
hashes distintos, y los dos spec se declaraban «Versión 2».

Comparé hashes y longitudes: los `(1)` son más largos (482 vs 439 y 93 vs 83 líneas).
Los `(1)` son la fuente de verdad y son los que viven en el repo (`CONVENTIONS.md`,
`docs/spec.md`).

## 6. Primer SSH Windows→ctrl01 con timeout

El primer `ssh root@<IP-DE-CTRL01>` expiró (puerto 22), con ping funcionando en ambos
sentidos y sshd activo dentro del contenedor.

Desde pve01, `/dev/tcp/<IP-DE-CTRL01>/22` abría sin problema, y al reintentar desde
Windows ~1 min después funcionó. Encaja con la propagación ARP/MAC de un invitado
anidado (VMware bridge) recién arrancado.

Se resolvió reintentando (era transitorio). Si reaparece, el camino garantizado es entrar
por pve01 (`ProxyJump pve01`), como está anotado en el runbook.

Tras un reinicio del host el problema volvió, y además la IP de ctrl01 cambió (DHCP: de
.36 a .35). El acceso queda fijado permanentemente vía `ProxyJump pve01` y la IP se
descubre con `pct exec 900 -- ip -4 -o addr show eth0` (runbook).

## 7. El ISO live de OPNsense secuestró el gateway de la red doméstica

Fue la incidencia más seria del bloque 1. Con `fw01` arrancada (ISO live, sin instalar),
el PC físico anfitrión perdió Internet. `ping` a un destino externo pasó de responder a
devolver «Respuesta desde <PUERTA-DE-ENLACE-DOMESTICA>: Host de destino inaccesible», y
no se recuperó hasta apagar la VM de VMware entera.

La configuración de fábrica del live de OPNsense asigna LAN a la primera NIC, con IP
estática <PUERTA-DE-ENLACE-DOMESTICA>/24 y servidor DHCP activo. La `net0` de fw01 estaba
conectada a vmbr0, la LAN doméstica, que es justamente <RED-DOMESTICA>/24, así que el
firewall del lab se anunció como <PUERTA-DE-ENLACE-DOMESTICA> y envenenó la entrada ARP
del router real en el PC. Como el live no tiene ruta de salida, respondía ICMP
*destination unreachable* a todo. Al arrancar pve01, fw01 además autoarrancaba
(`onboot=1`), reintroduciendo el secuestro.

La contención inmediata se hizo como root (única vez, de emergencia):
`qm set 600 --net0 ...,link_down=1`, `--onboot 0` y `qm stop 600`. El ping del PC a
8.8.8.8 volvió al 0% de pérdida y <PUERTA-DE-ENLACE-DOMESTICA> volvió a responder con
TTL=64 (el router real). El arreglo estructural está en Terraform, para que sea imposible
por construcción: net0 = vmbr2 (trunk aislado: la LAN de fábrica cae ahí y no ve la
casa) y net1 = WAN en vmbr0 con `disconnected = true` hasta que la instalación manual
asigne interfaces. La reconexión de la WAN es un paso explícito post-instalación
documentado en el runbook.

## 8. Crear los LXC falló con 403: `VM.Config.Options` en `/vms/<vmid>`

El primer `terraform apply` creó las 5 VMs pero rompió al crear el primer contenedor:
`Permission check failed (/vms/610, VM.Config.Options)` (403).

Es una asimetría de la API de PVE. Al crear una VM QEMU con `pool=`, los checks de
permisos se resuelven a través del pool; al crear un LXC, varios checks (description,
tags y onboot, que piden `VM.Config.Options`) se evalúan contra la ruta `/vms/<vmid>`,
sobre la que el rol concedido en el pool aún no aplica (la VMID todavía no es miembro).
La validación del bootstrap solo creaba una VM QEMU de prueba, así que el hueco no se
detectó.

Se añadieron ACLs del rol `NetsegTFGuest` también sobre `/vms/<vmid>` para las
VMIDs reservadas del lab (600, 601, 610, 620, 621, 630, 640, 650, 699 + las de prueba
998/999). Sigue siendo mínimo privilegio: ninguna VMID ajena queda cubierta. Además, el
bootstrap valida ahora los dos tipos: crea y borra una VM y un CT de prueba vía API con
el token de terraform.

Quedaba el ciclo destroy/up. Borrar un invitado elimina los ACL de su ruta
`/vms/<vmid>`, y la prueba real del ciclo demostró que pasa con o sin purge, también en
LXC: tras `make destroy && make up`, los cuatro contenedores volvieron a fallar con 403.
El cierre definitivo es que el bootstrap instala `/usr/local/sbin/netseg-acl-restore.sh`
(sin argumentos, lista fija de VMIDs reservadas, misma cuenta `netseg` vía sudoers) y
`make up` lo invoca antes de terraform. Es el mismo patrón que el wrapper de exec:
delegación explícita y acotada en lugar de privilegios amplios.

## 9. AlmaLinux bloquea `guest-exec` en el agente QEMU de fábrica

En la primera pasada del harness contra las 8 máquinas, los 4 LXC respondieron, pero las
4 VMs devolvieron
`Agent error: Command guest-exec has been disabled: the command is not allowed`.
El agente corre (responde), pero el RPC que necesita el harness está vetado.

En RHEL/AlmaLinux, `/etc/sysconfig/qemu-ga` arranca el agente con
`FILTER_RPC_ARGS="--allow-rpcs=..."`, y esa allow-list no incluye `guest-exec` ni
`guest-exec-status` (endurecimiento por defecto de la familia RHEL). Se confirmó
montando el qcow2 GenericCloud con `qemu-nbd` y leyendo el fichero. El problema es de
huevo y gallina: sin exec no se puede entrar a arreglarlo, y las VLANs aún no rutan.

La primera capa del arreglo está en `scripts/fetch-images.sh`, que genera una variante
de la imagen (`AlmaLinux-9-GenericCloud-netseg.x86_64.qcow2`) montándola offline con
`qemu-nbd` y ampliando la allow-list con los dos RPCs. Terraform importa esa variante.

Con el RPC ya permitido apareció una segunda capa, SELinux: `guest-exec` moría con
`Failed to execute child process "hostname" (Permission denied)` sin un solo AVC, ni
siquiera con `semodule -DB` (dontaudit). Un `strace -f` del agente en una VM de
diagnóstico desechable (creada con el token en vmbr0) mostró el `execve` real:
`execve("/usr/bin/hostname") = -1 EACCES`. Con `id -Z` vía agente se confirmó que los
hijos corren confinados en `virt_qemu_ga_t`: ese dominio solo ejecuta `bin_t`/shell, no
ve binarios con dominio propio (`hostname_exec_t`) y no tiene red, así que no sirve para
sondas. El boolean `virt_qemu_ga_run_unconfined` por sí solo no transiciona un `sh`
normal. El mecanismo del vendor es un entrypoint etiquetado
`virt_qemu_ga_unconfined_exec_t`, que con el boolean activo transiciona los hijos a
`virt_qemu_ga_unconfined_t`.

Para esa segunda capa, la imagen lleva ahora (a) una unidad oneshot de primer arranque
que hace `setsebool -P virt_qemu_ga_run_unconfined on` y se autodesactiva, y (b)
`/usr/local/sbin/netseg-agent-runner` (`exec "$@"`, etiquetado
`virt_qemu_ga_unconfined_exec_t` por xattr offline). El harness (conftest) prefija todos
los comandos de VM con ese runner. Verificado en vivo: `id -Z` devuelve
`virt_qemu_ga_unconfined_t`, y `hostname` y un connect TCP de Python funcionan, con
`getenforce` en `Enforcing`. SELinux no se toca (ni permissive ni dontaudit): se usa el
mecanismo que prevé la propia política de RHEL. El rol `common` declara boolean, runner y
allow-list para que la imagen y Ansible converjan al mismo estado.

## 22. La VPN levantada falseaba la matriz del perímetro

Tras montar WireGuard, la matriz empezó a reportar que `ext01` alcanzaba `jump01`: un
bloqueo incumplido que en apariencia era grave.

La política estaba bien; el fallo era de modelo. `ext01` hace dos papeles: el
desconocido de fuera (que la matriz mide) y el cliente VPN autenticado (que sí debe
llegar a MGMT). Con el túnel arriba, los dos se mezclaban.

Ahora los escenarios van separados. La matriz baja el túnel antes de sondear (mide
el perímetro frente a alguien sin credenciales) y `tests/test_vpn.py` lo levanta para lo
suyo. Cada test dice qué está midiendo.

## 21. El port forward de 443 se comió el acceso a la propia GUI

Al publicar 80/443 hacia `dmz-web`, la administración del firewall dejó de responder
desde cualquier sitio.

El destino del port forward es `wanip`, la IP de la WAN, que es la misma por la que se
administra el firewall. El NAT tiene prioridad, así que todo el 443 entrante se iba a la
DMZ, GUI incluida.

Se separó la gestión del servicio publicado, que es lo que se hace en producción: la GUI
pasa al 8443 y el 443 queda libre para la web publicada. El cambio se aplicó por el canal
del agente (`write_config()`), porque en ese momento la GUI ya no era alcanzable. El nuevo acceso de gestión se declara en `flows.yml`
(`gestion-del-firewall`), como cualquier otro flujo.

## 20. Dos detalles de Ansible que rompían el playbook

- `group_vars/vault.yml` no se carga solo: Ansible solo autocarga los ficheros
  de `group_vars` cuyo nombre coincide con un grupo. Se declara con `vars_files`.
- `ansible_managed` solo existe dentro del módulo `template`; en un `copy`
  con `content:` queda indefinido y aborta la tarea. Se sustituyó por una nota
  literal en el único sitio donde se usaba así.

## 19. «Salir a Internet» abría también las redes internas

Esta la encontró la propia suite. Con los servicios ya provisionados, la matriz destapó
que `guest01` e `iot01` alcanzaban la web de la DMZ, y que `dmz-web` llegaba a los
servidores internos: seis bloqueos incumplidos.

Yo había traducido «salida a Internet» (`dst: WAN`) como destino `any`. Y `any` incluye
10.10.0.0/16: permitir navegar por 80/443 abría de paso el camino hacia los demás
segmentos.

La solución es un alias `netseg_redes_internas`, derivado de los CIDR de `flows.yml`, y
las reglas de salida se generan negando ese alias: «a cualquier sitio menos aquí
dentro». Hay un test unitario que lo fija.

Es justo el tipo de fallo que este laboratorio está pensado para cazar, y lo cazó la
derivación exhaustiva de bloqueos, no una revisión a ojo. Un `deny` escrito a mano nunca
habría probado esa combinación.

## 18. Las sondas de los clientes Alpine no distinguen rechazo de silencio

En la primera pasada de la matriz, `guest01` e `iot01` aparecían con su salida a
Internet bloqueada pese a que la política la permite.

Los clientes Alpine sondean con `nc` de busybox, que devuelve el mismo código de salida
para un RST (puerto cerrado) y para el silencio de un `drop`, y la sonda traducía ese
fallo como «bloqueado». Instalar `python3` en esos contenedores para usar la sonda buena
no es posible: `apk` necesita salir a Internet por 80 y la política de IOT solo permite
443, así que el propio laboratorio impide el atajo.

El primer arreglo fue que la sonda reportara `sin-respuesta` y que la suite tratara ese
estado como no concluyente en los permisos, en vez de dar por verificado algo que la
herramienta no puede ver.

El arreglo definitivo fue medir el tiempo. Un rechazo vuelve al instante y un descarte
agota la espera, así que con dos lecturas de `date +%s` y un timeout de 2 s la sonda de
busybox ya distingue `closed` de `timeout`. Las cinco sondas no concluyentes
desaparecieron y la matriz quedó en 297/297.

## 18b. La semántica de `closed` estaba mal en la propia suite

Con los servicios sin provisionar, 24 permisos aparecían como incumplidos aunque la
política era correcta.

Yo daba por bueno un permiso solo si el puerto estaba `open`. Pero un RST (`closed`)
demuestra que el paquete atravesó el firewall y llegó al host: la política permite el
flujo, simplemente no hay servicio escuchando. El único estado que prueba un bloqueo es
la ausencia de respuesta.

Ahora un permiso se verifica con `open` o `closed`, y un bloqueo solo con silencio. Al
corregirlo, la suite dejó de dar falsos negativos y además empezó a dar verdaderos
positivos: así apareció la incidencia #19.

## 17. `search_rule` devuelve cero reglas aunque existan

`make check` reportaba que faltaban las 41 reglas justo después de crearlas, y una
segunda pasada de `apply.py` las habría duplicado.

`/api/firewall/filter/search_rule` devuelve `total: 0` tanto por GET como por POST,
mientras que el modelo (`/api/firewall/filter/get`) sí las lista con su uuid.

Así que se lee siempre el modelo. Como efecto secundario, los campos llegan en el
formato de enumerado de OPNsense (`{"opt1": {"selected": 1}}`), y la comparación los
normaliza con un ayudante compartido.

## 16. OPNsense no admite listas de puertos en una regla

`apply.py` abortó con
`rule.destination_port: Please specify a valid portnumber, name, alias or range`. El
campo admite un puerto, un rango o un alias, pero no `80,443`.

Así que se genera una regla por puerto. Sale una matriz más granular (41 reglas para 17
flujos) y cada permiso queda rastreable regla a regla.

## 15. El listado de VLAN decora el nombre del dispositivo

En la segunda ejecución de `setup_red.py`, las seis interfaces desaparecieron de la API
(aunque el sistema seguía teniendo las IPs).

`config.xml` tenía `<if>vlan01 [USERS]</if>`. El endpoint `vlan_settings/search_item`
decora la columna del dispositivo con el nombre de la interfaz cuando la VLAN ya está
asignada, y el script tomó ese texto como nombre real. La investigación previa de la API
ya avisaba de esta trampa.

Ahora el script limpia el sufijo `" [...]"` al leer. Era un fallo de idempotencia: solo
aparecía en la segunda pasada.

## 14b. Un `guest exec` largo deja colgado al agente QEMU

El script de asignación (que llama a `write_config()`) superaba el tiempo del agente y,
a partir de ahí, cualquier `qm guest exec` contra fw01 devolvía «QEMU guest agent is not
running» hasta reiniciar el servicio.

Había dos causas sumadas: el wrapper usaba `--timeout 30` (insuficiente también para las
baterías de sondas de la suite) y el proceso lanzado en segundo plano heredaba la salida
estándar, con lo que el agente seguía esperando el EOF. Además, pasar el script en
base64 como argumento del `exec` agravaba el bloqueo.

El timeout del wrapper sube a 300 s, y los scripts largos se transportan por HTTP
(`scripts/fw_run.py` levanta un servidor efímero) y se lanzan con doble fork y los tres
descriptores redirigidos.

## 13. La GUI de fw01 no respondía: LAN en la misma subred que la WAN

Con la instalación terminada y `pf` desactivado (`pfctl -d`), la GUI seguía inalcanzable
desde el PC físico: ARP sí (`REACHABLE`), pero ni ICMP ni TCP.

Por captura de la consola (`qm monitor … screendump`) se vio que LAN=vtnet0 quedó en
`<PUERTA-DE-ENLACE-DOMESTICA>/24` y WAN=vtnet1 en `<IP-WAN-DEL-FIREWALL>/24`, la misma
/24. FreeBSD tiene una sola ruta para esa red y se la lleva la LAN, así que las
respuestas hacia el PC salían por el trunk aislado y se perdían. `pf` era una pista
falsa; el problema era de routing.

Hubo que mover la LAN fuera de esa /24. Se automatizó el tecleo en la consola vía la API
de Proxmox (`sendkey`), con un helper `console-type.sh` que traduce el keymap (la consola
quedó en español; ver #12), y cada `screendump` se convertía a PNG para ver el paso. LAN
reasignada a `10.10.0.1/24` (opción 2 del menú); la GUI pasó a responder 200 y el ping a
0 ms. La IP definitiva de la LAN la fija la fase 3 con las VLANs.

## 12. Desajuste de keymap en la consola VNC de fw01

En la consola de fw01 no se podía escribir `-` (salía `/` o `'`).

La VM se creó sin `keyboard` (QEMU asume en-us en VNC) mientras la instalación de
OPNsense quedó con keymap español, así que los símbolos se traducían mal.

Se arregla con `keyboard_layout = "es"` en la VM de fw01 (Terraform). El helper
`console-type.sh` que se usa para pilotar la consola incorpora la misma tabla ES.

## 14. El plugin os-qemu-guest-agent no se instaló desde la GUI

Tras "instalarlo" en Firmware → Plugins, el harness contra fw01 daba
`QEMU guest agent is not running`; en consola, `pkg info -x qemu` no devolvía nada.

El plugin no llegó a quedar instalado (la acción de la GUI no cuajó). `os-firewall` ya
no existe como plugin en 25.7 (está integrado en el núcleo), así que solo hacía falta
este.

Se instaló en consola con `pkg install -y os-qemu-guest-agent`,
`sysrc qemu_guest_agent_enable=YES` y `service qemu-guest-agent start`. Con el agente
corriendo (pid 3921), las 9 máquinas del pool responden por el wrapper. La VM ya tenía
`agent = true` en Terraform desde este paso.

## 11. El instalador de OPNsense exige más RAM que la asignación del spec

Con los 2048 MB de fw01 (spec §4), el instalador del DVD avisó: «copying the full file
system … requires at least 3000MB of RAM».

El live monta el sistema en RAM durante la copia; con 2 GB el riesgo es quedarse sin
memoria a mitad de instalación.

Se subió temporalmente a 4096 MB vía Terraform (apply dirigido a fw01), se instaló y se
revirtió a 2048 en el mismo apply que reconectó la WAN. El presupuesto de memoria en
operación no cambia, y queda anotado en `main.tf` para la próxima reinstalación.

## 10. ansible-lint no veía las colecciones (aislamiento de venvs de pipx)

`ansible-lint` fallaba con `couldn't resolve module/action` para
`ansible.posix.firewalld`, `community.general.sefcontext` y
`community.postgresql.postgresql_pg_hba`, con las colecciones «instaladas».

pipx aísla: el paquete `ansible` trae las colecciones dentro de su venv, y
`ansible-lint` (otro venv, con su propio ansible-core) no las ve. `ansible-galaxy`
además decía «Nothing to do» porque las encontraba en su propia ruta. `ansible-doc`
confirmó que los módulos existían; era pura resolución.

La solución es `ansible-galaxy collection install -r ansible/requirements.yml -p
~/.ansible/collections --force`, la ruta que cualquier ansible-core consulta. En CI
ocurre lo mismo (`pip install ansible-lint` no trae colecciones): el workflow instala
las colecciones como paso previo al lint.
